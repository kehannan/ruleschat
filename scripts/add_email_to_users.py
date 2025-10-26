import sqlite3

def add_email_column():
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    
    try:
        # Add email column to users table
        cursor.execute("ALTER TABLE users ADD COLUMN email VARCHAR;")
        conn.commit()
        print("Successfully added email column to users table")
        
        # Create index for email column
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);")
        conn.commit()
        print("Successfully created index for email column")
        
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_email_column() 
