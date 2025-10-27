# delete_user.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import User
from app.services.user_service import get_user_by_email
def delete_user(db, user):
    db.delete(user)
    db.commit()
if __name__ == "__main__":
    username = input("Enter username to delete: ")
    db = SessionLocal()
    user = get_user_by_username(db, username)
    if user:
         delete_user(db, user)
         print(f"User {username} deleted.")
    else:
         print("User not found.")
    db.close()
