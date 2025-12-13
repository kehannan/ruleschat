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
        # Include file_search_call.results to get RAG chunks
        include = ["file_search_call.results"] if any(t.get("type") == "file_search" for t in (tools or [])) else []
        
        return self.client.responses.create(
            model=model,
            input=input,
            instructions=instructions,
            temperature=temperature,
            stream=stream,
            tools=tools or [],
            include=include if include else None
        )
    
    def stream_response(
        self,
        model: str,
        input: str,
        instructions: str,
        temperature: float,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Stream a Responses API response and allow access to the final accumulated response.
        
        Usage:
            with client.stream_response(...) as stream:
                for event in stream: ...
                final = stream.get_final_response()
        
        Returns:
            ResponseStreamManager context manager
        """
        # Include file_search_call.results to get RAG chunks
        include = ["file_search_call.results"] if any(t.get("type") == "file_search" for t in (tools or [])) else []
        
        return self.client.responses.stream(
            model=model,
            input=input,
            instructions=instructions,
            temperature=temperature,
            tools=tools or [],
            include=include if include else None
        )

