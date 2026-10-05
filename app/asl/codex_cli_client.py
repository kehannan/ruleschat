"""
Codex CLI chat wrapper — inference billed to a ChatGPT subscription.

Shells out to `codex exec` (headless Codex) instead of calling the OpenAI API,
so usage counts against the ChatGPT plan the CLI is signed in with
(`codex login`, stored under CODEX_HOME) rather than API credits.

This is the path used by "codex-cli/<model>" model names (e.g.
"codex-cli/gpt-6-astra"). Same `create_chat` surface and ChatCompletion-shaped
return as `ClaudeCliClient`, so the plain retrieval path in asl_service works
unchanged. Unlike `claude -p`, Codex has no flag to replace its system prompt
(the instructions + retrieved chunks are sent as part of the prompt), but its
tools can be switched off per feature flag: every built-in tool is disabled
(see _DISABLED_FEATURES), web search is off, and as a fallback the agent runs
in a read-only sandbox in an empty scratch directory with no inherited env.
Function calling is not supported, so these models are non-agentic.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.asl.claude_cli_client import _content_text

CODEX_CLI_PREFIX = "codex-cli/"

# With either of these set the CLI can bill the API account instead of the
# ChatGPT subscription, which defeats the point of this path.
_API_AUTH_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")

# Codex feature flags (`codex features list`) for every tool the agent could
# otherwise call. The model must answer from the prompt alone — on prod it
# runs as root, so a shell tool would mean a prompt-injected question could
# read the server's .env.
_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "remote_plugin",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "image_generation",
    "skill_search",
    "tool_suggest",
    "sleep_tool",
    "shell_snapshot",
    # Still present after the above (verified 2026-10-04): the code-mode
    # `exec` host (apply_patch/view_image/clock run through it) and the
    # multi-agent collaboration tools, which could spawn a less-locked agent.
    "code_mode_host",
    "multi_agent",
    "view_image",
)


def uses_codex_cli(model: Optional[str]) -> bool:
    return bool(model) and model.startswith(CODEX_CLI_PREFIX)


class CodexCliClient:
    """Wrapper that runs one `codex exec` process per completion."""

    def __init__(self, binary: str, timeout: Optional[float] = None):
        self.binary = binary
        if timeout is None:
            timeout = float(os.getenv("CODEX_CLI_TIMEOUT", "300"))
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
        Run a completion. `model` is the Codex model id ("gpt-6-astra").

        Sampling/routing kwargs the API clients take (temperature, max_tokens,
        reasoning, provider, …) have no CLI equivalent and are ignored;
        reasoning effort comes from CODEX_CLI_REASONING_EFFORT (default medium).
        """
        if tools:
            raise NotImplementedError(
                "Codex CLI models do not support the agentic tool loop."
            )
        if stream:
            raise NotImplementedError("Codex CLI path is non-streaming.")

        system = "\n\n".join(
            _content_text(m.get("content")) for m in messages if m.get("role") == "system"
        )
        turns = [m for m in messages if m.get("role") != "system"]
        if len(turns) == 1:
            question = _content_text(turns[0].get("content"))
        else:
            question = "\n\n".join(
                f"{m.get('role', 'user').capitalize()}: {_content_text(m.get('content'))}"
                for m in turns
            )
        # Codex's own system prompt frames it as a cautious coding agent, so
        # the wrapper has to say explicitly that this is a knowledge task and
        # that the retrieved excerpts are search results, not the whole
        # rulebook — otherwise it refuses whenever retrieval misses the rule,
        # where the API-routed models answer from their own rules knowledge.
        prompt = (
            "This is not a coding task and there is no repository to inspect; "
            "do not run commands or read files. Act as the assistant described "
            "in <instructions> and answer the <question> as that assistant "
            "would. The rulebook excerpts inside <instructions> are retrieved "
            "search results: use them as the primary source and cite them, "
            "but they may miss the relevant rule. If they do, answer from your "
            "own knowledge of the ASL rulebook, cite the sections you know, "
            "and say briefly that the excerpts did not cover it.\n\n"
            f"<instructions>\n{system}\n</instructions>\n\n"
            f"<question>\n{question}\n</question>"
        )

        env = {k: v for k, v in os.environ.items() if k not in _API_AUTH_ENV}
        effort = os.getenv("CODEX_CLI_REASONING_EFFORT", "medium")
        with tempfile.TemporaryDirectory(prefix="codex-cli-") as workdir:
            out_path = os.path.join(workdir, "answer.txt")
            cmd = [
                self.binary, "exec",
                "-m", model,
                "-c", f'model_reasoning_effort="{effort}"',
                "-c", 'web_search="disabled"',
                # Defence in depth should a tool slip through: no env vars
                # reach spawned commands, and the sandbox is read-only.
                "-c", 'shell_environment_policy.inherit="none"',
                "-s", "read-only",
                "-C", workdir,
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                *(f"--disable={f}" for f in _DISABLED_FEATURES),
                "--color", "never",
                "--json",
                "-o", out_path,
                "-",
            ]
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    timeout=self.timeout, cwd=workdir, env=env,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"Codex CLI timed out after {self.timeout:.0f}s (model {model})."
                )
            text = ""
            if os.path.exists(out_path):
                with open(out_path, encoding="utf-8") as f:
                    text = f.read().strip()

        # stdout is JSONL events; pull token usage and any error message.
        usage_raw: Dict[str, Any] = {}
        error_detail = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if etype == "turn.completed":
                usage_raw = event.get("usage") or {}
            elif etype == "error":
                error_detail = event.get("message") or error_detail
            elif etype == "turn.failed":
                error_detail = (event.get("error") or {}).get("message") or error_detail

        if proc.returncode != 0 or not text:
            detail = (
                error_detail
                or proc.stderr.strip()[-500:]
                or f"exit code {proc.returncode}, no answer produced"
            )
            raise RuntimeError(f"Codex CLI failed (model {model}): {detail}")

        usage = SimpleNamespace(
            # Codex's input_tokens already includes cached input.
            prompt_tokens=usage_raw.get("input_tokens") or 0,
            completion_tokens=usage_raw.get("output_tokens") or 0,
            cost=0.0,  # covered by the subscription
        )
        message = SimpleNamespace(content=text, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage)


def build_codex_cli_client_from_env() -> Optional["CodexCliClient"]:
    """Locate the `codex` binary (CODEX_CLI_PATH, else PATH).

    Returns None if it isn't installed — callers treat that as "Codex CLI
    routing is unavailable on this deployment" rather than an error.
    """
    binary = os.getenv("CODEX_CLI_PATH") or shutil.which("codex")
    if not binary:
        logging.info("codex CLI not found — ChatGPT subscription routing disabled.")
        return None
    return CodexCliClient(binary=binary)
