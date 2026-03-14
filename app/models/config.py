"""Site-wide configuration stored in the database."""
from sqlalchemy import Column, String, Text
from app.database import Base


class SiteConfig(Base):
    __tablename__ = "site_config"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)
