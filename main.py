import os
import sys
import logging
import asyncio
import secrets
import string
import random
import json
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, Response, status, Depends, HTTPException, Body, BackgroundTasks
from starlette.websockets import WebSocketState
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime, timedelta
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import EmailStr
from assistant import EventHandler  # Add this import

# Import Responses API handler
from responses_api import initialize_vector_store, get_vector_store_manager
from config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE, WEBSOCKET_PING_INTERVAL, STREAMING_DELAY

# Import your user model and auth utilities
from models import get_user_by_username, update_user_profile, User, Invitation  # Function to retrieve a user by username
from auth import verify_password, create_access_token, get_password_hash  # Functions for password verification and token creation
from models import SessionLocal

# Configure logging with forced flush
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

# Load environment variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
print("MAIL_USERNAME:", os.getenv("MAIL_USERNAME"))
print("MAIL_PASSWORD:", os.getenv("MAIL_PASSWORD"))
print("MAIL_SERVER:", os.getenv("MAIL_SERVER"))
print("MAIL_STARTTLS:", os.getenv("MAIL_STARTTLS"))
print("MAIL_SSL_TLS:", os.getenv("MAIL_SSL_TLS"))

# Initialize OpenAI client for new chat completions API
# Use the organization where the vector store is located
client = OpenAI(
    api_key=openai_api_key,
    organization="org-XgfOCezbMRf4TG0OpmpQs8q5"
)

# Initialize vector store manager
vector_store_manager = None
try:
    vector_store_manager = initialize_vector_store(openai_api_key)
    print("✅ Vector store manager initialized")
except Exception as e:
    print(f"❌ Failed to initialize vector store manager: {e}")

# Load Responses API configuration
responses_config = None
try:
    if os.path.exists("responses_api_config.json"):
        with open("responses_api_config.json", "r") as f:
            responses_config = json.load(f)
        print("✅ Loaded Responses API configuration")
    else:
        print("⚠️ No Responses API configuration found. Run setup_responses_api.py first.")
except Exception as e:
    print(f"❌ Error loading Responses API configuration: {e}")

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
        email = payload.get("sub")
        if not email:
            return None
        user = get_user_by_username(email)
        return user
    except JWTError:
        return None

# Admin middleware
async def get_admin_user(request: Request):
    user = await get_current_user(request)
    if not user or user.email != os.getenv("ADMIN_EMAIL"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_base_context(request, user=None):
    return {
        "request": request,
        "user_email": user.email if user else None,
        "admin_email": os.getenv("ADMIN_EMAIL"),
    }

# Home route - accessible without login
@app.get("/home", name="home", response_class=HTMLResponse)
def home(request: Request):
    token = request.cookies.get("access_token")
    user = None
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = get_user_by_username(username)
        except JWTError:
            pass
    context = get_base_context(request, user)
    return templates.TemplateResponse("home.html", context)

# Root route redirects to home
@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/home", status_code=303)

# Login page
@app.get("/login", name="login", response_class=HTMLResponse)
def login_page(request: Request):
    context = get_base_context(request)
    return templates.TemplateResponse("login.html", context)

# Login form submission
@app.post("/login")
def do_login(username: str = Form(...), password: str = Form(...)):
    # Treat username as email
    user = get_user_by_username(username)
    if not user or not verify_password(password, user.hashed_password):
        return HTMLResponse("<h3>Invalid credentials</h3>", status_code=401)
    token = create_access_token({"sub": user.email}, expires_delta=ACCESS_TOKEN_EXPIRE_MINUTES)
    response = RedirectResponse(url="/ruleschat", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="Lax",
        max_age=3600 * ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return response

# Protected Rules Chat page
@app.get("/ruleschat", response_class=HTMLResponse)
def ruleschat(request: Request):
    token = request.cookies.get("access_token")
    user = None
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return RedirectResponse(url="/login", status_code=303)
        user = get_user_by_username(username)
    except JWTError:
        return RedirectResponse(url="/login", status_code=303)
    context = get_base_context(request, user)
    return templates.TemplateResponse("ruleschat.html", context)

# Profile page
@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    context = get_base_context(request, user)
    context.update({
        "user": user,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("message_type", "info")
    })
    return templates.TemplateResponse("profile.html", context)

# Evals page - accessible to admin users
@app.get("/evals", response_class=HTMLResponse)
async def evals_page(request: Request):
    """Display evaluation results from manual_eval_results.jsonl"""
    try:
        import json
        from collections import Counter
        
        # Read evaluation results
        results = []
        eval_file_path = "evals/manual_eval_results.jsonl"
        
        if os.path.exists(eval_file_path):
            with open(eval_file_path, "r", encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        results.append(json.loads(line))
        
        # Calculate statistics
        total = len(results)
        if total > 0:
            judgment_counts = Counter(result['judgment'] for result in results)
            correct = judgment_counts.get('correct', 0)
            incorrect = judgment_counts.get('incorrect', 0)
            partial = judgment_counts.get('partial', 0)
            
            correct_pct = (correct / total) * 100
            incorrect_pct = (incorrect / total) * 100
            partial_pct = (partial / total) * 100
        else:
            correct = incorrect = partial = 0
            correct_pct = incorrect_pct = partial_pct = 0
        
        context = get_base_context(request)
        context.update({
            "results": results,
            "total": total,
            "correct": correct,
            "incorrect": incorrect,
            "partial": partial,
            "correct_pct": correct_pct,
            "incorrect_pct": incorrect_pct,
            "partial_pct": partial_pct
        })
        
        return templates.TemplateResponse("evals.html", context)
        
    except Exception as e:
        context = get_base_context(request)
        context.update({
            "error": f"Error loading evaluation results: {str(e)}",
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

# Generate API Key endpoint
@app.post("/generate-api-key", response_class=RedirectResponse, name="generate_api_key")
async def generate_api_key(request: Request, user: User = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    # Generate a secure random API key
    alphabet = string.ascii_letters + string.digits
    api_key = ''.join(secrets.choice(alphabet) for _ in range(32))
    
    # Update the user's API key in the database
    update_user_profile(user.id, api_key=api_key)
    
    return RedirectResponse(
        url=f"/profile?message=API+key+generated+successfully&message_type=success", 
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
                await asyncio.sleep(WEBSOCKET_PING_INTERVAL)  # Send ping every 30 seconds
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
        
        # Check if Responses API is properly configured
        if not responses_config:
            await websocket.send_text("Error: Responses API not properly configured. Please run setup_responses_api.py first.")
            return

        while True:  # Keep connection open for multiple interactions
            try:
                message = await websocket.receive_text()
                
                # Handle ping response
                if message == "__pong__":
                    logging.info("Received pong")
                    continue
                    
                logging.info(f"✅ Received question: {message}")

                # Use the latest Responses API with file search
                logging.info("🟢 Starting Responses API with file search...")
                try:
                    # Verify client has responses attribute
                    if not hasattr(client, 'responses'):
                        raise AttributeError("OpenAI client does not have 'responses' attribute")
                    
                    # Verify responses_config is loaded
                    if not responses_config or 'vector_store_id' not in responses_config:
                        raise ValueError("Responses API configuration not properly loaded")
                    
                    logging.info(f"📊 Using Vector Store: {responses_config['vector_store_id']}")
                    
                    response = client.responses.create(
                        model=DEFAULT_MODEL,
                        input=message,
                        instructions=ASL_SYSTEM_INSTRUCTIONS,
                        temperature=TEMPERATURE,
                        tools=[{
                            "type": "file_search",
                            "vector_store_ids": [responses_config["vector_store_id"]],
                        }]
                    )
                    
                    # Get the response text
                    assistant_response = response.output_text
                    
                    if assistant_response:
                        logging.info(f"📝 Full response: {assistant_response[:100]}...")
                        
                        # Stream the response character by character
                        logging.info("🔄 Streaming response...")
                        for char in assistant_response:
                            await websocket.send_text(char)
                            await asyncio.sleep(STREAMING_DELAY)  # Small delay for streaming effect
                        
                        logging.info("✅ Response streamed successfully")
                    else:
                        logging.warning("⚠️ No response content received")
                        await websocket.send_text("Sorry, I couldn't generate a response. Please try again.")
                        
                except AttributeError as attr_error:
                    logging.error(f"❌ Attribute Error: {attr_error}")
                    await websocket.send_text(f"Error: OpenAI client configuration issue. Please contact support.")
                except ValueError as val_error:
                    logging.error(f"❌ Configuration Error: {val_error}")
                    await websocket.send_text(f"Error: Responses API not properly configured. Please contact support.")
                except Exception as api_error:
                    logging.error(f"❌ API Error: {api_error}")
                    await websocket.send_text(f"Error: {str(api_error)}")

                logging.info("✅ Finished processing response. Waiting for the next message...")
                
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
        logging.info("🔻 WebSocket connection resources cleaned up.")

# Logout route: removes access token and redirects to login
@app.get("/logout", name="logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Admin dashboard
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user: User = Depends(get_admin_user), db=Depends(get_db)):
    users = db.query(User).all()
    invitations = db.query(Invitation).filter(
        Invitation.expires_at > datetime.utcnow()
    ).order_by(Invitation.created_at.desc()).all()
    context = get_base_context(request, user)
    context.update({
        "users": users,
        "invitations": invitations,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("message_type", "info")
    })
    return templates.TemplateResponse("admin.html", context)

# Admin API endpoints
@app.get("/api/admin/view-api-key/{email}")
async def admin_view_api_key(
    email: str,
    user: User = Depends(get_admin_user)
):
    target_user = db.query(User).filter(User.email == email).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"email": email, "api_key": target_user.api_key}

@app.get("/api/admin/generate-api-key/{email}")
async def admin_generate_api_key(
    email: str,
    user: User = Depends(get_admin_user)
):
    target_user = db.query(User).filter(User.email == email).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Generate a secure random API key
    alphabet = string.ascii_letters + string.digits
    api_key = ''.join(secrets.choice(alphabet) for _ in range(32))
    
    # Update the user's API key
    update_user_profile(target_user.id, api_key=api_key)
    
    return {"email": email, "api_key": api_key}

@app.post("/api/invite")
async def send_invitation(
    data: dict = Body(...),
    user: User = Depends(get_admin_user),
    db=Depends(get_db),
    background_tasks: BackgroundTasks = None
):
    email = data.get("email")
    if not email:
        return {"detail": "Email is required"}
    # Generate a unique invitation code
    code = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    # Check for existing invitation
    existing = db.query(Invitation).filter(Invitation.email == email, Invitation.expires_at > datetime.utcnow()).first()
    if existing:
        return {"detail": "Active invitation already exists for this email"}
    invitation = Invitation(
        code=code,
        email=email,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=7)
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    # Send the invitation email in the background
    invite_link = f"https://kevmo.us/register?code={code}"
    subject = "Invitation to Rule Chat"
    body = f"Dr {email}<br>You are invited to try the experimental Advanced Squad Leader Rules chat at <a href='{invite_link}'>{invite_link}</a>.<br><br>Regards,<br>Kevin"
    if background_tasks is not None:
        background_tasks.add_task(send_email, subject, email, body)
    else:
        await send_email(subject, email, body)

    return {"detail": "Invitation sent", "code": code}

@app.post("/api/invite/resend/{invitation_id}")
async def resend_invitation(
    invitation_id: int,
    user: User = Depends(get_admin_user),
    db=Depends(get_db),
    background_tasks: BackgroundTasks = None
):
    invitation = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not invitation:
        return {"detail": "Invitation not found"}
    if invitation.expires_at < datetime.utcnow():
        return {"detail": "Invitation expired"}
    invite_link = f"https://kevmo.us/register?code={invitation.code}"
    subject = "Invitation to Rule Chat"
    body = f"Dr {invitation.email}<br>You are invited to try the experimental Advanced Squad Leader Rules chat at <a href='{invite_link}'>{invite_link}</a>.<br><br>Regards,<br>Kevin"
    if background_tasks is not None:
        background_tasks.add_task(send_email, subject, invitation.email, body)
    else:
        await send_email(subject, invitation.email, body)
    return {"detail": "Invitation resent"}

@app.delete("/api/invite/{invitation_id}")
async def delete_invitation(
    invitation_id: int,
    user: User = Depends(get_admin_user),
    db=Depends(get_db)
):
    invitation = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not invitation:
        return {"detail": "Invitation not found"}
    db.delete(invitation)
    db.commit()
    return {"detail": "Invitation deleted"}

@app.delete("/api/admin/user/{user_id}")
async def delete_user(user_id: int, user: User = Depends(get_admin_user), db=Depends(get_db)):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        return {"detail": "User not found"}
    db.delete(target_user)
    db.commit()
    return {"detail": "User deleted"}

@app.post("/admin/create-test-user", name="admin_create_test_user")
async def admin_create_test_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    user: User = Depends(get_admin_user),
    db=Depends(get_db)
):
    # Check if user already exists
    if get_user_by_username(email):
        return RedirectResponse(
            url="/admin?message=User+already+exists&message_type=danger",
            status_code=303
        )
    # Hash password and create user
    hashed_password = get_password_hash(password)
    new_user = User(email=email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    return RedirectResponse(
        url="/admin?message=Test+user+created+successfully&message_type=success",
        status_code=303
    )

conf = ConnectionConfig(
    MAIL_USERNAME = os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD"),
    MAIL_FROM = os.getenv("MAIL_FROM"),
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587)),
    MAIL_SERVER = os.getenv("MAIL_SERVER"),
    MAIL_STARTTLS = os.getenv("MAIL_STARTTLS", "True") == "True",
    MAIL_SSL_TLS = os.getenv("MAIL_SSL_TLS", "False") == "True",
    USE_CREDENTIALS = True
)

async def send_email(subject: str, email_to: EmailStr, body: str):
    message = MessageSchema(
        subject=subject,
        recipients=[email_to],
        body=body,
        subtype="html"
    )
    fm = FastMail(conf)
    await fm.send_message(message)

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, code: str, db=Depends(get_db)):
    invitation = db.query(Invitation).filter(Invitation.code == code).first()
    if not invitation or invitation.expires_at < datetime.utcnow():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Invalid or expired invitation.", "code": "", "email": ""})
    if invitation.used:
        return templates.TemplateResponse("register.html", {"request": request, "error": "This invitation has already been used.", "code": code, "email": invitation.email})
    return templates.TemplateResponse("register.html", {"request": request, "code": code, "email": invitation.email})

@app.post("/register/complete", response_class=HTMLResponse)
async def register_complete(request: Request, code: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    invitation = db.query(Invitation).filter(Invitation.code == code).first()
    if not invitation or invitation.expires_at < datetime.utcnow() or invitation.used:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Invalid or expired invitation.", "code": code, "email": invitation.email if invitation else ""})
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == invitation.email).first()
    if existing_user:
        return templates.TemplateResponse("register.html", {"request": request, "error": "User already exists for this email.", "code": code, "email": invitation.email})
    # Create user with email only
    hashed_password = get_password_hash(password)
    user = User(email=invitation.email, hashed_password=hashed_password)
    db.add(user)
    # Mark invitation as used
    invitation.used_at = datetime.utcnow()
    invitation.used_by_user_id = user.id
    db.commit()
    return templates.TemplateResponse("register_success.html", {"request": request})
