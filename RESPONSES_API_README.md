# Responses API with Vector Store Setup

This document explains how the site uses the OpenAI Responses API with the OpenAI Vector Store for the ASL Rules Assistant.

## Overview

We index the ASL rulebook into an OpenAI Vector Store and query it with the Responses API using native streaming. Instead of uploading the full PDF per request, we:

1. Create a vector store (one-time)
2. Upload the rulebook PDF to the vector store (one-time)
3. Query via Responses API with `file_search` tool pointing at the vector store and `web_search` tool for additional resources
4. Stream deltas over WebSocket to the browser

Both `file_search` and `web_search` tools are available simultaneously, allowing the AI to search the rulebook and the web in parallel when needed.

## Benefits

- ✅ Handles large PDFs (88MB+)
- ✅ Fast retrieval via vector search (RAG)
- ✅ Native streaming responses
- ✅ Simple runtime: no threads/assistants needed
- ✅ Web search capability for recent clarifications and community discussions
- ✅ Parallel search: rulebook and web search run simultaneously

## Setup Instructions

### 1. Prerequisites

Make sure you have:
- Python 3.10+
- OpenAI API access
- The ASL rulebook PDF in the evals repo

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Variables

Ensure your `.env` contains:
```
OPENAI_API_KEY=...
OPENAI_ORG_ID=...
OPENAI_PROJECT_ID=...
DEFAULT_MODEL=gpt-4o
TEMPERATURE=0.2
SECRET_KEY=...
```

### 4. Prepare the PDF

The setup script expects the rulebook here:

```
../mysite2-evals-sft/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf
```

Adjust the path in `scripts/setup_responses_api.py` if needed.

### 5. Run Setup

```bash
python scripts/setup_responses_api.py
```

This will:
- Create a vector store
- Upload the PDF to that store
- Save configuration to `responses_api_config.json`

### 6. Test the Setup

```bash
python test_responses_api.py
```

This validates access to the configured vector store.

## Configuration File

### responses_api_config.json

After setup, the file contains:
```json
{
  "vector_store_id": "vs_...",
  "file_id": "file-...",
  "pdf_path": "../mysite2-evals-sft/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
}
```

Note: We do not use `assistant_id` or threads/runs.

## How It Works (Runtime)

The app uses the Responses API with `file_search` and native streaming. Minimal pattern:

```python
stream = client.responses.create(
    model=DEFAULT_MODEL,
    input=user_message,
    instructions=ASL_SYSTEM_INSTRUCTIONS,
    temperature=TEMPERATURE,
    stream=True,
    tools=[
        {
            "type": "file_search",
            "vector_store_ids": [responses_config["vector_store_id"]],
        },
        {
            "type": "web_search",
        }
    ],
)

for event in stream:
    if getattr(event, "type", None) == "response.output_text.delta":
        websocket.send_text(event.delta)
```

This is implemented in `app/api/chat.py`.

## WebSocket Integration

The WebSocket endpoint:
- Accepts the connection and starts a keep-alive ping task
- On each user message, calls the Responses API with `stream=True`
- For each `delta` event, forwards text to the client immediately

## Troubleshooting

1. "Responses API not properly configured"
   - Ensure `responses_api_config.json` exists and has a valid `vector_store_id`
2. "No response received"
   - Verify OpenAI credentials in `.env` (API key, org, project)
3. Streaming appears all at once
   - Check browser console; the UI buffers small chunks for smooth display

## Notes

- The vector store is created once and reused
- You can replace the PDF and re-run the setup to refresh content
- The model is configurable via `DEFAULT_MODEL`
- Web search is automatically available - no additional configuration needed
- The AI decides when to use web search vs file search based on the question
- Both tools can be used simultaneously for comprehensive answers