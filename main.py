import os
import sys
import logging
import asyncio
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from dotenv import load_dotenv
from openai import OpenAI

# Import your user model and auth utilities
from models import get_user_by_username  # Function to retrieve a user by username
from auth import verify_password, create_access_token  # Functions for password verification and token creation

# Configure logging with forced flush
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

# Load environment variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client and retrieve your assistant
client = OpenAI(api_key=openai_api_key)
assistant_id = "asst_M65nFsVKjQRamCQrfHThTeJt"
assistant = client.beta.assistants.retrieve(assistant_id)

app = FastAPI()

# --- JWT Configuration ---
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # Increased from 30 to 60 minutes

templates = Jinja2Templates(directory="templates")

# Mount static files
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")

# Home route
@app.get("/home", name="home", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

# Root route redirects to login
@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/login", status_code=303)

# Login page
@app.get("/login", name="login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# Login form submission
@app.post("/login")
def do_login(username: str = Form(...), password: str = Form(...)):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user.hashed_password):
        return HTMLResponse("<h3>Invalid credentials</h3>", status_code=401)
    token = create_access_token({"sub": username}, expires_delta=ACCESS_TOKEN_EXPIRE_MINUTES)
    response = RedirectResponse(url="/ruleschat", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="Lax",  # Default behavior; adjust if needed
        max_age=3600 * ACCESS_TOKEN_EXPIRE_MINUTES  # Set cookie expiration in seconds
        # secure=False for development; use True in production with HTTPS
    )
    return response

# Protected Rules Chat page
@app.get("/ruleschat", response_class=HTMLResponse)
def ruleschat(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return RedirectResponse(url="/login", status_code=303)
    except JWTError:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("ruleschat.html", {"request": request, "username": username})

@app.websocket("/ws/chat/")
async def websocket_chat(websocket: WebSocket):
    logging.info("🔹 WebSocket connection established.")
    await websocket.accept()
    
    # Keep-alive ping task
    ping_task = None
    
    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(30)  # Send ping every 30 seconds
                await websocket.send_text("__ping__")
                logging.info("Sent ping to keep WebSocket alive")
        except Exception as e:
            logging.error(f"Ping error: {e}")

    try:
        # Start the ping task
        ping_task = asyncio.create_task(keep_alive())
        
        # Create a single persistent OpenAI thread for the session
        thread = client.beta.threads.create()
        logging.info(f"🆕 Created persistent thread: {thread.id}")

        while True:  # Keep connection open for multiple interactions
            message = await websocket.receive_text()
            
            # Handle ping response
            if message == "__pong__":
                logging.info("Received pong")
                continue
                
            logging.info(f"✅ Received question: {message}")

            # Add the new message to the existing OpenAI thread
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=message
            )
            logging.info(f"📩 Added message to thread {thread.id}")

            # Start streaming the assistant's response
            logging.info("🟢 Starting OpenAI response stream...")
            with client.beta.threads.runs.stream(
                thread_id=thread.id,
                assistant_id=assistant.id,
                instructions="You are an expert in Advanced Squad Leader rules. Use your knowledge to answer questions accurately and concisely."
            ) as stream:
                for chunk in stream:
                    if chunk.event == "thread.message.delta":
                        for content_block in chunk.data.delta.content:
                            if content_block.type == "text":
                                text = content_block.text.value
                                await websocket.send_text(text)
                                logging.info(f"📤 Sent chunk: {text}")
                                sys.stdout.flush()

            logging.info("✅ Finished streaming response. Waiting for the next message...")

    except WebSocketDisconnect:
        logging.info("🔻 WebSocket disconnected by client.")
    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}")
    finally:
        # Cancel the ping task if it exists
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        logging.info("🔻 Closing WebSocket connection.")
        await websocket.close()

# Logout route: removes access token and redirects to login
@app.get("/logout", name="logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response