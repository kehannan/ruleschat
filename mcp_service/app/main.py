import os
import logging
import asyncio  # Add this line
from typing import Dict, Any, List
from dotenv import load_dotenv
from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.db import get_user_by_api_key

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

# Load environment variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

# Create FastAPI app
app = FastAPI(title="MCP API Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Store thread IDs for each user
conversations: Dict[str, str] = {}

# Request models
class QuestionRequest(BaseModel):
    question: str

# Dependency for API key authentication
async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = get_user_by_api_key(token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user

@app.get("/")
async def root():
    return {"message": "Welcome to MCP API Service"}

@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.post("/api/chat")
async def chat(
    request: QuestionRequest,
    user: Dict = Depends(get_current_user)
):
    """Endpoint to handle chat questions"""
    user_id = str(user["id"])
    return await ask_question(user_id, request.question)

@app.get("/api/conversations")
async def get_conversation_history(
    user: Dict = Depends(get_current_user)
):
    """Get conversation history for a user"""
    user_id = str(user["id"])
    if user_id not in conversations:
        return {"conversations": []}
    
    return {"conversations": conversations[user_id]}

async def ask_question(user_id: str, question: str):
       """
       Handle user questions and get responses from OpenAI using the Assistant API
       """
       if not user_id:
           raise HTTPException(status_code=400, detail="User ID is required")
       
       if not question:
           raise HTTPException(status_code=400, detail="Question is required")
       
       try:
           # Create a thread if one doesn't exist for this user
           if user_id not in conversations:
               thread = client.beta.threads.create()
               conversations[user_id] = thread.id
           
           thread_id = conversations[user_id]
           
           # Add message to thread
           client.beta.threads.messages.create(
               thread_id=thread_id,
               role="user",
               content=question
           )
           
           # Run the assistant on the thread
           assistant_id = "asst_M65nFsVKjQRamCQrfHThTeJt"  # The same assistant ID as your main app
           run = client.beta.threads.runs.create(
               thread_id=thread_id,
               assistant_id=assistant_id
           )
           
           # Wait for completion
           while True:
               run_status = client.beta.threads.runs.retrieve(
                   thread_id=thread_id,
                   run_id=run.id
               )
               if run_status.status == "completed":
                   break
               elif run_status.status in ["failed", "cancelled", "expired"]:
                   raise HTTPException(status_code=500, 
                                      detail=f"Assistant run failed with status: {run_status.status}")
               
               await asyncio.sleep(0.5)
           
           # Get the assistant's response
           messages = client.beta.threads.messages.list(
               thread_id=thread_id
           )
           
           # Get the last assistant message
           for message in messages.data:
               if message.role == "assistant":
                   answer = message.content[0].text.value
                   break
           
           return {"answer": answer}
       
       except Exception as e:
           logging.error(f"Error calling OpenAI: {str(e)}")
           raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")
