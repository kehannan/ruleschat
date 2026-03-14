"""Demo mode: public, rate-limited chat endpoint."""
import os
import json
import logging
import asyncio
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.websockets import WebSocket, WebSocketDisconnect
from sqlalchemy import func

from app.database import SessionLocal
from app.models.demo import DemoUsage
from app.services.asl_service import get_asl_service

router = APIRouter()
templates = Jinja2Templates(directory="templates")

DEMO_PER_IP_LIMIT = 5
DEMO_GLOBAL_LIMIT = 250
DEMO_MAX_CHUNKS = 20
DEMO_MODEL = "gpt-5-mini"
WEBSOCKET_PING_INTERVAL = 30


def _get_client_ip(websocket: WebSocket) -> str:
    """Extract real IP, respecting X-Forwarded-For from nginx."""
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
    return templates.TemplateResponse("demo.html", context)


@router.websocket("/ws/demo/")
async def websocket_demo(websocket: WebSocket):
    """Rate-limited public demo WebSocket — no auth required."""
    await websocket.accept()

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
                try:
                    cmd = json.loads(raw_message)
                    if cmd.get("type") == "chat" and cmd.get("text"):
                        message = cmd["text"].strip()
                        selected_model = cmd.get("model")
                    else:
                        continue
                except json.JSONDecodeError:
                    message = raw_message.strip()
                    selected_model = None

                if not message:
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

                    allowed_models = {"gpt-5-mini", "gpt-4.1-mini"}
                    model = selected_model if selected_model in allowed_models else DEMO_MODEL

                    asl_service = get_asl_service()
                    stream, timing_data = asl_service.get_answer(
                        message,
                        stream=True,
                        return_timing=True,
                        model=model,
                        max_chunks=DEMO_MAX_CHUNKS,
                    )

                    full_response = ""
                    response_received = False

                    for delta in stream:
                        await websocket.send_text(delta)
                        full_response += delta
                        response_received = True

                    if response_received:
                        rag_sources = timing_data.get("rag_sources", [])
                        timing_clean = {k: v for k, v in timing_data.items() if k != "rag_sources"}
                        timing_clean["model"] = model

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
