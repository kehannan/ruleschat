"""
To Hit / To Kill probability calculator — deterministic, no LLM.

  GET /thtk                 → the calculator UI page
  GET /api/thtk/compute     → JSON TH/TK result for one ordnance attack
"""
import os

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.asl.thtk import compute, get_options

router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def get_current_user(request: Request):
    """Resolve the current user from the access-token cookie (navbar context)."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    from jose import jwt, JWTError
    from app.core.auth import SECRET_KEY, ALGORITHM
    from app.services.user_service import get_user_by_email
    from app.database import SessionLocal
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email:
            db = SessionLocal()
            try:
                return get_user_by_email(db, email)
            finally:
                db.close()
    except JWTError:
        pass
    return None


@router.get("/thtk", name="thtk", response_class=HTMLResponse)
async def thtk_page(request: Request, user=Depends(get_current_user)):
    """Render the To Hit / To Kill probability calculator page."""
    from app.api.demo import is_demo_enabled
    opts = get_options()
    context = {
        "request": request,
        "demo_enabled": is_demo_enabled(),
        "target_types": opts["target_types"],
        "weapon_types": opts["weapon_types"],
        "ammo_types": opts["ammo_types"],
        "nationalities": opts["nationalities"],
    }
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return templates.TemplateResponse("thtk.html", context)


@router.get("/api/thtk/compute", name="thtk_compute")
async def thtk_compute(
    target_type: str = Query(..., description="vehicle | infantry | area"),
    range: int = Query(..., ge=0, description="Range to target, in hexes"),
    weapon_type: str = Query(..., description="* | L | LL (barrel class)"),
    ammo: str = Query(..., description="AP/HE | Smoke | APDS/APCR"),
    mm: int = Query(..., gt=0, description="Weapon size in mm"),
    nationality: str = Query("", description="Firer nationality"),
    hit_drm: int = Query(0, description="Net Hit Determination DRM; positive is harder"),
):
    """Return the TH/TK result for one ordnance attack."""
    try:
        data = compute(
            target_type=target_type,
            rng=range,
            weapon_type=weapon_type,
            ammo=ammo,
            mm=mm,
            nationality=nationality,
            hit_drm=hit_drm,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(data)
