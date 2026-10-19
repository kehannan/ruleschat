"""
In-process MCP bridge — gives the subscription CLI models our function tools.

`claude -p` and `codex exec` run their own agent loop, so they can't take
Chat Completions tool schemas the way the API clients do. They can, however,
call tools on an MCP server. For one CLI call the client opens a session
here (`open_session`): a random token mapped to the tool schemas and the
request's server-side tool context (parsed .vsav state, retrieval client, …).
The CLI is pointed at `{MCP_BRIDGE_URL}/internal/mcp/{token}`
(app/api/mcp_bridge.py), which answers MCP's JSON-RPC over plain HTTP POST
and runs each call through `execute_tool` in this process — exactly what the
API agentic loops execute. The session is closed when the CLI exits.

The token is the only credential, so it's 256 bits and lives for one call.
"""

import json
import logging
import os
import secrets
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from app.asl.tools import execute_tool

SERVER_NAME = "asl"
_PROTOCOL_VERSION = "2025-06-18"

_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}


def bridge_url(token: str) -> str:
    base = os.getenv("MCP_BRIDGE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/internal/mcp/{token}"


def tool_names(tools: List[Dict[str, Any]]) -> List[str]:
    return [t["function"]["name"] for t in tools]


@contextmanager
def open_session(
    tools: List[Dict[str, Any]],
    tool_context: Optional[Dict[str, Any]],
) -> Iterator[str]:
    """Register Chat-Completions-shaped `tools` for one CLI call; yield the URL."""
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = {
            "tools": {t["function"]["name"]: t["function"] for t in tools},
            "context": tool_context,
            "calls": [],
        }
    try:
        yield bridge_url(token)
    finally:
        with _lock:
            session = _sessions.pop(token, None)
        if session and session["calls"]:
            logging.info("MCP bridge: CLI made %d tool call(s): %s",
                         len(session["calls"]), ", ".join(session["calls"]))


def _rpc_result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(token: str, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Answer one JSON-RPC message. None for notifications (no reply).

    Raises KeyError for an unknown/expired token.
    """
    with _lock:
        session = _sessions[token]
    msg_id = msg.get("id")
    method = msg.get("method")
    if msg_id is None:
        return None  # notifications/initialized, cancellations, …

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        return _rpc_result(msg_id, {
            "protocolVersion": requested or _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0"},
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [
            {
                "name": name,
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            for name, fn in session["tools"].items()
        ]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in session["tools"]:
            return _rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        session["calls"].append(name)
        try:
            result = execute_tool(name, args, context=session["context"])
            is_error = False
        except Exception as e:  # report to the model, like the API loops do
            logging.warning("MCP bridge: %s failed: %s", name, e)
            result = {"error": str(e)}
            is_error = True
        return _rpc_result(msg_id, {
            "content": [{"type": "text", "text": json.dumps(result)}],
            "isError": is_error,
        })
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")
