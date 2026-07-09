"""Chat and WebSocket routes for ASL rules assistance."""
import os
import asyncio
import json
import logging
import time
from typing import Optional
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketState
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from openai import OpenAI

from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE, WEBSOCKET_PING_INTERVAL
from app.core.auth import SECRET_KEY, ALGORITHM, require_user
from app.services.user_service import get_user_by_email
from app.services.asl_service import get_asl_service
from app.services.chat_history_service import get_chat_history_service
from app.services.chat_log_service import append_chat_log
from app.services.image_storage import save_image_data_url, resolve_image_path, ImageValidationError
from app.services.vsav_service import (
    save_vsav_data_url, parse_vsav, render_board_state, VsavError,
)
from app.database import SessionLocal, get_db
from app.models.user import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(
    api_key=openai_api_key,
    organization=os.getenv("OPENAI_ORG_ID"),
    project=os.getenv("OPENAI_PROJECT_ID")
)

# Load Responses API configuration
responses_config = None
try:
    if os.path.exists("responses_api_config.json"):
        with open("responses_api_config.json", "r") as f:
            responses_config = json.load(f)
except Exception as e:
    logging.error(f"Error loading responses config: {e}")


def get_base_context(request: Request, user=None):
    """Get base template context."""
    import os
    from app.api.demo import is_demo_enabled
    context = {"request": request, "user": user, "demo_enabled": is_demo_enabled()}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return context


def get_current_user_from_request(request: Request) -> Optional[User]:
    """Extract user from request cookies."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    
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


async def get_user_from_websocket(websocket: WebSocket) -> Optional[User]:
    """
    Extract and validate user from WebSocket connection.
    
    Checks for token in query params first, then falls back to cookies.
    """
    # Try query param first (for explicit token passing)
    token = websocket.query_params.get("token")
    
    if not token:
        # Fall back to cookies (sent automatically by browser)
        cookies = websocket.cookies
        token = cookies.get("access_token")
    
    if not token:
        return None
    
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


@router.get("/", response_class=RedirectResponse)
def root():
    """Redirect root to home."""
    return RedirectResponse(url="/home")


@router.get("/home", name="home", response_class=HTMLResponse)
def home_page(request: Request):
    """Display home/landing page."""
    user = get_current_user_from_request(request)
    context = get_base_context(request, user)
    return templates.TemplateResponse("home.html", context)


@router.get("/api/uploads/{conversation_id}/{filename}")
def get_upload(
    conversation_id: int,
    filename: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Auth-gated retrieval of an uploaded image. Owner-only, with admin bypass."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    is_admin = user.email == os.getenv("ADMIN_EMAIL")
    if is_admin:
        conv = get_chat_history_service().get_conversation_any_owner(db, conversation_id)
    else:
        conv = get_chat_history_service().get_conversation(db, conversation_id, user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        fpath = resolve_image_path(f"{conversation_id}/{filename}")
    except ImageValidationError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(fpath)


@router.get("/api/admin/uploads/demo/{filename}")
def get_demo_upload(
    filename: str,
    user: User = Depends(require_user),
):
    """Admin-only retrieval of a demo (anonymous) uploaded image."""
    if user.email != os.getenv("ADMIN_EMAIL"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    try:
        fpath = resolve_image_path(f"demo/{filename}")
    except ImageValidationError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(fpath)


@router.get("/ruleschat", name="ruleschat", response_class=HTMLResponse)
def ruleschat(request: Request):
    """Protected rules chat page."""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return RedirectResponse(url="/login", status_code=303)
        
        db = SessionLocal()
        try:
            user = get_user_by_email(db, email)
        finally:
            db.close()
        
        if not user:
            return RedirectResponse(url="/login", status_code=303)
        
        context = get_base_context(request, user)
        context["cost_per_1m_input"] = float(os.getenv("COST_PER_1M_INPUT", "0.25"))
        context["cost_per_1m_output"] = float(os.getenv("COST_PER_1M_OUTPUT", "1.00"))
        return templates.TemplateResponse("ruleschat.html", context)
    except JWTError:
        return RedirectResponse(url="/login", status_code=303)


# ============================================================================
# REST API Endpoints for Conversation Management
# ============================================================================

@router.get("/api/conversations")
async def list_conversations(request: Request):
    """List user's conversations."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db = SessionLocal()
    try:
        service = get_chat_history_service()
        conversations = service.get_user_conversations(db, user.id)
        return [
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat()
            }
            for c in conversations
        ]
    finally:
        db.close()


@router.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: int, request: Request):
    """Get messages for a conversation."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db = SessionLocal()
    try:
        service = get_chat_history_service()
        
        # Verify ownership
        conv = service.get_conversation(db, conversation_id, user.id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        messages = service.get_conversation_messages(db, conversation_id)
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "rag_sources": m.rag_sources,
                "image_paths": m.image_paths,
                "vsav_paths": m.vsav_paths,
            }
            for m in messages
        ]
    finally:
        db.close()


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, request: Request):
    """Delete (soft) a conversation."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db = SessionLocal()
    try:
        service = get_chat_history_service()
        success = service.delete_conversation(db, conversation_id, user.id)
        if not success:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "deleted"}
    finally:
        db.close()


# ============================================================================
# WebSocket Chat Endpoint with History Support
# ============================================================================

@router.websocket("/ws/chat/")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with AI assistant and history support."""
    
    # Authenticate user before accepting connection
    user = await get_user_from_websocket(websocket)
    if not user:
        logging.warning("🔻 WebSocket connection rejected - unauthorized")
        await websocket.close(code=4001, reason="Unauthorized")
        return
    
    await websocket.accept()
    logging.info(f"🔹 WebSocket connection established for user: {user.email}")
    
    # Get conversation_id from query params if provided
    conversation_id_str = websocket.query_params.get("conversation_id")
    conversation_id = int(conversation_id_str) if conversation_id_str else None
    
    ping_task = None
    chat_history_service = get_chat_history_service()
    
    async def keep_alive():
        """Send periodic pings to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(WEBSOCKET_PING_INTERVAL)
                try:
                    await websocket.send_text("__ping__")
                    logging.debug("Sent ping to keep WebSocket alive")
                except RuntimeError:
                    break
        except Exception as e:
            logging.error(f"Ping error: {e}")
    
    try:
        # Start the ping task
        ping_task = asyncio.create_task(keep_alive())
        
        # Check if Responses API is properly configured
        if not responses_config:
            await websocket.send_text("Error: Responses API not properly configured.")
            return
        
        while True:
            try:
                raw_message = await websocket.receive_text()
                
                # Handle ping response
                if raw_message == "__pong__":
                    logging.debug("Received pong")
                    continue
                
                # Check for JSON commands
                try:
                    cmd = json.loads(raw_message)
                    
                    # Handle new conversation request
                    if cmd.get("type") == "new_conversation":
                        db = SessionLocal()
                        try:
                            conv = chat_history_service.create_conversation(
                                db, user.id, cmd.get("title", "New Chat")
                            )
                            conversation_id = conv.id
                            await websocket.send_text(json.dumps({
                                "type": "conversation_created",
                                "conversation_id": conv.id,
                                "title": conv.title
                            }))
                            logging.info(f"📝 Created new conversation: {conv.id}")
                        finally:
                            db.close()
                        continue
                    
                    # Handle switch conversation request
                    if cmd.get("type") == "switch_conversation":
                        new_conv_id = cmd.get("conversation_id")
                        db = SessionLocal()
                        try:
                            # Verify ownership before switching
                            conv = chat_history_service.get_conversation(db, new_conv_id, user.id)
                            if conv:
                                conversation_id = new_conv_id
                                await websocket.send_text(json.dumps({
                                    "type": "conversation_switched",
                                    "conversation_id": conversation_id
                                }))
                                logging.info(f"🔄 Switched to conversation: {conversation_id}")
                            else:
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "message": "Conversation not found"
                                }))
                        finally:
                            db.close()
                        continue
                    
                    # If it's a chat message with text field
                    if cmd.get("type") == "chat" and cmd.get("text"):
                        message = cmd.get("text")
                        selected_model = cmd.get("model")  # Optional model override
                        agentic = bool(cmd.get("agentic"))  # UI toggle: enable tool calling
                        # New shape: images is a list. Accept legacy single-image
                        # field too, just in case an old client connects.
                        image_data_urls = cmd.get("images") or []
                        if not image_data_urls and cmd.get("image"):
                            image_data_urls = [cmd.get("image")]
                        # Optional VASL .vsav save attachment (one per message)
                        vsav_data_url = cmd.get("vsav")
                    else:
                        continue  # Unknown command

                except json.JSONDecodeError:
                    # Not JSON, treat as plain text chat message
                    message = raw_message
                    selected_model = None
                    agentic = False
                    image_data_urls = []
                    vsav_data_url = None
                
                logging.info(f"✅ Received question: {message[:100]}...")
                
                # Process chat message
                db = SessionLocal()
                try:
                    asl_service = get_asl_service()
                    logging.info(f"📊 Using Vector Store: {asl_service.config.vector_store_id}")
                    
                    # Create conversation if needed
                    if not conversation_id:
                        conv = chat_history_service.create_conversation(db, user.id, message)
                        conversation_id = conv.id
                        await websocket.send_text(json.dumps({
                            "type": "conversation_created",
                            "conversation_id": conv.id,
                            "title": conv.title
                        }))
                        logging.info(f"📝 Auto-created conversation: {conv.id}")

                    # Persist attached images, if any (capped at 3 per message)
                    MAX_IMAGES_PER_MESSAGE = 3
                    image_paths: list[str] = []
                    if len(image_data_urls) > MAX_IMAGES_PER_MESSAGE:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"At most {MAX_IMAGES_PER_MESSAGE} images per message.",
                        }))
                        continue
                    image_save_failed = False
                    for url in image_data_urls:
                        try:
                            image_paths.append(save_image_data_url(url, conversation_id))
                        except ImageValidationError as e:
                            logging.warning(f"Image rejected: {e}")
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"Image rejected: {e}",
                            }))
                            image_save_failed = True
                            break
                    if image_save_failed:
                        continue
                    if image_paths:
                        logging.info(f"🖼️  Saved {len(image_paths)} image(s) for conv {conversation_id}: {image_paths}")

                    # Persist + parse an attached VASL .vsav save, if any.
                    # Parse errors are user-visible and never crash the socket.
                    vsav_paths: list[str] = []
                    board_state = None
                    vsav_state = None  # parsed dict for the resolve_attack tool
                    if vsav_data_url:
                        try:
                            vsav_rel_path = save_vsav_data_url(vsav_data_url, conversation_id)
                            vsav_state = parse_vsav(vsav_rel_path)
                            board_state = render_board_state(vsav_state)
                            vsav_paths = [vsav_rel_path]
                            val = vsav_state.get("validation", {})
                            logging.info(
                                f"🗺️  Parsed .vsav for conv {conversation_id}: "
                                f"{len(vsav_state.get('hexes', {}))} occupied hexes, "
                                f"breadcrumb check {val.get('n_matched')}/{val.get('n_breadcrumbs_checked')}"
                            )
                        except VsavError as ve:
                            logging.warning(f".vsav rejected: {ve}")
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"VASL save rejected: {ve}",
                            }))
                            continue
                        except Exception as ve:
                            logging.error(f".vsav processing failed: {ve}", exc_info=True)
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": "Could not process the attached VASL save. "
                                           "Please try again or ask without the attachment.",
                            }))
                            continue

                    # Build input with conversation history
                    history_prefix = chat_history_service.format_history_for_api(db, conversation_id)
                    if history_prefix:
                        full_input = history_prefix + message
                        logging.info(f"📚 Including {len(history_prefix)} chars of history")
                    else:
                        full_input = message
                    
                    # Validate selected model (whitelist). OpenRouter shortcuts
                    # are expanded to their full vendor/model slugs — the "/"
                    # is what triggers OpenRouter routing in ASLService.
                    OPENROUTER_SLUG = {
                        "deepseek-v3": "deepseek/deepseek-v3.2",
                        "mercury-2": "inception/mercury-2",
                        "fable": "anthropic/claude-fable-5",
                        "glm-5.2": "z-ai/glm-5.2",
                    }
                    allowed_models = {
                        "gpt-5.4", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-4.1-mini",
                        "deepseek-v3", "mercury-2", "fable", "glm-5.2",
                    }
                    if selected_model in allowed_models:
                        model_override = OPENROUTER_SLUG.get(selected_model, selected_model)
                    else:
                        model_override = None

                    # Force vision-capable model when image(s) attached
                    # (OpenRouter path doesn't support images yet).
                    if image_paths and model_override != "gpt-5.4":
                        logging.info(f"🖼️  Image(s) attached — overriding model {model_override} → gpt-5.4")
                        model_override = "gpt-5.4"

                    # Agentic tool calling is an OpenAI-Responses-only feature;
                    # disable it for OpenRouter-routed models (slug contains "/").
                    agentic_enabled = agentic and not (model_override and "/" in model_override)
                    if agentic and not agentic_enabled:
                        logging.info("🤖 Agentic requested but disabled for OpenRouter model %s", model_override)
                    elif agentic_enabled:
                        logging.info("🤖 Agentic tool calling enabled for this message")

                    # Get streaming response from service
                    stream, timing_data = asl_service.get_answer(
                        full_input,
                        stream=True,
                        return_timing=True,
                        model=model_override,
                        image_paths=image_paths or None,
                        board_state=board_state,
                        vsav_state=vsav_state,
                        use_agentic=agentic_enabled,
                    )
                    
                    logging.info("🔄 Streaming response from OpenAI...")
                    response_received = False
                    full_response = ""
                    delta_count = 0
                    first_delta_time = None
                    
                    # Stream deltas to client. Dict items are progress events
                    # from the agentic loop ({"status": label}) — forwarded as
                    # typed messages for the searching pill, not answer text.
                    for delta in stream:
                        if isinstance(delta, dict):
                            await websocket.send_text(json.dumps({
                                "type": "status",
                                "label": delta.get("status", ""),
                            }))
                            continue
                        delta_count += 1
                        if first_delta_time is None:
                            first_delta_time = time.time()

                        await websocket.send_text(delta)
                        full_response += delta
                        response_received = True
                    
                    if response_received:
                        total_time = (time.time() - first_delta_time) * 1000 if first_delta_time else 0
                        logging.info(f"✅ Response streamed - {delta_count} deltas in {total_time:.0f}ms")
                        
                        # Save messages to history
                        chat_history_service.add_message(
                            db, conversation_id, "user", message,
                            image_paths=image_paths or None,
                            vsav_paths=vsav_paths or None,
                        )

                        rag_sources = timing_data.get('rag_sources', [])
                        timing_without_sources = {k: v for k, v in timing_data.items() if k != 'rag_sources'}
                        timing_without_sources["model"] = model_override or DEFAULT_MODEL
                        timing_without_sources["image_attached"] = bool(image_paths)
                        timing_without_sources["vsav_attached"] = bool(vsav_paths)

                        chat_history_service.add_message(
                            db, conversation_id, "assistant", full_response,
                            rag_sources=rag_sources,
                            timing_data=timing_without_sources
                        )

                        logging.info(f"💾 Saved messages to conversation {conversation_id}")

                        # Append to JSONL log file
                        append_chat_log(
                            user_email=user.email,
                            question=message,
                            answer=full_response,
                            model=model_override or DEFAULT_MODEL,
                            timing_data=timing_without_sources,
                            image_paths=image_paths or None,
                            vsav_paths=vsav_paths or None,
                        )
                        logging.info(f"📤 Sending {len(rag_sources)} RAG sources to frontend")
                        
                        # Send completion signal
                        completion_signal = json.dumps({
                            "type": "stream_complete",
                            "conversation_id": conversation_id,
                            "timing": timing_without_sources,
                            "rag_sources": rag_sources
                        })
                        await websocket.send_text(completion_signal)
                    else:
                        logging.warning("⚠️ No response content received from stream")
                        await websocket.send_text("Sorry, I couldn't generate a response. Please try again.")
                
                except AttributeError as attr_error:
                    logging.error(f"❌ Attribute Error: {attr_error}")
                    await websocket.send_text("Error: OpenAI client configuration issue.")
                except ValueError as val_error:
                    logging.error(f"❌ Configuration Error: {val_error}")
                    await websocket.send_text("Error: Responses API not properly configured.")
                except Exception as api_error:
                    logging.error(f"❌ API Error: {api_error}", exc_info=True)
                    await websocket.send_text(f"Error: {str(api_error)}")
                finally:
                    db.close()
                
                logging.info("✅ Finished processing response.")
            
            except WebSocketDisconnect:
                logging.info("🔻 WebSocket disconnected while processing message.")
                raise
    
    except WebSocketDisconnect:
        logging.info(f"🔻 WebSocket disconnected for user: {user.email}")
    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}", exc_info=True)
    finally:
        if ping_task and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        logging.info("🔻 WebSocket connection resources cleaned up.")
