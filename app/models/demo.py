"""Demo usage tracking for rate limiting anonymous users."""
from sqlalchemy import Column, Integer, String
from app.database import Base


class DemoUsage(Base):
    __tablename__ = "demo_usage"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String(64), nullable=False, index=True)
    date = Column(String(10), nullable=False)   # YYYY-MM-DD
    count = Column(Integer, default=0, nullable=False)
