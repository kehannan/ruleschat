"""Chat conversation and message database models."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class ChatConversation(Base):
    """Represents a conversation thread."""
    __tablename__ = "chat_conversations"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255))  # Auto-generated from first question
    is_active = Column(Boolean, default=True)  # Soft delete support
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    messages = relationship(
        "ChatMessage", 
        back_populates="conversation",
        order_by="ChatMessage.created_at",
        lazy="dynamic"
    )


class ChatMessage(Base):
    """Individual messages in a conversation."""
    __tablename__ = "chat_messages"
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id"), 
                            nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    token_count = Column(Integer)  # Estimated tokens for history trimming
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Optional metadata (assistant messages only)
    rag_sources = Column(JSON)
    timing_data = Column(JSON)

    # Optional image attachments (user messages only); JSON list of relative paths
    # under data/uploads/, e.g. ["27/abc.jpg", "27/def.jpg"]. None when no images.
    image_paths = Column(JSON)

    # Optional VASL .vsav save attachments (user messages only); JSON list of
    # relative paths under data/uploads/, e.g. ["27/abc.vsav"]. None when none.
    vsav_paths = Column(JSON)

    # Relationship
    conversation = relationship("ChatConversation", back_populates="messages")




