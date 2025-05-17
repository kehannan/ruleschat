# models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, create_engine, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import secrets

DATABASE_URL = "sqlite:///./test.db"  # Adjust if you use another DB
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    # This column has been added to the database
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String)
    api_key = Column(String, unique=True, index=True, nullable=True)

class Invite(Base):
    __tablename__ = "invites"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    token = Column(String, unique=True, index=True)
    is_used = Column(Boolean, default=False)
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

def get_user_by_username(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
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

def create_user(username: str, hashed_password: str, email: str = None):
    db = SessionLocal()
    try:
        user = User(username=username, hashed_password=hashed_password, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()

def get_user_by_email(email: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()

def create_invite(email: str):
    db = SessionLocal()
    try:
        token = secrets.token_urlsafe(16)
        invite = Invite(email=email, token=token)
        db.add(invite)
        db.commit()
        db.refresh(invite)
        return invite
    finally:
        db.close()

def get_invite_by_token(token: str):
    db = SessionLocal()
    try:
        return db.query(Invite).filter(Invite.token == token).first()
    finally:
        db.close()

def list_invites():
    db = SessionLocal()
    try:
        return db.query(Invite).all()
    finally:
        db.close()

def mark_invite_used(invite: Invite):
    db = SessionLocal()
    try:
        inv = db.query(Invite).filter(Invite.id == invite.id).first()
        if inv:
            inv.is_used = True
            db.commit()
    finally:
        db.close()

def revoke_invite(invite_id: int):
    db = SessionLocal()
    try:
        invite = db.query(Invite).filter(Invite.id == invite_id).first()
        if invite:
            invite.is_revoked = True
            db.commit()
    finally:
        db.close()

# Ensure tables exist
Base.metadata.create_all(bind=engine)
