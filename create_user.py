# create_user.py
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
from models import engine, User

# Set up the database session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Set up password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_user(username: str, password: str):
    db = SessionLocal()
    hashed_password = pwd_context.hash(password)
    user = User(username=username, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user

if __name__ == "__main__":
    username = input("Enter username: ")
    password = input("Enter password: ")  # For production, use getpass.getpass() for security
    user = create_user(username, password)
    print(f"User {user.username} created with id: {user.id}")