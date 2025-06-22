# ASL Rules Assistant - Architecture Diagram

## System Overview
```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              INTERNET/USERS                                     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              NGINX (Reverse Proxy)                              │
│                              Port 80/443 (HTTPS)                                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              UVICORN (ASGI Server)                              │
│                              Port 8000                                          │
│                              ┌─────────────────┐                                │
│                              │  FastAPI App    │                                │
│                              │   (main.py)     │                                │
│                              └─────────────────┘                                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
┌─────────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐
│     HTTP ROUTES         │ │    WEBSOCKET CHAT       │ │    STATIC FILES         │
│                         │ │                         │ │                         │
│ ┌─────────────────────┐ │ │ ┌─────────────────────┐ │ │ ┌─────────────────────┐ │
│ │ / (redirect)        │ │ │ │ /ws/chat/           │ │ │ │ CSS, JS, Images     │ │
│ │ /home               │ │ │ │ WebSocket Handler   │ │ │ │ Static Assets       │ │
│ │ /login              │ │ │ │                     │ │ │ │                     │ │
│ │ /ruleschat          │ │ │ │ ┌─────────────────┐ │ │ │ └─────────────────────┘ │
│ │ /profile            │ │ │ │ │ Thread Creation │ │ │                         │
│ │ /admin              │ │ │ │ └─────────────────┘ │ │ └─────────────────────────┘
│ │ /evals              │ │ │ │         │           │ │
│ │ /register           │ │ │ │         ▼           │ │
│ └─────────────────────┘ │ │ │ ┌─────────────────┐ │ │
│                         │ │ │ │ Message Handling│ │ │
└─────────────────────────┘ │ │ └─────────────────┘ │ │
                            │ │         │           │ │
                            │ │         ▼           │ │
                            │ │ ┌─────────────────┐ │ │
                            │ │ │ OpenAI API      │ │ │
                            │ │ │ Assistant v1    │ │ │
                            │ │ └─────────────────┘ │ │
                            │ │         │           │ │
                            │ │         ▼           │ │
                            │ │ ┌─────────────────┐ │ │
                            │ │ │ Response        │ │ │
                            │ │ │ Streaming       │ │ │
                            │ │ └─────────────────┘ │ │
                            │ └─────────────────────┘ │
                            └─────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
┌─────────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐
│    TEMPLATES (Jinja2)   │ │    DATABASE (SQLite)    │ │    EXTERNAL APIs        │
│                         │ │                         │ │                         │
│ ┌─────────────────────┐ │ │ ┌─────────────────────┐ │ │ ┌─────────────────────┐ │
│ │ base.html           │ │ │ │ users table         │ │ │ │ OpenAI Assistant    │ │
│ │ home.html           │ │ │ │ - id                │ │ │ │ API v1              │ │
│ │ login.html          │ │ │ │ - email             │ │ │ │ - Threads           │ │
│ │ ruleschat.html      │ │ │ │ - hashed_password   │ │ │ │ - Messages          │ │
│ │ profile.html        │ │ │ │ - api_key           │ │ │ │ - Runs              │ │
│ │ admin.html          │ │ │ └─────────────────────┘ │ │ └─────────────────────┘ │
│ │ evals.html          │ │ │ ┌─────────────────────┐ │ │ ┌─────────────────────┐ │
│ │ register.html       │ │ │ │ invitations table   │ │ │ │ SMTP Email Service  │ │
│ └─────────────────────┘ │ │ │ - id                │ │ │ │ - Gmail SMTP        │ │
│                         │ │ │ - code              │ │ │ │ - Invitations       │ │
└─────────────────────────┘ │ │ - email             │ │ │ └─────────────────────┘ │
                            │ │ - created_at        │ │ │                         │
                            │ │ - expires_at        │ │ └─────────────────────────┘
                            │ │ - used_at           │ │
                            │ │ - used_by_user_id   │ │
                            │ └─────────────────────┘ │
                            └─────────────────────────┘
```

## Detailed Data Flow

### 1. Authentication Flow
```
User → Login Form → FastAPI → Database Check → JWT Token → Cookie → Protected Routes
```

### 2. Chat Flow
```
User Input → WebSocket → FastAPI → OpenAI Thread → Assistant API → Response → WebSocket → User
```

### 3. Admin Flow
```
Admin → Admin Panel → FastAPI → Database → User Management → Email Invitations
```

## Component Details

### Frontend (Browser)
- **HTML Templates**: Jinja2-rendered pages
- **WebSocket Client**: Real-time chat interface
- **Static Assets**: CSS, JavaScript, images

### Backend (FastAPI)
- **Route Handlers**: HTTP endpoints for pages and APIs
- **WebSocket Handler**: Real-time chat functionality
- **Authentication**: JWT-based user sessions
- **Database**: SQLAlchemy ORM with SQLite

### External Services
- **OpenAI Assistant API**: AI chat functionality
- **SMTP Email**: User invitation system

### Infrastructure
- **Uvicorn**: ASGI server for async handling
- **Nginx**: Reverse proxy and SSL termination
- **SQLite**: Local database storage

## Security Layers
```
┌─────────────────────────────────────────────────────────────────┐
│                        SECURITY LAYERS                         │
├─────────────────────────────────────────────────────────────────┤
│ 1. HTTPS/SSL (Nginx)                                           │
│ 2. JWT Authentication (FastAPI)                                │
│ 3. Password Hashing (bcrypt)                                   │
│ 4. Admin Middleware (Role-based access)                        │
│ 5. Invitation System (Controlled registration)                 │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure
```
mysite2/
├── main.py              # FastAPI application
├── models.py            # Database models
├── auth.py              # Authentication utilities
├── assistant.py         # OpenAI integration
├── templates/           # HTML templates
│   ├── base.html
│   ├── ruleschat.html
│   └── ...
├── static/              # Static assets
├── mysite.db           # SQLite database
└── requirements.txt    # Python dependencies
```

## Check Your Server Services

```bash
# Check what services are running
systemctl list-units --type=service --state=running

# Check nginx status
systemctl status nginx

# Check if nginx is enabled to start on boot
systemctl is-enabled nginx

# Check what's listening on ports 80 and 443
netstat -tlnp | grep :80
netstat -tlnp | grep :443

# Check what's listening on port 8000
netstat -tlnp | grep :8000

# Check nginx configuration
nginx -t

# View nginx logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

## Check Your FastAPI App

```bash
# Check if your app is running
ps aux | grep uvicorn

# Check if there are any Python processes
ps aux | grep python

# Check your app directory
ls -la /root/mysite2  # or wherever your app is located
```

## Check Firewall Rules

```bash
# Check UFW status (if using UFW)
ufw status

# Check iptables rules
iptables -L
```

Once you run these commands on your server, share the output with me and I can help you understand:

1. **How nginx is configured**
2. **Whether your FastAPI app is running**
3. **What ports are open/closed**
4. **Any issues with your current setup**

Just copy and paste the output from any of these commands, and I'll help you analyze your server configuration! 