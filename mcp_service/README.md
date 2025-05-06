# Question Answering MCP Service

This MCP service provides a question answering functionality similar to the web service. It allows users to submit questions via MCP, which are then processed by OpenAI, and the answers are returned to the user.

## Authentication

This service uses API key authentication. Each user from your existing user database can have an API key associated with their account. The API key must be provided in the `x-api-key` header for authenticated requests.

### Setting Up API Keys

1. First, list the tables in your database to identify the user table:

   ```
   python mcp_service/tools/add_api_key_to_users.py list-tables
   ```

2. Run the tool to add the API key field to your existing user table:

   ```
   python mcp_service/tools/add_api_key_to_users.py add-field
   ```

3. List all users with their emails to see which users are available:

   ```
   python mcp_service/tools/add_api_key_to_users.py list-users
   ```

4. Generate an API key for a specific user by email:

   ```
   python mcp_service/tools/add_api_key_to_users.py generate user@example.com
   ```

5. Or generate API keys for all users without one:

   ```
   python mcp_service/tools/add_api_key_to_users.py generate-all
   ```

6. View a user's API key:

   ```
   python mcp_service/tools/add_api_key_to_users.py show user@example.com
   ```

Alternatively, you can use the service's API endpoints (admin key required):
- `/qa/generate-api-key/{email}?admin_key=<admin_key>`
- `/qa/view-api-key/{email}?admin_key=<admin_key>`

## Setup

1. Create a `.env` file in the root directory with the following content:
   ```
   # OpenAI API Key
   OPENAI_API_KEY=your_openai_api_key_here

   # MCP Configuration
   MCP_SERVICE_NAME=QuestionAnsweringService
   MCP_SERVICE_VERSION=1.0.0
   
   # Database Configuration
   DB_PATH=/path/to/your/mysite2/mysite2.db
   
   # Admin Configuration
   ADMIN_SECRET_KEY=your_admin_secret_key
   
   # Development Mode (set to "true" to use hardcoded API keys)
   DEV_MODE=false
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Run the service:
   ```
   python run.py
   ```

## Docker

To run the service using Docker:

1. Build the Docker image:
   ```
   docker build -t question-answering-mcp .
   ```

2. Run the Docker container:
   ```
   docker run -p 8000:8000 --env-file .env question-answering-mcp
   ```

## API Endpoints

- `/qa/ask` - POST endpoint to ask a question (requires API key)
- `/qa/health` - GET endpoint to check service health
- `/qa/generate-api-key/{email}` - GET endpoint to generate an API key for a user (admin only)
- `/qa/view-api-key/{email}` - GET endpoint to view a user's API key (admin only)
- `/` - Root endpoint showing service information

## Using the Service

To use the service, make requests with your API key in the header:

```
curl -X POST http://localhost:8000/qa/ask \
  -H "Content-Type: application/json" \
  -H "x-api-key: your_api_key_here" \
  -d '{"question": "What is the capital of France?"}'
```

Or via the MCP endpoint:

```
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -H "x-api-key: your_api_key_here" \
  -d '{
    "service": "QuestionAnsweringService",
    "method": "ask_question",
    "parameters": {
      "question": "What is the capital of France?"
    }
  }'
``` 