"""Chat history service for managing conversations and messages."""
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.chat import ChatConversation, ChatMessage


def estimate_tokens(text: str) -> int:
    """Simple token estimation (4 chars ~ 1 token)."""
    return len(text) // 4


class ChatHistoryService:
    """Service for managing chat history and formatting for API calls."""
    
    # Leave room for system instructions (~2k) and response (~4k)
    MAX_HISTORY_TOKENS = 4000
    
    def create_conversation(
        self, 
        db: Session, 
        user_id: int, 
        first_question: str
    ) -> ChatConversation:
        """
        Create a new conversation with auto-generated title.
        
        Args:
            db: Database session
            user_id: Owner user ID
            first_question: First question to derive title from
            
        Returns:
            Created ChatConversation
        """
        # Generate title from first question (truncate if needed)
        title = first_question[:50] + "..." if len(first_question) > 50 else first_question
        
        conversation = ChatConversation(user_id=user_id, title=title)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation
    
    def get_conversation(
        self, 
        db: Session, 
        conversation_id: int, 
        user_id: int
    ) -> Optional[ChatConversation]:
        """
        Get conversation, verifying ownership.
        
        Args:
            db: Database session
            conversation_id: Conversation to retrieve
            user_id: User ID for ownership verification
            
        Returns:
            ChatConversation if found and owned by user, None otherwise
        """
        return db.query(ChatConversation).filter(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user_id,
            ChatConversation.is_active == True
        ).first()

    def get_conversation_any_owner(
        self,
        db: Session,
        conversation_id: int
    ) -> Optional[ChatConversation]:
        """Admin-only: get conversation without ownership check."""
        return db.query(ChatConversation).filter(
            ChatConversation.id == conversation_id,
            ChatConversation.is_active == True
        ).first()

    def get_user_conversations(
        self, 
        db: Session, 
        user_id: int, 
        limit: int = 50
    ) -> List[ChatConversation]:
        """
        Get user's recent conversations.
        
        Args:
            db: Database session
            user_id: User ID
            limit: Maximum number of conversations to return
            
        Returns:
            List of ChatConversation ordered by most recent first
        """
        return db.query(ChatConversation).filter(
            ChatConversation.user_id == user_id,
            ChatConversation.is_active == True
        ).order_by(ChatConversation.updated_at.desc()).limit(limit).all()
    
    def get_conversation_messages(
        self,
        db: Session,
        conversation_id: int
    ) -> List[ChatMessage]:
        """
        Get all messages for a conversation.
        
        Args:
            db: Database session
            conversation_id: Conversation ID
            
        Returns:
            List of ChatMessage ordered chronologically
        """
        return db.query(ChatMessage).filter(
            ChatMessage.conversation_id == conversation_id
        ).order_by(ChatMessage.created_at).all()
    
    def add_message(
        self,
        db: Session,
        conversation_id: int,
        role: str,
        content: str,
        rag_sources: dict = None,
        timing_data: dict = None,
        image_paths: List[str] = None,
    ) -> ChatMessage:
        """
        Add a message to a conversation.

        Args:
            db: Database session
            conversation_id: Conversation to add message to
            role: "user" or "assistant"
            content: Message text
            rag_sources: Optional RAG chunks used (assistant only)
            timing_data: Optional timing metrics (assistant only)
            image_paths: Optional list of stored image relative paths (user only)

        Returns:
            Created ChatMessage
        """
        message = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=estimate_tokens(content),
            rag_sources=rag_sources,
            timing_data=timing_data,
            image_paths=image_paths or None,
        )
        db.add(message)
        
        # Update conversation's updated_at timestamp
        db.query(ChatConversation).filter(
            ChatConversation.id == conversation_id
        ).update({"updated_at": message.created_at})
        
        db.commit()
        return message
    
    def delete_conversation(
        self,
        db: Session,
        conversation_id: int,
        user_id: int
    ) -> bool:
        """
        Soft-delete a conversation.
        
        Args:
            db: Database session
            conversation_id: Conversation to delete
            user_id: User ID for ownership verification
            
        Returns:
            True if deleted, False if not found
        """
        result = db.query(ChatConversation).filter(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user_id,
            ChatConversation.is_active == True
        ).update({"is_active": False})
        db.commit()
        return result > 0
    
    def format_history_for_api(
        self, 
        db: Session, 
        conversation_id: int
    ) -> str:
        """
        Format conversation history for OpenAI input, respecting token limits.
        
        Uses a sliding window of most recent messages that fit within
        MAX_HISTORY_TOKENS.
        
        Args:
            db: Database session
            conversation_id: Conversation to format
            
        Returns:
            Formatted history string ready to prepend to current question
        """
        # Get messages in reverse chronological order (most recent first)
        messages = db.query(ChatMessage).filter(
            ChatMessage.conversation_id == conversation_id
        ).order_by(ChatMessage.created_at.desc()).all()
        
        if not messages:
            return ""
        
        # Accumulate messages from most recent, respecting token limit
        selected = []
        total_tokens = 0
        
        for msg in messages:
            msg_tokens = msg.token_count or estimate_tokens(msg.content)
            if total_tokens + msg_tokens > self.MAX_HISTORY_TOKENS:
                break
            selected.append(msg)
            total_tokens += msg_tokens
        
        if not selected:
            return ""
        
        # Reverse to chronological order
        selected.reverse()
        
        # Format with clear structure
        formatted = "=== Previous conversation ===\n\n"
        for msg in selected:
            role_label = "User" if msg.role == "user" else "Assistant"
            formatted += f"{role_label}: {msg.content}\n\n"
        
        formatted += "=== Current question ===\n"
        return formatted


# Singleton instance
_chat_history_service: Optional[ChatHistoryService] = None


def get_chat_history_service() -> ChatHistoryService:
    """Get the global ChatHistoryService instance."""
    global _chat_history_service
    if _chat_history_service is None:
        _chat_history_service = ChatHistoryService()
    return _chat_history_service




