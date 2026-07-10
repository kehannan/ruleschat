# ASL Rules Assistant

A personal experiment: can a RAG-based AI reliably answer rules questions for a tabletop wargame with a 1M+ token rulebook?

It's a FastAPI web application that lets players ask Advanced Squad Leader (ASL) rules questions and get answers with cited rule section numbers. It started as plain RAG on OpenAI's **Responses API** (`file_search`), and has grown an agentic layer on top: deterministic rules calculators, exact-text section lookup, and a code-driven cite-check pass that grounds every cited section in its actual rulebook text before the answer is finalized.

> **Personal use only.** The ASL rulebook is copyrighted material (Avalon Hill / Multi-Man Publishing). This project does not distribute the rulebook and is not affiliated with or endorsed by the publishers.

## Features

- **RAG pipeline** — OpenAI Responses API with `file_search` against two vector stores (the rulebook and an ASL Q&A/errata compilation), up to 20 chunks per query
- **Agentic retrieval** — the model can call `get_section` (deterministic exact-text lookup over a locally extracted section store, ~2,800 sections) and `search_rules` (multi-hop vector search) mid-answer instead of relying on a single retrieval shot
- **Deterministic cite-check** — after the model drafts an answer, *code* (not the model) extracts the cited section IDs, fetches their exact text plus cross-references and official Q&A/errata, and forces one grounded revision turn. Reliability doesn't depend on the model choosing to use tools. See [docs/agentic_retrieval_plan.md](docs/agentic_retrieval_plan.md)
- **Thorough mode** — per-chat toggle: ON runs a small adaptive chunk baseline plus the agentic loop (slower, more accurate); OFF is the classic single-shot 20-chunk prompt
- **Deterministic calculators** — Infantry Fire Table odds and full attack resolution per A7.2–.36, and Close Combat odds/kill numbers per A11, exposed both as a standalone `/ift` page and as agentic tools (`ift_odds`, `ift_attack`, `resolve_attack`, `resolve_cc`, `cc_attack`) so the arithmetic is never hand-derived by the model
- **VASL save-file (.vsav) support** — attach a VASL save; it's parsed into normalized board state (units, hexes, stacking, broken/concealed/HIP flags, SSR terrain transforms), rendered in a visual board viewer, and given to the model as ground truth. CC questions about a hex resolve directly from the parsed save
- **Image attachment (multimodal)** — paste up to 3 VASL board screenshots into the chat; a fixed terrain legend is sent alongside so the model visually matches board hexes. Image queries auto-route to `gpt-5.4`. See [docs/multimodal_plan.md](docs/multimodal_plan.md)
- **Model selector** — one registry table ([app/model_registry.py](app/model_registry.py)) drives the model dropdowns, per-model tool gating, and cost chips; supports OpenAI-native models plus routed providers (Meta Model API, OpenRouter)
- **Streaming WebSocket** — responses stream token-by-token with a live status pill showing agentic tool calls; TTFT, cost, and token counts surfaced per query
- **In-browser PDF viewer** — rule citations (e.g. A4.34) open the rulebook PDF at the exact page
- **Automated evals** — AI Judge scores responses Pass/Fail/Needs Review, results manually reviewed and browsable at `/evals` with per-model cost/timing
- **Demo mode** — unauthenticated users get 5 questions/day
- **Mobile-optimized** — responsive layout tested at 375px, 768px, 1280px viewports

## Architecture

```
Browser ──WebSocket──▶ FastAPI ──▶ Model (OpenAI / Meta / OpenRouter)
                          │             │ tool calls
                          │        ┌────┴─────────────────────────┐
                          │        │ file_search (vector stores)  │
                          │        │ get_section / search_rules   │
                          │        │ IFT & CC calculators         │
                          │        └──────────────────────────────┘
                          │
                    cite-check: code extracts cited section IDs from the
                    draft, fetches their exact text, forces one grounded
                    revision turn
```

**Key files:**
- [app/services/asl_service.py](app/services/asl_service.py) — main assistant service; agentic loops for OpenAI (streaming + non-streaming) and OpenRouter, multimodal input build, vsav board-state injection
- [app/asl/tools.py](app/asl/tools.py) — agentic tool functions + schemas (Responses API format; Chat Completions format auto-derived), `execute_tool()` dispatcher
- [app/asl/rules_lookup.py](app/asl/rules_lookup.py) — deterministic section lookup over the extracted rulebook + Q&A stores (backs `get_section` and cite-check)
- [app/asl/cite_check.py](app/asl/cite_check.py) — extract cited section IDs from a draft, assemble exact-text grounding context
- [app/asl/ift.py](app/asl/ift.py), [app/asl/attack_resolver.py](app/asl/attack_resolver.py), [app/asl/cc_resolver.py](app/asl/cc_resolver.py) — deterministic IFT / attack / Close Combat engines
- [app/services/vsav_service.py](app/services/vsav_service.py) — parse VASL .vsav saves into normalized board state
- [app/model_registry.py](app/model_registry.py) — the one table that drives model dropdowns, tool gating, and pricing
- [app/api/chat.py](app/api/chat.py) / [app/api/demo.py](app/api/demo.py) — authed and public-demo WebSocket handlers
- [app/config.py](app/config.py) — app config and system instructions
- [static/js/chat-shared.js](static/js/chat-shared.js) — chat client: streaming, status pill, image paste, PDF citation links

## Project Structure

```
├── app/
│   ├── api/           # FastAPI routes (auth, chat, demo, evals, ift, board viewer)
│   ├── asl/           # Model clients, agentic tools, calculators, cite-check, retrieval
│   ├── core/          # JWT auth, password hashing
│   ├── models/        # SQLAlchemy models
│   ├── services/      # Business logic (ASL assistant, chat history, vsav parsing, board render)
│   ├── config.py      # App config and system instructions
│   ├── model_registry.py  # Model table (dropdowns, tool gating, pricing)
│   └── main.py        # FastAPI app
├── deployment/        # nginx config, systemd service, env.example
├── scripts/           # DB init, user management, vector-store setup, rulebook extraction
├── static/            # CSS, JS, images
├── templates/         # Jinja2 HTML templates
├── tests/             # pytest suite (calculators, cite-check, vsav parsing) + Playwright mobile tests
├── data/uploads/      # User-uploaded images (gitignored)
├── data/rulebook/     # Extracted section/Q&A stores (gitignored — copyrighted text)
├── docs/              # Design docs (agentic retrieval, multimodal, IFT attack tool)
└── run.py             # Dev server runner
```

## Setup

### 1. Install dependencies

```bash
# Conda (recommended)
conda env create -f environment.yml
conda activate ruleschat-env

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
| `SECRET_KEY` | JWT signing key (generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_ORG_ID` | OpenAI organization ID |
| `OPENAI_PROJECT_ID` | OpenAI project ID |
| `ADMIN_EMAIL` | Admin account email |
| `DEFAULT_MODEL` | Default model (e.g. `gpt-5.4`) |

Optional (only needed for routed models in the registry): `META_API_KEY` (Meta Model API), `OPENROUTER_API_KEY` (OpenRouter). `ADAPTIVE_RAG_CHUNKS` tunes the thorough-mode chunk baseline (default 5).

### 3. Set up the vector stores

You need to upload the ASL rulebook PDF to OpenAI and create a vector store (and, optionally, a second store for the ASL Q&A/errata compilation). See [docs/RESPONSES_API_README.md](docs/RESPONSES_API_README.md) for details.

```bash
# After uploading, copy the example config and fill in your IDs
cp responses_api_config.example.json responses_api_config.json
# Edit responses_api_config.json with your vector_store_id and file_id
```

### 4. Build the local section stores (for `get_section` and cite-check)

The agentic lookup tools and the cite-check pass read locally extracted, gitignored stores. Build them from your own rulebook/Q&A PDFs:

```bash
python scripts/extract_rulebook_sections.py   # -> data/rulebook/sections.json
python scripts/extract_qa_entries.py          # -> data/rulebook/qa_entries.json (optional)
```

The app degrades gracefully without them (tools return a clean error; classic RAG still works).

### 5. Initialize the database

```bash
python scripts/init_db.py
# Prompts for admin email and password (or set ADMIN_EMAIL / ADMIN_PASSWORD env vars)
```

### 6. Run

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
# Unit tests (calculators, cite-check, rules lookup, vsav parsing, ...)
# Each test file also runs standalone: python tests/test_cite_check.py
python -m pytest tests/ -q

# Mobile/responsive UI tests (requires Playwright + a running server)
pip install playwright && python -m playwright install chromium
python tests/test_mobile.py
```

## Evaluation

Eval results are stored in `data/evals/` as JSON. The evals page (`/evals`) reads from this directory. See [docs/RESPONSES_API_README.md](docs/RESPONSES_API_README.md) for how evals are run.

## Related

- [ruleschat-evals](https://github.com/kehannan/ruleschat-evals) — evaluation datasets and fine-tuning data

## License

MIT — see [LICENSE](LICENSE).

Advanced Squad Leader is a trademark of Avalon Hill Games, Inc. This project is not affiliated with or endorsed by Hasbro, Avalon Hill Games, Inc., or Multi-Man Publishing, Inc.
