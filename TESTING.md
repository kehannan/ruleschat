# Testing Guide for ASL Rules Assistant

## Quick Start

### 1. Smoke Test

```bash
python run.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 2. Check Routes

```
Home page:       http://localhost:8000/
Login page:      http://localhost:8000/login
Chat page:       http://localhost:8000/ruleschat (requires login)
Profile:         http://localhost:8000/profile (requires login)
API docs:        http://localhost:8000/docs
```

## Manual Testing

### Authentication
1. Create a test user: `python scripts/create_user.py`
2. Login at `/login` → should redirect to `/ruleschat`
3. Test profile at `/profile`
4. Logout → verify protected pages redirect to login

### Chat
1. Login and go to `/ruleschat`
2. Type a question — response should stream in real-time
3. Switch model via dropdown (gpt-5-mini / gpt-4.1-mini)
4. Verify cost estimate updates per model
5. Check collapsible "Sources Used" section appears after response
6. Test conversation history — create new, switch between conversations

### Server Logs
```bash
# Expected log output during chat:
INFO: 🔹 WebSocket connection established for user: ...
INFO: ✅ Received question: ...
INFO: 📊 Using Vector Store: vs_...
INFO: 🔄 Streaming response from OpenAI...
INFO: ✅ Response streamed - N deltas in Xms
INFO: 💾 Saved messages to conversation N
```

## Component Tests

```bash
# Test app loads
python -c "from app.main import app; print('App loads successfully')"

# Test models
python -c "from app.models import User, Invitation; print('Models work')"

# Test database
python -c "from app.database import engine, SessionLocal; print('Database works')"

# Test auth
python -c "from app.core.auth import get_password_hash, verify_password; print('Auth works')"

# Test Responses API config
python test_responses_api.py
```

## API Testing

```bash
# Test login
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your-email@example.com&password=yourpassword"

# Test conversations API (requires auth cookie)
curl http://localhost:8000/api/conversations -b cookies.txt
```

## Database

```bash
sqlite3 mysite.db
.tables
SELECT email FROM users;
.quit
```

## Common Issues

| Issue | Solution |
|-------|----------|
| Module not found | Run from project root (`cd` into the repo) |
| Database errors | `python scripts/init_db.py` |
| OpenAI API errors | Check `.env` has valid `OPENAI_API_KEY`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` |
| WebSocket fails | Check `responses_api_config.json` exists with valid `vector_store_id` |
| Static files missing | Verify `static/` and `templates/` directories exist |

## Evaluations

Run evals from the `mysite2-evals-sft` repo:

```bash
cd ../mysite2-evals-sft
conda activate mysite2-evals-sft-env
python evals/src/scripts/asl_evals.py --model gpt-5-mini --file asl-evals-section-a-closed.jsonl
```

See `mysite2-evals-sft/evals/EVAL_CREATION_GUIDE.md` for eval details.
