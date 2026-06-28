"""Invitation management routes (admin-only).

Backs the "Send invitation" / resend / delete controls on the admin page.
Invitations are stored in the `invitations` table keyed by a random `code`;
the emailed link points at /register?code=<code>, which the registration flow
consumes in app/main.py.
"""
import os
import logging
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database import get_db
from app.models import User, Invitation

logger = logging.getLogger(__name__)

router = APIRouter()


class InvitationCreate(BaseModel):
    email: EmailStr


def require_admin(user: User) -> None:
    """Raise unless the request is from the configured admin user."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    admin_email = os.getenv("ADMIN_EMAIL")
    if not admin_email or user.email != admin_email:
        raise HTTPException(status_code=403, detail="Admin access required")


def send_invitation_email(email: str, code: str, base_url: str) -> None:
    """Send the invitation email synchronously (run via BackgroundTasks).

    Uses the MAIL_* settings from the environment (see deployment/env.example).
    Logs and swallows failures so a misconfigured mail server never crashes the
    background task — the invitation row is already persisted and can be resent.
    """
    sender = os.getenv("MAIL_FROM") or os.getenv("MAIL_USERNAME")
    username = os.getenv("MAIL_USERNAME")
    password = os.getenv("MAIL_PASSWORD")
    server = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    port = int(os.getenv("MAIL_PORT", "587"))
    use_starttls = os.getenv("MAIL_STARTTLS", "True").lower() in ("1", "true", "yes")

    if not (sender and username and password):
        logger.warning(
            "Invitation for %s not emailed: mail is not configured "
            "(set MAIL_USERNAME / MAIL_PASSWORD / MAIL_FROM). "
            "Registration link: %s/register?code=%s",
            email, base_url.rstrip("/"), code,
        )
        return

    register_url = f"{base_url.rstrip('/')}/register?code={code}"
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = email
    msg["Subject"] = "Your invitation to ASL Ruleschat"
    body = f"""
    <html>
      <body style="font-family: sans-serif; color: #1a1a1a;">
        <h2>You've been invited to ASL Ruleschat</h2>
        <p>Click the link below to set a password and create your account:</p>
        <p><a href="{register_url}">{register_url}</a></p>
        <p style="color: #666;">This invitation expires in 7 days.</p>
      </body>
    </html>
    """
    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP(server, port) as smtp:
            if use_starttls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
        logger.info("Invitation email sent to %s", email)
    except Exception as e:
        logger.error("Failed to send invitation email to %s: %s", email, e)


@router.post("/api/invite")
async def create_invitation(
    payload: InvitationCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an invitation for an email and send the registration link."""
    require_admin(user)

    email = payload.email.lower().strip()

    # Don't invite someone who already has an account.
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="A user with that email already exists")

    # Reuse an existing un-expired, unused invitation rather than piling up duplicates.
    existing = db.query(Invitation).filter(
        Invitation.email == email,
        Invitation.used_at.is_(None),
        Invitation.expires_at > datetime.utcnow(),
    ).first()

    if existing:
        invitation = existing
    else:
        invitation = Invitation(
            email=email,
            code=secrets.token_urlsafe(32),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()
        db.refresh(invitation)

    base_url = str(request.base_url)
    background_tasks.add_task(send_invitation_email, email, invitation.code, base_url)

    return {"detail": "Invitation sent", "email": email}


@router.post("/api/invite/resend/{invitation_id}")
async def resend_invitation(
    invitation_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-send the invitation email for an existing pending invitation."""
    require_admin(user)

    invitation = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if invitation.used_at is not None:
        raise HTTPException(status_code=400, detail="Invitation has already been used")

    # Refresh expiry so a resent link is valid for a full window again.
    invitation.expires_at = datetime.utcnow() + timedelta(days=7)
    db.commit()

    base_url = str(request.base_url)
    background_tasks.add_task(send_invitation_email, invitation.email, invitation.code, base_url)

    return {"detail": "Invitation resent", "email": invitation.email}


@router.delete("/api/invite/{invitation_id}")
async def delete_invitation(
    invitation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an invitation."""
    require_admin(user)

    invitation = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    db.delete(invitation)
    db.commit()

    return {"detail": "Invitation deleted"}
