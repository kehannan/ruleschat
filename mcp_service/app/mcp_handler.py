from fastapi import FastAPI, Request
from metalcloud.service import McpHandler
from .mcp_config import question_answering_service
from .router import router

# Initialize FastAPI app
app = FastAPI(title="Question Answering MCP Service")

# Add the router
app.include_router(router, prefix="/qa")

# Create MCP handler
mcp_handler = McpHandler(services=[question_answering_service])

@app.post("/{path:path}")
async def handle_mcp_request(request: Request, path: str):
    """
    Handle MCP requests and forward them to the appropriate router endpoints
    """
    return await mcp_handler.handle_request(request, path)

@app.get("/")
async def root():
    """
    Root endpoint for the service
    """
    return {"message": "Question Answering MCP Service"} 