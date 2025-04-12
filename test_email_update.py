from models import get_user_by_username, update_user_profile

def test_email_update():
    # Get the user
    username = "kevinhannan"  # Replace with an existing username if needed
    user = get_user_by_username(username)
    
    if not user:
        print(f"User {username} not found")
        return
    
    # Display current info
    print(f"Current user info: ID={user.id}, Username={user.username}, Email={user.email}")
    
    # Update email
    test_email = "test@example.com"
    update_user_profile(user.id, email=test_email)
    print(f"Updated user email to: {test_email}")
    
    # Verify by fetching the user again
    verified_user = get_user_by_username(username)
    print(f"Verified user info: ID={verified_user.id}, Username={verified_user.username}, Email={verified_user.email}")

if __name__ == "__main__":
    test_email_update() 