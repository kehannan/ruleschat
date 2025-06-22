#!/usr/bin/env python3
"""
Setup script for Responses API with vector store
This script will:
1. Create a vector store
2. Upload the ASL rules PDF
3. Save the IDs to a config file
"""

import os
import json
import logging
from dotenv import load_dotenv
from openai import OpenAI
from responses_api import initialize_vector_store

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def main():
    # Load environment variables
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ OPENAI_API_KEY not found in environment variables")
        return
    
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    # PDF file path - adjust this to your actual PDF path
    pdf_path = "evals/sources/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"  # Using existing ASL rules PDF
    
    if not os.path.exists(pdf_path):
        print(f"❌ PDF file not found: {pdf_path}")
        print("Please place your ASL rules PDF file in the project directory and update the path in this script.")
        return
    
    try:
        print("🚀 Initializing Vector Store...")
        
        # Initialize the vector store manager
        manager = initialize_vector_store(api_key)
        
        print("📚 Creating vector store...")
        vector_store_id = manager.create_vector_store()
        
        print("📄 Uploading PDF to vector store...")
        file_id = manager.upload_file_to_vector_store(pdf_path, vector_store_id)
        
        # Save configuration
        config = {
            "vector_store_id": vector_store_id,
            "file_id": file_id,
            "pdf_path": pdf_path
        }
        
        with open("responses_api_config.json", "w") as f:
            json.dump(config, f, indent=2)
        
        print("✅ Setup completed successfully!")
        print(f"📊 Vector Store ID: {vector_store_id}")
        print(f"📄 File ID: {file_id}")
        print("💾 Configuration saved to responses_api_config.json")
        
        # Test the setup with the new Responses API
        print("\n🧪 Testing the setup with Responses API...")
        try:
            response = client.responses.create(
                model="gpt-4o",
                input="What are the basic rules for movement in ASL?",
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                }]
            )
            
            if response.output_text:
                print("✅ Test successful!")
                print(f"📝 Response preview: {response.output_text[:200]}...")
            else:
                print("⚠️ No response received")
                
        except Exception as test_error:
            print(f"❌ Test failed: {test_error}")
        
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        logging.error(f"Setup error: {e}")

if __name__ == "__main__":
    main() 