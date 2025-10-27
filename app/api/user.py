"""User profile and account management routes."""
import secrets
import string
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_password_hash, verify_password
from app.database import get_db
from app.models import User
from app.services.user_service import update_user_profile, get_user_by_email

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


def generate_api_key(length: int = 32) -> str:
    """Generate a secure API key."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@router.get("/profile", name="profile_page", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Display user profile page."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    context = get_base_context(request, user)
    context.update({
        "user": user,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("message_type", "info")
    })
    return templates.TemplateResponse("profile.html", context)


@router.post("/update-profile", response_class=RedirectResponse, name="update_profile")
async def update_profile(
    request: Request,
    email: str = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user email address."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    if email and email != user.email:
        # Check if email is already taken
        existing_user = get_user_by_email(db, email)
        if existing_user and existing_user.id != user.id:
            return RedirectResponse(
                url="/profile?message=Email already in use&message_type=error",
                status_code=303
            )
        
        # Update email
        update_user_profile(db, user.id, email=email)
        return RedirectResponse(
            url="/profile?message=Profile updated successfully&message_type=success",
            status_code=303
        )
    
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/change-password", response_class=RedirectResponse, name="change_password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change user password."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Verify current password
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse(
            url="/profile?message=Current password is incorrect&message_type=error",
            status_code=303
        )
    
    # Check password match
    if new_password != confirm_password:
        return RedirectResponse(
            url="/profile?message=New passwords do not match&message_type=error",
            status_code=303
        )
    
    # Update password
    hashed_password = get_password_hash(new_password)
    update_user_profile(db, user.id, hashed_password=hashed_password)
    
    return RedirectResponse(
        url="/profile?message=Password changed successfully&message_type=success",
        status_code=303
    )


@router.post("/generate-api-key", response_class=RedirectResponse, name="generate_api_key")
async def generate_new_api_key(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generate a new API key for the user."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Generate new API key
    new_api_key = generate_api_key()
    update_user_profile(db, user.id, api_key=new_api_key)
    
    return RedirectResponse(
        url="/profile?message=New API key generated&message_type=success",
        status_code=303
    )


@router.get("/admin", name="admin_dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin dashboard - simple placeholder for now."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Check if user is admin
    import os
    admin_email = os.getenv("ADMIN_EMAIL")
    if user.email != admin_email:
        return RedirectResponse(url="/", status_code=303)
    
    # Get all users and invitations
    from app.models import User as UserModel, Invitation
    from datetime import datetime
    
    users = db.query(UserModel).all()
    invitations = db.query(Invitation).filter(
        Invitation.expires_at > datetime.utcnow()
    ).order_by(Invitation.created_at.desc()).all()
    
    context = get_base_context(request, user)
    context["users"] = users
    context["invitations"] = invitations
    context["message"] = request.query_params.get("message")
    context["message_type"] = request.query_params.get("message_type", "info")
    
    return templates.TemplateResponse("admin.html", context)


@router.post("/admin/create-test-user", name="admin_create_test_user")
async def admin_create_test_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a test user (admin only)."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Check if user is admin
    import os
    admin_email = os.getenv("ADMIN_EMAIL")
    if user.email != admin_email:
        return RedirectResponse(url="/", status_code=303)
    
    # Check if user already exists
    existing_user = get_user_by_email(db, email)
    if existing_user:
        return RedirectResponse(
            url="/admin?message=User already exists&message_type=danger",
            status_code=303
        )
    
    # Create new user
    from app.core.auth import get_password_hash
    hashed_password = get_password_hash(password)
    
    new_user = User(
        email=email,
        hashed_password=hashed_password
    )
    db.add(new_user)
    db.commit()
    
    return RedirectResponse(
        url="/admin?message=Test user created successfully&message_type=success",
        status_code=303
    )

