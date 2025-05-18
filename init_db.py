from models import Base, engine, User, SessionLocal
from auth import get_password_hash

# Create all tables
Base.metadata.create_all(bind=engine)

# Create admin user if not exists
ADMIN_EMAIL = "kevin.hannan@gmail.com"
ADMIN_PASSWORD = "admin123"  # Change this after first login!

db = SessionLocal()
existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
if not existing:
    user = User(
        email=ADMIN_EMAIL,
        hashed_password=get_password_hash(ADMIN_PASSWORD)
    )
    db.add(user)
    db.commit()
    print(f"Admin user '{ADMIN_EMAIL}' created with password '{ADMIN_PASSWORD}'!")
else:
    print(f"Admin user '{ADMIN_EMAIL}' already exists.")
db.close() 