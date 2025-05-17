# crud.py
from sqlalchemy.orm import sessionmaker
from models import engine, Base, User
from passlib.context import CryptContext

# Create the database tables if they don't exist
Base.metadata.create_all(bind=engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_user(db, username: str, password: str):
    hashed_password = pwd_context.hash(password)
    user = User(username=username, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()
