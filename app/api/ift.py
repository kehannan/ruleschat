"""
Infantry Fire Table probability calculator — deterministic, no LLM.

  GET /ift                      → the calculator UI page
  GET /api/ift/distribution     → JSON probability distribution for one attack
"""
import os

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.asl.ift import compute_distribution, valid_columns, COWERING_SHIFT, get_table

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


@router.get("/api/ift/distribution", name="ift_distribution")
async def ift_distribution(
    column: int = Query(..., description="FP column, e.g. 16"),
    drm: int = Query(0, description="Total DR modifier; negative is favorable"),
    cowering: str = Query("none", description="none | regular | double"),
):
    """Return the probability distribution of IFT results for one attack."""
    try:
        data = compute_distribution(column=column, drm=drm, cowering=cowering)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(data)
