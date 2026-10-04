"""
Claude Code CLI chat wrapper — inference billed to a Claude subscription.

Shells out to `claude -p` (headless Claude Code) instead of calling an API,
so usage counts against the logged-in Claude Pro/Max subscription rather than
API credits. Auth is whatever the CLI already has: the local login, or
CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) on a server.

This is the path used by "claude-cli/<model>" model names (e.g.
"claude-cli/opus"). It exposes the same `create_chat` surface as
`OpenRouterClient` and returns an OpenAI-ChatCompletion-shaped object, so the
plain retrieval path in asl_service works unchanged. All Claude Code tools,
settings, MCP servers and CLAUDE.md discovery are switched off — the CLI is
used as a bare completion endpoint. Function calling is not supported, so
these models are non-agentic.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

CLAUDE_CLI_PREFIX = "claude-cli/"

# With either of these set the CLI bills the API account instead of the
# subscription, which defeats the point of this path.
_API_AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def uses_claude_cli(model: Optional[str]) -> bool:
    return bool(model) and model.startswith(CLAUDE_CLI_PREFIX)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict) and c.get("text"):
            parts.append(c["text"])
    return "\n".join(parts)


class ClaudeCliClient:
    """Wrapper that runs one `claude -p` process per completion."""

    def __init__(self, binary: str, timeout: Optional[float] = None):
        self.binary = binary
        if timeout is None:
            timeout = float(os.getenv("CLAUDE_CLI_TIMEOUT", "300"))
        self.timeout = timeout

    def create_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        **_ignored: Any,
    ):
        """
        Run a completion. `model` is the CLI model name or alias ("opus").

        Sampling/routing kwargs the API clients take (temperature, max_tokens,
        reasoning, provider, …) have no CLI equivalent and are ignored.
        """
        if tools:
            raise NotImplementedError(
                "Claude CLI models do not support the agentic tool loop."
            )
        if stream:
            raise NotImplementedError("Claude CLI path is non-streaming.")

        system = "\n\n".join(
            _content_text(m.get("content")) for m in messages if m.get("role") == "system"
        )
        turns = [m for m in messages if m.get("role") != "system"]
        if len(turns) == 1:
            prompt = _content_text(turns[0].get("content"))
        else:
            prompt = "\n\n".join(
                f"{m.get('role', 'user').capitalize()}: {_content_text(m.get('content'))}"
                for m in turns
            )

        env = {k: v for k, v in os.environ.items() if k not in _API_AUTH_ENV}
        # Empty scratch cwd: no project CLAUDE.md or settings to pick up. The
        # system prompt goes through a file — it carries the retrieved chunks
        # and can exceed Linux's 128KB single-argument limit.
        with tempfile.TemporaryDirectory(prefix="claude-cli-") as workdir:
            sys_path = os.path.join(workdir, "system.txt")
            with open(sys_path, "w", encoding="utf-8") as f:
                f.write(system)
            cmd = [
                self.binary, "-p",
                "--model", model,
                "--system-prompt-file", sys_path,
                "--tools", "",
                "--strict-mcp-config",
                "--setting-sources", "",
                "--no-session-persistence",
                "--output-format", "json",
            ]
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    timeout=self.timeout, cwd=workdir, env=env,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"Claude CLI timed out after {self.timeout:.0f}s (model {model})."
                )

        try:
            result = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            result = None
        # `--output-format json` normally prints one result object; tolerate
        # the event-array form by taking its final result event.
        if isinstance(result, list):
            result = next(
                (e for e in reversed(result) if isinstance(e, dict) and e.get("type") == "result"),
                None,
            )
        if not isinstance(result, dict) or result.get("is_error") or proc.returncode != 0:
            detail = (
                (result or {}).get("result")
                or proc.stderr.strip()
                or proc.stdout.strip()[:500]
                or f"exit code {proc.returncode}"
            )
            raise RuntimeError(f"Claude CLI failed (model {model}): {detail}")

        u = result.get("usage") or {}
        input_tokens = (
            (u.get("input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
        )
        usage = SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=u.get("output_tokens") or 0,
            # Covered by the subscription — the CLI's total_cost_usd is only
            # an API-price estimate, not a charge.
            cost=0.0,
        )
        message = SimpleNamespace(content=result.get("result") or "", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage)


def build_claude_cli_client_from_env() -> Optional["ClaudeCliClient"]:
    """Locate the `claude` binary (CLAUDE_CLI_PATH, else PATH).

    Returns None if it isn't installed — callers treat that as "Claude CLI
    routing is unavailable on this deployment" rather than an error.
    """
    binary = os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude")
    if not binary:
        logging.info("claude CLI not found — Claude subscription routing disabled.")
        return None
    return ClaudeCliClient(binary=binary)
