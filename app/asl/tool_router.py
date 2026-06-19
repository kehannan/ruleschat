"""
Lightweight LLM router for the agentic calculator tools.

gpt-5.4 reliably has the *arithmetic* wrong unless it calls ift_attack /
cc_attack, but it rarely calls them on its own. This module uses a cheap, fast
model to classify a question into the calculator that should resolve it, so the
agentic path can FORCE that tool (tool_choice) on the first turn.

Fails safe: any error or an unrecognized label returns None, i.e. "do not
force a tool" — so a router hiccup never blocks or corrupts an answer.
"""
import logging
from typing import Optional

from openai import OpenAI

_ROUTER_SYSTEM = """You route questions for an Advanced Squad Leader (ASL) rules assistant to a calculator tool. Reply with EXACTLY one token and nothing else:

- ift_attack : needs an Infantry Fire Table computation — final/adjusted firepower, the FP column, a net IFT DRM total, Residual FP, or break/pin/Casualty/kill odds for small-arms/MG/HE/IFE fire.
- cc_attack  : needs a Close Combat computation — the CC odds ratio, the Kill Number, or the DR needed to eliminate/Casualty-Reduce a unit in CC/Melee.
- none       : anything else — movement-point (MF/MP) costs, LOS/blind-hex geometry, Morale/Task Checks, rally/self-rally, concealment dr, sniper checks, ordnance To-Hit, terrain/TEM lookups, definitions, sequencing, etc.

Output only one of: ift_attack, cc_attack, none"""

_VALID = {"ift_attack", "cc_attack"}
_client: Optional[OpenAI] = None


def _get_client(client: Optional[OpenAI] = None) -> OpenAI:
    global _client
    if client is not None:
        return client
    if _client is None:
        _client = OpenAI()  # API key from environment
    return _client


def classify_tool(
    question: str,
    model: str = "gpt-4.1-mini",
    client: Optional[OpenAI] = None,
) -> Optional[str]:
    """Return 'ift_attack' or 'cc_attack' for a calc question a tool can
    resolve, else None. A cheap one-shot classification; fails safe to None."""
    if not question or not question.strip():
        return None
    try:
        resp = _get_client(client).chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=8,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM},
                {"role": "user", "content": question.strip()[:2000]},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip().lower()
        label = raw.split()[0].strip(".,`\"'") if raw else ""
        tool = label if label in _VALID else None
        logging.info("🧭 tool_router(%s) -> %s", model, tool or "none")
        return tool
    except Exception as e:  # noqa: BLE001 - router must never break the answer
        logging.warning("tool_router failed (%s); not forcing a tool", e)
        return None
