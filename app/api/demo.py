"""Demo mode: public, rate-limited chat endpoint."""
import os
import json
import logging
import asyncio
import random
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.websockets import WebSocket, WebSocketDisconnect
from sqlalchemy import func

from app import model_registry
from app.database import SessionLocal
from app.models.demo import DemoUsage, DemoMessage
from app.models.config import SiteConfig
from app.services.image_storage import save_image_data_url, ImageValidationError
from app.services.vsav_service import (
    save_vsav_data_url, parse_vsav, render_board_state, VsavError,
)
from app.services.asl_service import get_asl_service

# In-memory flag — loaded from DB on startup, updated by admin toggle.
# Default True so demo works before any DB row exists.
_demo_enabled: bool = True


def is_demo_enabled(db=None) -> bool:
    """Return current demo enabled state from in-memory cache."""
    return _demo_enabled


def load_demo_enabled_from_db():
    """Called at startup to sync in-memory flag with DB."""
    global _demo_enabled
    db = SessionLocal()
    try:
        row = db.query(SiteConfig).filter_by(key="demo_enabled").first()
        _demo_enabled = (row.value == "true") if row else True
    finally:
        db.close()


def set_demo_enabled(value: bool, db):
    """Persist to DB and update in-memory flag."""
    global _demo_enabled
    _demo_enabled = value
    row = db.query(SiteConfig).filter_by(key="demo_enabled").first()
    str_val = "true" if value else "false"
    if row:
        row.value = str_val
    else:
        db.add(SiteConfig(key="demo_enabled", value=str_val))
    db.commit()

router = APIRouter()
templates = Jinja2Templates(directory="templates")

DEMO_PER_IP_LIMIT = 5
DEMO_GLOBAL_LIMIT = 250
DEMO_MAX_CHUNKS = 20
DEMO_MODEL = "gpt-5.4"
WEBSOCKET_PING_INTERVAL = 30


def _get_client_ip(websocket: WebSocket) -> str:
    """Extract real IP from X-Real-IP, which nginx sets from $remote_addr.

    Never trust X-Forwarded-For here: nginx appends to the client-supplied
    value, so its first entry is attacker-controlled and would let anyone
    reset the per-IP demo limit with a spoofed header.
    """
    real_ip = websocket.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return websocket.client.host or "unknown"


def _get_usage(db, ip: str, today: str) -> DemoUsage | None:
    return db.query(DemoUsage).filter_by(ip_address=ip, date=today).first()


def _get_global_count(db, today: str) -> int:
    return db.query(func.sum(DemoUsage.count)).filter(DemoUsage.date == today).scalar() or 0


def _get_remaining(db, ip: str, today: str) -> int:
    usage = _get_usage(db, ip, today)
    used = usage.count if usage else 0
    return max(0, DEMO_PER_IP_LIMIT - used)


def _increment(db, ip: str, today: str):
    usage = _get_usage(db, ip, today)
    if usage:
        usage.count += 1
    else:
        usage = DemoUsage(ip_address=ip, date=today, count=1)
        db.add(usage)
    db.commit()


@router.get("/demo", name="demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    db = SessionLocal()
    try:
        if not is_demo_enabled(db):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/", status_code=302)
    finally:
        db.close()

    context = {"request": request}
    # Pass auth state for navbar
    token = request.cookies.get("access_token")
    if token:
        from jose import jwt, JWTError
        from app.core.auth import SECRET_KEY, ALGORITHM
        from app.services.user_service import get_user_by_email
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            email = payload.get("sub")
            if email:
                db = SessionLocal()
                try:
                    user = get_user_by_email(db, email)
                    if user:
                        context["user_email"] = user.email
                        context["admin_email"] = os.getenv("ADMIN_EMAIL")
                finally:
                    db.close()
        except JWTError:
            pass
    context["models"] = model_registry.specs_for("demo")
    context["model_pricing"] = model_registry.pricing_table()
    return templates.TemplateResponse("demo.html", context)


@router.get("/api/demo/random-question")
async def random_question():
    """Return a random question from the eval set."""
    evals_dir = Path(os.getenv("EVALS_DIR", "data/evals"))
    questions = []
    try:
        for file_path in evals_dir.glob("*.json"):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results = data.get("results", []) if isinstance(data, dict) else data
            for r in results:
                q = r.get("question", "").strip()
                if q:
                    questions.append(q)
    except Exception as e:
        logging.warning(f"random-question: {e}")

    if not questions:
        return {"question": None}
    return {"question": random.choice(questions)}


@router.websocket("/ws/demo/")
async def websocket_demo(websocket: WebSocket):
    """Rate-limited public demo WebSocket — no auth required."""
    await websocket.accept()

    db = SessionLocal()
    try:
        enabled = is_demo_enabled(db)
    finally:
        db.close()

    if not enabled:
        await websocket.send_text(json.dumps({"type": "error", "message": "Demo is currently disabled."}))
        await websocket.close()
        return

    ip = _get_client_ip(websocket)
    logging.info(f"🔹 Demo WebSocket connected from {ip}")

    # Send initial remaining count
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        remaining = _get_remaining(db, ip, today)
        await websocket.send_text(json.dumps({
            "type": "demo_status",
            "remaining": remaining,
            "limit": DEMO_PER_IP_LIMIT
        }))
    finally:
        db.close()

    ping_task = None

    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(WEBSOCKET_PING_INTERVAL)
                try:
                    await websocket.send_text("__ping__")
                except RuntimeError:
                    break
        except Exception:
            pass

    try:
        ping_task = asyncio.create_task(keep_alive())

        while True:
            try:
                raw_message = await websocket.receive_text()

                if raw_message == "__pong__":
                    continue

                # Parse message
                image_data_urls: list[str] = []
                vsav_data_url = None
                try:
                    cmd = json.loads(raw_message)
                    if cmd.get("type") == "chat" and cmd.get("text"):
                        message = cmd["text"].strip()
                        selected_model = cmd.get("model")
                        image_data_urls = cmd.get("images") or []
                        if not image_data_urls and cmd.get("image"):
                            image_data_urls = [cmd.get("image")]
                        # Optional VASL .vsav save attachment (one per message)
                        vsav_data_url = cmd.get("vsav")
                    else:
                        continue
                except json.JSONDecodeError:
                    message = raw_message.strip()
                    selected_model = None

                if not message:
                    continue

                # Cap + validate + save attached images before touching the rate limit
                MAX_DEMO_IMAGES_PER_MESSAGE = 3
                image_paths: list[str] = []
                if len(image_data_urls) > MAX_DEMO_IMAGES_PER_MESSAGE:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"At most {MAX_DEMO_IMAGES_PER_MESSAGE} images per message.",
                    }))
                    continue
                image_save_failed = False
                for url in image_data_urls:
                    try:
                        image_paths.append(save_image_data_url(url, "demo"))
                    except ImageValidationError as ive:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"Image rejected: {ive}",
                        }))
                        image_save_failed = True
                        break
                if image_save_failed:
                    continue
                if image_paths:
                    logging.info(f"🖼️  Demo {len(image_paths)} image(s) saved: {image_paths}")

                # Persist + parse an attached VASL .vsav save, if any. A
                # message with a vsav still counts as 1 against the daily cap
                # (same as images). Errors are user-visible, never fatal.
                vsav_paths: list[str] = []
                board_state = None
                vsav_state = None  # parsed dict for the resolve_attack tool
                if vsav_data_url:
                    try:
                        vsav_rel_path = save_vsav_data_url(vsav_data_url, "demo")
                        vsav_state = parse_vsav(vsav_rel_path)
                        board_state = render_board_state(vsav_state)
                        vsav_paths = [vsav_rel_path]
                        val = vsav_state.get("validation", {})
                        logging.info(
                            f"🗺️  Demo .vsav parsed: {len(vsav_state.get('hexes', {}))} occupied hexes, "
                            f"breadcrumb check {val.get('n_matched')}/{val.get('n_breadcrumbs_checked')}"
                        )
                    except VsavError as ve:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"VASL save rejected: {ve}",
                        }))
                        continue
                    except Exception as ve:
                        logging.error(f"Demo .vsav processing failed: {ve}", exc_info=True)
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Could not process the attached VASL save. "
                                       "Please try again or ask without the attachment.",
                        }))
                        continue

                db = SessionLocal()
                try:
                    today = date.today().isoformat()

                    # Check global cap first
                    global_count = _get_global_count(db, today)
                    if global_count >= DEMO_GLOBAL_LIMIT:
                        await websocket.send_text(json.dumps({
                            "type": "rate_limit",
                            "message": "The demo has reached its daily usage limit. Please try again tomorrow or create an account for full access."
                        }))
                        continue

                    # Check per-IP limit
                    remaining = _get_remaining(db, ip, today)
                    if remaining <= 0:
                        await websocket.send_text(json.dumps({
                            "type": "rate_limit",
                            "message": f"You've used all {DEMO_PER_IP_LIMIT} demo questions for today. Try again tomorrow or create an account for unlimited access."
                        }))
                        continue

                    # Increment before calling API to prevent racing
                    _increment(db, ip, today)
                    remaining_after = remaining - 1

                    # Validate against the model registry (app/model_registry.py
                    # is the one table to edit when adding/removing models).
                    if selected_model in model_registry.allowed_keys("demo"):
                        model = model_registry.resolve(selected_model)
                    else:
                        model = DEMO_MODEL

                    # Force vision-capable model when image(s) attached.
                    # OpenRouter path doesn't support images yet, so we
                    # collapse to gpt-5.4 here regardless of user selection.
                    if image_paths and model != "gpt-5.4":
                        logging.info(f"🖼️  Demo image(s) attached - overriding model {model} -> gpt-5.4")
                        model = "gpt-5.4"

                    # A .vsav attachment enables the deterministic resolve_attack
                    # tool, which needs the OpenAI function-calling path — force
                    # gpt-5.4 (OpenRouter models would reject use_agentic).
                    if vsav_paths and model != "gpt-5.4":
                        logging.info(f"🗺️  Demo .vsav attached - overriding model {model} -> gpt-5.4")
                        model = "gpt-5.4"

                    asl_service = get_asl_service()
                    stream, timing_data = asl_service.get_answer(
                        message,
                        stream=True,
                        return_timing=True,
                        model=model,
                        max_chunks=DEMO_MAX_CHUNKS,
                        image_paths=image_paths or None,
                        board_state=board_state,
                        vsav_state=vsav_state,
                        use_agentic=bool(vsav_paths),
                    )

                    full_response = ""
                    response_received = False

                    # The generator is synchronous (blocking OpenAI SDK reads) —
                    # run each next() on a worker thread so the event loop can
                    # flush sends as they happen instead of buffering the whole
                    # response into one burst (see chat.py for details).
                    stream_iter = iter(stream)
                    _stream_end = object()
                    while True:
                        delta = await asyncio.to_thread(next, stream_iter, _stream_end)
                        if delta is _stream_end:
                            break
                        # Agentic-loop progress events ({"status": label}) go
                        # out as typed messages for the searching pill.
                        if isinstance(delta, dict):
                            await websocket.send_text(json.dumps({
                                "type": "status",
                                "label": delta.get("status", ""),
                            }))
                            continue
                        await websocket.send_text(delta)
                        full_response += delta
                        response_received = True

                    if response_received:
                        rag_sources = timing_data.get("rag_sources", [])
                        timing_clean = {k: v for k, v in timing_data.items() if k != "rag_sources"}
                        timing_clean["model"] = model
                        timing_clean["image_attached"] = bool(image_paths)
                        timing_clean["vsav_attached"] = bool(vsav_paths)

                        # Log user + assistant messages for stats
                        log_db = SessionLocal()
                        try:
                            log_db.add(DemoMessage(
                                ip_address=ip, role="user", content=message,
                                image_paths=image_paths or None,
                                vsav_paths=vsav_paths or None,
                            ))
                            log_db.add(DemoMessage(ip_address=ip, role="assistant", content=full_response, timing_data=timing_clean))
                            log_db.commit()
                        except Exception as log_err:
                            logging.warning(f"Demo message log failed: {log_err}")
                        finally:
                            log_db.close()

                        await websocket.send_text(json.dumps({
                            "type": "stream_complete",
                            "timing": timing_clean,
                            "rag_sources": rag_sources,
                            "remaining": remaining_after,
                        }))
                    else:
                        await websocket.send_text("Sorry, I couldn't generate a response. Please try again.")

                except Exception as e:
                    logging.error(f"❌ Demo API error: {e}", exc_info=True)
                    await websocket.send_text("Error processing your question. Please try again.")
                    # Close out the message so the client finalizes it —
                    # otherwise the next answer streams into the same bubble.
                    await websocket.send_text(json.dumps({"type": "stream_complete"}))
                finally:
                    db.close()

            except WebSocketDisconnect:
                raise

    except WebSocketDisconnect:
        logging.info(f"🔻 Demo WebSocket disconnected from {ip}")
    except Exception as e:
        logging.error(f"❌ Demo WebSocket error: {e}", exc_info=True)
    finally:
        if ping_task and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
