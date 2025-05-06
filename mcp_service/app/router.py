from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from typing import Dict, Any, Optional
from .main import ask_question as ask_question_service
from .auth import get_current_user, generate_api_key
from .db import get_user_by_email, update_user_api_key
import os

router = APIRouter()

class QuestionRequest(BaseModel):
    question: str

class UserApiKeyResponse(BaseModel):
    email: str
    api_key: Optional[str] = None
    message: str

@router.post("/ask")
async def ask_question(
    request: Request, 
    question_req: QuestionRequest,
    user_email: str = Depends(get_current_user)
):
    """
    Handle question answering requests from MCP
    Requires API key authentication
    """
    # Call the service function with the authenticated user email
    result = await ask_question_service(user_id=user_email, question=question_req.question)
    
    return result

@router.get("/health")
async def health():
    """
    Health check endpoint for MCP
    No authentication required
    """
    return {"status": "healthy"}

@router.get("/generate-api-key/{email}")
async def generate_api_key_endpoint(
    request: Request,
    email: EmailStr,
    admin_key: str
):
    """
    Generate a new API key for a user
    Requires admin authentication
    """
    # Verify admin authorization
    # In production, use a more secure approach than a simple string comparison
    admin_secret = os.getenv("ADMIN_SECRET_KEY", "admin-secret")
    if admin_key != admin_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Get user from database
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with email '{email}' not found")
    
    # Generate a new API key
    new_api_key = generate_api_key()
    
    # Update the user's API key in the database
    success = update_user_api_key(user["id"], new_api_key)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update API key")
    
    return UserApiKeyResponse(
        email=email,
        api_key=new_api_key,
        message="Store this API key securely. It won't be shown again."
    )

@router.get("/view-api-key/{email}")
async def view_api_key(
    request: Request,
    email: EmailStr,
    admin_key: str
):
    """
    View a user's API key
    Requires admin authentication
    For admin use only
    """
    # Verify admin authorization
    admin_secret = os.getenv("ADMIN_SECRET_KEY", "admin-secret")
    if admin_key != admin_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Get user from database
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with email '{email}' not found")
    
    if not user.get("api_key"):
        return UserApiKeyResponse(
            email=email,
            message="User does not have an API key"
        )
    
    return UserApiKeyResponse(
        email=email,
        api_key=user["api_key"],
        message="API key retrieved successfully"
    ) 