import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import Base, engine, SessionLocal
from app.models import User
from app.core.auth import get_password_hash

# Create all tables
Base.metadata.create_all(bind=engine)

# Create admin user — configure via environment variables or pass as arguments
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL") or input("Admin email: ")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or input("Admin password: ")

db = SessionLocal()
existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
if not existing:
    user = User(
        email=ADMIN_EMAIL,
        hashed_password=get_password_hash(ADMIN_PASSWORD)
    )
    db.add(user)
    db.commit()
    print(f"Admin user '{ADMIN_EMAIL}' created.")
else:
    print(f"Admin user '{ADMIN_EMAIL}' already exists.")
db.close()
