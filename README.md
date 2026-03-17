# ASL Rules Assistant

A personal experiment: can a RAG-based AI reliably answer rules questions for a tabletop wargame with a 1M+ token rulebook?

This is a FastAPI web application that lets players ask Advanced Squad Leader (ASL) rules questions and get answers with cited rule section numbers. Built on OpenAI's **Responses API** with native RAG via `file_search`. Accuracy varies — results are mixed and improving.

> **Personal use only.** The ASL rulebook is copyrighted material (Avalon Hill / Multi-Man Publishing). This project does not distribute the rulebook and is not affiliated with or endorsed by the publishers.

## Features

- **RAG pipeline** — OpenAI Responses API with `file_search` against a vector store of the rulebook (up to 20 chunks per query)
- **Streaming WebSocket** — responses stream token-by-token; TTFT, cost, and token counts surfaced per query
- **In-browser PDF viewer** — rule citations (e.g. A4.34) open the rulebook PDF at the exact page
- **Model selector** — switch between models in-chat (tested with gpt-4.1-mini and gpt-5-mini)
- **Automated evals** — zero-shot AI Judge scores responses Pass/Fail/Needs Review; results manually reviewed
- **Demo mode** — unauthenticated users get 5 questions/day
- **Mobile-optimized** — responsive layout tested at 375px, 768px, 1280px viewports

## Architecture

```
Browser ──WebSocket──▶ FastAPI ──▶ OpenAI Responses API
                                        │
                                   file_search
                                        │
                                   Vector Store
                                   (rulebook chunks)
```

**Key files:**
- [app/asl/client.py](app/asl/client.py) — Responses API wrapper
- [app/services/asl_service.py](app/services/asl_service.py) — main assistant service
- [app/api/chat.py](app/api/chat.py) — WebSocket handler
- [app/config.py](app/config.py) — system instructions

## Project Structure

```
├── app/
│   ├── api/           # FastAPI routes (auth, chat, demo, evals)
│   ├── asl/           # Responses API wrapper, config, tools
│   ├── core/          # JWT auth, password hashing
│   ├── models/        # SQLAlchemy models
│   ├── services/      # Business logic (ASL assistant, chat history)
│   ├── config.py      # App config and system instructions
│   └── main.py        # FastAPI app
├── deployment/        # nginx config, systemd service, env.example
├── scripts/           # DB init, user management
├── static/            # CSS, JS, images
├── templates/         # Jinja2 HTML templates
├── tests/             # Playwright mobile tests, manual API tests
└── run.py             # Dev server runner
```

## Setup

### 1. Install dependencies

```bash
# Conda (recommended)
conda env create -f environment.yml
conda activate mysite2_env

# Or pip
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp deployment/env.example .env
# Edit .env with your values
```

Required variables:

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing key (generate with `python scripts/generate_key.py`) |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_ORG_ID` | OpenAI organization ID |
| `OPENAI_PROJECT_ID` | OpenAI project ID |
| `ADMIN_EMAIL` | Admin account email |
| `DEFAULT_MODEL` | Default model (e.g. `gpt-4.1-mini`) |

### 3. Set up the vector store

You need to upload the ASL rulebook PDF to OpenAI and create a vector store. See [RESPONSES_API_README.md](RESPONSES_API_README.md) for details.

```bash
# After uploading, copy the example config and fill in your IDs
cp responses_api_config.example.json responses_api_config.json
# Edit responses_api_config.json with your vector_store_id and file_id
```

### 4. Initialize the database

```bash
python scripts/init_db.py
# Prompts for admin email and password (or set ADMIN_EMAIL / ADMIN_PASSWORD env vars)
```

### 5. Run

```bash
# Development
python run.py

# Or directly
uvicorn app.main:app --reload
```

The app will be at `http://localhost:8000`. The demo page is public; full chat requires login.

## Deployment

See [deployment/QUICKSTART.md](deployment/QUICKSTART.md) for production setup (nginx, systemd, SSL).

## Tests

```bash
# Mobile/responsive UI tests (requires Playwright + a running server)
pip install playwright && python -m playwright install chromium
python test_mobile.py
```

## Evaluation

Eval results are stored in `data/evals/` as JSON. The evals page (`/evals`) reads from this directory. See [RESPONSES_API_README.md](RESPONSES_API_README.md) for how evals are run.

## Related

- [mysite2-evals-sft](https://github.com/kehannan/mysite2-evals-sft) — evaluation datasets and fine-tuning data

## License

MIT — see [LICENSE](LICENSE).

Advanced Squad Leader is a trademark of Avalon Hill Games, Inc. This project is not affiliated with or endorsed by Hasbro, Avalon Hill Games, Inc., or Multi-Man Publishing, Inc.
