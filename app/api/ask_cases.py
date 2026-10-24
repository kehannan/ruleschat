"""Eval cases captured from the VASL extension ("Save as eval case").

An admin flags an Ask LLM answer in the extension; it re-sends the original
/api/ask request (with the .vsav) plus the answer it got and an optional
note. Each case is stored as

    data/ask_cases/<id>/case.json   question, VASL facts, answer, note, ...
    data/ask_cases/<id>/save.vsav   the game save, as sent

so it can be replayed (calculator eval) and viewed (board viewer). Admin-only
throughout: saves contain both sides' units, hidden ones included.
"""
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.api import ask as ask_api
from app.core.auth import get_current_user
from app.models.user import User
from app.services import board_render
from app.services.user_service import is_admin
from app.services.vsav_service import (
    VsavError, decode_vsav_data_url, parse_vsav, render_manifest,
)
from app.templating import templates


def _context(request: Request, user: User, **extra) -> Dict[str, Any]:
    from app.api.user import get_base_context  # local: user.py imports many routers
    ctx = get_base_context(request, user)
    ctx.update(extra)
    return ctx

router = APIRouter()

CASES_DIR = Path(os.getenv("ASK_CASES_DIR", "data/ask_cases"))
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
_MAX_TEXT = 20000


def _case_dir(case_id: str) -> Path:
    if not _ID.match(case_id or ""):
        raise HTTPException(status_code=404, detail="No such case")
    d = CASES_DIR / case_id
    if not (d / "case.json").exists():
        raise HTTPException(status_code=404, detail="No such case")
    return d


def _load(case_id: str) -> Dict[str, Any]:
    return json.loads((_case_dir(case_id) / "case.json").read_text())


def _admin_by_key(authorization: Optional[str]) -> User:
    mode, cred = ask_api._resolve_credential(authorization)
    if mode != "account" or not is_admin(cred):
        raise HTTPException(status_code=403,
                            detail="Saving eval cases needs an admin ruleschat key")
    return cred


@router.post("/api/ask/cases")
def save_case(payload: dict = Body(...),
              authorization: Optional[str] = Header(None)):
    """Store one flagged answer. Body: {request, answer, note?,
    calculator_html?, meta?, extension_version?} — `request` is the
    original /api/ask body."""
    user = _admin_by_key(authorization)
    req = payload.get("request")
    if not isinstance(req, dict) or not str(req.get("question") or "").strip():
        raise HTTPException(status_code=400, detail="request.question is required")
    raw = None
    if req.get("vsav"):
        try:
            raw = decode_vsav_data_url(req["vsav"])
        except VsavError as e:
            raise HTTPException(status_code=400, detail=f"VASL save rejected: {e}")

    now = datetime.now(timezone.utc)
    case_id = f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    d = CASES_DIR / case_id
    d.mkdir(parents=True, exist_ok=False)
    request = {k: v for k, v in req.items() if k != "vsav"}
    case = {
        "id": case_id,
        "created_at": now.isoformat(),
        "user_id": user.id,
        "extension_version": str(payload.get("extension_version") or "")[:40],
        "note": str(payload.get("note") or "")[:_MAX_TEXT],
        "answer": str(payload.get("answer") or "")[:_MAX_TEXT * 5],
        "meta": str(payload.get("meta") or "")[:500],
        "has_save": raw is not None,
        "request": request,
        # Filled in by hand when the case becomes an eval (see
        # docs/vasl_extension.md): structured golden fields + golden note.
        "expected": None,
        "expected_answer": None,
    }
    if raw is not None:
        (d / "save.vsav").write_bytes(raw)
    (d / "case.json").write_text(json.dumps(case, indent=2, default=str))
    logging.info("ask case saved: %s (%s)", case_id, request.get("question", "")[:80])
    return {"id": case_id}


def _admin_page_user(user: Optional[User]):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=303)
    return None


def _recompute(case: Dict[str, Any], case_dir: Path) -> Optional[Dict[str, Any]]:
    """Re-run the calculator on the stored save + facts with today's code."""
    req = case.get("request") or {}
    if not case.get("has_save") or not req.get("native_los"):
        return None
    try:
        state = parse_vsav(str(case_dir / "save.vsav"))
        return ask_api._precompute_attack(
            req.get("question", ""), state, req.get("native_los"),
            req.get("game_phase"), req.get("selected_firers"),
            req.get("selected_targets"))
    except Exception as e:
        logging.warning("ask case %s: recompute failed: %s", case.get("id"), e)
        return {"error": f"recompute failed: {e}"}


@router.get("/admin/ask-cases", name="admin_ask_cases", response_class=HTMLResponse)
async def list_cases(request: Request, user: User = Depends(get_current_user)):
    redirect = _admin_page_user(user)
    if redirect:
        return redirect
    cases = []
    if CASES_DIR.exists():
        for d in sorted(CASES_DIR.iterdir(), reverse=True):
            try:
                c = json.loads((d / "case.json").read_text())
            except Exception:
                continue
            cases.append(c)
    return templates.TemplateResponse(
        "admin_ask_cases.html", _context(request, user, cases=cases, case=None))


@router.get("/admin/ask-cases/{case_id}", name="admin_ask_case", response_class=HTMLResponse)
async def view_case(case_id: str, request: Request,
                    user: User = Depends(get_current_user)):
    redirect = _admin_page_user(user)
    if redirect:
        return redirect
    case = _load(case_id)
    result = _recompute(case, _case_dir(case_id))
    los = (case.get("request") or {}).get("native_los") or {}
    calc_html = ask_api._calculator_html(result, los) if result else None
    return templates.TemplateResponse(
        "admin_ask_cases.html", _context(
            request, user, cases=None, case=case, calc_html=calc_html,
            facts_json=json.dumps(los, indent=2, default=str)))


@router.get("/api/admin/ask-cases/{case_id}/manifest")
async def case_manifest(case_id: str, user: User = Depends(get_current_user)):
    if not user or not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    d = _case_dir(case_id)
    if not (d / "save.vsav").exists():
        raise HTTPException(status_code=404, detail="This case has no save")
    state = parse_vsav(str(d / "save.vsav"))
    background = None
    try:
        background = board_render.build_background(state)
    except Exception as e:  # best-effort, as in /api/vsav/preview
        logging.warning("ask case %s: background build failed: %s", case_id, e)
    return render_manifest(state, background)


@router.get("/api/admin/ask-cases/{case_id}/{name}")
async def case_file(case_id: str, name: str, user: User = Depends(get_current_user)):
    """Raw downloads for the evals sync: case.json or save.vsav."""
    if not user or not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    if name not in ("case.json", "save.vsav"):
        raise HTTPException(status_code=404, detail="No such file")
    path = _case_dir(case_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(path, filename=f"{case_id}-{name}")
