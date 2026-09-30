#!/usr/bin/env python
"""
Tests for the reasoning-budget safeguards added after the 2026-09-26/29
empty-answer incidents: reasoning models spent the whole max_tokens budget
on hidden reasoning and returned finish_reason="length" with empty content.

- _require_content raises a readable error instead of streaming/saving "".
- MetaModelClient maps OpenRouter's {"effort": X} onto Meta's native
  reasoning_effort and drops the OpenRouter-only extras.
- deepseek-v4-flash and muse-spark-1.3 have per-model reasoning defaults.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl.openrouter_client import MetaModelClient
from app.services.asl_service import (
    _openrouter_reasoning_config,
    _require_content,
)


def _completion(content, finish_reason, reasoning_tokens=None, completion_tokens=None):
    details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    usage = SimpleNamespace(
        completion_tokens=completion_tokens, completion_tokens_details=details
    )
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def test_length_with_empty_content_raises_with_token_counts():
    resp = _completion("", "length", reasoning_tokens=8192, completion_tokens=8192)
    with pytest.raises(RuntimeError, match=r"8192 completion tokens, 8192 of them reasoning"):
        _require_content(resp, "deepseek/deepseek-v4-flash")


def test_length_with_content_passes():
    _require_content(_completion("truncated but present", "length"), "m")


def test_stop_with_empty_content_passes():
    # Empty on a clean stop is a model quirk, not a budget failure.
    _require_content(_completion("", "stop"), "m")


def _meta_client(monkeypatch):
    client = MetaModelClient(api_key="test")
    create = MagicMock(return_value="ok")
    monkeypatch.setattr(client.client.chat.completions, "create", create)
    return client, create


def test_meta_maps_effort_to_native_reasoning_effort(monkeypatch):
    client, create = _meta_client(monkeypatch)
    client.create_chat(
        model="muse-spark-1.3-contributor",
        messages=[],
        reasoning={"effort": "low"},
        provider={"sort": "throughput"},
    )
    kwargs = create.call_args.kwargs
    assert kwargs["reasoning_effort"] == "low"
    assert "extra_body" not in kwargs  # no reasoning/provider leak to Meta


def test_meta_drops_openrouter_only_reasoning_shapes(monkeypatch):
    client, create = _meta_client(monkeypatch)
    client.create_chat(model="muse-spark-1.3", messages=[], reasoning={"enabled": False})
    kwargs = create.call_args.kwargs
    assert "reasoning_effort" not in kwargs
    assert "extra_body" not in kwargs


def test_per_model_reasoning_defaults(monkeypatch):
    for var in (
        "OPENROUTER_REASONING_EFFORT",
        "OPENROUTER_REASONING_MAX_TOKENS",
        "OPENROUTER_REASONING_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    assert _openrouter_reasoning_config("deepseek/deepseek-v4-flash") == {"enabled": False}
    assert _openrouter_reasoning_config("meta/muse-spark-1.3-contributor") == {"effort": "low"}
    assert _openrouter_reasoning_config("meta/muse-spark-1.3") == {"effort": "low"}
