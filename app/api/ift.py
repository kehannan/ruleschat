"""
Infantry Fire Table probability calculator — deterministic, no LLM.

  GET  /ift                      → the calculator UI page
  GET  /api/ift/distribution     → JSON probability distribution for one attack
  POST /api/ift/attack           → full attack builder (units → FP → DRM →
                                   distribution → target effects)
"""
import os
from typing import List, Optional

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.asl.ift import (
    compute_attack,
    compute_distribution,
    valid_columns,
    COWERING_SHIFT,
    PBF_MULTIPLIER,
    get_table,
)

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


@router.get("/ift", name="ift", response_class=HTMLResponse)
async def ift_page(request: Request, user=Depends(get_current_user)):
    """Render the IFT probability calculator page."""
    from app.api.demo import is_demo_enabled
    context = {
        "request": request,
        "demo_enabled": is_demo_enabled(),
        "ift_columns": valid_columns(),
        "cowering_modes": list(COWERING_SHIFT.keys()),
        "ift_table": get_table(),
    }
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return templates.TemplateResponse("ift.html", context)


# --------------------------------------------------------------------------- #
# Attack builder request schema — mirrors ift.compute_attack's contract.
# --------------------------------------------------------------------------- #

class FiringUnit(BaseModel):
    fp: float = Field(..., gt=0, description="Printed FP of the unit or weapon")
    pbf: str = Field("none", description="none | pbf (x2) | tpbf (x3)")
    long_range: bool = False
    pinned: bool = False
    assault_fire: bool = False


class OtherDrm(BaseModel):
    label: str
    drm: int


class Target(BaseModel):
    kind: str = Field("personnel", description="personnel | vehicle")
    morale: Optional[int] = Field(None, ge=0, le=10)
    mc_drm: int = 0
    encircled: bool = False


class AttackRequest(BaseModel):
    units: List[FiringUnit]
    afph: bool = False
    opportunity_fire: bool = False
    area_fire_halvings: int = Field(0, ge=0, le=6)
    tem: int = Field(0, ge=-10, le=10)
    hindrance: int = Field(0, ge=0, le=10)
    ffnam: bool = False
    ffmo: bool = False
    leadership: int = Field(0, ge=-3, le=3)
    encircled_firer: bool = False
    other_drm: List[OtherDrm] = []
    inexperienced: bool = False
    firer_cowering_exempt: bool = False
    san: Optional[int] = Field(None, ge=2, le=12)
    target: Optional[Target] = None


def _dump(model: BaseModel) -> dict:
    """Pydantic v1/v2 compatible model → dict (no version pin in this repo)."""
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


@router.post("/api/ift/attack", name="ift_attack")
async def ift_attack(req: AttackRequest):
    """
    Resolve a full IFT attack: per-unit FP modification (A7.2-.36) → column →
    itemized DRM → cowering → distribution → optional target effects.

    A total FP below the 1 column is not an HTTP error — the engine returns a
    200 payload with an `error` field plus the fp_breakdown, so the UI can
    still show the math that got there.
    """
    if not req.units:
        return JSONResponse({"error": "At least one firing unit is required."},
                            status_code=400)
    if any(u.pbf not in PBF_MULTIPLIER for u in req.units):
        return JSONResponse(
            {"error": f"pbf must be one of {list(PBF_MULTIPLIER)}"}, status_code=400
        )
    try:
        data = compute_attack(
            units=[_dump(u) for u in req.units],
            afph=req.afph,
            opportunity_fire=req.opportunity_fire,
            area_fire_halvings=req.area_fire_halvings,
            tem=req.tem,
            hindrance=req.hindrance,
            ffnam=req.ffnam,
            ffmo=req.ffmo,
            leadership=req.leadership,
            encircled_firer=req.encircled_firer,
            other_drm=[_dump(o) for o in req.other_drm],
            inexperienced=req.inexperienced,
            firer_cowering_exempt=req.firer_cowering_exempt,
            san=req.san,
            target=_dump(req.target) if req.target else None,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(data)


@router.get("/api/ift/distribution", name="ift_distribution")
async def ift_distribution(
    column: int = Query(..., description="FP column, e.g. 16"),
    drm: int = Query(0, description="Total DR modifier; negative is favorable"),
    cowering: str = Query("none", description="none | regular | double"),
    san: int = Query(3, description="Enemy Sniper Activation Number, 2-12"),
):
    """Return the probability distribution of IFT results for one attack."""
    try:
        data = compute_distribution(column=column, drm=drm, cowering=cowering, san=san)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(data)
