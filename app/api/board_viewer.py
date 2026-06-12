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

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.auth import require_user
from app.models.user import User
from app.services import board_render
from app.services.board_render import BoardRenderError
from app.services.vsav_service import (
    VsavError, decode_vsav_data_url, parse_vsav, render_manifest,
)

router = APIRouter()


@router.post("/api/vsav/preview")
async def vsav_preview(
    payload: dict = Body(...),
    user: User = Depends(require_user),
):
    """Parse an attached .vsav (base64 data URL) into a render manifest."""
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
def get_board_background(
    filename: str,
    user: User = Depends(require_user),
):
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
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/counter-art/{art_path:path}")
def get_counter_art(
    art_path: str,
    user: User = Depends(require_user),
):
    """Serve one counter image, extracted on demand from the local vmod."""
    try:
        path, media = board_render.extract_counter_art(art_path)
    except BoardRenderError:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "private, max-age=604800"})
