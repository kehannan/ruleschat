"""Generate a new API key for a user and store it on their account."""
import os
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import User
from app.database import SessionLocal

USER_EMAIL = os.environ.get("USER_EMAIL") or input("User email: ")

# URL-safe token; ~43 chars of entropy
api_key = secrets.token_urlsafe(32)

db = SessionLocal()
try:
    user = db.query(User).filter(User.email == USER_EMAIL).first()

    if user:
        user.api_key = api_key
        db.commit()
        print(f"✅ Generated API key for '{USER_EMAIL}': {api_key}")
    else:
        print(f"❌ User '{USER_EMAIL}' not found. Run scripts/init_db.py first.")

except Exception as e:
    print(f"❌ Error generating API key: {e}")
    db.rollback()
finally:
    db.close()
