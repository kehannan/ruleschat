import os
import logging
from openai import OpenAI
from typing import Dict, Any

class VectorStoreManager:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)
        self.vector_store_id = None
        
    def create_vector_store(self, name: str = "ASL Rules Vector Store") -> str:
        """Create a vector store for ASL rules documents"""
        try:
            response = self.client.vector_stores.create(
                name=name,
                expires_after={"anchor": "last_active_at", "days": 30}
            )
            self.vector_store_id = response.id
            logging.info(f"✅ Created vector store: {self.vector_store_id}")
            return self.vector_store_id
        except Exception as e:
            logging.error(f"❌ Error creating vector store: {e}")
            raise
    
    def upload_file_to_vector_store(self, file_path: str, vector_store_id: str = None) -> str:
        """Upload a file to the vector store"""
        if vector_store_id is None:
            vector_store_id = self.vector_store_id
            
        if not vector_store_id:
            raise ValueError("Vector store ID is required")
            
        try:
            # First upload the file to OpenAI
            with open(file_path, 'rb') as file:
                file_response = self.client.files.create(
                    file=file,
                    purpose="assistants"
                )
            
            # Then attach the file to the vector store
            response = self.client.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=file_response.id
            )
            logging.info(f"✅ Uploaded file to vector store: {response.id}")
            return response.id
        except Exception as e:
            logging.error(f"❌ Error uploading file to vector store: {e}")
            raise
    
    def setup_asl_vector_store(self, pdf_path: str) -> Dict[str, str]:
        """Complete setup for ASL vector store"""
        try:
            # Create vector store
            vector_store_id = self.create_vector_store()
            
            # Upload PDF to vector store
            file_id = self.upload_file_to_vector_store(pdf_path, vector_store_id)
            
            return {
                "vector_store_id": vector_store_id,
                "file_id": file_id,
                "pdf_path": pdf_path
            }
        except Exception as e:
            logging.error(f"❌ Error in setup: {e}")
            raise

# Global instance
vector_store_manager = None

def initialize_vector_store(api_key: str):
    """Initialize the vector store manager"""
    global vector_store_manager
    vector_store_manager = VectorStoreManager(api_key)
    return vector_store_manager

def get_vector_store_manager() -> VectorStoreManager:
    """Get the global vector store manager instance"""
    if vector_store_manager is None:
        raise RuntimeError("Vector store manager not initialized. Call initialize_vector_store() first.")
    return vector_store_manager 