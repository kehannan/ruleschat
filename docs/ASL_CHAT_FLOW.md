# ASL Rules Chat: Question to Answer Flow

## System Overview

- **FastAPI** backend with WebSocket support
- **OpenAI Responses API** with file_search (RAG, 20 chunks)
- **Models**: gpt-5-mini (default) or gpt-4.1-mini (user-selectable)
- **Singleton ASLService** for consistent configuration
- **True real-time streaming** — text deltas sent immediately
- **Conversation history** — persistent per-user chat history
- **RAG sources** — displays chunk content from vector store search

## Complete Flow

```
User types question in browser
    ↓
Frontend (templates/ruleschat.html)
  • JavaScript captures question + selected model
  • Sends JSON via WebSocket: {type: "chat", text: "...", model: "gpt-5-mini"}
    ↓
WebSocket Handler (app/api/chat.py — websocket_chat())
  • Authenticates user from cookie/token
  • Parses JSON command (chat/new_conversation/switch_conversation)
  • Validates model against whitelist: {"gpt-5-mini", "gpt-4.1-mini"}
  • Creates/loads conversation for history
  • Prepends conversation history to input
    ↓
ASL Service (app/services/asl_service.py — get_answer())
  • Builds instructions via app/asl/policy.py
  • Starts timing measurement
    ↓
OpenAI Responses API (app/asl/client.py)
  • client.responses.stream(
      model="gpt-5-mini",
      input=question,
      instructions=system_instructions,
      tools=[{type: "file_search", vector_store_ids: [...], max_num_results: 20}],
      include=["file_search_call.results"]
    )
    ↓
OpenAI processes request:
  1. FILE_SEARCH (RAG) — searches vector store, retrieves top 20 chunks
  2. GENERATE — uses chunks as context, generates answer
    ↓
Streaming (asl_service.py)
  • Yields text deltas immediately as they arrive
  • Tracks timing: first event, file search completion, TTFT
  • After stream: extracts RAG sources from final response
    ↓
WebSocket streams to frontend (chat.py)
  • Each delta sent via websocket.send_text(delta)
  • After completion: saves user + assistant messages to DB
  • Sends stream_complete signal with timing + RAG sources
    ↓
Frontend displays answer
  • Renders markdown in real-time
  • Shows collapsible "Sources Used (N chunks)" section
  • Displays per-query cost estimate
```

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Frontend | `templates/ruleschat.html` | UI, WebSocket client, model selector, cost display |
| WebSocket | `app/api/chat.py` | Message routing, auth, history, model validation |
| Service | `app/services/asl_service.py` | OpenAI API calls, streaming, RAG extraction |
| Client | `app/asl/client.py` | Responses API wrapper |
| Config | `app/config.py` | System instructions (Answer + References format) |
| Policy | `app/asl/policy.py` | Instruction building |
| History | `app/services/chat_history_service.py` | Conversation persistence |
| Vector Store | `responses_api_config.json` | Store ID, versioned config |

## System Instructions Format

The system prompt requests concise output:

```
Answer: [1-2 sentences with direct answer]

References:
- (A4.34) Section Title — brief relevance
```

Two few-shot examples anchor the format. See `app/config.py` for full prompt.

## Timing Metrics

- **First Event**: when OpenAI first responds
- **File Search Complete**: when RAG finishes
- **TTFT**: time to first generated token
- **Total Time**: end-to-end response time

## RAG Sources

- **Chunks**: up to 20 per query (configurable via `RAG_MAX_CHUNKS` env var)
- **Extraction**: from `stream.get_final_response().output` after streaming
- **Content**: text, filename, file_id, relevance score
- **Display**: collapsible section in frontend

## Configuration

- **Vector store**: set up via `scripts/setup_responses_api.py`, config in `responses_api_config.json`
- **Model whitelist**: `{"gpt-5-mini", "gpt-4.1-mini"}` in `app/api/chat.py`
- **GPT-5 family**: does not support `temperature` parameter — omitted automatically
