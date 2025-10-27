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
from app.database import SessionLocal

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(
    api_key=openai_api_key,
    organization=os.getenv("OPENAI_ORG_ID", "org-XgfOCezbMRf4TG0OpmpQs8q5")
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
    return {"request": request, "user": user}


@router.get("/", response_class=RedirectResponse)
def root():
    """Redirect root to login."""
    return RedirectResponse(url="/login")


@router.get("/home", name="home", response_class=HTMLResponse)
def home_page(request: Request):
    """Display home/landing page."""
    context = get_base_context(request)
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


@router.get("/evals", name="evals_page", response_class=HTMLResponse)
def evals_page(request: Request):
    """Display evaluation results page."""
    import json
    from collections import Counter
    
    context = get_base_context(request)
    
    # Since evals were moved to separate repo, show message
    context.update({
        "error": "Evaluation data has been moved to the mysite2-evals-sft repository",
        "results": [],
        "total": 0,
        "correct": 0,
        "incorrect": 0,
        "partial": 0,
        "correct_pct": 0,
        "incorrect_pct": 0,
        "partial_pct": 0
    })
    
    return templates.TemplateResponse("evals.html", context)


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
                
                # Use Responses API with file search and native streaming
                logging.info("🟢 Starting Responses API with file search and streaming...")
                try:
                    if not hasattr(client, 'responses'):
                        raise AttributeError("OpenAI client does not have 'responses' attribute")
                    
                    if not responses_config or 'vector_store_id' not in responses_config:
                        raise ValueError("Responses API configuration not properly loaded")
                    
                    logging.info(f"📊 Using Vector Store: {responses_config['vector_store_id']}")
                    
                    # Create streaming response
                    stream = client.responses.create(
                        model=DEFAULT_MODEL,
                        input=message,
                        instructions=ASL_SYSTEM_INSTRUCTIONS,
                        temperature=TEMPERATURE,
                        stream=True,  # Enable native streaming
                        tools=[{
                            "type": "file_search",
                            "vector_store_ids": [responses_config["vector_store_id"]],
                        }]
                    )
                    
                    logging.info("🔄 Streaming response from OpenAI...")
                    response_received = False
                    
                    # Stream events as they arrive
                    for event in stream:
                        try:
                            # Handle text delta events (this is where the actual response text is)
                            if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                                if hasattr(event, 'delta') and event.delta:
                                    await websocket.send_text(event.delta)
                                    response_received = True
                            elif hasattr(event, 'type') and event.type == 'error':
                                logging.error(f"Stream error event: {event}")
                                raise Exception(f"Stream error: {event}")
                        except Exception as stream_error:
                            logging.error(f"Error processing stream event: {stream_error}")
                            # Continue processing other events
                            continue
                    
                    if response_received:
                        logging.info("✅ Response streamed successfully")
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

