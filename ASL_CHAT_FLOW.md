# ASL Rules Chat: Question to Answer Flow

**Date:** December 12, 2024  
**System:** mysite2 - ASL Rules Assistant  
**Last Updated:** After refactoring to modular architecture

---

## System Architecture Overview

The ASL Rules Assistant uses:
- **FastAPI** backend with WebSocket support
- **OpenAI Responses API** with vector store (RAG)
- **Singleton ASLService** for consistent configuration
- **Modular architecture** with separated concerns (config, client, policy, postprocess)
- **Real-time streaming** for responsive user experience

---

## Complete Flow: User Question → Answer

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER ASKS QUESTION                            │
│              (types in browser chat interface)                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              FRONTEND (ruleschat.html)                           │
│  • JavaScript captures question                                 │
│  • Sends via WebSocket.send()                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         WEBSOCKET HANDLER (app/api/chat.py)                      │
│         websocket_chat() receives message                        │
│         Line: 107                                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         GET ASL SERVICE (Singleton Pattern)                       │
│  • get_asl_service() called                                     │
│  • If first call: Creates ASLService instance                   │
│    - app/asl/config.py: Loads config (responses_api_config.json)│
│    - app/asl/client.py: Creates OpenAIResponsesClient           │
│    - Gets vector_store_id from config                            │
│  • If exists: Returns existing instance                          │
│  Line: 151 (chat.py)                                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         ASLService.get_answer()                                  │
│  • app/asl/policy.py: Checks if calculation question            │
│  • app/asl/policy.py: Builds instructions                       │
│  • Starts timing measurement                                    │
│  Line: 210 (asl_service.py)                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         OPENAI RESPONSES API CALL                               │
│  app/asl/client.py: OpenAIResponsesClient.create_response()   │
│    input=question,                                              │
│    instructions=...,                                            │
│    tools=[                                                      │
│      {type: "file_search", vector_store_ids: [...]},           │
│      {type: "web_search"}                                      │
│    ],                                                           │
│    stream=True                                                  │
│  Line: 284 (asl_service.py)                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         OPENAI PROCESSES REQUEST                                 │
│  ┌──────────────────────────────────────────────┐              │
│  │ 1. FILE_SEARCH (RAG)                          │              │
│  │    • Searches vector store using question     │              │
│  │    • Retrieves relevant chunks from rulebook   │              │
│  │    • Returns: file_id + chunk indices          │              │
│  └──────────────────────────────────────────────┘              │
│  ┌──────────────────────────────────────────────┐              │
│  │ 2. WEB_SEARCH (optional)                     │              │
│  │    • Searches web for recent info            │              │
│  └──────────────────────────────────────────────┘              │
│  ┌──────────────────────────────────────────────┐              │
│  │ 3. GENERATE RESPONSE                         │              │
│  │    • Uses retrieved chunks + web results     │              │
│  │    • Generates answer with citations         │              │
│  └──────────────────────────────────────────────┘              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         STREAM EVENTS BACK (asl_service.py)                     │
│  • Collects all events from stream                              │
│  • Tracks timing:                                               │
│    - First event time                                           │
│    - File search completion time                               │
│    - First token time (TTFT)                                    │
│  • Accumulates text deltas → output_text                        │
│  • app/asl/postprocess.py: Computes timing metrics              │
│  Lines: 316-395 (asl_service.py)                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         EXTRACT CITATIONS (asl_service.py)                      │
│  • Simplified extraction (gated behind DEBUG_RAG=1)             │
│  • Processes events for:                                        │
│    - response.output_text.annotation.added                      │
│      → Gets file_id, filename, chunk_index                      │
│  • Creates citation objects with metadata                       │
│  • Stores in timing_data['citations']                           │
│  Lines: 134-208 (asl_service.py)                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         CREATE STREAM GENERATOR                                  │
│  • Replays events, yielding text deltas                         │
│  • Returns: (generator, timing_data)                             │
│  Line: 436 (asl_service.py)                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         STREAM TO FRONTEND (chat.py)                            │
│  for delta in stream:                                            │
│    await websocket.send_text(delta)                             │
│  • Sends each text chunk as it arrives                          │
│  • Frontend displays text in real-time                          │
│  Lines: 165-171                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         SEND COMPLETION SIGNAL (chat.py)                         │
│  • After streaming completes:                                    │
│    await websocket.send_text({                                  │
│      type: "stream_complete",                                   │
│      timing: {...},                                             │
│      citations: [...]                                           │
│    })                                                           │
│  Lines: 185-190                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         FRONTEND PROCESSES (ruleschat.html)                      │
│  • Receives stream_complete message                             │
│  • Parses markdown in response text                             │
│  • Adds citations as clickable footnotes [1], [2], etc.         │
│  • Creates "References" section at bottom                        │
│  • Attaches click handlers for citation modals                   │
│  Lines: 272-307                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    USER SEES ANSWER                              │
│  • Formatted text with markdown                                 │
│  • Clickable citation footnotes                                 │
│  • References section                                            │
│  • Clicking [1] shows citation content in modal                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Components

### 1. Frontend (Browser)
- **File:** `templates/ruleschat.html`
- **Role:** User interface, WebSocket client, real-time display
- **Key Functions:**
  - Captures user input
  - Sends questions via WebSocket
  - Displays streaming text
  - Adds citation footnotes
  - Shows citation modals

### 2. WebSocket Handler
- **File:** `app/api/chat.py`
- **Function:** `websocket_chat()` (line 107)
- **Role:** Receives questions, coordinates response, streams to frontend

### 3. ASL Service (Singleton) - Orchestrator
- **File:** `app/services/asl_service.py` (~480 lines, refactored)
- **Class:** `ASLService`
- **Function:** `get_answer()` (line 210)
- **Role:** Thin orchestrator that coordinates modules
- **Dependencies:**
  - `app/asl/config.py` - Configuration loading
  - `app/asl/client.py` - OpenAI client wrapper
  - `app/asl/policy.py` - Instruction building
  - `app/asl/postprocess.py` - Response processing

### 4. ASL Module Components (New Architecture)
- **`app/asl/config.py`** - Configuration management
  - `ASLConfig` dataclass
  - `load_asl_config()` - Loads config from env/files
  - `load_vector_store_id()` - Handles versioned config
  
- **`app/asl/client.py`** - OpenAI client wrapper
  - `OpenAIResponsesClient` class
  - Wraps `client.responses.create()` calls
  
- **`app/asl/policy.py`** - Instruction building
  - `is_calculation_question()` - Detects calculation questions
  - `get_structured_output_schema()` - JSON schema for structured output
  - `build_instructions()` - Builds complete instruction strings
  
- **`app/asl/postprocess.py`** - Response processing
  - `extract_response_text()` - Extracts text from response objects
  - `format_structured_answer()` - Formats JSON to human-readable
  - `parse_structured_json()` - Parses JSON from markdown code blocks
  - `compute_timing_metrics()` - Calculates timing metrics

### 5. OpenAI Responses API
- **Service:** OpenAI cloud service
- **Tools Used:**
  - `file_search`: Searches vector store (RAG)
  - `web_search`: Searches web for current information

### 6. Vector Store
- **Setup:** `scripts/setup_responses_api.py`
- **Content:** ASL rulebook chunks (1186 chunks for v4)
- **Purpose:** Fast semantic search for relevant rules

---

## Data Flow Summary

1. **Question** (String) → WebSocket → Backend
2. **Service Instance** → Singleton (created once, reused)
3. **API Request** → Question + Instructions + Tools → OpenAI
4. **RAG Search** → Vector store search → Relevant chunks
5. **Response Events** → Streamed back → Text + Citations
6. **Text Streaming** → Deltas sent to frontend in real-time
7. **Citations** → Extracted from events → Sent on completion
8. **Display** → Frontend formats + adds footnotes

---

## Timing Metrics Tracked

- **First Event Time:** When OpenAI first responds
- **File Search Complete:** When RAG search finishes
- **First Token (TTFT):** Time to first generated token
- **Total Time:** End-to-end response time
- **RAG Time:** Time spent searching vector store
- **Generation Time:** Time spent generating response after RAG

---

## Current Limitations

- **Citation Content:** Citations are extracted with metadata (file_id, index) but full chunk content is not yet retrieved from the vector store
- **Citation Extraction:** Simplified extraction gated behind `DEBUG_RAG=1` environment variable for verbose logging
- **File Search Results:** The `file_search_call.completed` event doesn't contain actual chunk content, only completion signal

---

## File Locations

- Frontend: `templates/ruleschat.html`
- WebSocket Handler: `app/api/chat.py` (line 107)
- Service (Orchestrator): `app/services/asl_service.py` (line 210)
- Config Module: `app/asl/config.py`
- Client Module: `app/asl/client.py`
- Policy Module: `app/asl/policy.py`
- Postprocess Module: `app/asl/postprocess.py`
- Config File: `responses_api_config.json`
- Vector Store Setup: `scripts/setup_responses_api.py`

---

## Architecture Notes

**Refactoring (December 12, 2024):**
- Split monolithic `asl_service.py` (~1381 lines) into modular architecture
- `ASLService` is now a thin orchestrator (~480 lines) that coordinates modules
- Separated concerns: config, client, policy, postprocessing
- External API unchanged - all existing callers work without modification
- Citation extraction simplified and gated behind `DEBUG_RAG=1`

---

*Document generated: December 11, 2024*  
*Last updated: December 12, 2024 (after refactoring)*

