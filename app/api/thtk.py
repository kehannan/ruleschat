"""
Ordnance To Hit / To Kill calculator — deterministic, no LLM.

  GET  /thtk                 → the calculator UI page
  GET  /api/thtk/options     → enums for the form (nationalities, ammo, ...)
  POST /api/thtk/attack      → situation → TH ledger → hit tree → TK/IFT odds
  POST /api/thtk/odds        → known Modified TH# / DRM / Final TK# → odds

Same engine (`app.asl.thtk`) as the `ordnance_attack` / `thtk_odds` agentic
tools, so the page, the website chat and the VASL extension always agree.
"""
import inspect
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.ift import get_current_user
from app.asl import thtk
from app.templating import templates

router = APIRouter()

_SHOT_KEYS = set(inspect.signature(thtk.compute_shot).parameters) - {"th_drm", "double_lower_dr"}
_ATTACK_KEYS = _SHOT_KEYS | thtk._DRM_KEYS
_ODDS_KEYS = set(inspect.signature(thtk.quick_odds).parameters)


@router.get("/thtk", name="thtk", response_class=HTMLResponse)
async def thtk_page(request: Request, user=Depends(get_current_user)):
    """Render the TH/TK calculator page."""
    from app.api.demo import is_demo_enabled
    context = {
        "request": request,
        "demo_enabled": is_demo_enabled(),
        "thtk_options": thtk.get_options(),
    }
    if user:
        from app.services.user_service import is_admin
        context["user_email"] = user.email
        context["is_admin"] = is_admin(user)
    return templates.TemplateResponse("thtk.html", context)


@router.get("/api/thtk/options", name="thtk_options")
async def thtk_options():
    return thtk.get_options()


def _run(fn, payload: Dict[str, Any], allowed: set):
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return JSONResponse({"error": f"Unknown field(s): {', '.join(unknown)}"}, status_code=400)
    try:
        return fn(**payload)
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/thtk/attack", name="thtk_attack")
def thtk_attack(payload: Dict[str, Any] = Body(...)):
    """Resolve one ordnance shot from the situation (see thtk.compute_attack)."""
    return _run(thtk.compute_attack, payload, _ATTACK_KEYS)


@router.post("/api/thtk/odds", name="thtk_odds")
def thtk_odds(payload: Dict[str, Any] = Body(...)):
    """Odds from already-known Modified TH# / DRM / Final TK# (thtk.quick_odds)."""
    return _run(thtk.quick_odds, payload, _ODDS_KEYS)
