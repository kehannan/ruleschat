"""Model registry — one table drives which models appear where.

Edit MODELS and restart the server; the /chat and /demo dropdowns, the
allowed-model checks in both websockets, the Tools-toggle enablement, and
the cost-chip pricing all read from here. Nothing else needs touching to
add, remove, or re-gate a model.

Columns:
  key        dropdown value and display name ("muse-spark-1.1")
  label      dropdown text shown to the user
  slug       provider model id sent to the API. None = key sent as-is
             (OpenAI-native). "meta/…" routes to the Meta Model API,
             any other "vendor/…" routes to OpenRouter.
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


MODELS: tuple = (
    #         key               label                       slug                   chat   demo   agentic  $in    $out
    ModelSpec("gpt-5.4",        "gpt-5.4 · ¢¢/fast",        None,                  True,  True,  True,    2.50,  15.00),
    ModelSpec("gpt-5.6-luna",   "gpt-5.6-luna · ¢/new",     None,                  True,  True,  True,    1.00,  6.00),
    ModelSpec("gpt-5.6-terra",  "gpt-5.6-terra · ¢¢/new",   None,                  True,  False, True,    2.50,  15.00),
    ModelSpec("muse-spark-1.1", "muse-spark-1.1 · ¢/new",   "meta/muse-spark-1.1", True,  True,  True,    1.25,  4.25),
)


def specs_for(surface: str) -> List[ModelSpec]:
    """Models visible on a surface: 'chat' or 'demo', in dropdown order."""
    flag = "in_chat" if surface == "chat" else "in_demo"
    return [m for m in MODELS if getattr(m, flag)]


def allowed_keys(surface: str) -> Set[str]:
    return {m.key for m in specs_for(surface)}


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


def agentic_keys() -> List[str]:
    return [m.key for m in MODELS if m.agentic]
