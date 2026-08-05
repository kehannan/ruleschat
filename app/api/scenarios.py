"""ASL scenario collection — catalogue, query view, and an open demo.

Two unlisted pages, deliberately absent from the nav:

    /scenarios        the catalogue, and the query view behind it — admin only
    /scenarios-demo   the query view, open to anyone, capped per visitor

The scenario data and its query tools live in a separate project. Rather than
copy a thousand lines that would immediately drift, this imports them from a
checkout whose path is SCENARIOS_DIR. Unset, none of these routes are
registered at all and ruleschat runs exactly as before — the feature is
optional, and a missing checkout is a silent no-op rather than a boot failure.

Access is ruleschat's own: the logged-in user and the `admin` group, no second
allowlist and no shared secret. The demo needs neither.
"""
import asyncio
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database import get_db
from app.models import User
from app.services.user_service import is_admin

router = APIRouter()

SCENARIOS_DIR = os.getenv("SCENARIOS_DIR")


def available() -> bool:
    """Whether the scenario project is present and importable."""
    return bool(SCENARIOS_DIR and (Path(SCENARIOS_DIR) / "webapp" / "main.py").exists())


if available():
    _root = Path(SCENARIOS_DIR).resolve()
    for p in (_root / "webapp", _root / "inventory"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import agent as _agent            # noqa: E402
    import providers as _providers    # noqa: E402
    import serve as _serve            # noqa: E402
    import tools as _tools            # noqa: E402

    _templates = Jinja2Templates(directory=str(_root / "webapp" / "templates"))
    # The templates emit every path from these, so the same files serve here
    # under /scenarios and standalone at a site root.
    _templates.env.globals.update(
        base="/scenarios", list_url="/scenarios",
        chat_url="/scenarios/chat", demo_url="/scenarios-demo",
    )
    _DB = _root / "inventory" / "inventory.db"


# ---------------------------------------------------------------- demo limits

_lock = Lock()
_day: Optional[date] = None
_counts: dict = {}


def _demo_limit() -> int:
    try:
        return int(os.getenv("SCENARIOS_DEMO_LIMIT", "5"))
    except ValueError:
        return 5


def _client(request) -> str:
    """Identify a visitor for rate limiting, trusting nginx's header.

    Without X-Real-IP every request looks like it came from the proxy and the
    whole internet would share one allowance.
    """
    for header in ("x-real-ip", "x-forwarded-for"):
        v = request.headers.get(header)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _take(request) -> tuple:
    """Consume one demo question. Returns (allowed, remaining).

    In-memory and reset daily, so a restart forgets every count and visitors
    behind one NAT share an allowance. That is the right trade for an open
    demo; it bounds casual over-use, and it is not a security control.
    """
    cap = _demo_limit()
    key = _client(request)
    global _day
    with _lock:
        today = date.today()
        if _day != today:
            _day, _ = today, _counts.clear()
        used = _counts.get(key, 0)
        if used >= cap:
            return False, 0
        _counts[key] = used + 1
        return True, cap - used - 1


def _remaining(request) -> int:
    with _lock:
        if _day != date.today():
            return _demo_limit()
        return max(0, _demo_limit() - _counts.get(_client(request), 0))


# ---------------------------------------------------------------------- pages

def _deny(user: Optional[User]):
    """Admin-only, matching how the admin dashboard guards itself."""
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=303)
    return None


@router.get("/scenarios", name="scenarios", response_class=HTMLResponse)
async def scenarios_list(request: Request, user: User = Depends(get_current_user)):
    if (deny := _deny(user)) is not None:
        return deny
    with sqlite3.connect(f"file:{_DB}?mode=ro", uri=True) as con:
        extracted = [r[0] for r in con.execute("SELECT uid FROM scenario_facts")]
    return _templates.TemplateResponse("list.html", {
        "request": request, "active_page": "list",
        "facets": _serve.facets(), "extracted_uids": extracted,
    })


@router.get("/scenarios/chat", name="scenarios_chat", response_class=HTMLResponse)
async def scenarios_chat(request: Request, user: User = Depends(get_current_user)):
    if (deny := _deny(user)) is not None:
        return deny
    return _templates.TemplateResponse("chat.html", {
        "request": request, "active_page": "chat",
        "models": _providers.models_for_dropdown(), "pricing": _providers.pricing(),
        "coverage": _tools.coverage(), "demo": False,
        "ws_path": "/scenarios/ws",
    })


@router.get("/scenarios-demo", name="scenarios_demo", response_class=HTMLResponse)
async def scenarios_demo(request: Request, user: User = Depends(get_current_user)):
    """Open to anyone — no login, capped per visitor per day."""
    return _templates.TemplateResponse("chat.html", {
        "request": request, "active_page": "demo",
        "models": _providers.models_for_dropdown()[:1],
        "pricing": _providers.pricing(), "coverage": _tools.coverage(),
        "demo": True, "remaining": _remaining(request), "daily_limit": _demo_limit(),
        "imported_summary": _tools.imported_summary(),
        "signed_in": user is not None, "allowed": is_admin(user),
        "ws_path": "/scenarios-demo/ws", "login_url": "/login",
    })


# ----------------------------------------------------------------- data + ws

@router.get("/scenarios/api/list")
async def scenarios_api(request: Request, user: User = Depends(get_current_user)):
    if (deny := _deny(user)) is not None:
        return deny
    return _serve.query(dict(request.query_params))


def _card_pdf(uid: str, inline: bool):
    row = _serve.get_row(uid)
    if not row:
        return PlainTextResponse("unknown scenario", status_code=404)
    data, err = _serve.build_export(row)
    if data is None:
        return PlainTextResponse(err or "no scan available", status_code=404)
    disposition = "inline" if inline else "attachment"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{_serve.export_filename(row)}"'})


# The card scans are the publisher's artwork. Admin only, never on the demo.
@router.get("/scenarios/api/view")
async def scenarios_view(uid: str, user: User = Depends(get_current_user)):
    if (deny := _deny(user)) is not None:
        return deny
    return _card_pdf(uid, inline=True)


@router.get("/scenarios/api/export")
async def scenarios_export(uid: str, user: User = Depends(get_current_user)):
    if (deny := _deny(user)) is not None:
        return deny
    return _card_pdf(uid, inline=False)


async def _run(websocket: WebSocket, demo: bool):
    await websocket.accept()
    history: list = []
    try:
        while True:
            payload = json.loads(await websocket.receive_text())
            question = (payload.get("question") or "").strip()
            model = payload.get("model") or _providers.models_for_dropdown()[0]["key"]
            if not question:
                continue

            if demo:
                ok, left = _take(websocket)
                if not ok:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "text": f"Demo limit reached — {_demo_limit()} questions a day.",
                    }))
                    await websocket.send_text(json.dumps({"type": "complete"}))
                    continue
                await websocket.send_text(json.dumps({"type": "quota", "remaining": left}))

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def emit(event):
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def work():
                try:
                    answer = _agent.run(question, list(history), model, emit)
                    emit({"type": "done", "answer": answer})
                except Exception as e:
                    emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
                    emit({"type": "done", "answer": ""})

            task = loop.run_in_executor(None, work)
            while True:
                event = await queue.get()
                if event["type"] == "done":
                    if event["answer"]:
                        history.append({"role": "user", "content": question})
                        history.append({"role": "assistant", "content": event["answer"]})
                        del history[:-12]
                    await websocket.send_text(json.dumps({"type": "complete"}))
                    break
                await websocket.send_text(json.dumps(event))
            await task
    except WebSocketDisconnect:
        return


@router.websocket("/scenarios/ws")
async def scenarios_ws(websocket: WebSocket, db: Session = Depends(get_db)):
    # The handshake carries the same cookies as the page, so the admin check
    # runs here too — otherwise the demo page could open the unmetered socket.
    user = await get_current_user(websocket, db)
    if not is_admin(user):
        await websocket.close(code=1008)
        return
    await _run(websocket, demo=False)


@router.websocket("/scenarios-demo/ws")
async def scenarios_demo_ws(websocket: WebSocket):
    await _run(websocket, demo=True)
