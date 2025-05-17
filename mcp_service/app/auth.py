import os
import secrets
import string
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Depends
from dotenv import load_dotenv
from .db import get_user_by_api_key

# Load environment variables
load_dotenv()

# For development/testing when DB is not available
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
DEV_API_KEYS = {
    "test-key": "test@example.com"
}

def validate_api_key(api_key: str) -> Optional[str]:
    """
    Validate an API key and return the associated user email.
    Checks the database or falls back to dev keys in DEV_MODE.
    """
    if not api_key:
        return None
    
    # Check database first
    user = get_user_by_api_key(api_key)
    if user:
        return user.get("email")
    
    # Fall back to dev keys if in DEV_MODE
    if DEV_MODE and api_key in DEV_API_KEYS:
        return DEV_API_KEYS[api_key]
    
    return None

def generate_api_key(length: int = 32) -> str:
    """
    Generate a secure random API key
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

async def get_api_key(request: Request) -> str:
    """
    Extract API key from request headers
    """
    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key missing",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key

async def get_current_user(request: Request = Depends()) -> str:
    """
    Validate API key and get current user email
    """
    api_key = await get_api_key(request)
    user_email = validate_api_key(api_key)
    
    if not user_email:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    return user_email 
