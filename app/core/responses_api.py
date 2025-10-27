import os
import logging
from openai import OpenAI
from typing import Dict, Any

class VectorStoreManager:
    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        self.vector_store_id = None
        
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