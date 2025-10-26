from models import User, SessionLocal
from auth import get_password_hash

# Admin email from your environment
ADMIN_EMAIL = "kevin.hannan@gmail.com"
NEW_PASSWORD = "admin123"  # You can change this to whatever you want

db = SessionLocal()
try:
    # Find the admin user
    admin_user = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    
    if admin_user:
        # Update the password
        admin_user.hashed_password = get_password_hash(NEW_PASSWORD)
        db.commit()
        print(f"✅ Password reset successfully for admin user '{ADMIN_EMAIL}'")
        print(f"New password: '{NEW_PASSWORD}'")
        print("You can now log in with this password.")
    else:
        print(f"❌ Admin user '{ADMIN_EMAIL}' not found in database.")
        print("You may need to run python init_db.py first.")
        
except Exception as e:
    print(f"❌ Error resetting password: {e}")
    db.rollback()
finally:
    db.close() 