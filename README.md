# ASL Rules Assistant

A FastAPI web application that helps Advanced Squad Leader (ASL) players understand and apply the complex rules of the game using AI assistance powered by OpenAI's **Responses API**.

## Features

- **AI-Powered Rules Assistant**: Ask questions about ASL rules and get accurate, concise answers with rule section references
- **Vector Store Integration**: Searches through the official ASL rulebook via OpenAI file_search (RAG with 20 chunks)
- **Model Selection**: Users can switch between gpt-5-mini (more accurate) and gpt-4.1-mini (faster) via in-chat dropdown
- **User Authentication**: Secure login with JWT and admin-managed invitations
- **WebSocket Chat**: Real-time streaming responses with conversation history
- **Conversation History**: Persistent chat history with conversation management
- **Feedback System**: Users can provide feedback on answers
- **Per-Query Cost Display**: Shows estimated cost for each response

## API Architecture

This application uses OpenAI's **Responses API** (not Chat Completions API) for all AI inference:

- **Native RAG**: Built-in `file_search` tool queries the vector store (20 chunks per query)
- **Streaming**: Real-time response streaming via WebSocket
- **Model Whitelist**: Backend validates model selection against allowed models

**Key Implementation Files**:
- [app/asl/client.py](app/asl/client.py) — Responses API wrapper
- [app/services/asl_service.py](app/services/asl_service.py) — Main ASL service
- [app/api/chat.py](app/api/chat.py) — WebSocket handler with model selection and conversation history
- [app/config.py](app/config.py) — System instructions (concise Answer + References format)

## Related Repositories

- **[mysite2-evals-sft](https://github.com/kehannan/mysite2-evals-sft)**: Evaluation datasets, fine-tuning data, and eval tooling

## Documentation

- **[PRODUCTION.md](PRODUCTION.md)** — Production environment guide
- **[deployment/QUICKSTART.md](deployment/QUICKSTART.md)** — Quick deployment reference
- **[ASL_CHAT_FLOW.md](ASL_CHAT_FLOW.md)** — Question-to-answer data flow
- **[RESPONSES_API_README.md](RESPONSES_API_README.md)** — Responses API and vector store setup
- **[TESTING.md](TESTING.md)** — Testing guide

## Project Structure

```
mysite2/
├── app/                          # Main application package
│   ├── models/                   # Database models
│   ├── api/                      # API routes/routers
│   │   ├── auth.py              # Authentication routes
│   │   ├── user.py              # User profile routes
│   │   └── chat.py              # Chat, WebSocket, conversation history
│   ├── asl/                      # ASL-specific modules (Responses API)
│   │   ├── client.py            # OpenAI Responses API wrapper
│   │   ├── config.py            # ASL configuration
│   │   ├── policy.py            # Instruction building
│   │   ├── postprocess.py       # Response processing utilities
│   │   └── tools.py             # Custom function tools (unused in production)
│   ├── core/                     # Core utilities
│   │   └── auth.py              # JWT and password hashing
│   ├── services/                 # Business logic
│   │   ├── asl_service.py       # Main ASL assistant service
│   │   ├── user_service.py      # User operations
│   │   └── chat_history_service.py  # Conversation persistence
│   ├── database.py              # Database configuration
│   ├── config.py                # App config and system instructions
│   └── main.py                  # FastAPI application
├── deployment/                   # Production deployment configs
│   └── nginx.conf               # Nginx reverse proxy configuration
├── scripts/                      # Admin/utility scripts
│   ├── create_user.py
│   ├── init_db.py
│   └── setup_responses_api.py   # Vector store setup
├── static/                       # Static files (CSS, images)
├── templates/                    # HTML templates
├── tests/manual/                 # Manual test scripts
├── responses_api_config.json    # Vector store config (versioned)
└── run.py                       # Application runner
```

## Setup

### Using Conda (Recommended)

```bash
conda env create -f environment.yml
conda activate mysite2_env
```

### Using pip

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root:

- `SECRET_KEY` — JWT signing key
- `OPENAI_API_KEY` — OpenAI API key
- `OPENAI_ORG_ID` — OpenAI organization ID
- `OPENAI_PROJECT_ID` — OpenAI project ID
- `DEFAULT_MODEL` — default model (e.g. `gpt-5-mini`)
- `ADMIN_EMAIL` — admin email
- `TEMPERATURE` — optional, defaults to 0.2
- `DATABASE_URL` — optional, defaults to `sqlite:///./mysite.db`
- `RAG_MAX_CHUNKS` — optional, defaults to 20
- `COST_PER_1M_INPUT` — optional, for cost display
- `COST_PER_1M_OUTPUT` — optional, for cost display

## Running

```bash
# Development
python run.py

# Or directly
uvicorn app.main:app --reload

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The server will be available at `http://localhost:8000`
