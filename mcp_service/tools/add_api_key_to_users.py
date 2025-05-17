import os
import sys
import secrets
import string
import sqlite3
from dotenv import load_dotenv

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Load environment variables
load_dotenv()

# Database path - updated to point to mysite2.db in the mysite2 directory
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(project_root, "mysite2", "mysite2.db")
print(f"Using database at: {DB_PATH}")

def generate_api_key(length: int = 32) -> str:
    """Generate a secure random API key"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def list_tables():
    """List all tables in the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    print("Tables in the database:")
    for table in tables:
        print(f"- {table[0]}")
        
        # Show columns in each table
        cursor.execute(f"PRAGMA table_info({table[0]})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  • {col[1]} ({col[2]})")
    
    conn.close()

def list_users():
    """List all users with their emails in the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Based on mysite2 model, the table is "users"
    table_name = "users"
    
    try:
        # Check if users table exists
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        if not cursor.fetchone():
            print(f"Table '{table_name}' does not exist.")
            conn.close()
            return
        
        # Get all columns to see what we can display
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Determine which fields we can show
        id_field = "id" if "id" in columns else "rowid"
        fields = [id_field]
        
        if "username" in columns:
            fields.append("username")
        
        if "email" in columns:
            fields.append("email")
        
        if "api_key" in columns:
            fields.append("api_key")
        
        # Get users
        cursor.execute(f"SELECT {', '.join(fields)} FROM {table_name}")
        users = cursor.fetchall()
        
        if not users:
            print("No users found in the database.")
            conn.close()
            return
        
        print(f"\nFound {len(users)} users:")
        print("-" * 80)
        
        # Print header
        header = " | ".join(fields)
        print(header)
        print("-" * len(header))
        
        # Print users
        for user in users:
            user_data = []
            for i, field in enumerate(fields):
                value = user[i] if i < len(user) else "N/A"
                # Truncate api_key for display
                if field == "api_key" and value:
                    value = f"{value[:10]}..." if value else "None"
                user_data.append(str(value) if value is not None else "None")
            print(" | ".join(user_data))
            
        print("-" * 80)
        
    except Exception as e:
        print(f"Error listing users: {e}")
    finally:
        conn.close()

def add_api_key_field_to_users():
    """Add an api_key column to the users table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # First list tables to help troubleshoot
    list_tables()
    
    # Based on mysite2 model, the table is "users"
    table_name = "users"
    
    # Check if api_key column already exists
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "api_key" not in columns:
        print(f"Adding api_key column to {table_name} table...")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN api_key TEXT")
        conn.commit()
        print("Column added successfully.")
    else:
        print("api_key column already exists.")
    
    conn.close()

def generate_api_key_for_user(email):
    """Generate and store an API key for a specific user"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Based on mysite2 model, the table is "users"
    table_name = "users"
    
    # Check if user exists
    cursor.execute(f"SELECT id FROM {table_name} WHERE email = ?", (email,))
    user = cursor.fetchone()
    
    if not user:
        print(f"Error: User with email '{email}' not found.")
        conn.close()
        return None
    
    # Generate a new API key
    api_key = generate_api_key()
    
    # Update user record with new API key
    cursor.execute(f"UPDATE {table_name} SET api_key = ? WHERE email = ?", (api_key, email))
    conn.commit()
    
    print(f"API key generated for user with email '{email}'.")
    conn.close()
    
    return api_key

def generate_api_keys_for_all_users():
    """Generate API keys for all users that don't already have one"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Based on mysite2 model, the table is "users"
    table_name = "users"
    
    # Check if api_key column exists, if not add it
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "api_key" not in columns:
        print(f"Adding api_key column to {table_name} table...")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN api_key TEXT")
        conn.commit()
        print("Column added successfully.")
    
    # Get all users without API keys
    cursor.execute(f"SELECT id, email FROM {table_name} WHERE api_key IS NULL OR api_key = ''")
    users = cursor.fetchall()
    
    if not users:
        print("All users already have API keys.")
        conn.close()
        return
    
    for user_id, email in users:
        api_key = generate_api_key()
        cursor.execute(f"UPDATE {table_name} SET api_key = ? WHERE id = ?", (api_key, user_id))
        print(f"Generated API key for user with email '{email}'")
    
    conn.commit()
    print(f"Generated API keys for {len(users)} users.")
    conn.close()

def show_user_api_key(email):
    """Show the API key for a specific user"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Based on mysite2 model, the table is "users"
    table_name = "users"
    
    cursor.execute(f"SELECT api_key FROM {table_name} WHERE email = ?", (email,))
    result = cursor.fetchone()
    
    if not result:
        print(f"Error: User with email '{email}' not found.")
    elif not result[0]:
        print(f"User with email '{email}' does not have an API key.")
    else:
        print(f"API key for user with email '{email}': {result[0]}")
    
    conn.close()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="API Key Management Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Add field command
    subparsers.add_parser("add-field", help="Add API key field to users table")
    
    # List tables command
    subparsers.add_parser("list-tables", help="List all tables in the database")
    
    # List users command
    subparsers.add_parser("list-users", help="List all users with their emails in the database")
    
    # Generate command
    generate_parser = subparsers.add_parser("generate", help="Generate API key for user")
    generate_parser.add_argument("email", help="Email of the user to generate API key for")
    
    # Generate all command
    subparsers.add_parser("generate-all", help="Generate API keys for all users without one")
    
    # Show command
    show_parser = subparsers.add_parser("show", help="Show API key for user")
    show_parser.add_argument("email", help="Email of the user to show API key for")
    
    # Parse arguments
    args = parser.parse_args()
    
    if args.command == "add-field":
        add_api_key_field_to_users()
    elif args.command == "list-tables":
        list_tables()
    elif args.command == "list-users":
        list_users()
    elif args.command == "generate":
        api_key = generate_api_key_for_user(args.email)
        if api_key:
            print(f"Generated API key: {api_key}")
    elif args.command == "generate-all":
        generate_api_keys_for_all_users()
    elif args.command == "show":
        show_user_api_key(args.email)
    else:
        parser.print_help() 
