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
        stream: bool,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        previous_response_id: Optional[str] = None
    ):
        """
        Create a Responses API response.

        Args:
            model: Model name
            input: Input question/text
            instructions: System instructions
            stream: Whether to stream response
            temperature: Temperature setting (omit for models that don't support it)
            tools: List of tools (file_search, web_search)
            previous_response_id: Optional ID of previous response for multi-turn

        Returns:
            Response object (streaming or non-streaming)
        """
        # Include file_search_call.results to get RAG chunks
        include = ["file_search_call.results"] if any(t.get("type") == "file_search" for t in (tools or [])) else []

        # Build kwargs
        kwargs = {
            "model": model,
            "input": input,
            "instructions": instructions,
            "stream": stream,
            "tools": tools or [],
            "include": include if include else None
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Add previous_response_id if provided
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        return self.client.responses.create(**kwargs)
    
    def stream_response(
        self,
        model: str,
        input: str,
        instructions: str,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        previous_response_id: Optional[str] = None
    ):
        """
        Stream a Responses API response and allow access to the final accumulated response.

        Usage:
            with client.stream_response(...) as stream:
                for event in stream: ...
                final = stream.get_final_response()

        Args:
            previous_response_id: Optional ID of a previous response for multi-turn
                continuation (e.g. submitting function_call_output items). When set,
                `input` may be the list of function outputs rather than a question.

        Returns:
            ResponseStreamManager context manager
        """
        # Include file_search_call.results to get RAG chunks
        include = ["file_search_call.results"] if any(t.get("type") == "file_search" for t in (tools or [])) else []

        kwargs = {
            "model": model,
            "input": input,
            "instructions": instructions,
            "tools": tools or [],
            "include": include if include else None
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        return self.client.responses.stream(**kwargs)

