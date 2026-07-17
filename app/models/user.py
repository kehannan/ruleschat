"""User and related database models."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
from app.database import Base


class Group(Base):
    """Access-control group. Two groups exist: 'admin' and 'users'.

    Seeded at startup (see _seed_groups in app/main.py); every user belongs
    to exactly one group. Admin-only features key off membership in 'admin'.
    """
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)


class User(Base):
    """User model for authentication and profile."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String)
    api_key = Column(String, unique=True, index=True, nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    # lazy="joined": user objects are often returned from short-lived sessions
    # (see get_current_user_from_request), so the group must load with the user.
    group = relationship("Group", lazy="joined")


class Invitation(Base):
    """Invitation model for user registration."""
    __tablename__ = "invitations"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True)
    email = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=7))
    used_at = Column(DateTime, nullable=True)
    used_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    @property
    def used(self):
        """Check if invitation has been used."""
        return self.used_at is not None


class AnswerFeedback(Base):
    """Feedback model for user responses to AI answers."""
    __tablename__ = "answer_feedback"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    thumbs_up = Column(Boolean, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

