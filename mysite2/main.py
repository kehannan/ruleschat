from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from routers import invite
from models import Base, User, Invitation
from database import engine, get_db
from datetime import datetime
import secrets

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(invite.router, prefix="/api", tags=["invitations"])

@app.get("/", response_class=HTMLResponse)
async def root():
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>MyAPI Home</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                h1 {
                    color: #333;
                }
                .container {
                    background-color: #f9f9f9;
                    border-radius: 5px;
                    padding: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Welcome to MyAPI</h1>
                <p>This is the homepage of our API service. Here are some useful links:</p>
                <ul>
                    <li><a href="/docs">API Documentation</a></li>
                    <li><a href="/api/invite">Invitation API</a></li>
                </ul>
            </div>
        </body>
    </html>
    """
    return html_content

@app.get("/register", response_class=HTMLResponse)
async def register_form(token: str, db: Session = Depends(get_db)):
    # Validate token
    invitation = db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.used == False,
        Invitation.expires_at > datetime.utcnow()
    ).first()
    
    if not invitation:
        return """
        <!DOCTYPE html>
        <html>
            <head>
                <title>Invalid Invitation</title>
                <style>
                    body {
                        font-family: Arial, sans-serif;
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 20px;
                    }
                    .error {
                        color: #d93025;
                        background-color: #fce8e6;
                        padding: 15px;
                        border-radius: 5px;
                    }
                </style>
            </head>
            <body>
                <div class="error">
                    <h2>Invalid or Expired Invitation</h2>
                    <p>The invitation link you used is invalid or has expired.</p>
                </div>
            </body>
        </html>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Complete Registration</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .form-group {{
                    margin-bottom: 15px;
                }}
                label {{
                    display: block;
                    margin-bottom: 5px;
                    font-weight: bold;
                }}
                input[type="password"] {{
                    width: 100%;
                    padding: 8px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    box-sizing: border-box;
                }}
                button {{
                    background-color: #4285f4;
                    color: white;
                    border: none;
                    padding: 10px 15px;
                    border-radius: 4px;
                    cursor: pointer;
                }}
                button:hover {{
                    background-color: #3367d6;
                }}
                .card {{
                    background-color: #f9f9f9;
                    border-radius: 8px;
                    padding: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Complete Your Registration</h2>
                <p>You've been invited to join our platform with the email: <strong>{invitation.email}</strong></p>
                
                <form action="/register/complete" method="post">
                    <input type="hidden" name="token" value="{token}">
                    
                    <div class="form-group">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="confirm_password">Confirm Password</label>
                        <input type="password" id="confirm_password" name="confirm_password" required>
                    </div>
                    
                    <button type="submit">Complete Registration</button>
                </form>
            </div>
            
            <script>
                // Simple password validation
                document.querySelector('form').addEventListener('submit', function(e) {{
                    const password = document.getElementById('password').value;
                    const confirmPassword = document.getElementById('confirm_password').value;
                    
                    if (password !== confirmPassword) {{
                        e.preventDefault();
                        alert('Passwords do not match!');
                    }}
                    
                    if (password.length < 8) {{
                        e.preventDefault();
                        alert('Password must be at least 8 characters long.');
                    }}
                }});
            </script>
        </body>
    </html>
    """

@app.post("/register/complete")
async def complete_registration(
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Find and validate the invitation
    invitation = db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.used == False,
        Invitation.expires_at > datetime.utcnow()
    ).first()
    
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")
    
    # Create new user
    new_user = User(
        email=invitation.email,
        password=password  # In production, hash this password!
    )
    db.add(new_user)
    db.flush()
    
    # Mark invitation as used
    invitation.used = True
    invitation.used_by = new_user.id
    
    db.commit()
    
    # Redirect to success page
    return RedirectResponse(url="/register/success", status_code=303)

@app.get("/register/success", response_class=HTMLResponse)
async def registration_success():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Registration Successful</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }
                .success {
                    color: #0f5132;
                    background-color: #d1e7dd;
                    padding: 15px;
                    border-radius: 5px;
                }
                .btn {
                    display: inline-block;
                    margin-top: 15px;
                    background-color: #4285f4;
                    color: white;
                    border: none;
                    padding: 10px 15px;
                    border-radius: 4px;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #3367d6;
                }
            </style>
        </head>
        <body>
            <div class="success">
                <h2>Registration Successful!</h2>
                <p>Your account has been created successfully.</p>
                <a href="/" class="btn">Go to Home</a>
            </div>
        </body>
    </html>
    """ 