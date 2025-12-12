"""OpenAI client wrapper for Responses API."""
import logging
from typing import Optional, List, Dict, Any
from openai import OpenAI

from app.asl.config import ASLConfig


class OpenAIResponsesClient:
    """Wrapper for OpenAI Responses API client."""
    
    def __init__(self, config: ASLConfig):
        """
        Initialize OpenAI client.
        
        Args:
            config: ASL configuration
        """
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            organization=config.org_id,
            project=config.project_id
        )
        logging.info(f"OpenAI client initialized with vector store: {config.vector_store_id}")
    
    def create_response(
        self,
        model: str,
        input: str,
        instructions: str,
        temperature: float,
        stream: bool,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Create a Responses API response.
        
        Args:
            model: Model name
            input: Input question/text
            instructions: System instructions
            temperature: Temperature setting
            stream: Whether to stream response
            tools: List of tools (file_search, web_search)
            
        Returns:
            Response object (streaming or non-streaming)
        """
        return self.client.responses.create(
            model=model,
            input=input,
            instructions=instructions,
            temperature=temperature,
            stream=stream,
            tools=tools or []
        )

