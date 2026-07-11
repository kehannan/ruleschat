"""Render AGENTIC_DEBUG_LOG records as readable transcripts.

Usage:
    python scripts/view_agentic_debug.py [path] [-n N] [--full]

    path    JSONL file (default: debug_agentic.jsonl)
    -n N    show only the last N questions (default: all)
    --full  don't truncate tool outputs / system prompts
"""
import argparse
import json
import sys
from datetime import datetime

TRUNC = 400


def _clip(text: str, full: bool, limit: int = TRUNC) -> str:
    text = str(text)
    if full or len(text) <= limit:
        return text
    return text[:limit] + f"… [{len(text) - limit} more chars, use --full]"


def _pretty_json(raw, full: bool) -> str:
    """Tool outputs arrive as JSON-encoded strings; decode for display."""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        return _clip(json.dumps(obj, indent=2, ensure_ascii=False), full)
    except Exception:
        return _clip(raw, full)


def render(rec: dict, full: bool) -> None:
    ts = datetime.fromtimestamp(rec["ts"]).strftime("%Y-%m-%d %H:%M:%S") if rec.get("ts") else "?"
    kind = rec.get("kind", "?")
    print("=" * 78)
    print(f"{ts}  ·  {kind}  ·  path={rec.get('path', '?')}  ·  model={rec.get('model', '?')}")

    if kind == "cite_check":
        info = rec.get("info", {})
        print(f"\nQ: {_clip(rec.get('question', ''), full)}")
        print(f"\ncited={info.get('cited')} cross_refs={info.get('cross_refs')} "
              f"missing={info.get('missing')} skipped={info.get('skipped')} "
              f"error={info.get('error')}")
        print(f"\n--- draft ---\n{_clip(rec.get('draft', ''), full, 2000)}")
        if rec.get("revised"):
            print(f"\n--- revised ---\n{_clip(rec['revised'], full, 2000)}")
        print()
        return

    print(f"\nQ: {_clip(rec.get('question', ''), full)}")
    if rec.get("force_tool"):
        print(f"forced tool: {rec['force_tool']}")
    if rec.get("instructions"):
        print(f"\n── system prompt " + "─" * 44)
        print(_clip(rec["instructions"], full))

    # OpenAI path: server-side file_search chunks (the baseline retrieval that
    # happens inside turn 1, before any explicit get_section calls).
    rag = rec.get("rag_sources", [])
    if rag:
        print(f"\n── baseline retrieval: file_search, {len(rag)} chunk(s) " + "─" * 20)
        for src in rag:
            head = " ".join(str(src.get("content", "")).split())
            print(f"[{src.get('index')}] score={src.get('score')}  {_clip(head, full, 120)}")

    # OpenAI path: per-iteration transcript. OpenRouter path: raw messages list.
    for turn in rec.get("transcript", []):
        print(f"\n── turn {turn.get('iteration')} " + "─" * 50)
        if turn.get("text"):
            print(f"model text: {_clip(turn['text'], full)}")
        calls = turn.get("tool_calls", [])
        outputs = turn.get("tool_outputs", [])
        for i, call in enumerate(calls):
            print(f"→ {call.get('name')}({call.get('arguments')})")
            if i < len(outputs):
                print(f"← {_pretty_json(outputs[i], full)}")
    for msg in rec.get("messages", []):
        role = msg.get("role")
        print(f"\n── {role} " + "─" * 50)
        if msg.get("content"):
            limit = TRUNC if role == "system" else 2000
            print(_clip(msg["content"], full, limit))
        for call in msg.get("tool_calls", []):
            fn = call.get("function", {})
            print(f"→ {fn.get('name')}({fn.get('arguments')})")

    print(f"\n── final answer " + "─" * 45)
    print(_clip(rec.get("final_text", ""), full, 4000))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="debug_agentic.jsonl")
    parser.add_argument("-n", type=int, default=None, help="last N records")
    parser.add_argument("--full", action="store_true", help="no truncation")
    args = parser.parse_args()

    try:
        with open(args.path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        sys.exit(f"{args.path} not found — set AGENTIC_DEBUG_LOG and ask a question first")

    if args.n:
        records = records[-args.n:]
    for rec in records:
        render(rec, args.full)
    print(f"{len(records)} record(s) shown")


if __name__ == "__main__":
    main()
