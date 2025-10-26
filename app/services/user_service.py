"""User service for database operations."""
from sqlalchemy.orm import Session
from app.models import User
from typing import Optional


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email address."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, email: str, hashed_password: str, api_key: str = None) -> User:
    """Create a new user."""
    user = User(email=email, hashed_password=hashed_password, api_key=api_key)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user_profile(
    db: Session,
    user_id: int,
    email: str = None,
    hashed_password: str = None,
    api_key: str = None
) -> Optional[User]:
    """Update user profile information."""
    user = get_user_by_id(db, user_id)
    if not user:
        return None
    
    if email is not None:
        user.email = email
    if hashed_password is not None:
        user.hashed_password = hashed_password
    if api_key is not None:
        user.api_key = api_key
    
    db.commit()
    db.refresh(user)
    return user


def generate_api_key_for_user(db: Session, user_id: int, api_key: str) -> Optional[User]:
    """Generate and assign API key to user."""
    return update_user_profile(db, user_id, api_key=api_key)

