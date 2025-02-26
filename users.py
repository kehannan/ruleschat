# users.py

class User:
    def __init__(self, username: str, hashed_password: str):
        self.username = username
        self.hashed_password = hashed_password

# A fake in-memory "database"
fake_users_db = {
    "alice": User("alice", "$2b$12$EXAMPLEHASHEDPASSWORD"),
    "bob": User("bob", "$2b$12$EXAMPLEHASHEDPASSWORD"),
}

def get_user_by_username(username: str):
    return fake_users_db.get(username)