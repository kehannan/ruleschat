# models.py
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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

def get_user_by_username(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()

def update_user_profile(user_id: int, email: str = None, hashed_password: str = None):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
            
        if email is not None:
            user.email = email
        if hashed_password is not None:
            user.hashed_password = hashed_password
            
        db.commit()
        return user
    finally:
        db.close()