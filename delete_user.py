# delete_user.py
from crud import SessionLocal, get_user_by_username
from models import User
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