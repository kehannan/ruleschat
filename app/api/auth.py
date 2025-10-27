"""Authentication routes."""
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import create_access_token, verify_password, get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES
from app.database import get_db
from app.services.user_service import get_user_by_email
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_base_context(request: Request, user: User = None):
    """Get base template context."""
    import os
    context = {"request": request, "user": user}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return context


@router.get("/login", name="login", response_class=HTMLResponse)
def login_page(request: Request):
    """Show login page."""
    context = get_base_context(request)
    return templates.TemplateResponse("login.html", context)


@router.post("/login")
def do_login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Handle login form submission."""
    user = get_user_by_email(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return HTMLResponse("<h3>Invalid credentials</h3>", status_code=401)
    
    token = create_access_token(
        {"sub": user.email},
        expires_delta=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    response = RedirectResponse(url="/ruleschat", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="Lax",
        max_age=3600 * ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return response


@router.get("/logout", name="logout")
def logout():
    """Log out user."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response

