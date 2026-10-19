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


IFT_TOOL = {"type": "function", "function": {
    "name": "ift_odds", "description": "IFT odds",
    "parameters": {"type": "object", "properties": {"column": {"type": "integer"}}}}}


def test_tools_go_through_mcp_bridge(monkeypatch):
    captured = {}
    out = json.dumps({"type": "result", "is_error": False, "result": "ok", "usage": {}})
    monkeypatch.setattr(claude_cli_client.subprocess, "run", _fake_run(out, captured=captured))
    ClaudeCliClient("claude", timeout=5).create_chat(
        model="opus", messages=MESSAGES, tools=[IFT_TOOL], tool_context={"x": 1})
    cmd = captured["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""  # still no built-ins
    mcp = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["asl"]
    assert mcp["type"] == "http" and "/internal/mcp/" in mcp["url"]
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__asl"
    assert "mcp__asl__ift_odds" in captured["system"]
    # the session is closed once the CLI exits
    token = mcp["url"].rsplit("/", 1)[1]
    assert token not in mcp_bridge._sessions


# --- MCP bridge --------------------------------------------------------------

from app.asl import mcp_bridge


def test_bridge_lists_and_calls_tools(monkeypatch):
    seen = {}
    def fake_execute(name, args, context=None):
        seen.update(name=name, args=args, context=context)
        return {"ok": True}
    monkeypatch.setattr(mcp_bridge, "execute_tool", fake_execute)
    with mcp_bridge.open_session([IFT_TOOL], {"vsav_state": "S"}) as url:
        token = url.rsplit("/", 1)[1]
        init = mcp_bridge.handle(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "2025-03-26"}})
        assert init["result"]["protocolVersion"] == "2025-03-26"
        assert mcp_bridge.handle(token, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        tools = mcp_bridge.handle(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert tools["result"]["tools"][0]["name"] == "ift_odds"
        assert tools["result"]["tools"][0]["inputSchema"]["properties"]["column"]
        call = mcp_bridge.handle(token, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                          "params": {"name": "ift_odds", "arguments": {"column": 4}}})
        assert json.loads(call["result"]["content"][0]["text"]) == {"ok": True}
        assert seen == {"name": "ift_odds", "args": {"column": 4}, "context": {"vsav_state": "S"}}
        # tools outside the session's list are refused
        bad = mcp_bridge.handle(token, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                         "params": {"name": "search_rules", "arguments": {}}})
        assert "error" in bad
    with pytest.raises(KeyError):
        mcp_bridge.handle(token, {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})


def test_bridge_endpoint_refuses_proxied_and_unknown_tokens():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import mcp_bridge as api_bridge
    app = FastAPI()
    app.include_router(api_bridge.router)
    c = TestClient(app)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert c.post("/internal/mcp/nope", json=msg).status_code == 404
    with mcp_bridge.open_session([IFT_TOOL], None) as url:
        path = "/internal/mcp/" + url.rsplit("/", 1)[1]
        assert c.post(path, json=msg).status_code == 200
        # via nginx (X-Real-IP set) the endpoint does not exist
        assert c.post(path, json=msg, headers={"X-Real-IP": "1.2.3.4"}).status_code == 404


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
    # Every built-in tool is switched off; the model answers from the prompt.
    assert "--disable=shell_tool" in cmd and "--disable=unified_exec" in cmd
    assert 'web_search="disabled"' in cmd
    prompt = captured["kwargs"]["input"]
    assert MESSAGES[0]["content"] in prompt and MESSAGES[1]["content"] in prompt
    assert "--disable=code_mode_host" in cmd and "mcp_servers" not in " ".join(cmd)
    # API-key auth would bill the API account instead of the subscription.
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]


def test_codex_failure_raises_with_event_message(monkeypatch):
    out = json.dumps({"type": "turn.failed", "error": {"message": "usage limit reached"}})
    monkeypatch.setattr(codex_cli_client.subprocess, "run",
                        _fake_codex_run(out, None, returncode=1))
    with pytest.raises(RuntimeError, match="usage limit reached"):
        CodexCliClient("codex", timeout=5).create_chat(model="gpt-6-astra", messages=MESSAGES)


def test_codex_tools_go_through_mcp_bridge(monkeypatch):
    captured = {}
    out = json.dumps({"type": "turn.completed", "usage": {}})
    monkeypatch.setattr(codex_cli_client.subprocess, "run",
                        _fake_codex_run(out, "ok", captured=captured))
    CodexCliClient("codex", timeout=5).create_chat(
        model="gpt-6-astra", messages=MESSAGES, tools=[IFT_TOOL], tool_context={})
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    assert 'mcp_servers.asl.url="http://' in joined and "/internal/mcp/" in joined
    # Codex calls MCP tools through the code-mode host; shell stays off.
    assert "--disable=code_mode_host" not in cmd
    assert "--disable=shell_tool" in cmd and 'web_search="disabled"' in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only"
