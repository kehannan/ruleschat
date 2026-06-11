#!/usr/bin/env python
"""
Tests for the agentic UI tools (ift_odds / ift_attack) and the streaming
agentic loop in ASLService.

No network calls: the loop is driven by a fake Responses client. Runnable
directly (`python tests/test_agentic_tools.py`) or under pytest.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import ift
from app.asl.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS, execute_tool, ift_odds
from app.services.asl_service import (
    ASLService,
    _output_function_calls,
    _extract_rag_sources_from_output,
)


def _schema(name):
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)


# --------------------------------------------------------------------------- #
# Tool wrappers
# --------------------------------------------------------------------------- #

def test_ift_odds_valid_and_strips_cells():
    r = ift_odds(column=16, drm=2, cowering="regular")
    assert "cells" not in r, "UI-only heatmap should be stripped from tool output"
    assert r["column"] == 16 and r["drm"] == 2 and r["cowering"] == "regular"
    probs = [d["probability"] for d in r["distribution"]]
    assert abs(sum(probs) - 1.0) < 0.01, f"probabilities should sum to ~1, got {sum(probs)}"


def test_ift_odds_invalid_column_raises():
    try:
        ift_odds(column=7)  # not a real IFT column
    except ValueError:
        return
    raise AssertionError("ift_odds(column=7) should have raised ValueError")


def test_schemas_enums_match_engines():
    """Schema enums must mirror the engines so the model only sends valid values."""
    assert _schema("ift_odds")["parameters"]["properties"]["column"]["enum"] == ift.valid_columns()
    assert _schema("ift_odds")["parameters"]["properties"]["cowering"]["enum"] == list(ift.COWERING_SHIFT.keys())


def test_registry_only_ui_tools():
    assert set(TOOL_FUNCTIONS) == {"ift_odds", "ift_attack", "resolve_attack"}, \
        "hand-rolled calculators should be retired"


def test_execute_tool_unknown_raises():
    try:
        execute_tool("calculate_drm", {})  # retired
    except ValueError:
        return
    raise AssertionError("execute_tool should reject a retired/unknown tool name")


# --------------------------------------------------------------------------- #
# Streaming agentic loop (mocked Responses client)
# --------------------------------------------------------------------------- #

class _FakeEvent:
    def __init__(self, type, delta=None):
        self.type = type
        self.delta = delta


class _FakeUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _FakeFinal:
    def __init__(self, id, output, usage):
        self.id = id
        self.output = output
        self.usage = usage


class _FakeStream:
    def __init__(self, events, final):
        self._events = events
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        return self._final


class _FakeClient:
    """Turn 1 calls ift_odds (silent); turn 2 streams the final answer."""
    def __init__(self):
        self.calls = []

    def stream_response(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            output = [{
                "type": "function_call", "call_id": "call_1",
                "name": "ift_odds", "arguments": '{"column": 16, "drm": 2}',
            }]
            return _FakeStream(
                events=[_FakeEvent("response.file_search_call.completed")],
                final=_FakeFinal("resp_1", output, _FakeUsage(100, 0)),
            )
        output = [{"type": "message", "content": [{"type": "output_text", "text": "About 30%."}]}]
        return _FakeStream(
            events=[_FakeEvent("response.output_text.delta", delta="About "),
                    _FakeEvent("response.output_text.delta", delta="30%.")],
            final=_FakeFinal("resp_2", output, _FakeUsage(50, 8)),
        )


class _FakeService:
    """Minimal stand-in; the handler only touches self.client."""
    def __init__(self):
        self.client = _FakeClient()


def test_agentic_loop_resolves_tool_then_streams():
    svc = _FakeService()
    gen, timing = ASLService._handle_agentic_streaming_response(
        svc,
        input_data="What are the odds on the 16 column at +2?",
        instructions="sys",
        model="gpt-5.4",
        temperature=0.2,
        tools=[],
        api_call_start_time=time.time(),
        return_timing=True,
    )
    text = "".join(gen)  # consume generator (fills timing)

    assert text == "About 30%.", f"final answer should stream through, got {text!r}"
    assert timing["tools_called"] == ["ift_odds"]
    assert timing["input_tokens"] == 150 and timing["output_tokens"] == 8
    assert timing["ttft_ms"] is not None
    # second turn must continue from the first response
    assert svc.client.calls[1]["previous_response_id"] == "resp_1"
    # exactly two turns: one tool, one answer
    assert len(svc.client.calls) == 2


def test_agentic_loop_tool_error_is_caught():
    """A failing tool shouldn't crash the loop; it submits an error output."""
    class _ErrClient(_FakeClient):
        def stream_response(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                output = [{
                    "type": "function_call", "call_id": "c", "name": "ift_odds",
                    "arguments": '{"column": 999}',  # invalid -> ValueError in engine
                }]
                return _FakeStream([], _FakeFinal("r1", output, _FakeUsage(1, 0)))
            output = [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]
            return _FakeStream([_FakeEvent("response.output_text.delta", delta="done")],
                               _FakeFinal("r2", output, _FakeUsage(1, 1)))

    svc = _FakeService()
    svc.client = _ErrClient()
    gen, timing = ASLService._handle_agentic_streaming_response(
        svc, input_data="q", instructions="s", model="m", temperature=None,
        tools=[], api_call_start_time=time.time(), return_timing=True,
    )
    text = "".join(gen)
    assert text == "done"
    assert timing["tools_called"] == ["ift_odds"]  # attempted, even though it errored


def test_output_helpers():
    output = [
        {"type": "function_call", "call_id": "x", "name": "ift_odds", "arguments": "{}"},
        {"type": "file_search_call", "results": [{"text": "A6.4 ...", "filename": "rulebook", "score": 0.9}]},
    ]
    calls = _output_function_calls(output)
    assert len(calls) == 1 and calls[0]["name"] == "ift_odds"
    rag = _extract_rag_sources_from_output(output)
    assert len(rag) == 1 and rag[0]["content"].startswith("A6.4")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
