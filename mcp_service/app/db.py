import os
import sqlite3
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Path to the database - updated to point to test.db in the root directory
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.getenv("DB_PATH", os.path.join(project_root, "test.db"))
print(f"Using database at: {DB_PATH}")

def get_db_connection():
    """Get a connection to the SQLite database"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # This enables column access by name
    return conn

def find_user_table():
    """Find the user table in the database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Based on model.py, the table is "users"
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cursor.fetchone():
        # Get the columns to confirm structure
        cursor.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        print(f"Found users table with columns: {', '.join(column_names)}")
        conn.close()
        return "users"
    
    conn.close()
    return None

# Find the user table on module load
USER_TABLE = find_user_table()
if USER_TABLE:
    print(f"Found user table: {USER_TABLE}")
else:
    print("Warning: Could not find user table. Functionality may be limited.")
    USER_TABLE = "users"  # Default fallback

def get_user_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by their API key
    Returns None if no user is found with that API key
    """
    if not api_key:
        return None
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Dynamic query based on table structure
    try:
        cursor.execute(f"SELECT * FROM {USER_TABLE} WHERE api_key = ?", (api_key,))
        user = cursor.fetchone()
        
        conn.close()
        
        if user:
            return dict(user)  # Convert to dictionary
    except Exception as e:
        print(f"Error querying user by API key: {e}")
        conn.close()
    
    return None

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by their email
    Returns None if no user is found with that email
    """
    if not email:
        return None
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"SELECT * FROM {USER_TABLE} WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        conn.close()
        
        if user:
            return dict(user)  # Convert to dictionary
    except Exception as e:
        print(f"Error querying user by email: {e}")
        conn.close()
    
    return None

def update_user_api_key(user_id: int, api_key: str) -> bool:
    """
    Update a user's API key
    Returns True if successful, False otherwise
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"UPDATE {USER_TABLE} SET api_key = ? WHERE id = ?", (api_key, user_id))
        conn.commit()
        success = cursor.rowcount > 0
    except Exception as e:
        print(f"Error updating API key: {e}")
        success = False
    finally:
        conn.close()
    
    return success 
