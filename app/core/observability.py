"""Optional Langfuse tracing for the answer pipeline.

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (plus
optional LANGFUSE_HOST, default Langfuse Cloud EU). Without keys — or with
LANGFUSE_ENABLED=false — every function here is a silent no-op, so the answer
paths never depend on tracing being configured.

Design notes:
- Spans are created with EXPLICIT parent handles (span.start_observation),
  never the contextvar-based "current span" API. The chat WebSocket consumes
  answer generators via asyncio.to_thread, which gives every next() a fresh
  context copy — implicit OTel context would silently detach children there.
- Every wrapper method swallows its own exceptions: a tracing bug must never
  break answering.
"""
import logging
import os
import threading
from typing import Any, Dict, List, Optional

_client = None
_client_lock = threading.Lock()
_init_attempted = False

# Cap payload sizes sent to Langfuse. The full transcripts (system prompt with
# 5-20 RAG chunks baked in, repeated every iteration) already live in
# AGENTIC_DEBUG_LOG; traces only need enough to read the flow.
_TRIM_LIMIT = 4000


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


def get_langfuse():
    """Lazily build the singleton Langfuse client, or None when unconfigured."""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    with _client_lock:
        if _init_attempted:
            return _client
        _init_attempted = True
        if not _truthy(os.getenv("LANGFUSE_ENABLED", "true")):
            logging.info("Langfuse tracing disabled via LANGFUSE_ENABLED")
            return None
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        if not (public_key and secret_key):
            logging.info("Langfuse tracing off (LANGFUSE_PUBLIC_KEY/SECRET_KEY not set)")
            return None
        try:
            from langfuse import Langfuse
            _client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                # v4 name is LANGFUSE_BASE_URL; accept the older LANGFUSE_HOST too.
                base_url=(
                    os.getenv("LANGFUSE_BASE_URL")
                    or os.getenv("LANGFUSE_HOST")
                    or "https://cloud.langfuse.com"
                ),
                environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT"),
            )
            logging.info("📡 Langfuse tracing enabled")
        except Exception as e:
            logging.warning(f"Langfuse init failed — tracing disabled: {e}")
            _client = None
    return _client


def trim_text(value: Any, limit: int = _TRIM_LIMIT) -> Any:
    """Truncate long strings so trace events stay small."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… [truncated, {len(value)} chars total]"
    return value


def trim_messages(messages: List[Dict[str, Any]], limit: int = _TRIM_LIMIT) -> List[Dict[str, Any]]:
    """Chat-format messages with each content field truncated for tracing."""
    out = []
    for m in messages or []:
        entry = {"role": m.get("role")}
        content = m.get("content")
        if content is not None:
            entry["content"] = trim_text(content, limit)
        if m.get("tool_calls"):
            entry["tool_calls"] = [
                {
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments": trim_text((tc.get("function") or {}).get("arguments"), 1000),
                }
                for tc in m["tool_calls"]
            ]
        if m.get("tool_call_id"):
            entry["tool_call_id"] = m["tool_call_id"]
        out.append(entry)
    return out


class Observation:
    """Thin, exception-proof wrapper around a Langfuse observation handle.

    Wraps None when tracing is off — all methods then no-op, and child()
    returns another no-op Observation, so call sites never branch on whether
    tracing is enabled.
    """

    __slots__ = ("_obs", "_ended")

    def __init__(self, obs=None):
        self._obs = obs
        self._ended = False

    @property
    def trace_id(self) -> Optional[str]:
        """Langfuse trace ID of this observation, or None when tracing is off."""
        if self._obs is None:
            return None
        return getattr(self._obs, "trace_id", None)

    def child(self, name: str, as_type: str = "span", **kwargs) -> "Observation":
        if self._obs is None:
            return Observation(None)
        try:
            return Observation(
                self._obs.start_observation(name=name, as_type=as_type, **kwargs)
            )
        except Exception as e:
            logging.warning(f"Langfuse child span failed: {e}")
            return Observation(None)

    def update(self, **kwargs) -> "Observation":
        if self._obs is not None:
            try:
                self._obs.update(**kwargs)
            except Exception as e:
                logging.warning(f"Langfuse span update failed: {e}")
        return self

    def end(self, **kwargs) -> None:
        """Update-and-end. Idempotent: later calls on an ended span are ignored."""
        if self._obs is None or self._ended:
            return
        self._ended = True
        try:
            if kwargs:
                self._obs.update(**kwargs)
            self._obs.end()
        except Exception as e:
            logging.warning(f"Langfuse span end failed: {e}")


def start_trace(
    name: str,
    *,
    input: Any = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    as_type: str = "span",
) -> Observation:
    """Open a root observation (== one Langfuse trace). Returns a no-op
    Observation when tracing is unconfigured. Caller must .end() it."""
    client = get_langfuse()
    if client is None:
        return Observation(None)
    try:
        from langfuse import propagate_attributes

        # propagate_attributes stamps trace-level fields (user, session, tags)
        # onto spans created inside its context — the root carrying them is
        # what makes the trace filterable in the UI.
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            trace_name=name,
        ):
            obs = client.start_observation(
                name=name,
                as_type=as_type,
                input=trim_text(input),
                metadata=metadata,
            )
        return Observation(obs)
    except Exception as e:
        logging.warning(f"Langfuse start_trace failed: {e}")
        return Observation(None)


def flush() -> None:
    """Block until buffered spans are exported. For short-lived scripts/evals."""
    client = get_langfuse()
    if client is not None:
        try:
            client.flush()
        except Exception as e:
            logging.warning(f"Langfuse flush failed: {e}")


class TraceNotFound(Exception):
    """Trace ID unknown to Langfuse — usually not ingested yet (async export)."""


def _iso(dt: Any) -> Optional[str]:
    try:
        return dt.isoformat() if dt is not None else None
    except Exception:
        return None


def _duration_ms(start: Any, end: Any) -> Optional[float]:
    try:
        if start is None or end is None:
            return None
        return round((end - start).total_seconds() * 1000, 1)
    except Exception:
        return None


def fetch_trace_tree(trace_id: str) -> Dict[str, Any]:
    """Fetch one trace from the Langfuse API as a JSON-ready dict.

    Observations come back flat; they are nested here into a tree via
    parent_observation_id (children sorted by start time) so the frontend
    renders without knowing Langfuse's schema.

    Raises RuntimeError when tracing is unconfigured, TraceNotFound when the
    trace isn't queryable yet (ingestion is async — retryable).
    """
    client = get_langfuse()
    if client is None:
        raise RuntimeError("Langfuse is not configured on this deployment")

    from langfuse.api.core.api_error import ApiError

    try:
        trace = client.api.trace.get(trace_id)
    except ApiError as e:
        if e.status_code == 404:
            raise TraceNotFound(trace_id) from e
        raise RuntimeError(f"Langfuse API error (HTTP {e.status_code})") from e

    nodes: Dict[str, Dict[str, Any]] = {}
    for obs in trace.observations or []:
        usage = getattr(obs, "usage", None)
        nodes[obs.id] = {
            "id": obs.id,
            "parent_id": obs.parent_observation_id,
            "name": obs.name,
            "type": (obs.type or "SPAN").upper(),
            "start_time": _iso(obs.start_time),
            "duration_ms": _duration_ms(obs.start_time, obs.end_time),
            "model": obs.model,
            "input_tokens": getattr(usage, "input", None) if usage else None,
            "output_tokens": getattr(usage, "output", None) if usage else None,
            "level": obs.level,
            "status_message": obs.status_message,
            "input": obs.input,
            "output": obs.output,
            "metadata": obs.metadata,
            "children": [],
        }

    roots: List[Dict[str, Any]] = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        (parent["children"] if parent else roots).append(node)
    for node in list(nodes.values()) + [{"children": roots}]:
        node["children"].sort(key=lambda n: n["start_time"] or "")

    base_url = (
        os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    ).rstrip("/")
    html_path = getattr(trace, "html_path", None)

    return {
        "id": trace.id,
        "name": trace.name,
        "timestamp": _iso(trace.timestamp),
        "latency_s": getattr(trace, "latency", None),
        "user_id": trace.user_id,
        "session_id": trace.session_id,
        "tags": trace.tags,
        "metadata": trace.metadata,
        "langfuse_url": (base_url + html_path) if html_path else None,
        "observations": roots,
    }
