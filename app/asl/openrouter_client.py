"""
OpenRouter chat completion wrapper.

Thin shim around the OpenAI Python SDK pointed at the OpenRouter endpoint.
The OpenAI Chat Completions API is wire-compatible with OpenRouter, so we
get streaming, tool-calls, and the standard usage shape for free.

This is the path used by `/` model names (e.g. "deepseek/deepseek-v3.2",
"inception/mercury-2"). Retrieval is done separately by `app.asl.retrieval`
and the chunks are baked into the system prompt before calling here —
OpenRouter has no equivalent of OpenAI's server-side file_search tool.
"""

import os
from typing import List, Dict, Any, Optional
import logging

from openai import OpenAI


class OpenRouterClient:
    """Wrapper for OpenRouter's OpenAI-compatible chat completions endpoint."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str,
        app_name: Optional[str] = None,
        app_url: Optional[str] = None,
    ):
        self.client = OpenAI(api_key=api_key, base_url=self.BASE_URL)
        # OpenRouter recommends these headers for attribution; harmless if missing.
        self._extra_headers = {}
        if app_url:
            self._extra_headers["HTTP-Referer"] = app_url
        if app_name:
            self._extra_headers["X-Title"] = app_name

    def create_chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Make a chat-completions call.

        Returns an OpenAI ChatCompletion (non-streaming) or a stream object
        (streaming). Matches the OpenAI SDK shape so callers can use
        `response.choices[0].message.content` and `response.usage.prompt_tokens`.
        """
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers
        return self.client.chat.completions.create(**kwargs)


def build_openrouter_client_from_env() -> Optional["OpenRouterClient"]:
    """Initialize from OPENROUTER_API_KEY (+ optional attribution env vars).

    Returns None if no key is set — callers should treat that as "OpenRouter
    routing is unavailable on this deployment" rather than an error.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logging.info("OPENROUTER_API_KEY not set — OpenRouter routing disabled.")
        return None
    return OpenRouterClient(
        api_key=api_key,
        app_name=os.getenv("OPENROUTER_APP_NAME"),
        app_url=os.getenv("OPENROUTER_APP_URL"),
    )
