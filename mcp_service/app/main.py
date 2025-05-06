import os
import logging
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

# Store conversation history for each user
conversations: Dict[str, List[Dict[str, str]]] = {}

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
    Handle user questions and get responses from OpenAI
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")
    
    # Initialize conversation for new users
    if user_id not in conversations:
        conversations[user_id] = [
            {"role": "system", "content": "You are a helpful assistant providing accurate and helpful information."}
        ]
    
    # Add user message to history
    conversations[user_id].append({"role": "user", "content": question})
    
    try:
        # Get response from OpenAI
        logging.info(f"Sending question to OpenAI: {question}")
        response = client.chat.completions.create(
            model="gpt-4-turbo-preview",  # Use the model of your choice
            messages=conversations[user_id]
        )
        
        answer = response.choices[0].message.content
        
        # Add assistant's message to history
        conversations[user_id].append({"role": "assistant", "content": answer})
        
        # Keep conversation history manageable
        if len(conversations[user_id]) > 10:
            # Keep system message and last 4 exchanges
            conversations[user_id] = [conversations[user_id][0]] + conversations[user_id][-8:]
        
        return {"answer": answer}
    
    except Exception as e:
        logging.error(f"Error calling OpenAI: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}") 