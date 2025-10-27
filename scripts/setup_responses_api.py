#!/usr/bin/env python3
"""
Setup script for Responses API with vector store
This script will:
1. Create a vector store
2. Upload the ASL rules PDF
3. Save the IDs to a config file
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import logging
import time
from dotenv import load_dotenv
from openai import OpenAI
from app.core.responses_api import initialize_vector_store
from typing import Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def main():
    """Main function to set up vector store"""
    logging.info("🚀 Initializing Vector Store...")
    
    # Load environment variables
    load_dotenv()
    
    try:
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in .env file")
        
        client = OpenAI(
            api_key=api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        # PDF file path - now in the evals-sft repository
        pdf_path = "../mysite2-evals-sft/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
        
        # Set up the vector store
        config_data = setup_asl_vector_store(client, pdf_path)
        
        # Save configuration to file
        with open("responses_api_config.json", "w") as f:
            json.dump(config_data, f, indent=4)
        
        logging.info(f"💾 Configuration saved to responses_api_config.json")
        
        # Test the API
        logging.info("\n🧪 Testing the setup with Responses API...")
        test_responses_api(client, config_data)
        
    except Exception as e:
        logging.error(f"Setup error: {e}")

def setup_asl_vector_store(client, pdf_path: str) -> Dict[str, str]:
    """Complete setup for ASL vector store"""
    try:
        # Create vector store
        vector_store_id = create_vector_store(client)
        
        # Upload PDF to vector store and wait for it to be ready
        file_id = upload_file_to_vector_store_and_wait(client, pdf_path, vector_store_id)
        
        return {
            "vector_store_id": vector_store_id,
            "file_id": file_id,
            "pdf_path": pdf_path
        }
    except Exception as e:
        logging.error(f"❌ Error in setup: {e}")
        raise

def upload_file_to_vector_store_and_wait(client, file_path: str, vector_store_id: str) -> str:
    """Upload a file, add it to the vector store, and wait for it to be ready."""
    if not vector_store_id:
        raise ValueError("Vector store ID is required")
        
    try:
        logging.info(f"📤 Uploading file '{file_path}' to OpenAI...")
        with open(file_path, 'rb') as file:
            file_response = client.files.create(
                file=file,
                purpose="assistants"
            )
        logging.info(f"✅ File uploaded to OpenAI with ID: {file_response.id}")

        logging.info(f"➕ Attaching file {file_response.id} to vector store {vector_store_id}...")
        vector_store_file = client.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=file_response.id
        )
        logging.info(f"✅ File attached to vector store.")

        logging.info(f"⏳ Waiting for file to be processed...")
        while True:
            vector_store_file = client.vector_stores.files.retrieve(
                vector_store_id=vector_store_id,
                file_id=file_response.id
            )
            if vector_store_file.status == 'completed':
                logging.info(f"✅ File processing complete.")
                break
            elif vector_store_file.status in ['failed', 'cancelled']:
                raise Exception(f"File processing failed with status: {vector_store_file.status}")
            
            logging.info(f"   Current status: {vector_store_file.status}... waiting 10 seconds.")
            time.sleep(10)
            
        return vector_store_file.id
    except Exception as e:
        logging.error(f"❌ Error during file upload and processing: {e}")
        raise

def create_vector_store(client) -> str:
    """Create a vector store for ASL rules documents"""
    logging.info("📚 Creating vector store...")
    try:
        response = client.vector_stores.create(
            name="ASL Rules Vector Store",
            expires_after={"anchor": "last_active_at", "days": 365}
        )
        logging.info(f"✅ Created vector store: {response.id}")
        return response.id
    except Exception as e:
        logging.error(f"❌ Error creating vector store: {e}")
        raise

def test_responses_api(client, config_data: Dict[str, Any]):
    """Test the Responses API with a sample query"""
    try:
        response = client.responses.create(
            model=config.DEFAULT_MODEL,
            input="What are the basic rules for movement in ASL?",
            instructions=config.ASL_SYSTEM_INSTRUCTIONS,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [config_data["vector_store_id"]],
            }]
        )
        
        if response.output_text:
            logging.info("✅ Test successful!")
            logging.info(f"📝 Response preview: {response.output_text[:200]}...")
        else:
            logging.warning("⚠️ No response received from test query.")
            
    except Exception as test_error:
        logging.error(f"❌ Test failed: {test_error}")

if __name__ == "__main__":
    main() 