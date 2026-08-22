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
import json
import logging
import os
import tempfile
import time
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
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


ASK_MAX_HISTORY_CHARS = int(os.getenv("ASK_MAX_HISTORY_CHARS", "8000"))


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASK_MAX_QUESTION_CHARS)
    vsav: Optional[str] = None      # base64 data URL, same shape as the site
    side: Optional[str] = None      # perspective side name, e.g. "Russian"
    player: Optional[str] = None    # VASSAL player id, mapped via player_sides
    model: Optional[str] = None     # registry key (account) or vendor/slug (provider)
    native_los: Optional[dict] = None  # VASL client's authoritative LOS result
    game_phase: Optional[str] = None   # current VASL phase-wheel phase
    selected_firers: Optional[list[str]] = None  # counters chosen on the VASL map
    selected_targets: Optional[list[str]] = None  # target counters chosen on the VASL map
    # Recent [question, answer] pairs from the client's transcript, oldest
    # first. The endpoint itself is stateless; follow-up context rides along
    # in the prompt.
    history: Optional[list] = None


def _question_with_history(question: str, history) -> str:
    if not history:
        return question
    parts = []
    for pair in list(history)[-6:]:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        parts.append(f"Q: {pair[0]}\nA: {pair[1]}")
    if not parts:
        return question
    ctx = "\n\n".join(parts)[-ASK_MAX_HISTORY_CHARS:]
    return ("Earlier exchanges in this conversation, for context only "
            "(answer just the current question):\n"
            + ctx + "\n\nCurrent question: " + question)


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
    admin = is_admin(user)
    if (
        requested
        and requested in model_registry.allowed_keys("chat", admin)
        and (not has_vsav or model_registry.agentic_allowed(requested))
    ):
        return model_registry.resolve(requested)
    if has_vsav:
        return VSAV_MODEL
    return model_registry.resolve(model_registry.specs_for("chat", admin)[0].key)


def _prepare_ask(payload: AskRequest, authorization: Optional[str]) -> dict:
    """Everything both endpoints share: credential, limit, vsav, model."""
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

    return {
        "mode": mode,
        "remaining": remaining,
        "board_state": board_state,
        "vsav_state": vsav_state,
        "perspective": perspective,
        "model": model,
        "service": service,
        "trace_ctx": trace_ctx,
        "question": _question_with_history(payload.question, payload.history),
        "native_los": payload.native_los,
        "game_phase": payload.game_phase,
        "selected_firers": payload.selected_firers,
        "selected_targets": payload.selected_targets,
    }


def _board_summary(ctx: dict) -> Optional[dict]:
    if ctx["vsav_state"] is None:
        return None
    val = ctx["vsav_state"].get("validation") or {}
    return {
        "hexes": len(ctx["vsav_state"].get("hexes", {})),
        "validation": f"{val.get('n_matched', 0)}"
                      f"/{val.get('n_breadcrumbs_checked', 0)}",
        "perspective": ctx["perspective"],
    }


def _usage_fields(model: str, timing) -> Dict[str, Any]:
    """Tokens plus provider-reported cost, falling back to registry pricing."""
    timing = timing if isinstance(timing, dict) else {}
    tin = timing.get("input_tokens")
    tout = timing.get("output_tokens")
    out: Dict[str, Any] = {
        "tokens_in": int(tin) if tin is not None else None,
        "tokens_out": int(tout) if tout is not None else None,
        "cost_usd": None,
    }
    provider_cost = timing.get("provider_cost_usd")
    if provider_cost is not None:
        try:
            out["cost_usd"] = round(float(provider_cost), 6)
            return out
        except (TypeError, ValueError):
            pass
    price = model_registry.price_for_model_id(model)
    if price and tin is not None and tout is not None:
        out["cost_usd"] = round((tin * price[0] + tout * price[1]) / 1e6, 4)
    return out


def _sanitize_llm_error(e: Exception, mode: str) -> HTTPException:
    # Bad pass-through key surfaces as a provider auth error; everything
    # else is a generic upstream failure. Never echo the key.
    msg = str(e)
    if mode == "provider" and ("401" in msg or "auth" in msg.lower()):
        return HTTPException(status_code=401,
                             detail="OpenRouter rejected the provided key")
    logging.error("ask: LLM call failed: %s", e, exc_info=True)
    return HTTPException(status_code=502, detail="The LLM call failed")


@router.post("/api/ask")
def ask(payload: AskRequest, authorization: Optional[str] = Header(None)):
    # Sync endpoint on purpose: get_answer blocks on the LLM SDK, and FastAPI
    # runs plain-def endpoints on the threadpool.
    ctx = _prepare_ask(payload, authorization)
    mode = ctx["mode"]
    model = ctx["model"]
    service = ctx["service"]
    board_state = ctx["board_state"]
    vsav_state = ctx["vsav_state"]
    perspective = ctx["perspective"]
    trace_ctx = ctx["trace_ctx"]
    remaining = ctx["remaining"]

    t0 = time.monotonic()
    try:
        result = service.get_answer(
            ctx["question"],
            stream=False,
            return_timing=True,
            model=model,
            max_output_tokens=ASK_MAX_OUTPUT_TOKENS,
            board_state=board_state,
            vsav_state=vsav_state,
            use_agentic=bool(vsav_state),
            trace_ctx=trace_ctx,
            native_los=ctx["native_los"],
            game_phase=ctx["game_phase"],
            selected_firers=ctx["selected_firers"],
            selected_targets=ctx["selected_targets"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_llm_error(e, mode)
    if isinstance(result, tuple):
        answer, timing = result[0], result[1]
    else:
        answer, timing = result, {}

    result: Dict[str, Any] = {
        "answer": answer,
        "model": model,
        "mode": mode,
        "remaining_today": remaining,
        "elapsed_seconds": round(time.monotonic() - t0, 1),
        **_usage_fields(model, timing),
    }
    board = _board_summary(ctx)
    if board is not None:
        result["board"] = board
    return result


@router.post("/api/ask/stream")
def ask_stream(payload: AskRequest, authorization: Optional[str] = Header(None)):
    """Streaming variant: NDJSON lines.

    {"delta": "..."}   answer text chunk
    {"status": "..."}  agentic-loop progress (tool running, etc.)
    {"error": "..."}   mid-stream failure; the stream ends after it
    {"done": true, ...} final line with model/mode/remaining/board metadata
                        plus tokens_in/tokens_out/cost_usd (list price; null
                        when the model isn't in the pricing registry)

    Errors *before* the stream starts (auth, limits, bad vsav) are normal
    HTTP error responses, same as /api/ask.
    """
    ctx = _prepare_ask(payload, authorization)

    def gen():
        t0 = time.monotonic()
        try:
            # Every streaming path of the service returns (generator,
            # timing_data) when return_timing=True — the OpenAI paths return
            # a 2-tuple even without it — so always ask for it and unwrap.
            result = ctx["service"].get_answer(
                ctx["question"],
                stream=True,
                return_timing=True,
                model=ctx["model"],
                max_output_tokens=ASK_MAX_OUTPUT_TOKENS,
                board_state=ctx["board_state"],
                vsav_state=ctx["vsav_state"],
                use_agentic=bool(ctx["vsav_state"]),
                trace_ctx=ctx["trace_ctx"],
                native_los=ctx["native_los"],
                game_phase=ctx["game_phase"],
                selected_firers=ctx["selected_firers"],
                selected_targets=ctx["selected_targets"],
            )
            stream = result[0] if isinstance(result, tuple) else result
            timing = result[1] if isinstance(result, tuple) else {}
            for delta in stream:
                if not isinstance(delta, (str, dict)):
                    logging.error("ask: stream yielded %s, not text",
                                  type(delta).__name__)
                    continue
                if isinstance(delta, dict):
                    yield json.dumps(
                        {"status": delta.get("status", "")}) + "\n"
                elif delta:
                    yield json.dumps({"delta": delta}) + "\n"
        except Exception as e:
            exc = _sanitize_llm_error(e, ctx["mode"])
            yield json.dumps({"error": exc.detail}) + "\n"
            return
        done: Dict[str, Any] = {
            "done": True,
            "model": ctx["model"],
            "mode": ctx["mode"],
            "remaining_today": ctx["remaining"],
            "elapsed_seconds": round(time.monotonic() - t0, 1),
            # timing_data is filled in by the service as the stream is consumed
            **_usage_fields(ctx["model"], timing),
        }
        board = _board_summary(ctx)
        if board is not None:
            done["board"] = board
        yield json.dumps(done) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
