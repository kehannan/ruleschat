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
        timeout: Optional[float] = None,
    ):
        # A per-request timeout turns a provider that hangs (OpenRouter routes
        # across providers of wildly varying speed) into a fast failure the
        # caller can retry, instead of a multi-minute stall. Default from
        # OPENROUTER_TIMEOUT (seconds); None => SDK default (~600s).
        if timeout is None:
            env_t = os.getenv("OPENROUTER_TIMEOUT")
            timeout = float(env_t) if env_t else None
        client_kwargs = {"api_key": api_key, "base_url": self.BASE_URL}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        self.client = OpenAI(**client_kwargs)
        # OpenRouter recommends these headers for attribution; harmless if missing.
        self._extra_headers = {}
        if app_url:
            self._extra_headers["HTTP-Referer"] = app_url
        if app_name:
            self._extra_headers["X-Title"] = app_name

    def create_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ):
        """
        Make a chat-completions call.

        Returns an OpenAI ChatCompletion (non-streaming) or a stream object
        (streaming). Matches the OpenAI SDK shape so callers can use
        `response.choices[0].message.content` and `response.usage.prompt_tokens`.

        `tools` / `tool_choice` use the OpenAI Chat Completions function-calling
        shape (tools nested under {"type": "function", "function": {...}}). They
        let the OpenRouter path run the same agentic calculator loop the OpenAI
        Responses path uses; omit them for a plain RAG call.

        `reasoning` is OpenRouter's unified reasoning control, e.g.
        {"effort": "low"} or {"max_tokens": 4000} or {"enabled": False}. It's
        passed through `extra_body` (not a native OpenAI param). Bounding it
        matters for reasoning models like z-ai/glm-5.2, which otherwise emit
        tens of thousands of hidden reasoning tokens per question.

        `provider` is OpenRouter's provider-routing control, e.g.
        {"sort": "throughput"} or {"order": ["deepinfra"], "allow_fallbacks":
        True}. Steers away from slow/flaky providers — also passed via
        extra_body.
        """
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if stream:
            # Usage arrives on the final chunk only when asked for.
            kwargs["stream_options"] = {"include_usage": True}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        extra_body: Dict[str, Any] = {}
        if reasoning is not None:
            extra_body["reasoning"] = reasoning
        if provider is not None:
            extra_body["provider"] = provider
        if extra_body:
            kwargs["extra_body"] = extra_body
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


class MetaModelClient(OpenRouterClient):
    """Meta Model API (Muse Spark) — same OpenAI-compatible wire format.

    Base URL per the official docs (dev.meta.ai/docs/getting-started/overview);
    override with META_API_BASE_URL if Meta moves it. Model ids on this
    endpoint carry no vendor prefix ("muse-spark-1.1") — the service layer's
    "meta/" prefix exists only to route here and is stripped before calling.
    OpenRouter-specific extras (reasoning, provider) must not be passed.
    """
    BASE_URL = "https://api.meta.ai/v1"

    def __init__(self, api_key: str, timeout: Optional[float] = None):
        base_override = os.getenv("META_API_BASE_URL")
        if base_override:
            self.BASE_URL = base_override
        super().__init__(api_key=api_key, timeout=timeout)

    def create_chat(self, *args, **kwargs):
        # Meta rejects named/required tool_choice with a 400 — only "auto" is
        # supported (as of 2026-07). Downgrade forced choices; the tool
        # instructions in the prompt still steer the model.
        tc = kwargs.get("tool_choice")
        if tc is not None and tc != "auto":
            logging.info("Meta API: downgrading tool_choice %s -> 'auto'", tc)
            kwargs["tool_choice"] = "auto"
        return super().create_chat(*args, **kwargs)


def build_meta_client_from_env() -> Optional["MetaModelClient"]:
    """Initialize from META_API_KEY. Returns None if no key is set."""
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        logging.info("META_API_KEY not set — Meta Model API routing disabled.")
        return None
    return MetaModelClient(api_key=api_key)
