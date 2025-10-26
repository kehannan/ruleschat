# models.py
from sqlalchemy import Column, Integer, String, create_engine, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

DATABASE_URL = "sqlite:///./mysite.db"  # Changed from test.db
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String)
    api_key = Column(String, unique=True, index=True, nullable=True)

class Invitation(Base):
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
        return self.used_at is not None

class AnswerFeedback(Base):
    __tablename__ = "answer_feedback"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    thumbs_up = Column(Boolean, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def get_user_by_username(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == username).first()
    finally:
        db.close()

def update_user_profile(user_id: int, email: str = None, hashed_password: str = None, api_key: str = None):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
            
        if email is not None:
            user.email = email
        if hashed_password is not None:
            user.hashed_password = hashed_password
        if api_key is not None:
            user.api_key = api_key
            
        db.commit()
        return user
    finally:
        db.close()
