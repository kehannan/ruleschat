#!/usr/bin/env python
"""
Tests for _require_choices in app.services.asl_service.

OpenRouter can return an error payload inside an HTTP 200 (e.g. an upstream
provider 502); the OpenAI SDK parses that into a ChatCompletion whose
`choices` is None. These tests verify we raise a RuntimeError carrying the
real provider message instead of a bare TypeError from `choices[0]`.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.asl_service import _require_choices


UPSTREAM_MSG = (
    "Upstream error from Alibaba: tool_choice is not supported in thinking mode"
)


def test_none_choices_with_dict_error_raises_with_message():
    response = SimpleNamespace(
        choices=None,
        error={"message": UPSTREAM_MSG, "code": 502},
    )
    with pytest.raises(RuntimeError, match="tool_choice is not supported"):
        _require_choices(response)


def test_none_choices_with_object_error_raises_with_message():
    response = SimpleNamespace(
        choices=None,
        error=SimpleNamespace(message=UPSTREAM_MSG, code=502),
    )
    with pytest.raises(RuntimeError, match="tool_choice is not supported"):
        _require_choices(response)


def test_none_choices_without_error_falls_back_to_model_dump():
    class FakeResponse:
        choices = None
        error = None

        def model_dump(self):
            return {"id": "gen-123", "choices": None}

    with pytest.raises(RuntimeError, match="gen-123"):
        _require_choices(FakeResponse())


def test_empty_choices_list_also_raises():
    response = SimpleNamespace(choices=[], error={"message": UPSTREAM_MSG})
    with pytest.raises(RuntimeError, match="no completion choices"):
        _require_choices(response)


def test_valid_choices_pass_through():
    response = SimpleNamespace(choices=[SimpleNamespace(message="hi")])
    _require_choices(response)  # must not raise
