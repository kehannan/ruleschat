#!/usr/bin/env python3
"""
Test script for the new chat completions API with PDF file.
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_chat_completion():
    """Test the new chat completions API with ASL PDF."""
    
    # Initialize OpenAI client
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    # ASL PDF file ID — set via environment variable or replace with your own
    ASL_PDF_FILE_ID = os.environ.get("ASL_PDF_FILE_ID", "")
    if not ASL_PDF_FILE_ID:
        print("❌ Set ASL_PDF_FILE_ID environment variable to your uploaded PDF file ID.")
        return
    
    try:
        print("Testing chat completion with ASL PDF...")
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user", 
                    "content": [
                        {"type": "file", "file": {"file_id": ASL_PDF_FILE_ID}},
                        {"type": "text", "text": "What happens if I roll a 12 on a morale check?"}
                    ]
                }
            ]
        )
        
        print("✅ API call successful!")
        print(f"Response: {response.choices[0].message.content}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print(f"Error type: {type(e)}")

if __name__ == "__main__":
    test_chat_completion() 