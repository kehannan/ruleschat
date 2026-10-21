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
from html import escape as html_escape
import logging
import os
import queue
import re
import tempfile
import threading
import time
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import model_registry
from app.asl.openrouter_client import OpenRouterClient
from app.asl.tools import execute_tool
from app.database import SessionLocal
from app.models.user import User
from app.asl.claude_cli_client import uses_claude_cli
from app.asl.codex_cli_client import uses_codex_cli
from app.services.asl_service import (
    _tool_status_label, get_asl_service, uses_meta_api,
)
from app.services.user_service import is_admin
from app.services.vsav_service import (
    VsavError, decode_vsav_data_url, parse_vsav, render_board_state,
)

router = APIRouter()

ASK_DAILY_LIMIT = int(os.getenv("ASK_DAILY_LIMIT", "50"))
ASK_MAX_QUESTION_CHARS = int(os.getenv("ASK_MAX_QUESTION_CHARS", "4000"))
ASK_MAX_OUTPUT_TOKENS = int(os.getenv("ASK_MAX_OUTPUT_TOKENS", "4000"))
# /api/ask/stream sends a status line at least this often while the answer
# is being produced; nginx drops upstreams that are silent for 60s.
STREAM_HEARTBEAT_SECONDS = float(os.getenv("ASK_STREAM_HEARTBEAT_SECONDS", "10"))
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
ASK_MAX_RETRIEVAL_QUERY_CHARS = int(os.getenv("ASK_MAX_RETRIEVAL_QUERY_CHARS", "3900"))


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
    # Extension >= 0.4 renders the calculator as a native card
    # (calculator_html) instead of markdown tables in the answer text.
    rich: Optional[bool] = False


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
    prefix = (
        "Earlier exchanges in this conversation, for context only "
        "(answer just the current question):\n"
    )
    suffix = "\n\nCurrent question: " + question
    ctx_budget = max(
        0,
        min(
            ASK_MAX_HISTORY_CHARS,
            ASK_MAX_RETRIEVAL_QUERY_CHARS - len(prefix) - len(suffix),
        ),
    )
    ctx = "\n\n".join(parts)[-ctx_budget:] if ctx_budget else ""
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


def _is_subscription(model: Optional[str]) -> bool:
    return uses_claude_cli(model) or uses_codex_cli(model)


def _render_native_los(native_los: Optional[dict]) -> Optional[str]:
    """One-line summary of the VASL client's own LOS check, for the model."""
    if not native_los or not native_los.get("source") or not native_los.get("target"):
        return None
    if native_los.get("blocked"):
        reason = native_los.get("reason")
        verdict = f"BLOCKED ({reason})" if reason else "BLOCKED"
    else:
        verdict = f"clear, hindrance DRM +{native_los.get('hindrance', 0)}"
    def where(level) -> str:
        # The extension sends Location.getLevelInHex(): 0 is the hex's own
        # ground level (a Level 1 hill hex is still 0), not absolute height.
        level = level or 0
        return "ground level of hex" if level == 0 else f"{level} level(s) above the hex's ground"
    return (
        f"VASL NATIVE LOS: {native_los['source']} ({where(native_los.get('source_level'))})"
        f" -> {native_los['target']} ({where(native_los.get('target_level'))}):"
        f" {verdict}, range {native_los.get('range', '?')}"
        " [hex elevations are in the BOARD STATE terrain annotations]"
    )


# A question that asks to resolve a fire attack (vs a pure LOS / rules one).
_FIRE_QUESTION = re.compile(
    r"\b(fire[sd]?|firing|shoot\w*|attack\w*|IFT|DRM|FP|odds|resolve\w*|"
    r"prep|break|kill)\b", re.IGNORECASE)
_PHASE_NAMES = {
    "prep": "Prep Fire", "defensive_first": "Defensive First Fire",
    "defensive_final": "Final Fire", "advancing": "Advancing Fire",
}


def _precompute_attack(question: str, vsav_state: Optional[dict],
                       native_los: Optional[dict], game_phase: Optional[str],
                       selected_firers, selected_targets) -> Optional[dict]:
    """Run resolve_attack server-side for the attack the extension picked.

    The pair comes from native_los (the extension's LOS picker, or hexes it
    found in the question), so the model can't skip the calculator or feed
    it the wrong hexes. None when this isn't a fire question about one pair.
    """
    if not (vsav_state and native_los and native_los.get("source")
            and native_los.get("target") and _FIRE_QUESTION.search(question)):
        return None
    context = {"vsav_state": vsav_state, "native_los": native_los,
               "game_phase": game_phase, "selected_firers": selected_firers,
               "selected_targets": selected_targets}
    try:
        return execute_tool("resolve_attack", {
            "firing_hex": native_los["source"],
            "target_hex": native_los["target"],
        }, context=context)
    except Exception:
        logging.error("ask: precomputed resolve_attack failed", exc_info=True)
        return None


def _pct(p) -> str:
    return f"{100 * float(p):.1f}%"


def _cell(text) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _attack_summary(result: dict, native_los: dict) -> str:
    """The calculator's numbers as fixed markdown tables above the answer."""
    pair = f"{native_los.get('source')} → {native_los.get('target')}"
    if result.get("error"):
        return f"**Calculator** · {pair}: no attack — {result['error']}"
    phase = _PHASE_NAMES.get(result.get("phase"), result.get("phase") or "")
    rng = result.get("range") or {}
    rng_txt = str(rng.get("hexes")) if rng.get("hexes") is not None else "?"
    if rng.get("pbf") and rng.get("pbf") != "none":
        rng_txt += f" ({rng['pbf'].upper()})"
    rows = [("Firers", ", ".join(f"{f.get('name')} ({f.get('fp')} FP)"
                                 for f in result.get("firers") or [])),
            ("Range", rng_txt),
            ("Total FP", result.get("total_fp")),
            ("IFT column", f"**{result.get('column')}**")]
    for d in result.get("drm_breakdown") or []:
        rows.append((f"DRM {d['drm']:+d}", d["label"]))
    rows.append(("Final DRM", f"**{result.get('drm', 0):+d}**"))
    if result.get("cowering") and result["cowering"] != "none":
        rows.append(("Cowering", f"possible on doubles ({result['cowering']}, A7.9)"))
    out = [f"**Calculator** · {pair}, {phase} (computed from the save)", "",
           "| Step | Result |", "|---|---|"]
    out += [f"| {_cell(k)} | {_cell(v)} |" for k, v in rows]
    vt = (result.get("ift") or {}).get("vs_target") or {}
    if "p_broken" in vt:
        morale = vt.get("effective_morale", vt.get("morale"))
        out += ["",
                f"| vs morale {morale} | Broken | Pinned | Eliminated/reduced | No effect |",
                "|---|---|---|---|---|",
                f"| Odds | {_pct(vt['p_broken'])} | {_pct(vt.get('p_pinned', 0))} | "
                f"{_pct(vt.get('p_eliminated_or_reduced', 0))} | "
                f"{_pct(vt.get('p_no_effect', 0))} |"]
    return "\n".join(out)


def _h(text) -> str:
    return html_escape(str(text), quote=True)


# Palette mirrors the extension's (static/css/site-design-system.css).
_INK, _MUTED, _HAIR, _PAPER, _WHITE = "#1E2A33", "#6A757D", "#CFD6D5", "#EEF1EF", "#FFFFFF"
_BROKEN, _PINNED, _ELIM = "#A4453B", "#C98A2E", "#1E2A33"


_RULE_REF = re.compile(r"\b([A-H]\d+(?:\.\d+)*)\b")


def _short_drm_label(label: str) -> str:
    """'TEM: Wall hexside TEM +2 (B9.3: the LOS enters ...)' -> 'Wall hexside
    TEM (B9.3)'. The full wording still reaches the model."""
    head, _, rest = str(label).partition("(")
    head = re.sub(r"^\s*TEM:\s*", "", head)
    head = re.sub(r"\s*[+-]\d+\s*$", "", head.strip())
    refs = list(dict.fromkeys(_RULE_REF.findall(rest)))[:2]
    return f"{head} ({', '.join(refs)})" if refs else head


def _calculator_html(result: dict, native_los: dict) -> str:
    """The calculator card as HTML 3.2 for the extension's Swing renderer
    (tables + bgcolor only: no flexbox, no rounded corners)."""
    pair = f"{native_los.get('source')} → {native_los.get('target')}"

    def card(inner: str) -> str:
        return (f"<table width='100%' cellspacing='1' cellpadding='7' bgcolor='{_HAIR}'>"
                f"<tr><td bgcolor='{_WHITE}' class='calc'>{inner}</td></tr></table>")

    if result.get("error"):
        return card(
            f"<div class='calchead'>{_h(pair)} · <font color='{_BROKEN}'><b>no attack"
            f"</b></font></div><div class='calcerr'>{_h(result['error'])}</div>")
    phase = _PHASE_NAMES.get(result.get("phase"), result.get("phase") or "")
    rng = result.get("range") or {}
    head = f"{pair} · {phase}"
    if rng.get("hexes") is not None:
        head += f" · range {rng['hexes']}"
        if rng.get("pbf") and rng["pbf"] != "none":
            head += f" ({rng['pbf'].upper()})"
    vt = (result.get("ift") or {}).get("vs_target") or {}

    tiles = [("IFT column", result.get("column")),
             ("Final DRM", f"{result.get('drm', 0):+d}")]
    if "p_broken" in vt:
        tiles.append(("Break", f"{round(100 * float(vt['p_broken']))}%"))
    tile_html = "".join(
        f"<td bgcolor='{_PAPER}' class='tile'><div class='tilelbl'>{_h(k)}</div>"
        f"<div class='tileval'>{_h(v)}</div></td>" for k, v in tiles)

    rows = [(f"{f.get('name')}", f"{f.get('fp')} FP")
            for f in result.get("firers") or []]
    if len(rows) > 1:
        rows.append(("Total", f"{result.get('total_fp')} FP"))
    rows += [(_short_drm_label(d["label"]), f"{d['drm']:+d}")
             for d in result.get("drm_breakdown") or []]
    if result.get("cowering") and result["cowering"] != "none":
        rows.append(("Cowering possible on doubles (A7.9)", result["cowering"]))
    row_html = "".join(
        f"<tr><td class='calck'>{_h(k)}</td><td class='calcv' align='right' "
        f"nowrap>{_h(v)}</td></tr>"
        for k, v in rows)

    odds_html = ""
    if "p_broken" in vt:
        parts = [(vt.get("p_eliminated_or_reduced", 0), _ELIM, "Eliminated/reduced"),
                 (vt["p_broken"], _BROKEN, "Broken"),
                 (vt.get("p_pinned", 0), _PINNED, "Pinned"),
                 (vt.get("p_no_effect", 0), _HAIR, "No effect")]
        cells = "".join(
            f"<td width='{max(1, round(100 * float(p)))}%' bgcolor='{c}' class='bar'>&nbsp;</td>"
            for p, c, _ in parts if float(p) > 0)
        legend = " · ".join(f"{name} {_pct(p)}" for p, _, name in parts if float(p) > 0)
        morale = vt.get("effective_morale", vt.get("morale"))
        odds_html = (
            "<div class='calcbargap'>&nbsp;</div>"
            f"<table width='100%' cellspacing='0' cellpadding='0'><tr>{cells}</tr></table>"
            f"<div class='calcleg'>vs morale {_h(morale)}: {_h(legend)}</div>")

    return card(
        f"<div class='calchead'>{_h(head)}</div>"
        f"<table width='100%' cellspacing='4' cellpadding='0'><tr>{tile_html}</tr></table>"
        f"<table width='100%' cellspacing='0' cellpadding='0'>{row_html}</table>"
        f"{odds_html}")


# Appended to the system instructions for /api/ask: the answer is shown in
# the VASL extension's narrow side panel, so keep it short and structured.
# The extension folds the trailing "Assumptions:" and "Rules:" sections.
ASK_ANSWER_FORMAT = (
    "ANSWER FORMAT (this answer is shown in a narrow side panel inside VASL):\n"
    "- Start with ONE plain sentence that answers the question. No 'Answer:' "
    "label, no headings.\n"
    "- Then at most 3 short bullets, each giving one reason with its rule "
    "number in parentheses.\n"
    "- If a PRECOMPUTED ATTACK RESOLUTION is attached, the user already sees "
    "its numbers in a calculator card: explain them, don't repeat the "
    "FP/column/DRM/odds.\n"
    "- Then, if there are any, a line 'Assumptions:' followed by one short "
    "bullet per assumption or warning to confirm.\n"
    "- End with one line 'Rules: ' listing the sections you cited, comma "
    "separated.\n"
    "- No other headings, tables or horizontal rules. Keep everything before "
    "'Assumptions:' under 90 words."
)


def _precomputed_prompt_block(result: dict) -> str:
    return (
        "=== PRECOMPUTED ATTACK RESOLUTION (the server already ran "
        "resolve_attack for the hexes the user picked) ===\n"
        + json.dumps(result, default=str) + "\n"
        "This is authoritative and is ALREADY shown to the user verbatim as a "
        "calculator summary above your answer. Do not call resolve_attack "
        "again for this pair, do not repeat its numbers in your own table, and do "
        "not change or second-guess any value. Explain why each FP/range/DRM "
        "line applies with rule citations (fetch them), and relay every "
        "warning and assumption it lists. If it is an error (e.g. LOS "
        "blocked), explain that error using the obstacles it names.\n"
        "=== END PRECOMPUTED ATTACK RESOLUTION ===")


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
        los_line = _render_native_los(payload.native_los)
        if los_line:
            board_state = f"{board_state}\n\n{los_line}"

    precomputed = _precompute_attack(
        payload.question, vsav_state, payload.native_los, payload.game_phase,
        payload.selected_firers, payload.selected_targets)
    summary = calculator_html = None
    if precomputed is not None:
        if payload.rich:
            calculator_html = _calculator_html(precomputed, payload.native_los)
        else:
            summary = _attack_summary(precomputed, payload.native_los)
        board_state = f"{board_state}\n\n{_precomputed_prompt_block(precomputed)}"

    if mode == "account":
        model = _account_model(payload.model, credential, bool(vsav_state))
        service = get_asl_service()
        trace_ctx = {"user_id": str(credential.id), "tags": ["ask", "account"]}
    else:
        model = payload.model or DEFAULT_PROVIDER_MODEL
        # The CLI prefixes route to the server's own subscriptions, never
        # to the caller's OpenRouter key — reject them here.
        if "/" not in model or uses_meta_api(model) or _is_subscription(model):
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
        "summary": summary,
        "calculator_html": calculator_html,
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
            use_agentic=bool(ctx["vsav_state"]),
            trace_ctx=trace_ctx,
            native_los=ctx["native_los"],
            game_phase=ctx["game_phase"],
            selected_firers=ctx["selected_firers"],
            selected_targets=ctx["selected_targets"],
            extra_instructions=ASK_ANSWER_FORMAT,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_llm_error(e, mode)
    if isinstance(result, tuple):
        answer, timing = result[0], result[1]
    else:
        answer, timing = result, {}

    if ctx["summary"]:
        answer = f"{ctx['summary']}\n\n---\n\n{answer}"
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
    if ctx["calculator_html"]:
        result["calculator_html"] = ctx["calculator_html"]
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
        events: "queue.Queue" = queue.Queue()

        def call():
            # Every streaming path of the service returns (generator,
            # timing_data) when return_timing=True — the OpenAI paths return
            # a 2-tuple even without it — so always ask for it and unwrap.
            try:
                events.put(("result", ctx["service"].get_answer(
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
                    on_tool_call=lambda name, args: events.put(
                        ("status", _tool_status_label(name, args))),
                    extra_instructions=ASK_ANSWER_FORMAT,
                )))
            except Exception as e:
                events.put(("error", e))

        # The subscription CLI paths block for the whole tool loop (often
        # >60s, nginx's read timeout) before yielding anything, so run the
        # call on a worker and keep the stream alive with status lines.
        threading.Thread(target=call, daemon=True).start()
        if ctx["calculator_html"]:
            yield json.dumps({"calculator_html": ctx["calculator_html"]}) + "\n"
        elif ctx["summary"]:
            yield json.dumps({"delta": f"{ctx['summary']}\n\n---\n\n"}) + "\n"
        status = "Thinking"
        try:
            while True:
                try:
                    kind, value = events.get(timeout=STREAM_HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield json.dumps({"status": status}) + "\n"
                    continue
                if kind == "status":
                    status = value
                    yield json.dumps({"status": status}) + "\n"
                elif kind == "error":
                    raise value
                else:
                    result = value
                    break
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
