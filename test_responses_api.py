#!/usr/bin/env python3
"""
Test script for Responses API with file search functionality
This script tests the complete flow using the modern Responses API
"""

import os
import json
import logging
from dotenv import load_dotenv
from openai import OpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def test_responses_api():
    """Test the complete Responses API flow with file search"""
    
    # Load environment variables
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ OPENAI_API_KEY not found in environment variables")
        return False
    
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    try:
        print("🧪 Testing Responses API with file search...")
        
        # Load configuration
        if not os.path.exists("responses_api_config.json"):
            print("❌ No configuration found. Run setup_responses_api.py first.")
            return False
            
        with open("responses_api_config.json", "r") as f:
            config = json.load(f)
        
        print(f"📊 Using Vector Store: {config['vector_store_id']}")
        
        # Test conversation flow with Responses API
        print("\n💬 Testing Responses API conversation...")
        
        # Test question
        question = "What are the basic rules for movement in ASL?"
        print(f"❓ Question: {question}")
        
        response = client.responses.create(
            model="gpt-4o",
            input=question,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [config["vector_store_id"]],
            }]
        )
        
        if response.output_text:
            print("✅ Test successful!")
            print(f"📝 Response: {response.output_text[:500]}...")
            return True
        else:
            print("⚠️ No response received")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        logging.error(f"Test error: {e}")
        return False

def test_multiple_questions():
    """Test multiple questions using Responses API"""
    
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ OPENAI_API_KEY not found")
        return False
    
    client = OpenAI(api_key=api_key)
    
    try:
        with open("responses_api_config.json", "r") as f:
            config = json.load(f)
        
        print("\n🔄 Testing multiple questions with Responses API...")
        
        questions = [
            "What is the basic movement allowance for infantry?",
            "How does terrain affect movement?",
            "What are the rules for vehicle movement?"
        ]
        
        for i, question in enumerate(questions, 1):
            print(f"\n❓ Question {i}: {question}")
            
            response = client.responses.create(
                model="gpt-4o",
                input=question,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [config["vector_store_id"]],
                }]
            )
            
            if response.output_text:
                print(f"✅ Response {i}: {response.output_text[:200]}...")
            else:
                print(f"⚠️ No response for question {i}")
        
        return True
        
    except Exception as e:
        print(f"❌ Multiple questions test failed: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Starting Responses API Tests\n")
    
    # Test basic functionality
    success1 = test_responses_api()
    
    # Test multiple questions
    success2 = test_multiple_questions()
    
    print(f"\n📊 Test Results:")
    print(f"Basic functionality: {'✅ PASS' if success1 else '❌ FAIL'}")
    print(f"Multiple questions: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 and success2:
        print("\n🎉 All tests passed! Responses API is working correctly.")
    else:
        print("\n⚠️ Some tests failed. Check the configuration and try again.") 