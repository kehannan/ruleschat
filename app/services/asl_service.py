"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""

import os
import json
import logging
from typing import Optional
from openai import OpenAI
from pathlib import Path


class ASLService:
    """Service for getting ASL rule answers via Responses API."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        vector_store_id: Optional[str] = None,
        config_file: Optional[str] = None
    ):
        """
        Initialize ASL Service.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            vector_store_id: Vector store ID (defaults to loading from config file)
            config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        """
        # Load configuration
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        # Load vector store ID
        if vector_store_id:
            self.vector_store_id = vector_store_id
        else:
            self.vector_store_id = self._load_vector_store_id(config_file)
        
        if not self.vector_store_id:
            raise ValueError("Vector store ID is required. Please configure responses_api_config.json")
        
        # Import config from app.config (ensures consistency)
        from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE
        
        self.model = DEFAULT_MODEL
        self.temperature = TEMPERATURE
        self.system_instructions = ASL_SYSTEM_INSTRUCTIONS
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=self.api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        logging.info(f"ASL Service initialized with vector store: {self.vector_store_id}")
    
    def _load_vector_store_id(self, config_file: Optional[str] = None) -> Optional[str]:
        """Load vector store ID from config file."""
        if config_file:
            config_path = Path(config_file)
        else:
            # Default location: responses_api_config.json in project root
            config_path = Path(__file__).parent.parent.parent / "responses_api_config.json"
        
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    return config.get("vector_store_id")
            except Exception as e:
                logging.error(f"Error loading config file: {e}")
        else:
            logging.warning(f"Config file not found: {config_path}")
        
        return None
    
    def get_answer(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Get an answer to an ASL question.
        
        Args:
            question: The ASL question to ask
            stream: Whether to stream the response (returns generator if True)
            model: Override default model
            temperature: Override default temperature
            
        Returns:
            The answer as a string (or generator if stream=True)
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        
        try:
            # Use Responses API with file_search tool
            response = self.client.responses.create(
                model=model,
                input=question,
                instructions=self.system_instructions,
                temperature=temperature,
                stream=stream,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [self.vector_store_id],
                }]
            )
            
            if stream:
                # Return generator for streaming
                def stream_generator():
                    for event in response:
                        if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                            if hasattr(event, 'delta') and event.delta:
                                yield event.delta
                return stream_generator()
            else:
                # Return complete response
                return self._extract_response_text(response)
                
        except Exception as e:
            error_msg = f"Error getting response: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _extract_response_text(self, response) -> str:
        """Extract text from response object."""
        if hasattr(response, 'output_text') and response.output_text:
            return response.output_text
        elif hasattr(response, 'output') and response.output:
            if isinstance(response.output, str):
                return response.output
            elif isinstance(response.output, dict) and 'text' in response.output:
                return response.output['text']
        
        return "No response content found"


# Global service instance (lazy initialization)
_global_service: Optional[ASLService] = None


def get_asl_service(
    api_key: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    config_file: Optional[str] = None
) -> ASLService:
    """
    Get the global ASL service instance (singleton pattern).
    
    Args:
        api_key: Optional API key (only used if creating new instance)
        vector_store_id: Optional vector store ID (only used if creating new instance)
        config_file: Optional config file path (only used if creating new instance)
        
    Returns:
        ASLService instance
    """
    global _global_service
    
    if _global_service is None:
        _global_service = ASLService(
            api_key=api_key,
            vector_store_id=vector_store_id,
            config_file=config_file
        )
    
    return _global_service


def reset_service():
    """Reset the global service instance (useful for testing)."""
    global _global_service
    _global_service = None

