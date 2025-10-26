# ASL Rules Assistant

A FastAPI web application that helps Advanced Squad Leader (ASL) players understand and apply the complex rules of the game using AI assistance powered by OpenAI.

## Features

- **AI-Powered Rules Assistant**: Ask questions about ASL rules and get accurate answers
- **Vector Store Integration**: Searches through the official ASL rulebook
- **User Authentication**: Secure login and user management
- **Invitation System**: Admin-managed user invitations
- **WebSocket Chat**: Real-time streaming responses
- **Feedback System**: Users can provide feedback on answers

## Related Repositories

- **[mysite2-evals-sft](https://github.com/kehannan/mysite2-evals-sft)**: Evaluation datasets, fine-tuning data, and data processing scripts

## Project Structure

```
mysite2/
├── app/                          # Main application package
│   ├── models/                   # Database models
│   ├── api/                      # API routes/routers
│   │   ├── auth.py              # Authentication routes
│   │   ├── user.py              # User profile routes
│   │   └── chat.py              # Chat and WebSocket routes
│   ├── core/                     # Core utilities
│   │   ├── auth.py              # JWT and password hashing
│   │   └── responses_api.py     # OpenAI integration
│   ├── services/                 # Business logic
│   │   └── user_service.py      # User operations
│   ├── database.py              # Database configuration
│   ├── config.py                # Application configuration
│   └── main.py                  # FastAPI application
├── scripts/                      # Admin/utility scripts
│   ├── create_user.py
│   ├── init_db.py
│   └── ...
├── static/                       # Static files (CSS, images)
├── templates/                    # HTML templates
├── tests/manual/                 # Manual test scripts
└── run.py                       # Application runner
```

## Setup

## Environment Variables

The application relies on the following environment variables:

- `SECRET_KEY` – secret key used for signing JWT tokens.
- `OPENAI_API_KEY` – API key for communicating with OpenAI.
- `ADMIN_USERNAME` – optional username for the site administrator.

Create a `.env` file in the project root and set these values.

## Installation

Install the required Python packages. You can either install them individually:

```bash
pip install fastapi uvicorn sqlalchemy passlib[bcrypt] python-jose python-dotenv openai
```

or install everything from the requirements file:

```bash
pip install -r requirements.txt
```

## Database Setup

Initialize the database (tables are created automatically on first run, but you can also run):

```bash
python scripts/init_db.py
```

## Create the Admin User

Create the initial admin account:

```bash
python scripts/create_user.py
```

## Running the Server

Start the FastAPI development server:

```bash
# Option 1: Using the run script (recommended)
python run.py

# Option 2: Using uvicorn directly
uvicorn app.main:app --reload

# Option 3: For production
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The server will be available at `http://localhost:8000`

