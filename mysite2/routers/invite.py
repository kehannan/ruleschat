from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Dict
import secrets
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from pydantic import BaseModel
from database import get_db
from models import Invitation, User

router = APIRouter()

class InvitationCreate(BaseModel):
    email: str

def send_invitation_email_background(email: str, token: str, base_url: str):
    """Send invitation email in the background"""
    sender_email = os.getenv('SMTP_USERNAME')
    sender_password = os.getenv('SMTP_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = email
    msg['Subject'] = "Invitation to Join Our Platform"
    
    register_url = f"{base_url.rstrip('/')}/register?token={token}"
    body = f"""
    <html>
        <body>
            <h2>You've been invited to join our platform!</h2>
            <p>Click the link below to create your account:</p>
            <p><a href="{register_url}">{register_url}</a></p>
            <p>This invitation will expire in 7 days.</p>
        </body>
    </html>
    """
    
    msg.attach(MIMEText(body, 'html'))
    
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print(f"Invitation email sent successfully to {email}")
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

@router.post("/invite", response_model=Dict[str, str])
async def create_invitation(
    invitation: InvitationCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db)
):
    # Check if user already exists
    user = db.query(User).filter(User.email == invitation.email).first()
    if user:
        raise HTTPException(status_code=400, detail="User already exists")
    
    # Check for existing invitation
    existing_invite = db.query(Invitation).filter(
        Invitation.email == invitation.email,
        Invitation.used == False,
        Invitation.expires_at > datetime.utcnow()
    ).first()
    
    if existing_invite:
        # Return the existing invitation token
        return {"message": "Invitation already exists", "token": existing_invite.token}
    
    # Create new invitation
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    new_invite = Invitation(
        email=invitation.email,
        token=token,
        expires_at=expires_at
    )
    
    db.add(new_invite)
    db.commit()
    
    # Send invitation email in background
    base_url = str(request.base_url)
    background_tasks.add_task(send_invitation_email_background, invitation.email, token, base_url)
    
    return {"message": "Invitation sent successfully", "token": token}

@router.get("/register")
async def register(
    token: str,
    db: Session = Depends(get_db)
):
    invitation = db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.used == False,
        Invitation.expires_at > datetime.utcnow()
    ).first()
    
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")
    
    return {"email": invitation.email} 