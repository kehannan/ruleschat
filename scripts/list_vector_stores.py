#!/usr/bin/env python3
"""
Script to list all vector stores on OpenAI
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def list_vector_stores():
    """List all vector stores on OpenAI"""
    try:
        # Initialize OpenAI client
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        print("🔍 Fetching vector stores from OpenAI...")
        
        # Get all vector stores
        vector_stores = client.vector_stores.list()
        
        print(f"\n📊 Found {len(vector_stores.data)} vector store(s):")
        print("=" * 80)
        
        for i, vs in enumerate(vector_stores.data, 1):
            print(f"\n{i}. Vector Store Details:")
            print(f"   ID: {vs.id}")
            print(f"   Name: {vs.name}")
            print(f"   Status: {vs.status}")
            print(f"   Created: {vs.created_at}")
            print(f"   Expires: {vs.expires_after}")
            
            # Get files in this vector store
            try:
                files = client.vector_stores.files.list(vector_store_id=vs.id)
                print(f"   Files: {len(files.data)} file(s)")
                
                for j, file in enumerate(files.data, 1):
                    print(f"     {j}. File ID: {file.id}")
                    print(f"        Status: {file.status}")
                    print(f"        Created: {file.created_at}")
                    
            except Exception as e:
                print(f"   Error getting files: {e}")
            
            print("-" * 40)
        
        # Also check your local config
        if os.path.exists("responses_api_config.json"):
            import json
            with open("responses_api_config.json", "r") as f:
                config = json.load(f)
            print(f"\n📁 Local config file:")
            print(f"   Vector Store ID: {config.get('vector_store_id', 'Not found')}")
            print(f"   File ID: {config.get('file_id', 'Not found')}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    list_vector_stores() 