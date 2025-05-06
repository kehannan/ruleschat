import secrets
import string
import sqlite3

# Generate a secure random API key
alphabet = string.ascii_letters + string.digits
api_key = ''.join(secrets.choice(alphabet) for _ in range(32))

# Connect to the database
conn = sqlite3.connect('test.db')
cursor = conn.cursor()

# Get the first user (or specify username if you know it)
cursor.execute("UPDATE users SET api_key = ? WHERE id = 1", (api_key,))
conn.commit()
conn.close()

print(f"Generated API key: {api_key}")
print("The key has been saved to the database.")
