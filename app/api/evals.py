import os
import json
import logging
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, cast, String
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.chat import ChatMessage

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- Dependencies ---

async def get_current_user(request: Request):
    """Dependency to get the current user from the access token cookie."""
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


# --- Routes ---

@router.get("/evals", name="evals", response_class=HTMLResponse)
async def evals_page(request: Request, user = Depends(get_current_user)):
    """Display evaluation results summary page."""
    context = {"request": request}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    
    eval_data = load_eval_runs()
    context.update(eval_data)
        
    return templates.TemplateResponse("evals.html", context)

@router.get("/evals/detail", name="evals_detail_default", response_class=HTMLResponse)
@router.get("/evals/detail/{file_id}", name="evals_detail", response_class=HTMLResponse)
async def evals_detail_page(request: Request, file_id: str = None, judge: str = "ai", user = Depends(get_current_user)):
    """Display detailed evaluation results page."""
    context = {"request": request}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    
    use_human_review = judge.lower() == "human"
    eval_data = load_eval_results(file_id, use_human_review=use_human_review)
    context.update(eval_data)
    context["judge_type"] = "Human Review" if use_human_review else "AI Judge"
    return templates.TemplateResponse("evals_detail.html", context)


@router.get("/api/usage/daily", name="usage_daily")
async def usage_daily(db: Session = Depends(get_db)):
    """Return daily token usage and cost aggregated by model."""
    # Query all assistant messages with timing_data
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.role == "assistant")
        .filter(ChatMessage.timing_data.isnot(None))
        .order_by(ChatMessage.created_at)
        .all()
    )

    # Aggregate by date + model
    daily = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "count": 0})
    for msg in messages:
        timing = msg.timing_data or {}
        model = timing.get("model", "unknown")
        date_str = msg.created_at.strftime("%Y-%m-%d") if msg.created_at else "unknown"
        key = (date_str, model)
        daily[key]["input_tokens"] += timing.get("input_tokens", 0) or 0
        daily[key]["output_tokens"] += timing.get("output_tokens", 0) or 0
        daily[key]["count"] += 1

    # Build response grouped by model
    models = sorted(set(k[1] for k in daily.keys()))
    dates = sorted(set(k[0] for k in daily.keys()))

    series = {}
    for model in models:
        series[model] = {
            "dates": [],
            "input_tokens": [],
            "output_tokens": [],
            "cost": [],
        }
        for date in dates:
            key = (date, model)
            data = daily.get(key, {"input_tokens": 0, "output_tokens": 0})
            inp = data["input_tokens"]
            out = data["output_tokens"]
            # Cost per 1M tokens (approximate OpenAI pricing)
            if "5-mini" in model:
                cost = (inp * 0.40 + out * 1.60) / 1_000_000
            elif "4.1-mini" in model:
                cost = (inp * 0.40 + out * 1.60) / 1_000_000
            else:
                cost = (inp * 0.50 + out * 1.50) / 1_000_000
            series[model]["dates"].append(date)
            series[model]["input_tokens"].append(inp)
            series[model]["output_tokens"].append(out)
            series[model]["cost"].append(round(cost, 4))

    return JSONResponse({"dates": dates, "models": models, "series": series})


# --- Public Logic ---

def load_eval_runs():
    """Load all evaluation runs and return a list of runs with metadata."""
    evals_dir = _get_evals_dir()
    eval_runs = []
    
    try:
        if not evals_dir.exists():
            return {"error": f"Directory not found: {evals_dir}"}
        
        for file_path in sorted(evals_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    eval_data = json.load(f)
                
                # Handle new format with metadata
                if isinstance(eval_data, dict) and "metadata" in eval_data:
                    metadata = eval_data["metadata"]
                    ai_summary = metadata.get("summary", {}).get("ai_judge", {})
                    human_summary = metadata.get("summary", {}).get("with_human_review", {})
                    
                    # Extract date from timestamp or filename
                    date_str = None
                    if "timestamp" in metadata:
                        try:
                            date_str = datetime.fromisoformat(metadata["timestamp"]).strftime("%Y-%m-%d")
                        except: pass
                    
                    if not date_str:
                        match = re.search(r'(\d{8})_(\d{6})', file_path.name)
                        if match:
                            try:
                                date_str = datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
                            except: pass
                    
                    if not date_str:
                        date_str = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d")
                    
                    total = metadata.get("total_questions", 0)

                    # Extract question_type breakdown if available
                    by_question_type = metadata.get("summary", {}).get("by_question_type", {})

                    model_display = _format_model_name(metadata.get("model_name", "Unknown"))
                    eval_file = metadata.get("eval_file", file_path.name)
                    # Remove extension if present
                    eval_file = Path(eval_file).stem

                    # Create rows for each question type (Human Review only)
                    if by_question_type:
                        # Create separate row for each question type
                        for q_type in sorted(by_question_type.keys()):
                            stats = by_question_type[q_type]
                            q_total = stats.get("total", 0)
                            human_stats = stats.get("with_human_review", {})

                            eval_runs.append({
                                "date": date_str,
                                "model": model_display,
                                "eval_name": eval_file,
                                "judge_type": "Human Review",
                                "question_type": q_type.capitalize(),
                                "total": q_total,
                                "pass_count": human_stats.get("pass", 0),
                                "pass_pct": (human_stats.get("pass", 0) / q_total * 100) if q_total > 0 else 0,
                                "fail_count": human_stats.get("fail", 0),
                                "fail_pct": (human_stats.get("fail", 0) / q_total * 100) if q_total > 0 else 0,
                                "needs_review": human_stats.get("pending_review", 0),
                                "needs_review_pct": (human_stats.get("pending_review", 0) / q_total * 100) if q_total > 0 else 0,
                                "file_id": file_path.stem,
                                "filename": file_path.name,
                            })
                    else:
                        # Fallback: single row with overall stats if no question type breakdown
                        human_pass = human_summary.get("pass", 0)
                        human_fail = human_summary.get("fail", 0)
                        human_pending = human_summary.get("pending_review", 0)

                        eval_runs.append({
                            "date": date_str,
                            "model": model_display,
                            "eval_name": eval_file,
                            "judge_type": "Human Review",
                            "question_type": "All",
                            "total": total,
                            "pass_count": human_pass,
                            "pass_pct": (human_pass / total * 100) if total > 0 else 0,
                            "fail_count": human_fail,
                            "fail_pct": (human_fail / total * 100) if total > 0 else 0,
                            "needs_review": human_pending,
                            "needs_review_pct": (human_pending / total * 100) if total > 0 else 0,
                            "file_id": file_path.stem,
                            "filename": file_path.name,
                        })
                # Handle legacy list format
                elif isinstance(eval_data, list) and eval_data:
                    summary = _process_eval_data(eval_data)
                    
                    date_str = None
                    match = re.search(r'(\d{8})_(\d{6})', file_path.name)
                    if match:
                        try:
                            date_str = datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
                        except: pass
                    
                    if not date_str:
                        date_str = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d")
                    
                    model = eval_data[0].get("judge_model", eval_data[0].get("model", "Unknown"))
                    model_display = _format_model_name(model)
                    total = summary["total"]
                    
                    eval_runs.append({
                        "date": date_str,
                        "model": model_display,
                        "eval_name": file_path.stem,
                        "judge_type": "AI Judge",
                        "total": total,
                        "pass_count": summary["ai_stats"]["pass"],
                        "pass_pct": summary["ai_stats"]["pass_pct"],
                        "fail_count": summary["ai_stats"]["fail"],
                        "fail_pct": summary["ai_stats"]["fail_pct"],
                        "needs_review": summary["ai_stats"]["partial"],
                        "needs_review_pct": summary["ai_stats"]["partial_pct"],
                        "file_id": file_path.stem,
                        "filename": file_path.name
                    })
            except Exception as e:
                logging.warning(f"Skipping {file_path.name}: {e}")
                continue
        
        # Sort by filename descending (newest first based on timestamp in filename)
        eval_runs.sort(key=lambda x: x.get("filename", ""), reverse=True)
        
        return {"eval_runs": eval_runs, "error": None}
    except Exception as e:
        return {"error": str(e)}


def load_eval_results(file_id=None, use_human_review=False):
    """Load and process evaluation results from a specific file."""
    evals_dir = _get_evals_dir()
    path = evals_dir / f"{file_id}.json" if file_id else evals_dir / "asl_eval_results.json"
    
    if not path.exists():
        return {"error": f"File not found: {path}"}
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            eval_data = json.load(f)
        
        # Handle new format with metadata
        if isinstance(eval_data, dict) and "results" in eval_data:
            results_list = eval_data["results"]
        else:
            # Legacy list format
            results_list = eval_data
            
        result = _process_eval_data(results_list, use_human_review=use_human_review)
        result["error"] = None
        return result
    except Exception as e:
        return {"error": str(e)}


# --- Private Helpers ---


def _format_model_name(model_name: str) -> str:
    """Format a model name for display, extracting base model and custom name from fine-tuned models."""
    if not model_name or model_name == "Unknown":
        return model_name
    
    # OpenAI fine-tuned format: ft:{base_model}:{org}:{custom_name}:{id}
    if model_name.startswith("ft:"):
        parts = model_name.split(":")
        if len(parts) >= 4:
            base_model = parts[1]  # e.g., gpt-4o-2024-08-06
            custom_name = parts[3]  # e.g., asl-formatted-v2
            # Simplify base model name (remove date suffix if present)
            base_short = base_model.split("-2024")[0].split("-2025")[0].split("-2026")[0]
            return f"{base_short} / {custom_name}"
    
    return model_name


def _get_evals_dir() -> Path:
    """Resolve the directory containing evaluation results."""
    return Path(os.getenv("EVALS_DIR", "data/evals")).resolve()


def _process_eval_data(eval_data: list, use_human_review: bool = False) -> dict:
    """
    Process raw evaluation JSON data into a structured summary.
    
    Args:
        eval_data: List of evaluation result items
        use_human_review: If True, use final_evaluation (with human overrides), 
                          otherwise use llm_judgment (AI only)
    
    Returns a dict containing:
    - results: transformed individual result items
    - section_summary: stats grouped by section prefix
    - stats: overall AI and Human performance metrics
    """
    results = []
    section_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    
    # Process each item
    for item in eval_data:
        section = item.get("section", "Unknown")
        section_letter = section[0] if section and section[0].isalpha() else "Unknown"
        
        # Determine judgment based on view type
        if use_human_review:
            # Use final_evaluation which respects human overrides
            final_eval = item.get("final_evaluation", "").lower()
            # Map pass/fail to correct/incorrect for consistency
            if final_eval == "pass":
                judgment = "correct"
            elif final_eval == "fail":
                judgment = "incorrect"
            else:
                judgment = item.get("llm_judgment", "unknown").lower()
        else:
            # AI Judge only
            judgment = item.get("llm_judgment", "unknown").lower()
        
        # Section aggregation
        section_stats[section_letter]["total"] += 1
        if judgment == "correct":
            section_stats[section_letter]["correct"] += 1
            
        # Individual result mapping
        human_override = item.get("human_override")
        results.append({
            "question": item.get("question", ""),
            "expected_answer": item.get("expected_answer", ""),
            "assistant_response": item.get("model_response", ""),
            "section": section,
            "question_type": item.get("question_type", "unknown"),
            "judgment": judgment,
            "comments": item.get("llm_reasoning", ""),
            "confidence": item.get("llm_confidence", 0.0),
            "evaluation": item.get("ai_evaluation", item.get("evaluation", "")),
            "human_override": human_override is not None,
            "human_override_value": human_override if human_override else "",
            "human_notes": item.get("human_notes", ""),
            "final_evaluation": item.get("final_evaluation", "")
        })
    
    # Build section summary
    section_summary = []
    for section_letter, stats in sorted(section_stats.items()):
        correct_pct = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0
        section_summary.append({
            "section": section_letter,
            "prompts": stats["total"],
            "correct": stats["correct"],
            "correct_pct": correct_pct,
        })
        
    # AI Stats
    ai_pass = sum(1 for r in results if r["judgment"] == "correct")
    ai_fail = sum(1 for r in results if r["judgment"] == "incorrect")
    ai_partial = sum(1 for r in results if r["judgment"] == "partial")
    ai_total = ai_pass + ai_fail + ai_partial
    
    # Human Stats (respecting overrides)
    human_pass = 0
    human_fail = 0
    for r in results:
        override = r["human_override"]
        override_val = r["human_override_value"].lower() if r["human_override_value"] else ""
        if override:
            if override_val in ("correct", "pass"): human_pass += 1
            elif override_val in ("incorrect", "fail"): human_fail += 1
        else:
            if r["judgment"] == "correct": human_pass += 1
            elif r["judgment"] == "incorrect": human_fail += 1
            
    human_total = human_pass + human_fail
    
    total = len(results)
    return {
        "results": results,
        "section_summary": section_summary,
        "total": total,
        "ai_stats": {
            "pass": ai_pass, "fail": ai_fail, "partial": ai_partial, "total": ai_total,
            "pass_pct": (ai_pass / ai_total * 100) if ai_total > 0 else 0,
            "fail_pct": (ai_fail / ai_total * 100) if ai_total > 0 else 0,
            "partial_pct": (ai_partial / ai_total * 100) if ai_total > 0 else 0
        },
        "human_stats": {
            "pass": human_pass, "fail": human_fail, "total": human_total,
            "pass_pct": (human_pass / human_total * 100) if human_total > 0 else 0,
            "fail_pct": (human_fail / human_total * 100) if human_total > 0 else 0
        },
        # Legacy/Compatibility keys for specific calls
        "correct": ai_pass,
        "partial": ai_partial,
        "incorrect": ai_fail,
        "correct_pct": (ai_pass / total * 100) if total > 0 else 0,
        "partial_pct": (ai_partial / total * 100) if total > 0 else 0,
        "incorrect_pct": (ai_fail / total * 100) if total > 0 else 0
    }
