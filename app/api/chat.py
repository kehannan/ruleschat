"""Chat and WebSocket routes for ASL rules assistance."""
import os
import asyncio
import json
import logging
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketState
from jose import jwt, JWTError
from openai import OpenAI

from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE, WEBSOCKET_PING_INTERVAL
from app.core.auth import SECRET_KEY, ALGORITHM
from app.services.user_service import get_user_by_email
from app.services.asl_service import get_asl_service
from app.database import SessionLocal

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
    context = {"request": request, "user": user}
    if user:
        context["user_email"] = user.email
        context["admin_email"] = os.getenv("ADMIN_EMAIL")
    return context


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    """Display home/landing page at root."""
    # Check if user is logged in (optional - home is public)
    user = None
    token = request.cookies.get("access_token")
    if token:
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
    
    context = get_base_context(request, user)
    return templates.TemplateResponse("home.html", context)


@router.get("/home", name="home", response_class=HTMLResponse)
def home_page(request: Request):
    """Display home/landing page."""
    # Check if user is logged in
    user = None
    token = request.cookies.get("access_token")
    if token:
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
    
    context = get_base_context(request, user)
    return templates.TemplateResponse("home.html", context)


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
        return templates.TemplateResponse("ruleschat.html", context)
    except JWTError:
        return RedirectResponse(url="/login", status_code=303)

@router.websocket("/ws/chat/")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with AI assistant."""
    logging.info("🔹 WebSocket connection established.")
    await websocket.accept()
    
    ping_task = None
    
    async def keep_alive():
        """Send periodic pings to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(WEBSOCKET_PING_INTERVAL)
                try:
                    await websocket.send_text("__ping__")
                    logging.info("Sent ping to keep WebSocket alive")
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
                message = await websocket.receive_text()
                
                # Handle ping response
                if message == "__pong__":
                    logging.info("Received pong")
                    continue
                
                logging.info(f"✅ Received question: {message}")
                
                # Use ASL Service for consistent responses
                logging.info("🟢 Using ASL Service for response...")
                try:
                    # Get the ASL service (uses same config as web app)
                    asl_service = get_asl_service()
                    logging.info(f"📊 Using Vector Store: {asl_service.vector_store_id}")
                    
                    # Get streaming response from service
                    stream = asl_service.get_answer(message, stream=True)
                    
                    logging.info("🔄 Streaming response from OpenAI...")
                    response_received = False
                    
                    # Stream deltas from service
                    import time
                    delta_count = 0
                    first_delta_time = None
                    
                    for delta in stream:
                        delta_count += 1
                        if first_delta_time is None:
                            first_delta_time = time.time()
                        
                        await websocket.send_text(delta)
                        response_received = True
                    
                    if response_received:
                        total_time = (time.time() - first_delta_time) * 1000 if first_delta_time else 0
                        logging.info(f"✅ Response streamed successfully - {delta_count} deltas in {total_time:.0f}ms")
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
                    logging.error(f"❌ API Error: {api_error}")
                    await websocket.send_text(f"Error: {str(api_error)}")
                
                logging.info("✅ Finished processing response.")
            
            except WebSocketDisconnect:
                logging.info("🔻 WebSocket disconnected while processing message.")
                raise
    
    except WebSocketDisconnect:
        logging.info("🔻 WebSocket disconnected by client.")
    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}")
    finally:
        if ping_task and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        logging.info("🔻 WebSocket connection resources cleaned up.")

