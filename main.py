import os
import sys
import logging
import asyncio
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, Response, status, Depends, HTTPException
from starlette.websockets import WebSocketState
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from dotenv import load_dotenv
from openai import OpenAI

# Import your user model and auth utilities
from models import get_user_by_username, update_user_profile, User  # Function to retrieve a user by username
from auth import verify_password, create_access_token, get_password_hash  # Functions for password verification and token creation

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

# User dependency
async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        user = get_user_by_username(username)
        return user
    except JWTError:
        return None

# Home route - accessible without login
@app.get("/home", name="home", response_class=HTMLResponse)
def home(request: Request):
    token = request.cookies.get("access_token")
    username = None
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
        except JWTError:
            pass
            
    return templates.TemplateResponse("home.html", {"request": request, "username": username})

# Root route redirects to home
@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/home", status_code=303)

# Login page
@app.get("/login", name="login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "username": None})

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

# Profile page
@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("profile.html", {
        "request": request, 
        "user": user,
        "username": user.username,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("message_type", "info")
    })

# Update profile
@app.post("/update-profile", response_class=RedirectResponse, name="update_profile")
async def update_profile(request: Request, email: str = Form(None), user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    update_user_profile(user.id, email=email)
    return RedirectResponse(
        url=f"/profile?message=Profile+updated+successfully&message_type=success", 
        status_code=303
    )

# Change password
@app.post("/change-password", response_class=RedirectResponse, name="change_password")
async def change_password(
    request: Request, 
    current_password: str = Form(...), 
    new_password: str = Form(...), 
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user)
):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Verify current password
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse(
            url=f"/profile?message=Current+password+is+incorrect&message_type=danger", 
            status_code=303
        )
    
    # Confirm passwords match
    if new_password != confirm_password:
        return RedirectResponse(
            url=f"/profile?message=New+passwords+do+not+match&message_type=danger", 
            status_code=303
        )
    
    # Update password
    hashed_password = get_password_hash(new_password)
    update_user_profile(user.id, hashed_password=hashed_password)
    
    return RedirectResponse(
        url=f"/profile?message=Password+changed+successfully&message_type=success", 
        status_code=303
    )

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
                try:
                    await websocket.send_text("__ping__")
                    logging.info("Sent ping to keep WebSocket alive")
                except RuntimeError:
                    # Connection likely already closed
                    break
        except Exception as e:
            logging.error(f"Ping error: {e}")

    try:
        # Start the ping task
        ping_task = asyncio.create_task(keep_alive())
        
        # Store conversation history
        conversation_history = [
            {"role": "system", "content": "You are an expert in Advanced Squad Leader rules. Use your knowledge to answer questions accurately and concisely."}
        ]

        while True:  # Keep connection open for multiple interactions
            try:
                message = await websocket.receive_text()
                
                # Handle ping response
                if message == "__pong__":
                    logging.info("Received pong")
                    continue
                    
                logging.info(f"✅ Received question: {message}")

                # Add user message to history
                conversation_history.append({"role": "user", "content": message})

                # Start streaming the assistant's response
                logging.info("🟢 Starting OpenAI response stream...")
                stream = client.chat.completions.create(
                    model="gpt-4-turbo-preview",
                    messages=conversation_history,
                    stream=True
                )

                collected_message = ""
                for chunk in stream:
                    if chunk.choices[0].delta.content is not None:
                        text = chunk.choices[0].delta.content
                        collected_message += text
                        await websocket.send_text(text)
                        logging.info(f"📤 Sent chunk: {text}")
                        await asyncio.sleep(0.01)  # Small delay to ensure chunks are sent separately

                # Add assistant's message to history
                conversation_history.append({"role": "assistant", "content": collected_message})
                
                # Keep conversation history manageable
                if len(conversation_history) > 10:
                    # Keep system message and last 4 exchanges
                    conversation_history = [conversation_history[0]] + conversation_history[-8:]

                logging.info("✅ Finished streaming response. Waiting for the next message...")
            except WebSocketDisconnect:
                # This could happen during receive_text or send_text
                logging.info("🔻 WebSocket disconnected while processing message.")
                raise  # Re-raise to be caught by the outer try/except

    except WebSocketDisconnect:
        logging.info("🔻 WebSocket disconnected by client.")
    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}")
        # No need to close here - let the finally block handle it
    finally:
        # Cancel the ping task
        if ping_task and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        
        # Log that we're done but don't try to close the connection
        # FastAPI will handle the connection closure
        logging.info("🔻 WebSocket connection resources cleaned up.")

# Logout route: removes access token and redirects to login
@app.get("/logout", name="logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response