# Manual Test Scripts

This directory contains manual testing scripts for development and debugging purposes.

## Files

- `test.py` - Basic OpenAI API connection test
- `test_email_update.py` - Test email update functionality
- `test_feedback.py` - Test feedback submission system
- `test_new_api.py` - Test new API endpoints
- `test_openai_messages.py` - Test OpenAI messages API
- `test_openai_run.py` - Test OpenAI run functionality
- `test_responses_api.py` - Test Responses API with file search
- `ws_test.py` - WebSocket connection test

## Usage

Run any test script directly from the project root:

```bash
python tests/manual/test.py
python tests/manual/test_responses_api.py
# etc.
```

## Note

These are **manual test scripts** for development, not automated unit tests. For automated testing, see the main `tests/` directory (when implemented).

