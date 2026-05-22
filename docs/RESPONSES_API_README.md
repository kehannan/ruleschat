# Responses API with Vector Store Setup

How the site uses the OpenAI Responses API with a Vector Store for the ASL Rules Assistant.

## Overview

The ASL rulebook is indexed into an OpenAI Vector Store and queried via the Responses API using native streaming:

1. Create a vector store (one-time)
2. Upload the rulebook PDF to the vector store (one-time)
3. Query via Responses API with `file_search` tool pointing at the vector store
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
../mysite2-evals-sft/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf
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

### Test

```bash
python test_responses_api.py
```

## Configuration File

### responses_api_config.json

The config supports versioned vector stores:
```json
{
  "active_version": "v5",
  "versions": {
    "v5": {
      "vector_store_id": "vs_...",
      "file_id": "file-...",
      "description": "..."
    }
  }
}
```

The active version's `vector_store_id` is used at runtime.

## How It Works (Runtime)

The app uses the Responses API with `file_search` and native streaming:

```python
stream = client.responses.stream(
    model="gpt-5-mini",
    input=user_message,
    instructions=system_instructions,
    tools=[{
        "type": "file_search",
        "vector_store_ids": [vector_store_id],
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
