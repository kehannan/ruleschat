"""Demo usage tracking for rate limiting anonymous users."""
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text
from datetime import datetime
from app.database import Base


class DemoUsage(Base):
    __tablename__ = "demo_usage"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String(64), nullable=False, index=True)
    date = Column(String(10), nullable=False)   # YYYY-MM-DD
    count = Column(Integer, default=0, nullable=False)


class DemoMessage(Base):
    __tablename__ = "demo_messages"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String(64), nullable=False, index=True)
    role = Column(String(20), nullable=False)   # "user" or "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    timing_data = Column(JSON)                  # assistant only
