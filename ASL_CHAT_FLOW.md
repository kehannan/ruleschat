# ASL Rules Chat: Question to Answer Flow

**Date:** December 13, 2024  
**System:** mysite2 - ASL Rules Assistant

---

## System Architecture Overview

The ASL Rules Assistant uses:
- **FastAPI** backend with WebSocket support
- **OpenAI Responses API** (SDK 2.11.0+) with vector store (RAG)
- **Singleton ASLService** for consistent configuration
- **True real-time streaming** - text deltas yielded immediately as they arrive
- **RAG Sources** - displays full chunk content from vector store search

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
│    - Loads config file (responses_api_config.json)              │
│    - Gets vector_store_id                                       │
│    - Creates OpenAI client                                      │
│  • If exists: Returns existing instance                          │
│  Line: 151                                                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         ASLService.get_answer()                                  │
│  • Builds instructions (adds web search emphasis if needed)     │
│  • Starts timing measurement                                    │
│  Line: 210                                                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         OPENAI RESPONSES API CALL                               │
│  client.responses.stream(                                       │
│    input=question,                                              │
│    instructions=...,                                            │
│    tools=[                                                      │
│      {                                                          │
│        type: "file_search",                                     │
│        vector_store_ids: [...],                                 │
│        max_num_results: 5  ← Limits to 5 chunks               │
│      },                                                         │
│      {type: "web_search"}                                      │
│    ],                                                           │
│    include=["file_search_call.results"]  ← Get RAG chunks      │
│  )                                                              │
│  Line: 270-280                                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         OPENAI PROCESSES REQUEST                                 │
│  ┌──────────────────────────────────────────────┐              │
│  │ 1. FILE_SEARCH (RAG)                          │              │
│  │    • Searches vector store using question     │              │
│  │    • Retrieves top 5 relevant chunks          │              │
│  │    • Returns: chunks with full text content   │              │
│  └──────────────────────────────────────────────┘              │
│  ┌──────────────────────────────────────────────┐              │
│  │ 2. WEB_SEARCH (optional)                     │              │
│  │    • Searches web for recent info            │              │
│  └──────────────────────────────────────────────┘              │
│  ┌──────────────────────────────────────────────┐              │
│  │ 3. GENERATE RESPONSE                         │              │
│  │    • Uses retrieved chunks + web results     │              │
│  │    • Generates answer using RAG context       │              │
│  └──────────────────────────────────────────────┘              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         TRUE STREAMING (asl_service.py)                         │
│  • Uses stream_manager context manager                          │
│  • Yields text deltas IMMEDIATELY as they arrive               │
│  • Tracks timing:                                               │
│    - First event time                                           │
│    - File search completion time                               │
│    - First token time (TTFT)                                    │
│  • No buffering - text streams to frontend in real-time         │
│  Lines: 381-409                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         EXTRACT RAG SOURCES (asl_service.py)                    │
│  • After stream completes:                                      │
│    final = stream.get_final_response()  ← Get final response   │
│  • Extracts from final.output:                                 │
│    - file_search_call.results[]                                │
│    - Each result contains: text, filename, file_id, score       │
│  • Creates RAG source objects with full chunk content           │
│  • Stores in timing_data['rag_sources']                        │
│  Lines: 338-376, 422-429                                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         RETURN STREAM GENERATOR                                  │
│  • Returns: (generator, timing_data)                            │
│  • Generator yields deltas immediately                           │
│  • timing_data populated after stream completes                 │
│  Line: 431-436                                                  │
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
│      rag_sources: [...]  ← Full chunk content from RAG          │
│    })                                                           │
│  Lines: 185-190                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         FRONTEND PROCESSES (ruleschat.html)                      │
│  • Receives stream_complete message                             │
│  • Parses markdown in response text                             │
│  • Calls addRAGSourcesSection()                                 │
│  • Creates collapsible "Sources Used (N chunks)" section          │
│  • Displays full chunk content for each RAG source              │
│  Lines: 758-835                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    USER SEES ANSWER                              │
│  • Formatted text with markdown                                 │
│  • Collapsible "Sources Used" section at bottom                  │
│  • Clicking header expands to show all RAG chunks               │
│  • Each chunk displays full text content from vector store       │
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
  - Displays streaming text in real-time
  - Adds RAG sources section (collapsible)
  - Shows full chunk content from vector store

### 2. WebSocket Handler
- **File:** `app/api/chat.py`
- **Function:** `websocket_chat()` (line 107)
- **Role:** Receives questions, coordinates response, streams to frontend

### 3. ASL Service (Singleton)
- **File:** `app/services/asl_service.py`
- **Class:** `ASLService`
- **Function:** `get_answer()` (line 210)
- **Role:** Main service layer, handles OpenAI API calls, extracts RAG sources
- **Key Methods:**
  - `_handle_streaming_response()` - True streaming with immediate delta yields
  - `_extract_rag_sources_from_final()` - Extracts full chunks from final response

### 4. OpenAI Responses API
- **Service:** OpenAI cloud service
- **SDK Version:** 2.11.0+ (required for `stream.get_final_response()`)
- **Tools Used:**
  - `file_search`: Searches vector store (RAG)
    - `max_num_results: 5` - Limits retrieved chunks to 5
    - `include=["file_search_call.results"]` - Requests full chunk content
  - `web_search`: Searches web for current information

### 5. Vector Store
- **Setup:** `scripts/setup_responses_api.py`
- **Content:** ASL rulebook chunks (1187 chunks for v4)
- **Purpose:** Fast semantic search for relevant rules
- **Retrieval:** Top 5 most relevant chunks per query

---

## Data Flow Summary

1. **Question** (String) → WebSocket → Backend
2. **Service Instance** → Singleton (created once, reused)
3. **API Request** → Question + Instructions + Tools → OpenAI
   - `file_search` with `max_num_results: 5`
   - `include=["file_search_call.results"]` to get full chunks
4. **RAG Search** → Vector store search → Top 5 relevant chunks (with full text)
5. **True Streaming** → Deltas yielded immediately as they arrive → Frontend
6. **Final Response** → `stream.get_final_response()` → Extract RAG sources
7. **RAG Sources** → Full chunk content extracted → Sent on completion
8. **Display** → Frontend formats text + shows collapsible sources section

---

## Timing Metrics Tracked

- **First Event Time:** When OpenAI first responds
- **File Search Complete:** When RAG search finishes
- **First Token (TTFT):** Time to first generated token
- **Total Time:** End-to-end response time
- **RAG Time:** Time spent searching vector store
- **Generation Time:** Time spent generating response after RAG

## RAG Sources

- **Extraction:** After stream completes, `stream.get_final_response()` provides full response object
- **Source:** `final.output` → `file_search_call.results[]`
- **Content:** Each result contains:
  - `text`: Full chunk content from vector store
  - `filename`: Original PDF filename (if available)
  - `file_id`: OpenAI file ID
  - `score`: Relevance score
  - `attributes`: Additional metadata
- **Display:** Frontend shows collapsible "Sources Used (N chunks)" section with full chunk text
- **Limit:** Maximum 5 chunks per query (via `max_num_results`)

---

## Implementation Details

### True Streaming
- Text deltas are yielded **immediately** as they arrive from OpenAI
- No buffering or two-pass collection
- Frontend receives and displays text in real-time

### RAG Sources Extraction
- Uses `stream.get_final_response()` (OpenAI SDK 2.11.0+)
- Extracts full chunk content from `final.output`
- All 5 chunks (or fewer) are included with complete text
- No reliance on streaming events for content (more reliable)

### Chunk Limiting
- `max_num_results: 5` limits vector store search to top 5 chunks
- Reduces token usage and focuses on most relevant content
- Improves response quality by reducing noise

---

## File Locations

- Frontend: `templates/ruleschat.html`
- WebSocket Handler: `app/api/chat.py` (line 107)
- Service: `app/services/asl_service.py` (line 340)
- Config: `responses_api_config.json`
- Vector Store Setup: `scripts/setup_responses_api.py`

---

*Document last updated: December 13, 2024*

