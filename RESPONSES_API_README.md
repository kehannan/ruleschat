# Responses API with Vector Store Setup

This document explains how to set up and use the new Responses API with vector store functionality for the ASL Rules Assistant.

## Overview

The Responses API provides a more robust way to handle large documents like the ASL rules PDF. Instead of attaching the entire 88MB PDF to each request (which exceeds the 32MB limit), we:

1. **Create a vector store** - A searchable index of the PDF content
2. **Upload the PDF** - The PDF is processed and indexed in the vector store
3. **Create an assistant** - Configured with file search capabilities
4. **Use threads** - Each conversation uses a thread to maintain context

## Benefits

- ✅ Handles large PDFs (88MB+) without size limits
- ✅ Better search and retrieval of relevant content
- ✅ Maintains conversation context across multiple questions
- ✅ More efficient processing
- ✅ Better accuracy through RAG (Retrieval Augmented Generation)

## Setup Instructions

### 1. Prerequisites

Make sure you have:
- Python 3.8+
- OpenAI API key with access to the beta APIs
- ASL rules PDF file

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Variables

Ensure your `.env` file contains:
```
OPENAI_API_KEY=your_openai_api_key_here
```

### 4. Prepare the PDF

Place your ASL rules PDF file in the project directory. Update the path in `setup_responses_api.py` if needed:

```python
pdf_path = "asl_rules.pdf"  # Change this to your PDF filename
```

### 5. Run Setup

```bash
python setup_responses_api.py
```

This script will:
- Create a vector store
- Upload your PDF
- Create an assistant with file search capabilities
- Save configuration to `responses_api_config.json`
- Test the setup

### 6. Test the Setup

```bash
python test_responses_api.py
```

This will verify that everything is working correctly.

## Configuration Files

### responses_api_config.json

After setup, this file contains:
```json
{
  "vector_store_id": "vs_abc123...",
  "file_id": "file_xyz789...",
  "assistant_id": "asst_def456...",
  "pdf_path": "asl_rules.pdf"
}
```

## How It Works

### 1. Vector Store Creation
```python
vector_store_id = handler.create_vector_store("ASL Rules Vector Store")
```

### 2. File Upload
```python
file_id = handler.upload_file_to_vector_store("asl_rules.pdf", vector_store_id)
```

### 3. Assistant Creation
```python
assistant_id = handler.create_assistant(
    name="ASL Rules Assistant",
    instructions="You are an expert on Advanced Squad Leader (ASL) rules..."
)
```

### 4. Conversation Flow
```python
# Create thread for conversation
thread_id = handler.create_thread()

# Add user message
message_id = handler.add_message_to_thread(thread_id, "What are the movement rules?")

# Run assistant
run_id = handler.run_assistant(thread_id, assistant_id)

# Wait for completion
result = handler.wait_for_run_completion(thread_id, run_id)

# Get response
response = handler.get_latest_assistant_message(thread_id)
```

## WebSocket Integration

The main application now uses the Responses API in the WebSocket handler:

1. **Connection**: Creates a new thread for each WebSocket connection
2. **Messages**: Adds user messages to the thread
3. **Processing**: Runs the assistant and waits for completion
4. **Response**: Streams the response back to the client

## Error Handling

The system handles various error conditions:

- **Configuration missing**: Prompts to run setup
- **API errors**: Logs details and provides user-friendly messages
- **Timeouts**: Handles long-running requests
- **Connection issues**: Graceful WebSocket disconnection

## Monitoring and Logging

The system provides detailed logging:

- ✅ Connection events
- ✅ API calls and responses
- ✅ Error conditions
- ✅ Performance metrics

## Troubleshooting

### Common Issues

1. **"Responses API not properly configured"**
   - Run `setup_responses_api.py` first
   - Check that `responses_api_config.json` exists

2. **"No response received"**
   - Check OpenAI API key and quota
   - Verify PDF file exists and is readable
   - Check API logs for errors

3. **"Run failed"**
   - Check the `last_error` field in the run object
   - Verify vector store and assistant IDs are correct

4. **"Run timed out"**
   - Increase timeout in `wait_for_run_completion()`
   - Check network connectivity

### Debug Mode

Enable detailed logging:
```python
logging.basicConfig(level=logging.DEBUG)
```

## Performance Considerations

- **Vector store creation**: One-time setup cost
- **File upload**: Depends on PDF size
- **Response time**: Generally faster than chat completions with large files
- **Memory usage**: More efficient than loading entire PDF

## Migration from Chat Completions API

The main changes:

1. **No more file attachments** in messages
2. **Thread-based conversations** instead of message history
3. **Vector store search** for content retrieval
4. **Run-based processing** with status tracking

## API Limits

- **Vector stores**: 100 per organization
- **Files per vector store**: 10,000
- **File size**: Up to 512MB per file
- **Threads**: Unlimited
- **Messages per thread**: Unlimited

## Security

- API keys are stored in environment variables
- Configuration files contain only IDs (no sensitive data)
- WebSocket connections use standard security practices

## Future Enhancements

Potential improvements:
- Multiple PDF support
- Custom chunking strategies
- Advanced search filters
- Conversation analytics
- Performance optimization 