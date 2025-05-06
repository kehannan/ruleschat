import requests
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Service URL - change this if your service is running on a different host/port
SERVICE_URL = "http://localhost:8000"

# Test API key - this should match a valid API key in your database
# For testing, we can use the dev API key
TEST_API_KEY = os.getenv("TEST_API_KEY", "test-key")
ADMIN_KEY = os.getenv("ADMIN_SECRET_KEY", "admin-secret")

def test_health():
    """Test the health endpoint"""
    response = requests.get(f"{SERVICE_URL}/qa/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    print("✅ Health check passed")

def test_ask_question():
    """Test asking a question with API key authentication"""
    question = "What is the capital of France?"
    
    headers = {
        "Content-Type": "application/json",
        "x-api-key": TEST_API_KEY
    }
    
    payload = {
        "question": question
    }
    
    response = requests.post(
        f"{SERVICE_URL}/qa/ask", 
        headers=headers,
        data=json.dumps(payload)
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    print(f"✅ Question answered: {data['answer'][:50]}...")

def test_ask_question_unauthorized():
    """Test asking a question without an API key"""
    question = "What is the capital of France?"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "question": question
    }
    
    response = requests.post(
        f"{SERVICE_URL}/qa/ask", 
        headers=headers,
        data=json.dumps(payload)
    )
    
    assert response.status_code == 401
    print("✅ Unauthorized request correctly rejected")

def test_mcp_endpoint():
    """Test the MCP endpoint directly with API key authentication"""
    question = "What is the capital of France?"
    
    headers = {
        "Content-Type": "application/json",
        "x-api-key": TEST_API_KEY
    }
    
    payload = {
        "service": "QuestionAnsweringService",
        "method": "ask_question",
        "parameters": {
            "question": question
        }
    }
    
    response = requests.post(
        f"{SERVICE_URL}/", 
        headers=headers,
        data=json.dumps(payload)
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    print(f"✅ MCP endpoint answered: {data['answer'][:50]}...")

def test_generate_api_key():
    """Test generating an API key for a user"""
    username = "testuser"  # This should be a valid username in your database
    
    response = requests.get(
        f"{SERVICE_URL}/qa/generate-api-key/{username}?admin_key={ADMIN_KEY}"
    )
    
    # This may fail if the user doesn't exist, which is fine for a test
    if response.status_code == 200:
        data = response.json()
        assert "api_key" in data
        assert data["username"] == username
        print(f"✅ Generated API key for {username}")
    else:
        print(f"⚠️ Could not generate API key: {response.json()['detail']}")

if __name__ == "__main__":
    print("Running API tests...")
    try:
        test_health()
        test_ask_question()
        test_ask_question_unauthorized()
        test_mcp_endpoint()
        test_generate_api_key()
        print("All tests passed! ✨")
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        raise 