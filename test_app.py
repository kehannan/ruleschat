#!/usr/bin/env python
"""Quick test script to verify the refactored app works."""
import sys

def test_imports():
    """Test that all modules can be imported."""
    print("🧪 Testing imports...")
    try:
        from app import database
        from app import models
        from app.core import auth
        from app.services import user_service
        from app.api import auth as auth_router, user, chat
        print("  ✅ All imports successful")
        return True
    except Exception as e:
        print(f"  ❌ Import error: {e}")
        return False


def test_app_creation():
    """Test that the FastAPI app can be created."""
    print("\n🧪 Testing app creation...")
    try:
        from app.main import app
        print(f"  ✅ App created successfully")
        print(f"  ✅ {len(app.routes)} routes registered")
        return True
    except Exception as e:
        print(f"  ❌ App creation error: {e}")
        return False


def test_database():
    """Test database connection."""
    print("\n🧪 Testing database...")
    try:
        from app.database import SessionLocal, engine
        from app.models import User
        
        # Try to query (won't fail even if empty)
        db = SessionLocal()
        user_count = db.query(User).count()
        db.close()
        
        print(f"  ✅ Database connected")
        print(f"  ✅ Found {user_count} user(s) in database")
        return True
    except Exception as e:
        print(f"  ❌ Database error: {e}")
        return False


def test_auth_utilities():
    """Test authentication utilities."""
    print("\n🧪 Testing auth utilities...")
    try:
        from app.core.auth import get_password_hash, verify_password
        
        # Test password hashing
        password = "test_password_123"
        hashed = get_password_hash(password)
        verified = verify_password(password, hashed)
        
        if verified:
            print("  ✅ Password hashing works")
            return True
        else:
            print("  ❌ Password verification failed")
            return False
    except Exception as e:
        print(f"  ❌ Auth utilities error: {e}")
        return False


def test_config():
    """Test configuration loading."""
    print("\n🧪 Testing configuration...")
    try:
        from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE
        import os
        
        has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
        has_secret_key = bool(os.getenv("SECRET_KEY"))
        
        print(f"  ✅ Config loaded")
        print(f"  {'✅' if has_openai_key else '⚠️ '} OPENAI_API_KEY: {'Set' if has_openai_key else 'Not set'}")
        print(f"  {'✅' if has_secret_key else '⚠️ '} SECRET_KEY: {'Set' if has_secret_key else 'Not set'}")
        print(f"  ✅ Model: {DEFAULT_MODEL}")
        print(f"  ✅ Temperature: {TEMPERATURE}")
        
        return has_openai_key and has_secret_key
    except Exception as e:
        print(f"  ❌ Config error: {e}")
        return False


def show_routes():
    """Display all available routes."""
    print("\n📋 Available Routes:")
    try:
        from app.main import app
        
        routes_by_tag = {}
        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                tags = getattr(route, 'tags', ['untagged'])
                tag = tags[0] if tags else 'untagged'
                
                if tag not in routes_by_tag:
                    routes_by_tag[tag] = []
                
                methods = ','.join(sorted(route.methods)) if route.methods else ''
                routes_by_tag[tag].append((methods, route.path))
        
        for tag, routes in sorted(routes_by_tag.items()):
            print(f"\n  {tag.upper()}:")
            for methods, path in routes:
                if methods:  # Skip routes without methods
                    print(f"    {methods:15} {path}")
        
        return True
    except Exception as e:
        print(f"  ❌ Error showing routes: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("🚀 ASL Rules Assistant - Test Suite")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_app_creation,
        test_database,
        test_auth_utilities,
        test_config,
    ]
    
    results = []
    for test in tests:
        result = test()
        results.append(result)
    
    # Show routes
    show_routes()
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    
    print(f"\nTests passed: {passed}/{total}")
    
    if passed == total:
        print("\n✅ All tests passed! Your app is ready to run.")
        print("\n🚀 Start the server with:")
        print("   python run.py")
        print("\n📖 Then visit:")
        print("   http://localhost:8000/docs")
        return 0
    else:
        print(f"\n⚠️  Some tests failed. Check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

