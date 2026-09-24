"""Model registry — one table drives which models appear where.

Edit MODELS and restart the server; the /chat and /demo dropdowns, the
allowed-model checks in both websockets, the Tools-toggle enablement, and
the cost-chip pricing all read from here. Nothing else needs touching to
add, remove, or re-gate a model.

Columns:
  key        dropdown value and display name ("muse-spark-1.1")
  label      dropdown text shown to the user
  slug       provider model id sent to the API. None = key sent as-is
             (OpenAI-native). "meta/muse-spark…" routes to the Meta Model
             API; any other "vendor/…" (including Meta's open-weight
             models such as "meta/muse-glimmer-30b") routes to OpenRouter.
  in_chat    appears on /ruleschat
  in_demo    appears on /demo
  agentic    the Tools toggle is honored for this model
  price_in   USD per 1M input tokens (client cost chips; not billing)
  price_out  USD per 1M output tokens
"""
from dataclasses import dataclass
from typing import List, Optional, Set


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    slug: Optional[str]
    in_chat: bool
    in_demo: bool
    agentic: bool
    price_in: float
    price_out: float
    admin_only: bool = False  # hidden from (and rejected for) non-admin users


MODELS: tuple = (
    # Dropdown order (both surfaces): cheap → expensive, admin-only last.
    # The first visible entry is each dropdown's default selection.
    # Prices checked 2026-09-07 (OpenRouter /api/v1/models, Meta pricing page,
    # OpenAI list price after the 2026-07-30 GPT-5.6 cut).
    #         key               label                       slug                   chat   demo   agentic  $in    $out
    ModelSpec("deepseek-v4-flash", "deepseek-v4-flash · <¢/slower", "deepseek/deepseek-v4-flash",
              True,  True,  True,    0.077, 0.15),
    # Compatibility key: older VASL extensions still send "ox-alpha". The
    # OpenRouter preview slug was retired; route it to its replacement.
    ModelSpec("ox-alpha",       "GLM 5.3 Flash · Ox successor", "z-ai/glm-5.3-flash",
              True,  False, True,    0.075, 0.25),
    ModelSpec("qwen3.8-flash",  "qwen3.8-flash · <¢/new",   "qwen/qwen3.8-flash",  True,  False, True,    0.15,  0.47),
    ModelSpec("gpt-5.6-luna",   "gpt-5.6-luna · <¢/fast",   None,                  True,  True,  True,    0.20,  1.20),
    # Meta's open-weight 30B distilled from Muse Spark; served via OpenRouter
    # (the Meta Model API only hosts Muse Spark — see asl_service routing).
    ModelSpec("muse-glimmer-30b", "muse-glimmer-30b · ¢/Meta open", "meta/muse-glimmer-30b",
              True,  False, True,    0.30,  1.10),
    ModelSpec("gemini-3.8-flash", "gemini-3.8-flash · ¢/new", "google/gemini-3.8-flash",
              True,  False, True,    0.75,  3.75),
    ModelSpec("deepseek-v4-pro", "deepseek-v4-pro · ¢/reasoning", "deepseek/deepseek-v4-pro",
              True,  False, True,    0.96,  1.91),
    # Muse Spark 1.3 on Meta's "contributor" tier (switched 2026-09-24):
    # ~12x cheaper than the standard endpoint because Meta may TRAIN on the
    # prompts and completions. This is the entry members and the demo use.
    ModelSpec("muse-spark-1.3", "muse-spark-1.3 · <¢/new",  "meta/muse-spark-1.3-contributor",
              True,  True,  True,    0.10,  0.20),
    # Standard (no-training) 1.3 endpoint, used before 2026-09-24; kept so
    # history rows and cost chips still resolve. Not shown in either dropdown.
    ModelSpec("muse-spark-1.3-standard", "muse-spark-1.3-standard · ¢/old", "meta/muse-spark-1.3",
              False, False, True,    1.25,  4.25),
    # Superseded by 1.3 (same price as standard); kept so history rows and
    # cost chips still resolve. Not shown in either dropdown.
    ModelSpec("muse-spark-1.1", "muse-spark-1.1 · ¢/old",   "meta/muse-spark-1.1", False, False, True,    1.25,  4.25),
    ModelSpec("gpt-5.4",        "gpt-5.4 · ¢¢/fast",        None,                  True,  True,  True,    2.50,  15.00),
    ModelSpec("gpt-5.6-terra",  "gpt-5.6-terra · ¢¢/new",   None,                  True,  False, True,    2.00,  12.00,
              True),  # admin_only — too expensive for general use
    ModelSpec("kimi-k3",        "kimi-k3 · ¢¢/new",         "moonshotai/kimi-k3",  True,  False, True,    3.00,  15.00,
              True),  # admin_only — admin-group trial before general release
)


def specs_for(surface: str, is_admin: bool = False) -> List[ModelSpec]:
    """Models visible on a surface: 'chat' or 'demo', in dropdown order."""
    flag = "in_chat" if surface == "chat" else "in_demo"
    return [
        m for m in MODELS
        if getattr(m, flag) and (is_admin or not m.admin_only)
    ]


def allowed_keys(surface: str, is_admin: bool = False) -> Set[str]:
    return {m.key for m in specs_for(surface, is_admin=is_admin)}


def by_key(key: str) -> Optional[ModelSpec]:
    return next((m for m in MODELS if m.key == key), None)


def resolve(key: str) -> Optional[str]:
    """Dropdown key → model id for the API, or None if unknown."""
    spec = by_key(key)
    if spec is None:
        return None
    return spec.slug or spec.key


def agentic_allowed(key: str) -> bool:
    spec = by_key(key)
    return bool(spec and spec.agentic)


def pricing_table() -> dict:
    """{key: {"input": $/1M, "output": $/1M}} for the client cost chips."""
    return {m.key: {"input": m.price_in, "output": m.price_out} for m in MODELS}


def price_for_model_id(model_id: Optional[str]):
    """Provider model id ("gpt-5.4", "deepseek/deepseek-v4-flash") ->
    (usd_per_1m_in, usd_per_1m_out), or None if not in the registry (e.g.
    an arbitrary OpenRouter slug on the pass-through path)."""
    if not model_id:
        return None
    for m in MODELS:
        if model_id == m.key or (m.slug and model_id == m.slug):
            return m.price_in, m.price_out
    return None


def agentic_keys() -> List[str]:
    return [m.key for m in MODELS if m.agentic]
