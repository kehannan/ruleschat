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
from app.models import User, Invitation, AnswerFeedback
from app.core.auth import get_current_user
from app.services.user_service import update_user_profile, get_user_by_email
from app.core.responses_api import initialize_vector_store

# Import routers
from app.api import auth, user, chat

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

# Initialize vector store
openai_api_key = os.getenv("OPENAI_API_KEY")
vector_store_manager = None
try:
    vector_store_manager = initialize_vector_store(openai_api_key)
    logging.info("✅ Vector store manager initialized")
except Exception as e:
    logging.error(f"❌ Failed to initialize vector store manager: {e}")

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


def load_eval_runs():
    """Load all evaluation runs and return a list of runs with metadata."""
    from pathlib import Path
    from datetime import datetime
    import re
    
    evals_dir = Path(__file__).parent.parent.parent / "mysite2-evals-sft" / "evals"
    eval_runs = []
    
    try:
        if not evals_dir.exists():
            return {"error": f"Evaluation directory not found at: {evals_dir}"}
        
        # Find all eval result files (main and backups)
        eval_files = []
        for file_path in evals_dir.glob("asl_eval_results*.json"):
            eval_files.append(file_path)
        
        if not eval_files:
            return {"error": "No evaluation result files found"}
        
        # Process each file
        for file_path in sorted(eval_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    eval_data = json.load(f)
                
                if not eval_data or not isinstance(eval_data, list):
                    continue
                
                # Extract date from filename or use file modification time
                date_str = None
                filename = file_path.name
                
                # Try to extract date from backup filename (format: asl_eval_results_backup_YYYYMMDD_HHMMSS.json)
                if "backup_" in filename:
                    match = re.search(r'backup_(\d{8})_(\d{6})', filename)
                    if match:
                        date_str = f"{match.group(1)}_{match.group(2)}"
                        try:
                            dt = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                            date_str = dt.strftime("%Y-%m-%d")
                        except:
                            pass
                
                # If no date from filename, use file modification time
                if not date_str:
                    mtime = file_path.stat().st_mtime
                    date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                
                # Extract model from first item (if available)
                model = "Unknown"
                if eval_data and isinstance(eval_data[0], dict):
                    model = eval_data[0].get("judge_model", eval_data[0].get("model", "Unknown"))
                
                # Calculate stats
                total = len(eval_data)
                correct = sum(1 for item in eval_data if item.get("llm_judgment", "").lower() == "correct")
                correct_pct = (correct / total * 100) if total > 0 else 0
                
                # Create identifier for the file (use filename without extension)
                file_id = file_path.stem
                
                eval_runs.append({
                    "date": date_str,
                    "model": model,
                    "prompts": total,
                    "correct": correct,
                    "correct_pct": correct_pct,
                    "file_id": file_id,
                    "filename": filename
                })
                
            except Exception as e:
                logging.warning(f"Error processing eval file {file_path}: {e}")
                continue
        
        # Return only the most recent eval run
        if eval_runs:
            return {
                "eval_runs": [eval_runs[0]],  # Only the most recent
                "error": None
            }
        else:
            return {
                "eval_runs": [],
                "error": None
            }
        
    except Exception as e:
        logging.error(f"Error loading eval runs: {e}")
        return {"error": f"Error loading evaluation runs: {str(e)}"}


def load_eval_results(file_id=None):
    """Load and process evaluation results from a specific file."""
    from pathlib import Path
    from collections import defaultdict
    
    evals_dir = Path(__file__).parent.parent.parent / "mysite2-evals-sft" / "evals"
    
    # Determine which file to load
    if file_id:
        evals_results_path = evals_dir / f"{file_id}.json"
    else:
        evals_results_path = evals_dir / "asl_eval_results.json"
    
    results = []
    section_summary = []
    error = None
    
    try:
        if evals_results_path.exists():
            with open(evals_results_path, "r", encoding="utf-8") as f:
                eval_data = json.load(f)
            
            # Group by letter prefix (A, C, etc.) and calculate stats
            section_stats = defaultdict(lambda: {"total": 0, "correct": 0})
            
            # Transform the data to match template expectations
            for item in eval_data:
                section = item.get("section", "Unknown")
                # Extract letter prefix (A, C, etc.)
                section_letter = section[0] if section and section[0].isalpha() else "Unknown"
                
                # Use llm_judgment to determine correct count
                judgment = item.get("llm_judgment", "unknown").lower()
                
                # Update section stats by letter
                section_stats[section_letter]["total"] += 1
                if judgment == "correct":
                    section_stats[section_letter]["correct"] += 1
                
                results.append({
                    "question": item.get("question", ""),
                    "expected_answer": item.get("expected_answer", ""),
                    "assistant_response": item.get("model_response", ""),
                    "section": section,
                    "judgment": judgment,
                    "comments": item.get("llm_reasoning", ""),
                    "confidence": item.get("llm_confidence", 0.0),
                    "evaluation": item.get("evaluation", ""),
                    "human_override": item.get("human_override", False),
                    "human_notes": item.get("human_notes", ""),
                })
            
            # Create section summary list (grouped by letter)
            for section_letter, stats in sorted(section_stats.items()):
                correct_pct = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0
                section_summary.append({
                    "section": section_letter,
                    "prompts": stats["total"],
                    "correct": stats["correct"],
                    "correct_pct": correct_pct,
                })
            
            # Calculate overall stats (using llm_judgment)
            total = len(results)
            correct = sum(1 for r in results if r["judgment"] == "correct")
            partial = sum(1 for r in results if r["judgment"] == "partial")
            incorrect = sum(1 for r in results if r["judgment"] == "incorrect")
            
            correct_pct = (correct / total * 100) if total > 0 else 0
            partial_pct = (partial / total * 100) if total > 0 else 0
            incorrect_pct = (incorrect / total * 100) if total > 0 else 0
            
            return {
                "results": results,
                "section_summary": section_summary,
                "correct": correct,
                "partial": partial,
                "incorrect": incorrect,
                "total": total,
                "correct_pct": correct_pct,
                "partial_pct": partial_pct,
                "incorrect_pct": incorrect_pct,
                "error": None
            }
        else:
            return {"error": f"Evaluation results file not found at: {evals_results_path}"}
    except Exception as e:
        logging.error(f"Error loading evals: {e}")
        return {"error": f"Error loading evaluation results: {str(e)}"}


@app.get("/evals", name="evals", response_class=HTMLResponse)
async def evals_page(request: Request):
    """Display evaluation results summary page."""
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
    eval_data = load_eval_runs()
    context.update(eval_data)
    return templates.TemplateResponse("evals.html", context)


@app.get("/evals/detail", name="evals_detail_default", response_class=HTMLResponse)
@app.get("/evals/detail/{file_id}", name="evals_detail", response_class=HTMLResponse)
async def evals_detail_page(request: Request, file_id: str = None):
    """Display detailed evaluation results page."""
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
    eval_data = load_eval_results(file_id)
    context.update(eval_data)
    return templates.TemplateResponse("evals_detail.html", context)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
