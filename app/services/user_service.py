"""User service for database operations."""
from sqlalchemy.orm import Session
from app.models import User, Group
from typing import Optional


def get_group_by_name(db: Session, name: str) -> Optional[Group]:
    """Get a group by name ('admin' or 'users')."""
    return db.query(Group).filter(Group.name == name).first()


def is_admin(user: Optional[User]) -> bool:
    """Whether the user belongs to the 'admin' group."""
    return bool(user and user.group and user.group.name == "admin")


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email address."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, email: str, hashed_password: str, api_key: str = None) -> User:
    """Create a new user. New accounts land in the 'users' group."""
    users_group = get_group_by_name(db, "users")
    user = User(
        email=email,
        hashed_password=hashed_password,
        api_key=api_key,
        group_id=users_group.id if users_group else None,
    )
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


def delete_user(db: Session, user_id: int) -> bool:
    """Delete a user and clean up related records.

    Removes the user's chat conversations (and their messages) and answer
    feedback, and clears any invitation references to the user, since none of
    these have cascade-delete configured at the ORM level. Returns True if a
    user was deleted, False if no user with that id exists.
    """
    from app.models import Invitation, AnswerFeedback
    from app.models.chat import ChatConversation, ChatMessage

    user = get_user_by_id(db, user_id)
    if not user:
        return False

    # Delete chat messages belonging to the user's conversations, then the
    # conversations themselves (no DB-level cascade is configured).
    conversation_ids = [
        c.id for c in db.query(ChatConversation.id)
        .filter(ChatConversation.user_id == user_id)
        .all()
    ]
    if conversation_ids:
        db.query(ChatMessage).filter(
            ChatMessage.conversation_id.in_(conversation_ids)
        ).delete(synchronize_session=False)
        db.query(ChatConversation).filter(
            ChatConversation.user_id == user_id
        ).delete(synchronize_session=False)

    # Delete the user's answer feedback.
    db.query(AnswerFeedback).filter(
        AnswerFeedback.user_id == user_id
    ).delete(synchronize_session=False)

    # Clear invitation references so the FK constraint isn't violated, while
    # preserving the invitation history.
    db.query(Invitation).filter(
        Invitation.used_by_user_id == user_id
    ).update({Invitation.used_by_user_id: None}, synchronize_session=False)

    db.delete(user)
    db.commit()
    return True

