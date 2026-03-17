import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import User
from app.database import SessionLocal
from app.core.auth import get_password_hash

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL") or input("Admin email: ")
NEW_PASSWORD = os.environ.get("ADMIN_PASSWORD") or input("New password: ")

db = SessionLocal()
try:
    admin_user = db.query(User).filter(User.email == ADMIN_EMAIL).first()

    if admin_user:
        admin_user.hashed_password = get_password_hash(NEW_PASSWORD)
        db.commit()
        print(f"✅ Password reset successfully for '{ADMIN_EMAIL}'")
    else:
        print(f"❌ User '{ADMIN_EMAIL}' not found. Run scripts/init_db.py first.")

except Exception as e:
    print(f"❌ Error resetting password: {e}")
    db.rollback()
finally:
    db.close()
