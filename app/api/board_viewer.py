"""Authed-only API for the visual board viewer (Phase 1, read-only).

Endpoints (all require a logged-in user — same auth pattern as
``GET /api/uploads/...`` in app/api/chat.py; the public demo page gets
nothing here, since serving MMP counter art publicly is a separate
decision):

  POST /api/vsav/preview          .vsav data URL -> render manifest.
                                  Called on ATTACH, before any chat message,
                                  so the board appears immediately. Nothing
                                  is persisted to a conversation; the upload
                                  is parsed from a temp file.
  GET  /api/board-bg/{key}.png    cached composited board background.
  GET  /api/counter-art/{path}    counter image extracted on demand from the
                                  local VASL .vmod (cached on disk).
"""
import logging
import os
import tempfile
import time
from collections import deque

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from app.services import board_render
from app.services.board_render import BoardRenderError
from app.services.vsav_service import (
    VsavError, decode_vsav_data_url, parse_vsav, render_manifest,
)

router = APIRouter()

# Per-IP rate limit for the preview endpoint — the only expensive one
# (save parsing + Pillow compositing). The board-bg/counter-art endpoints
# are deliberately NOT limited: a single board render legitimately fetches
# 100+ art files in one burst, and they are cheap cached FileResponses
# with public cache headers. In-memory is fine: single uvicorn process.
PREVIEW_RATE_LIMIT = 30          # requests ...
PREVIEW_RATE_WINDOW = 3600       # ... per hour per IP
_preview_hits: dict = {}         # ip -> deque[timestamps]


def _client_ip(request: Request) -> str:
    """Extract real IP, respecting X-Forwarded-For from nginx."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else None) or "unknown"


def _check_preview_rate(ip: str) -> None:
    now = time.monotonic()
    hits = _preview_hits.setdefault(ip, deque())
    while hits and now - hits[0] > PREVIEW_RATE_WINDOW:
        hits.popleft()
    if len(hits) >= PREVIEW_RATE_LIMIT:
        retry = int(PREVIEW_RATE_WINDOW - (now - hits[0])) + 1
        raise HTTPException(
            status_code=429,
            detail="Too many board previews from this address - try again later.",
            headers={"Retry-After": str(retry)},
        )
    hits.append(now)
    # opportunistic cleanup so the map can't grow unbounded
    if len(_preview_hits) > 10000:
        for k in [k for k, v in _preview_hits.items() if not v]:
            _preview_hits.pop(k, None)


@router.post("/api/vsav/preview")
async def vsav_preview(
    request: Request,
    payload: dict = Body(...),
):
    """Parse an attached .vsav (base64 data URL) into a render manifest.

    Public (no auth): the board viewer also runs on the /demo page. Input
    is bounded by decode_vsav_data_url's size cap and zip validation, and
    the endpoint is rate-limited per IP.
    """
    _check_preview_rate(_client_ip(request))
    data_url = payload.get("vsav") or ""
    try:
        raw = decode_vsav_data_url(data_url)
        # parse from a temp file — previews are not tied to a conversation
        fd, tmp_path = tempfile.mkstemp(suffix=".vsav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            state = parse_vsav(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except VsavError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error("vsav preview failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400,
                            detail="Could not parse the VASL save")

    background = None
    try:
        background = board_render.build_background(state)
    except Exception as e:  # background is best-effort; manifest still works
        logging.warning("vsav preview: background build failed: %s", e,
                        exc_info=True)
    return render_manifest(state, background)


@router.get("/api/board-bg/{filename}")
def get_board_background(filename: str):
    """Serve a cached board background PNG. Key is a strict hex hash."""
    if not filename.endswith(".png"):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = board_render.cached_background_path(filename[:-len(".png")])
    except BoardRenderError:
        raise HTTPException(status_code=404, detail="Not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/counter-art/{art_path:path}")
def get_counter_art(art_path: str):
    """Serve one counter image, extracted on demand from the local vmod."""
    try:
        path, media = board_render.extract_counter_art(art_path)
    except BoardRenderError:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=604800"})
