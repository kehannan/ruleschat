"""Authentication and JWT utilities."""
import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.user_service import get_user_by_email

# Load environment variables
load_dotenv()

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Password context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[int] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + timedelta(minutes=expires_delta)
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Get the current user from the request cookie."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    
    payload = decode_access_token(token)
    if not payload:
        return None
    
    email = payload.get("sub")
    if not email:
        return None
    
    user = get_user_by_email(db, email)
    return user


async def require_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Dependency that requires a logged-in user."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return current_user


def require_entitlement(feature: str, api: bool = False, no_access_url: str = "/"):
    """Dependency factory: the current user must hold `feature` (admins always do).

    Page routes get a redirect — /login when anonymous, `no_access_url` when
    signed in without the grant — so a shared link lands somewhere useful.
    API routes (`api=True`) get 401/403 instead; a redirect to an HTML page is
    no use to fetch(). Feature names live in app/services/entitlements.py.
    """
    from app.services.entitlements import has  # local: entitlements imports models

    async def dependency(current_user: Optional[User] = Depends(get_current_user)) -> User:
        if current_user is not None and has(current_user, feature):
            return current_user
        if api:
            if current_user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                    detail="Not authenticated")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Requires access to '{feature}'")
        target = "/login" if current_user is None else no_access_url
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER,
                            headers={"Location": target})

    return dependency


async def get_optional_user(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Get current user if authenticated, None otherwise (no error raised)."""
    return await get_current_user(request, db)

