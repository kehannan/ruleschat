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
from app.models.demo import DemoMessage

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
    from app.api.demo import is_demo_enabled
    context = {"request": request, "demo_enabled": is_demo_enabled()}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")

    eval_data = load_eval_runs()
    context.update(eval_data)
    context["is_archive"] = False

    return templates.TemplateResponse("evals.html", context)


@router.get("/evals/v1.0", name="evals_v1", response_class=HTMLResponse)
async def evals_v1_page(request: Request, user = Depends(get_current_user)):
    """Display the archived v1.0 multi-model comparison page."""
    from app.api.demo import is_demo_enabled
    context = {"request": request, "demo_enabled": is_demo_enabled()}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")

    v1_dir = _get_evals_dir() / "v1.0"
    eval_data = load_eval_runs(evals_dir=v1_dir, filter_to_present=False)
    context.update(eval_data)
    context["is_archive"] = True

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
    """Return daily per-question average tokens and cost by model.

    Image-attached queries are aggregated as a separate variant ("{model} (image)")
    so the /evals charts can show text vs image queries side by side.
    """
    # Production chat tags messages with the full model id sent to the API.
    # OpenRouter rows arrive as "deepseek/deepseek-v3.2" / "inception/mercury-2";
    # they're normalized to display labels below via _normalize_model_for_usage.
    ALLOWED_MODELS = {
        "gpt-5-mini", "gpt-4.1-mini", "gpt-5.4", "gpt-5.4-mini",
        "deepseek/deepseek-v3.2", "inception/mercury-2",
    }
    USAGE_DISPLAY = {
        "deepseek/deepseek-v3.2": "deepseek-v3",
        "inception/mercury-2": "mercury-2",
    }

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.role == "assistant")
        .filter(ChatMessage.timing_data.isnot(None))
        .order_by(ChatMessage.created_at)
        .all()
    )

    demo_messages = (
        db.query(DemoMessage)
        .filter(DemoMessage.role == "assistant")
        .filter(DemoMessage.timing_data.isnot(None))
        .order_by(DemoMessage.created_at)
        .all()
    )

    # Backfill: for assistant messages whose timing_data lacks image_attached,
    # detect the flag from the preceding user message's image_paths.
    image_user_msgs = (
        db.query(ChatMessage.conversation_id, ChatMessage.created_at)
        .filter(ChatMessage.role == "user")
        .filter(ChatMessage.image_paths.isnot(None))
        .all()
    )
    images_by_conv = defaultdict(list)
    for conv_id, created_at in image_user_msgs:
        images_by_conv[conv_id].append(created_at)

    def is_image_query(msg) -> bool:
        timing = msg.timing_data or {}
        if "image_attached" in timing:
            return bool(timing["image_attached"])
        if not hasattr(msg, "conversation_id"):
            return False
        for img_t in images_by_conv.get(msg.conversation_id, ()):
            if img_t <= msg.created_at:
                return True
        return False

    # Aggregate by (date, model, is_image, is_agentic). OpenRouter slugs are
    # normalized to short display names so the chart legend reads "deepseek-v3"
    # not the full provider/model id. "(agentic)" = any turn where the model
    # actually executed a tool call (tools_called in timing_data is non-empty);
    # this is independent of whether the user toggled the Tools checkbox.
    daily = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "total_time_ms": 0, "count": 0})
    variants_seen = set()
    for msg in list(messages) + list(demo_messages):
        timing = msg.timing_data or {}
        model = timing.get("model", "unknown")
        if model not in ALLOWED_MODELS:
            continue
        display_model = USAGE_DISPLAY.get(model, model)
        is_image = is_image_query(msg)
        is_agentic = bool(timing.get("tools_called"))
        suffix = ""
        if is_image:
            suffix = " (image)"
        elif is_agentic:
            suffix = " (agentic)"
        variant = f"{display_model}{suffix}"
        variants_seen.add(variant)
        date_str = msg.created_at.strftime("%Y-%m-%d") if msg.created_at else "unknown"
        key = (date_str, variant)
        daily[key]["input_tokens"] += timing.get("input_tokens", 0) or 0
        daily[key]["output_tokens"] += timing.get("output_tokens", 0) or 0
        daily[key]["total_time_ms"] += timing.get("total_time_ms", 0) or 0
        daily[key]["count"] += 1

    # USD per 1M tokens (input, output). OpenRouter prices are approximate —
    # verify against the live OpenRouter dashboard if exact COST chips matter.
    MODEL_PRICING = {
        "gpt-5-mini":    (0.25, 1.00),
        "gpt-5.4":       (3.00, 15.00),
        "gpt-5.4-mini":  (0.25, 2.00),
        "gpt-5.6-terra": (2.50, 15.00),
        "gpt-5.6-luna":  (1.00, 6.00),
        "gpt-4.1-mini":  (0.40, 1.60),
        "deepseek-v3":   (0.27, 1.10),
        "mercury-2":     (0.25, 1.00),
    }

    def base_model(variant: str) -> str:
        return variant.replace(" (image)", "").replace(" (agentic)", "")

    variants = sorted(variants_seen)
    dates = sorted(set(k[0] for k in daily.keys()))

    series = {}
    for variant in variants:
        series[variant] = {
            "dates": [],
            "input_tokens": [],
            "output_tokens": [],
            "cost": [],
            "total_time_s": [],
            "is_image": variant.endswith(" (image)"),
            "is_agentic": variant.endswith(" (agentic)"),
            "base_model": base_model(variant),
        }
        inp_price, out_price = MODEL_PRICING.get(base_model(variant), (0.40, 1.60))
        for date in dates:
            key = (date, variant)
            data = daily.get(key)
            series[variant]["dates"].append(date)
            if not data or data["count"] == 0:
                series[variant]["input_tokens"].append(None)
                series[variant]["output_tokens"].append(None)
                series[variant]["cost"].append(None)
                series[variant]["total_time_s"].append(None)
            else:
                count = data["count"]
                inp = data["input_tokens"] / count
                out = data["output_tokens"] / count
                total_time_s = (data["total_time_ms"] / count) / 1000
                cost = (inp * inp_price + out * out_price) / 1_000_000
                has_tokens = (inp > 0 or out > 0)
                series[variant]["input_tokens"].append(round(inp) if has_tokens else None)
                series[variant]["output_tokens"].append(round(out) if has_tokens else None)
                series[variant]["cost"].append(round(cost, 6) if has_tokens else None)
                series[variant]["total_time_s"].append(round(total_time_s, 1) if total_time_s > 0 else None)

    # "models" key kept for backward compat (old name = variant identifier)
    return JSONResponse({"dates": dates, "models": variants, "series": series})


# --- Public Logic ---

def load_eval_runs(evals_dir=None, filter_to_present=True):
    """Load all evaluation runs and return a list of runs with metadata.

    Args:
        evals_dir: directory to read result JSONs from. Defaults to the
            configured evals dir (``data/evals`` or ``$EVALS_DIR``).
        filter_to_present: when True (the main ``/evals`` page) the returned
            ``model_order`` is filtered to only models that actually have data,
            so the comparison table renders one row per real result instead of
            a fixed 6-row grid with blanks. The archived ``/evals/v1.0`` page
            sets this False to keep the full fixed ordering.
    """
    if evals_dir is None:
        evals_dir = _get_evals_dir()
    eval_runs = []
    # model -> metadata.performance block from its newest eval file. Used to
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

                    is_estimated = metadata.get("estimated", False)
                    eval_tier = _eval_tier(eval_file)

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
                                "eval_tier": eval_tier,
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
                                "estimated": is_estimated,
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
                            "eval_tier": eval_tier,
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
                        "eval_tier": _eval_tier(file_path.stem),
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
        
        # Sort by date descending, then Recall before Calc
        TYPE_ORDER = {"Recall": 0, "Calc": 1}
        eval_runs.sort(key=lambda x: (
            x.get("date", ""),
            -TYPE_ORDER.get(x.get("question_type", ""), 99),
        ), reverse=True)

        # Build the comparison-table rows, one per (model, eval tier) — e.g.
        # Fable has an Easy row and a Medium row. Order is "frontier → cheap";
        # deepseek-v3 sits with the cheaper tier (it's the first
        # OpenRouter-routed model in the table). Within a model, easier tiers
        # come first.
        MODEL_ORDER = ["Fable", "gpt-5.4", "gpt-5.4-mini", "gpt-5-mini", "deepseek-v3", "gpt-4.1-mini", "mercury-2"]
        MODEL_VIA_OPENROUTER = {"deepseek-v3", "mercury-2"}
        TIER_SORT = {"Easy": 0, "Medium": 1, "—": 2}

        rows_map = {}
        for run in eval_runs:
            m = run["model"]
            tier = run.get("eval_tier", "—")
            qt = run["question_type"].lower()
            key = (m, tier)
            if key not in rows_map:
                date_label = None
                if run.get("date"):
                    try:
                        date_label = datetime.strptime(run["date"], "%Y-%m-%d").strftime("%b %-d")
                    except (ValueError, TypeError):
                        pass
                rows_map[key] = {
                    "model": m, "tier": tier, "acc": {},
                    "file_id": run["file_id"], "date": date_label,
                    "estimated": bool(run.get("estimated")),
                    "via_openrouter": m in MODEL_VIA_OPENROUTER,
                }
            # eval_runs is sorted newest-first, so the first value seen per
            # (model, tier, qtype) is the latest run's accuracy.
            if qt not in rows_map[key]["acc"]:
                rows_map[key]["acc"][qt] = round(run["pass_pct"])

        def _row_sort(key):
            m, tier = key
            m_idx = MODEL_ORDER.index(m) if m in MODEL_ORDER else len(MODEL_ORDER)
            return (m_idx, TIER_SORT.get(tier, 99))

        row_order = sorted(rows_map.keys(), key=_row_sort)
        if not filter_to_present:
            # Archive page keeps the full fixed model grid even without data.
            # Fable postdates the v1.0 archive, so it's not padded in.
            present = {m for m, _ in rows_map.keys()}
            for m in MODEL_ORDER:
                if m == "Fable":
                    continue
                if m not in present:
                    rows_map[(m, "—")] = {
                        "model": m, "tier": "—", "acc": {}, "file_id": None,
                        "date": None, "estimated": False,
                        "via_openrouter": m in MODEL_VIA_OPENROUTER,
                    }
            row_order = sorted(rows_map.keys(), key=_row_sort)
        table_rows = [rows_map[k] for k in row_order]

        # Legacy per-model keys, still used by Section 01 prose links (and kept
        # for any external consumers). model_latest_file prefers the Easy-tier
        # run so prose that cites the easy-eval numbers links to that file.
        model_accuracy = {}
        model_latest_file = {}
        model_estimated = set()
        model_last_run_date = {}
        for key in row_order:
            row = rows_map[key]
            m = row["model"]
            if not row["file_id"]:
                continue
            if m not in model_latest_file:  # first row per model = easiest tier
                model_latest_file[m] = row["file_id"]
                model_accuracy[m] = dict(row["acc"])
                if row["date"]:
                    model_last_run_date[m] = row["date"]
            if row["estimated"]:
                model_estimated.add(m)

        model_order = MODEL_ORDER
        if filter_to_present:
            model_order = [m for m in MODEL_ORDER if m in model_accuracy]

        return {"eval_runs": eval_runs, "table_rows": table_rows,
                "model_accuracy": model_accuracy,
                "model_order": model_order, "model_latest_file": model_latest_file,
                "model_estimated": model_estimated,
                "model_last_run_date": model_last_run_date,
                "model_via_openrouter": MODEL_VIA_OPENROUTER, "error": None}
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


# OpenRouter slugs → display label. The eval files use the full provider/slug
# (e.g. `deepseek/deepseek-v3.2`); the public table shortens to `deepseek-v3`.
OPENROUTER_DISPLAY = {
    "deepseek/deepseek-v3.2": "deepseek-v3",
    "inception/mercury-2": "mercury-2",
}


def _eval_tier(eval_name: str) -> str:
    """Classify an eval file name into a difficulty tier for the comparison table.

    v1.1 eval files are named asl-evals-<tier>-... (easy/med). Files that
    predate the tier naming (the v1.0 archive) get "—" so the Eval column
    renders a neutral placeholder rather than a wrong label.
    """
    name = (eval_name or "").lower()
    if "med" in name:
        return "Medium"
    if "easy" in name:
        return "Easy"
    return "—"


def _format_model_name(model_name: str) -> str:
    """Format a model name for display, extracting base model and custom name from fine-tuned models."""
    if not model_name or model_name == "Unknown":
        return model_name

    # Anthropic model IDs → the short label used in prose and MODEL_ORDER.
    CLAUDE_DISPLAY = {"claude-fable-5": "Fable"}
    if model_name in CLAUDE_DISPLAY:
        return CLAUDE_DISPLAY[model_name]

    # OpenAI fine-tuned format: ft:{base_model}:{org}:{custom_name}:{id}
    if model_name.startswith("ft:"):
        parts = model_name.split(":")
        if len(parts) >= 4:
            base_model = parts[1]  # e.g., gpt-4o-2024-08-06
            custom_name = parts[3]  # e.g., asl-formatted-v2
            # Simplify base model name (remove date suffix if present)
            base_short = base_model.split("-2024")[0].split("-2025")[0].split("-2026")[0]
            return f"{base_short} / {custom_name}"

    # OpenRouter slug (vendor/model[:tag]) — use the registered short label, or
    # fall back to the post-slash portion.
    if "/" in model_name:
        return OPENROUTER_DISPLAY.get(model_name, model_name.split("/", 1)[1])

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
