"""Machine API for external clients (the VASL "Ask LLM" extension).

POST /api/ask — one-shot, non-streaming Q&A about the rules, optionally
grounded in an attached .vsav board state.

One header (Authorization: Bearer <key>), two credential modes:

  * account key — the key minted on /profile (User.api_key). LLM calls run
    on the server's own provider keys; usage is capped per user per day.
  * provider pass-through — an OpenRouter key ("sk-or-..."). Generation
    runs on the caller's OpenRouter account; the server's OpenAI key is
    used only for cheap retrieval against the hosted rulebook stores. The
    key lives in the request only — it is never persisted or logged.

Fog of war: callers should send their side ("Russian") and/or their VASSAL
player id. If neither resolves against the save, a side that matches no
unit still masks BOTH sides' hidden units (over-masking, never leaking).
"""
import copy
import logging
import os
import tempfile
import time
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import model_registry
from app.asl.openrouter_client import OpenRouterClient
from app.database import SessionLocal
from app.models.user import User
from app.services.asl_service import get_asl_service
from app.services.user_service import is_admin
from app.services.vsav_service import (
    VsavError, decode_vsav_data_url, parse_vsav, render_board_state,
)

router = APIRouter()

ASK_DAILY_LIMIT = int(os.getenv("ASK_DAILY_LIMIT", "50"))
ASK_MAX_QUESTION_CHARS = int(os.getenv("ASK_MAX_QUESTION_CHARS", "4000"))
ASK_MAX_OUTPUT_TOKENS = int(os.getenv("ASK_MAX_OUTPUT_TOKENS", "4000"))
# Provider mode: any OpenRouter slug is accepted (it's the caller's money);
# this is only the default when the request names no model.
DEFAULT_PROVIDER_MODEL = os.getenv(
    "ASK_DEFAULT_PROVIDER_MODEL", "deepseek/deepseek-v4-flash")
# Account mode with a .vsav needs the OpenAI function-calling path for the
# deterministic resolve_attack/resolve_cc tools — same forcing as the demo.
VSAV_MODEL = "gpt-5.4"

# In-memory daily counter, same tradeoff as the scenarios demo: single
# uvicorn process, resets on restart, deliberately not a security control.
_usage: Dict[str, Any] = {"day": None, "counts": {}}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASK_MAX_QUESTION_CHARS)
    vsav: Optional[str] = None      # base64 data URL, same shape as the site
    side: Optional[str] = None      # perspective side name, e.g. "Russian"
    player: Optional[str] = None    # VASSAL player id, mapped via player_sides
    model: Optional[str] = None     # registry key (account) or vendor/slug (provider)


def _lookup_user_by_key(key: str) -> Optional[User]:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.api_key == key).first()
    finally:
        db.close()


def _resolve_credential(authorization: Optional[str]):
    """Bearer token -> ("provider", key) | ("account", User). 401 otherwise."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Send Authorization: Bearer <ruleschat API key or OpenRouter key>")
    key = authorization.split(" ", 1)[1].strip()
    if not key:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    if key.startswith("sk-or-"):
        return "provider", key
    user = _lookup_user_by_key(key)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Unknown API key. Generate one on your ruleschat profile "
                   "page, or use your own OpenRouter key (sk-or-...).")
    return "account", user


def _check_daily_limit(credential_id: str) -> int:
    """Consume one request for this credential; return uses left today."""
    today = date.today().isoformat()
    if _usage["day"] != today:
        _usage["day"] = today
        _usage["counts"] = {}
    used = _usage["counts"].get(credential_id, 0)
    if used >= ASK_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {ASK_DAILY_LIMIT} questions reached — "
                   "try again tomorrow.")
    _usage["counts"][credential_id] = used + 1
    return ASK_DAILY_LIMIT - used - 1


def _resolve_perspective(state: dict, side: Optional[str],
                         player: Optional[str]) -> Optional[str]:
    """Pick the perspective side for fog-of-war masking.

    Preference order: an explicit side that matches a side actually present
    in the save; then the side mapped from the VASSAL player id; then the
    raw side as given. The last case masks everything hidden on BOTH sides
    (side matches no unit), which fails closed rather than leaking. Only a
    request with neither field gets the full-information view.
    """
    unit_sides = {u.get("side") for v in state.get("hexes", {}).values()
                  for u in v["units"]}
    unit_sides |= {u.get("side") for u in state.get("offboard", [])}
    unit_sides.discard(None)
    if side and side in unit_sides:
        return side
    player_sides = state.get("player_sides") or {}
    if player and player in player_sides:
        return player_sides[player]
    return side or None


def _parse_vsav_data_url(data_url: str) -> dict:
    raw = decode_vsav_data_url(data_url)
    fd, tmp_path = tempfile.mkstemp(suffix=".vsav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return parse_vsav(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _account_model(requested: Optional[str], user: User, has_vsav: bool) -> str:
    """Registry key -> provider model id, with the same forcing as the site."""
    if has_vsav:
        return VSAV_MODEL
    admin = is_admin(user)
    if requested and requested in model_registry.allowed_keys("chat", admin):
        return model_registry.resolve(requested)
    return model_registry.resolve(model_registry.specs_for("chat", admin)[0].key)


@router.post("/api/ask")
def ask(payload: AskRequest, authorization: Optional[str] = Header(None)):
    # Sync endpoint on purpose: get_answer blocks on the LLM SDK, and FastAPI
    # runs plain-def endpoints on the threadpool.
    mode, credential = _resolve_credential(authorization)
    if mode == "account":
        credential_id = f"user:{credential.id}"
    else:
        # Don't key the counter on the raw provider secret.
        import hashlib
        credential_id = "orkey:" + hashlib.sha256(credential.encode()).hexdigest()[:16]
    remaining = _check_daily_limit(credential_id)

    board_state = None
    vsav_state = None
    perspective = None
    if payload.vsav:
        try:
            vsav_state = _parse_vsav_data_url(payload.vsav)
        except VsavError as e:
            raise HTTPException(status_code=400, detail=f"VASL save rejected: {e}")
        except Exception:
            logging.error("ask: vsav parse failed", exc_info=True)
            raise HTTPException(status_code=400,
                                detail="Could not parse the VASL save")
        perspective = _resolve_perspective(vsav_state, payload.side, payload.player)
        board_state = render_board_state(vsav_state, perspective_side=perspective)

    if mode == "account":
        model = _account_model(payload.model, credential, bool(vsav_state))
        service = get_asl_service()
        trace_ctx = {"user_id": str(credential.id), "tags": ["ask", "account"]}
    else:
        model = payload.model or DEFAULT_PROVIDER_MODEL
        if "/" not in model or model.startswith("meta/"):
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model}' is not an OpenRouter slug "
                       "(expected vendor/model, e.g. deepseek/deepseek-v4-flash)")
        # Request-scoped shallow copy of the singleton: generation goes out on
        # the caller's OpenRouter key, everything else (retrieval client,
        # config) is shared server state.
        service = copy.copy(get_asl_service())
        service.openrouter_client = OpenRouterClient(
            api_key=credential,
            app_name=os.getenv("OPENROUTER_APP_NAME"),
            app_url=os.getenv("OPENROUTER_APP_URL"),
        )
        trace_ctx = {"tags": ["ask", "provider"]}

    t0 = time.monotonic()
    try:
        answer = service.get_answer(
            payload.question,
            stream=False,
            model=model,
            max_output_tokens=ASK_MAX_OUTPUT_TOKENS,
            board_state=board_state,
            vsav_state=vsav_state,
            use_agentic=bool(vsav_state),
            trace_ctx=trace_ctx,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Bad pass-through key surfaces as a provider auth error; everything
        # else is a generic upstream failure. Never echo the key.
        msg = str(e)
        if mode == "provider" and ("401" in msg or "auth" in msg.lower()):
            raise HTTPException(status_code=401,
                                detail="OpenRouter rejected the provided key")
        logging.error("ask: LLM call failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="The LLM call failed")

    result: Dict[str, Any] = {
        "answer": answer,
        "model": model,
        "mode": mode,
        "remaining_today": remaining,
        "elapsed_seconds": round(time.monotonic() - t0, 1),
    }
    if vsav_state is not None:
        val = vsav_state.get("validation") or {}
        result["board"] = {
            "hexes": len(vsav_state.get("hexes", {})),
            "validation": f"{val.get('n_matched', 0)}"
                          f"/{val.get('n_breadcrumbs_checked', 0)}",
            "perspective": perspective,
        }
    return result
