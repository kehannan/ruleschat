#!/usr/bin/env python
"""
Tests for app.asl.claude_cli_client — the subscription-billed `claude -p` path.

subprocess.run is mocked; these verify the command/env we build and that the
CLI's JSON result is mapped onto the ChatCompletion shape asl_service reads.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import claude_cli_client
from app.asl.claude_cli_client import ClaudeCliClient, uses_claude_cli


MESSAGES = [
    {"role": "system", "content": "You are an ASL rules assistant."},
    {"role": "user", "content": "What is the FP of a 4-6-7?"},
]


def _fake_run(stdout, returncode=0, stderr="", captured=None):
    def run(cmd, **kwargs):
        if captured is not None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            sys_path = cmd[cmd.index("--system-prompt-file") + 1]
            captured["system"] = Path(sys_path).read_text()
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return run


def test_uses_claude_cli():
    assert uses_claude_cli("claude-cli/opus")
    assert not uses_claude_cli("anthropic/claude-opus")
    assert not uses_claude_cli(None)


def test_success_maps_to_chat_completion_shape(monkeypatch):
    captured = {}
    out = json.dumps({
        "type": "result", "is_error": False, "result": "4 FP (A1.21).",
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                  "cache_read_input_tokens": 100, "output_tokens": 7},
        "total_cost_usd": 0.42,
    })
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setattr(claude_cli_client.subprocess, "run", _fake_run(out, captured=captured))

    resp = ClaudeCliClient("claude", timeout=5).create_chat(
        model="opus", messages=MESSAGES, temperature=0.2, max_tokens=8192,
        reasoning=None, provider=None,
    )

    assert resp.choices[0].message.content == "4 FP (A1.21)."
    assert resp.usage.prompt_tokens == 115
    assert resp.usage.completion_tokens == 7
    assert resp.usage.cost == 0.0
    cmd = captured["cmd"]
    assert cmd[:4] == ["claude", "-p", "--model", "opus"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert captured["system"] == MESSAGES[0]["content"]
    assert captured["kwargs"]["input"] == MESSAGES[1]["content"]
    # API-key auth would bill the API account instead of the subscription.
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]


def test_error_result_raises_with_cli_message(monkeypatch):
    out = json.dumps({"type": "result", "is_error": True, "result": "Invalid API key · Please run /login"})
    monkeypatch.setattr(claude_cli_client.subprocess, "run", _fake_run(out, returncode=1))
    with pytest.raises(RuntimeError, match="Please run /login"):
        ClaudeCliClient("claude", timeout=5).create_chat(model="opus", messages=MESSAGES)


def test_tools_rejected():
    with pytest.raises(NotImplementedError):
        ClaudeCliClient("claude", timeout=5).create_chat(
            model="opus", messages=MESSAGES, tools=[{"type": "function"}]
        )


# --- Codex CLI (ChatGPT subscription) -------------------------------------

from app.asl import codex_cli_client
from app.asl.codex_cli_client import CodexCliClient, uses_codex_cli


def _fake_codex_run(stdout, answer, returncode=0, stderr="", captured=None):
    def run(cmd, **kwargs):
        if captured is not None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
        if answer is not None:
            Path(cmd[cmd.index("-o") + 1]).write_text(answer)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return run


def test_codex_success_maps_to_chat_completion_shape(monkeypatch):
    captured = {}
    out = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps({"type": "turn.completed",
                    "usage": {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 9}}),
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setattr(codex_cli_client.subprocess, "run",
                        _fake_codex_run(out, "4 FP (A1.21).\n", captured=captured))

    resp = CodexCliClient("codex", timeout=5).create_chat(
        model="gpt-6-astra", messages=MESSAGES, temperature=0.2, max_tokens=8192,
    )

    assert uses_codex_cli("codex-cli/gpt-6-astra")
    assert resp.choices[0].message.content == "4 FP (A1.21)."
    assert resp.usage.prompt_tokens == 120
    assert resp.usage.completion_tokens == 9
    cmd = captured["cmd"]
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-6-astra"]
    assert cmd[cmd.index("-s") + 1] == "read-only"
    prompt = captured["kwargs"]["input"]
    assert MESSAGES[0]["content"] in prompt and MESSAGES[1]["content"] in prompt
    # API-key auth would bill the API account instead of the subscription.
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]


def test_codex_failure_raises_with_event_message(monkeypatch):
    out = json.dumps({"type": "turn.failed", "error": {"message": "usage limit reached"}})
    monkeypatch.setattr(codex_cli_client.subprocess, "run",
                        _fake_codex_run(out, None, returncode=1))
    with pytest.raises(RuntimeError, match="usage limit reached"):
        CodexCliClient("codex", timeout=5).create_chat(model="gpt-6-astra", messages=MESSAGES)
