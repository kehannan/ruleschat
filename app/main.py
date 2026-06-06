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
from app.models import User, Invitation, AnswerFeedback, ChatConversation, ChatMessage, DemoUsage, DemoMessage, SiteConfig
from app.core.auth import get_current_user
from app.services.user_service import update_user_profile, get_user_by_email

# Import routers
from app.api import auth, user, chat, evals, demo, ift, thtk

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

# One-shot idempotent column additions for SQLite (no migration tool in use)
def _ensure_column(table: str, column: str, ddl: str):
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            conn.commit()
            logging.info(f"Added column {table}.{column}")

def _migrate_image_path_to_paths(table: str):
    """Migrate single-image storage to multi-image:
      - Legacy schema (image_path only): RENAME the column to image_paths and
        wrap each existing string value as a JSON 1-element array.
      - In-progress state (both columns exist): backfill image_paths from
        image_path where empty, then DROP the legacy column.
      - Modern schema (image_paths only) or fresh table (neither): no-op.
    Idempotent across all states.
    """
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        has_old = "image_path" in cols
        has_new = "image_paths" in cols
        if not has_old:
            return  # already migrated, or table was created with the new schema
        # Path scheme is "<conv_id>/<uuid>.<ext>" with no quote/backslash chars,
        # so plain string interpolation in JSON construction is safe here.
        if not has_new:
            # SQLite 3.25+ supports RENAME COLUMN; the prod DO box has it.
            conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN image_path TO image_paths"))
            conn.execute(text(
                f"UPDATE {table} SET image_paths = '[\"' || image_paths || '\"]' "
                f"WHERE image_paths IS NOT NULL AND image_paths NOT LIKE '[%'"
            ))
            conn.commit()
            logging.info(f"Renamed + converted {table}.image_path -> image_paths (JSON)")
        else:
            # Both columns present (caused by interleaved migration steps in dev).
            # Backfill new column from old where new is empty, then drop the old.
            conn.execute(text(
                f"UPDATE {table} SET image_paths = '[\"' || image_path || '\"]' "
                f"WHERE image_paths IS NULL AND image_path IS NOT NULL"
            ))
            # SQLite 3.35+ supports DROP COLUMN; if the runtime is older, log
            # and leave the legacy column in place (harmless, just unused).
            try:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN image_path"))
                conn.commit()
                logging.info(f"Backfilled and dropped legacy {table}.image_path")
            except Exception as e:
                conn.commit()
                logging.warning(f"Backfilled {table}.image_paths but could not DROP image_path: {e}")


_migrate_image_path_to_paths("chat_messages")
_migrate_image_path_to_paths("demo_messages")

# Load runtime config from DB
from app.api.demo import load_demo_enabled_from_db, is_demo_enabled
load_demo_enabled_from_db()

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
app.include_router(demo.router, tags=["demo"])
app.include_router(ift.router, tags=["ift"])
app.include_router(thtk.router, tags=["thtk"])


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


@app.get("/privacy", name="privacy")
async def privacy_page(request: Request):
    """Display privacy policy."""
    from app.api.chat import get_base_context, get_current_user_from_request
    user = get_current_user_from_request(request)
    context = get_base_context(request, user)
    return templates.TemplateResponse("privacy.html", context)


# Registration routes
@app.get("/register", name="register")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
