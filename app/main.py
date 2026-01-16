"""Main FastAPI application."""
import os
import json
import logging
import secrets
import string
import random
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Body, BackgroundTasks, Request, Form, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.database import engine, Base, get_db
from app.models import User, Invitation, AnswerFeedback, ChatConversation, ChatMessage
from app.core.auth import get_current_user
from app.services.user_service import update_user_profile, get_user_by_email

# Import routers
from app.api import auth, user, chat, evals

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

# Create database tables
Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(title="Rules Chat for Advanced Squad Leader (ASL)")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Load Responses API configuration
responses_config = None
try:
    if os.path.exists("responses_api_config.json"):
        with open("responses_api_config.json", "r") as f:
            responses_config = json.load(f)
        logging.info("✅ Loaded Responses API configuration")
    else:
        logging.warning("⚠️ No Responses API configuration found")
except Exception as e:
    logging.error(f"❌ Error loading Responses API configuration: {e}")

# Email configuration (if needed)
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_SERVER = os.getenv("MAIL_SERVER")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))

# Include routers
app.include_router(auth.router, tags=["authentication"])
app.include_router(user.router, tags=["user"])
app.include_router(chat.router, tags=["chat"])
app.include_router(evals.router, tags=["evals"])


# Admin dependency
async def get_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Require admin user for certain routes."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Check if user is admin (you can define admin logic here)
    admin_email = os.getenv("ADMIN_EMAIL")
    if admin_email and current_user.email != admin_email:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return current_user


# Additional routes that don't fit in specific routers yet

@app.post("/api/feedback")
async def submit_feedback(
    data: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Submit feedback on an AI response."""
    feedback = AnswerFeedback(
        user_id=user.id if user else None,
        question=data["question"],
        answer=data["answer"],
        thumbs_up=data["thumbs_up"],
        comment=data.get("comment")
    )
    db.add(feedback)
    db.commit()
    return {"status": "ok"}


# Registration routes
@app.get("/register")
async def register_page(request: Request, code: str = None):
    """Display registration page."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    
    context = {"request": request}
    return templates.TemplateResponse("register.html", context)


@app.post("/register/complete")
async def register_complete(
    request: Request,
    code: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Complete user registration."""
    from fastapi import Form, Request
    from app.core.auth import get_password_hash
    from app.services.user_service import create_user
    
    invitation = db.query(Invitation).filter(
        Invitation.code == code,
        Invitation.expires_at > datetime.utcnow(),
        Invitation.used_at.is_(None)
    ).first()
    
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation code")
    
    # Create user
    hashed_password = get_password_hash(password)
    user = create_user(db, invitation.email, hashed_password)
    
    # Mark invitation as used
    invitation.used_at = datetime.utcnow()
    invitation.used_by_user_id = user.id
    db.commit()
    
    context = {"request": request}
    return templates.TemplateResponse("register_success.html", context)


@app.get("/about", name="about", response_class=HTMLResponse)
async def about_page(request: Request):
    """Display about page."""
    # Get current user for navbar context
    user = None
    token = request.cookies.get("access_token")
    if token:
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
                    user = get_user_by_email(db, email)
                finally:
                    db.close()
        except JWTError:
            pass
    
    context = {"request": request}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return templates.TemplateResponse("about.html", context)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
