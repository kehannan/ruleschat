# Responses API with Vector Store Setup

How the site uses the OpenAI Responses API with a Vector Store for the ASL Rules Assistant.

## Overview

The ASL rulebook is indexed into an OpenAI Vector Store and queried via the Responses API using native streaming. `file_search` actually queries **two** vector stores concatenated together (see `ASLConfig.all_vector_store_ids` in `app/asl/config.py`):

1. the **rulebook** store (`versions` / `active_version`), and
2. an **ASL Q&A errata** store (`qa_versions` / `active_qa_version`) — Scott Romanowski's "ASL Q&A v31" Q&A/clarifications/errata compilation.

The flow:

1. Create a vector store (one-time)
2. Upload the source PDF to the vector store (one-time)
3. Query via Responses API with `file_search` tool pointing at both vector stores
4. Stream deltas over WebSocket to the browser

## Benefits

- Handles large PDFs (88MB+)
- Fast retrieval via vector search (RAG, 20 chunks per query)
- Native streaming responses
- Simple runtime: no threads/assistants needed

## Setup

### Prerequisites

- Python 3.10+
- OpenAI API access
- The ASL rulebook PDF in the evals repo

### Environment Variables

Ensure your `.env` contains:
```
OPENAI_API_KEY=...
OPENAI_ORG_ID=...
OPENAI_PROJECT_ID=...
DEFAULT_MODEL=gpt-5-mini
TEMPERATURE=0.2
RAG_MAX_CHUNKS=20
```

### Prepare the PDF

The setup script expects the rulebook here:
```
../ruleschat-evals/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf
```

Adjust the path in `scripts/setup_responses_api.py` if needed.

### Run Setup

```bash
python scripts/setup_responses_api.py
```

This will:
- Create a vector store
- Upload the PDF to that store
- Save configuration to `responses_api_config.json`

### Q&A errata vector store (second store)

The Q&A errata store is built separately from `ASL-QA-v31.pdf` (a two-column Q&A/errata
compilation). Each chunk is one-or-more whole `{refs|pN}`-tagged Q&A entries:

```bash
python scripts/setup_qa_vector_store.py            # creates the store, writes qa_versions config
python scripts/setup_qa_vector_store.py --dry-run --output /tmp/qa.txt   # preview chunks first
```

This writes `qa_versions` / `active_qa_version` to `responses_api_config.json`. (This store
replaced the older "Perry Sez" store in 2026-06; the new doc subsumes that content.)

### Test

```bash
python test_responses_api.py
```

## Configuration File

### responses_api_config.json

The config supports versioned vector stores, with a parallel set of keys for the Q&A errata store:
```json
{
  "active_version": "v6",
  "versions": {
    "v6": { "vector_store_id": "vs_...", "file_id": "file-...", "description": "..." }
  },
  "active_qa_version": "qa_v1",
  "qa_versions": {
    "qa_v1": { "vector_store_id": "vs_...", "file_id": "file-...", "total_chunks": 296 }
  }
}
```

At runtime the active `versions` store and the active `qa_versions` store are both passed to `file_search` (`qa_versions` is optional — omit it and only the rulebook is searched).

## How It Works (Runtime)

The app uses the Responses API with `file_search` and native streaming:

```python
stream = client.responses.stream(
    model="gpt-5-mini",
    input=user_message,
    instructions=system_instructions,
    tools=[{
        "type": "file_search",
        "vector_store_ids": config.all_vector_store_ids,  # [rulebook, qa_errata]
        "max_num_results": 20,
    }],
    include=["file_search_call.results"],
)

with stream as s:
    for event in s:
        if event.type == "response.output_text.delta":
            websocket.send_text(event.delta)
    final = s.get_final_response()  # Extract RAG sources
```

Key files:
- `app/asl/client.py` — Responses API wrapper
- `app/services/asl_service.py` — Streaming and RAG source extraction
- `app/api/chat.py` — WebSocket handler

## Model Notes

- **gpt-5-mini**: Default. Higher accuracy (93.9% eval pass rate). Does not support `temperature` parameter.
- **gpt-4.1-mini**: Faster (~2.6s TTFT vs ~10s). Lower accuracy (~76-81%). Supports `temperature`.
- Users select model via dropdown in the chat UI. Backend validates against whitelist.

## Troubleshooting

1. **"Responses API not properly configured"** — Ensure `responses_api_config.json` exists with a valid `vector_store_id`
2. **"No response received"** — Verify OpenAI credentials in `.env`
3. **Streaming appears all at once** — Check browser console; the UI may buffer small chunks

## Notes

- The vector store is created once and reused across requests
- Replace the PDF and re-run setup to refresh content
- RAG chunk count is configurable via `RAG_MAX_CHUNKS` env var (default: 20)
