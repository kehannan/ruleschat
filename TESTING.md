# Testing Guide for ASL Rules Assistant

## Quick Start Testing

### 1. Basic Smoke Test

First, verify the application starts without errors:

```bash
# Start the server
python run.py
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 2. Check API Documentation

Open your browser and visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

This shows all available endpoints and lets you test them interactively.

### 3. Test Basic Routes

Visit these URLs in your browser:

```
✅ Home page:       http://localhost:8000/
✅ Login page:      http://localhost:8000/login
✅ Chat page:       http://localhost:8000/ruleschat (requires login)
✅ Profile:         http://localhost:8000/profile (requires login)
```

## Manual Testing Steps

### Test 1: User Authentication

1. **Create a test user**:
   ```bash
   python scripts/create_user.py
   # Follow prompts to create a user
   ```

2. **Test login**:
   - Go to http://localhost:8000/login
   - Enter credentials
   - Should redirect to `/ruleschat`

3. **Test profile**:
   - Go to http://localhost:8000/profile
   - Check if user info displays
   - Test password change

4. **Test logout**:
   - Click logout
   - Should redirect to login
   - Verify you can't access protected pages

### Test 2: Chat Functionality

1. **Access chat page**:
   - Login first
   - Go to http://localhost:8000/ruleschat
   - Chat interface should load

2. **Test WebSocket connection**:
   - Open browser console (F12)
   - Type a question in the chat
   - Watch console for WebSocket messages
   - Verify response streams back

3. **Check logs**:
   ```bash
   # In terminal where server is running, you should see:
   INFO: 🔹 WebSocket connection established.
   INFO: ✅ Received question: [your question]
   INFO: 🟢 Starting Responses API...
   INFO: ✅ Response streamed successfully
   ```

### Test 3: Admin Functions (if you're admin)

1. **Create invitation**:
   ```bash
   python scripts/create_user.py --admin
   ```

2. **Admin panel**:
   - Visit http://localhost:8000/admin (requires admin)
   - Verify users and invitations are listed

## Automated Testing

### Test Application Import

```bash
# Test if app loads without errors
python -c "from app.main import app; print('✓ App loads successfully')"
```

### Test Individual Components

```bash
# Test models
python -c "from app.models import User, Invitation; print('✓ Models work')"

# Test database
python -c "from app.database import engine, SessionLocal; print('✓ Database works')"

# Test auth utilities
python -c "from app.core.auth import get_password_hash, verify_password; print('✓ Auth works')"

# Test services
python -c "from app.services.user_service import get_user_by_email; print('✓ Services work')"
```

### Command-line test helpers

```bash
# Test Responses API configuration
python test_responses_api.py

# Test feedback endpoint helpers
python test_feedback.py

# Test WebSocket (interactive)
python ws_test.py
```

## API Testing with curl

### Test Authentication

```bash
# Test login
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your-email@example.com&password=yourpassword"

# Should return redirect to /ruleschat
```

### Test API Endpoints

```bash
# Test feedback submission (requires authentication)
curl -X POST http://localhost:8000/api/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Test question",
    "answer": "Test answer",
    "thumbs_up": true,
    "comment": "Great answer!"
  }'
```

## Database Testing

### Check Database Contents

```bash
# Using SQLite command line
sqlite3 mysite.db

# List all tables
.tables

# View users
SELECT * FROM users;

# View invitations
SELECT * FROM invitations;

# Exit
.quit
```

### Or use Python

```python
from app.database import SessionLocal
from app.models import User, Invitation

db = SessionLocal()

# List all users
users = db.query(User).all()
for user in users:
    print(f"User: {user.email}, API Key: {user.api_key}")

# List invitations
invitations = db.query(Invitation).all()
for inv in invitations:
    print(f"Code: {inv.code}, Email: {inv.email}, Used: {inv.used}")

db.close()
```

## Common Issues & Solutions

### Issue: "Module not found" errors

**Solution**: Make sure you're in the project root directory
```bash
cd /Users/kevinhannan/projects/mysite2
python run.py
```

### Issue: Database errors

**Solution**: Initialize the database
```bash
python scripts/init_db.py
```

### Issue: OpenAI API errors

**Solution**: Check environment variables
```bash
# Verify .env file exists and has:
OPENAI_API_KEY=sk-...
OPENAI_ORG_ID=org-...
OPENAI_PROJECT_ID=proj-...
SECRET_KEY=your-secret-key
```

### Issue: WebSocket connection fails

**Solution**: 
1. Check browser console for errors
2. Verify responses_api_config.json exists
3. Check server logs for WebSocket messages

### Issue: Static files not loading

**Solution**: Verify static directory structure
```bash
ls -la static/
ls -la templates/
```

## Performance Testing

### Test Response Time

```bash
# Using curl to measure response time
time curl http://localhost:8000/

# Should be < 1 second for basic pages
```

### Monitor WebSocket Performance

1. Open browser DevTools → Network → WS tab
2. Send a chat message
3. Check response time and streaming behavior

## Security Testing

### Test Authentication Protection

```bash
# Try accessing protected page without login
curl -v http://localhost:8000/profile
# Should redirect to login

# Try accessing admin endpoints without admin role
curl -v http://localhost:8000/admin
# Should return 401 or 403
```

### Test API Key Generation

1. Generate API key in profile
2. Verify it's unique and secure (32 chars)
3. Test that old key is invalidated

## Integration Testing Checklist

- [ ] App starts without errors
- [ ] All routes are accessible
- [ ] Login/logout flow works
- [ ] Profile management works
- [ ] Chat interface loads
- [ ] WebSocket connection works
- [ ] Responses stream correctly
- [ ] Feedback submission works
- [ ] Database operations work
- [ ] Static files load correctly
- [ ] API documentation accessible
- [ ] No console errors in browser
- [ ] Server logs look healthy

## Next Steps

For production deployment, consider:

1. **Add proper unit tests** using pytest:
   ```bash
   pip install pytest pytest-asyncio httpx
   # Create tests/test_auth.py, tests/test_user.py, etc.
   ```

2. **Add CI/CD** with GitHub Actions

3. **Load testing** with tools like:
   - `locust` for load testing
   - `pytest-benchmark` for performance testing

4. **Security audit**:
   - `bandit` for security issues
   - `safety` for vulnerable dependencies

## Quick Verification Commands

Run these to verify everything works:

```bash
# 1. Check app structure
tree app/ -L 2

# 2. Test imports
python -c "from app.main import app; print(f'Routes: {len(app.routes)}')"

# 3. Start server
python run.py

# 4. In another terminal, test endpoints
curl http://localhost:8000/
curl http://localhost:8000/docs
```

If all these work, your application is ready! 🚀

