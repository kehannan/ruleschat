"""ASL scenario collection — catalogue, query view, and an open demo.

The section's pages, rendered inside ruleschat's chrome (the checkout's
section.html extends our base.html):

    /scenarios         the catalogue, /scenarios/chat the query view — entitled
    /scenarios/demo    the query view, open to anyone, capped per visitor
    /scenarios/design  how the collection was built — open

The scenario data and its query tools live in a separate project. Rather than
copy a thousand lines that would immediately drift, this imports them from a
checkout whose path is SCENARIOS_DIR. Unset, none of these routes are
registered at all and ruleschat runs exactly as before — the feature is
optional, and a missing checkout is a silent no-op rather than a boot failure.

Access is ruleschat's own: the `scenarios` entitlement (see
app/services/entitlements.py), which admins hold implicitly; the card scans
need `scenarios.scans` on top. No second allowlist and no shared secret. The
demo needs neither.
"""
import asyncio
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_entitlement
from app.database import SessionLocal, get_db
from app.models import User
from app.models.demo import ScenarioDemoUsage
from app.services.entitlements import has

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
    # The pages extend ruleschat's base.html (via the checkout's section.html),
    # so ruleschat's templates resolve first and the section's own second.
    _templates.env.loader = ChoiceLoader([
        FileSystemLoader("templates"),
        FileSystemLoader(str(_root / "webapp" / "templates")),
    ])
    _static = _root / "webapp" / "static"
    # The templates emit every path from these, so the same files serve here
    # under /scenarios and standalone at a site root.
    _templates.env.globals.update(
        base="/scenarios", list_url="/scenarios",
        chat_url="/scenarios/chat", demo_url="/scenarios/demo",
        design_url="/scenarios/design",
        chrome="base.html",
        # Cache-buster for both stylesheets: ruleschat's design system (nginx
        # serves /static from our tree) and the section's own scenarios.css.
        css_version=int(max(Path("static/css/site-design-system.css").stat().st_mtime,
                            (_static / "css" / "scenarios.css").stat().st_mtime)),
    )
    _DB = _root / "inventory" / "inventory.db"


# ---------------------------------------------------------------- demo limits

def _demo_limit() -> int:
    try:
        return int(os.getenv("SCENARIOS_DEMO_LIMIT", "5"))
    except ValueError:
        return 5


def _client(request) -> str:
    """Identify a visitor for rate limiting, trusting nginx's X-Real-IP only.

    Without it every request looks like it came from the proxy and the whole
    internet would share one allowance. X-Forwarded-For is not used: nginx
    appends to the client-supplied value, so its first entry is whatever the
    visitor chose to send.
    """
    v = request.headers.get("x-real-ip")
    if v:
        return v.strip()
    return request.client.host if request.client else "unknown"


def _usage(db, key: str, today: str):
    return db.query(ScenarioDemoUsage).filter_by(ip_address=key, date=today).first()


def _take(request) -> tuple:
    """Consume one demo question. Returns (allowed, remaining).

    Counted per IP per day in scenario_demo_usage, so a restart keeps the
    tally. Visitors behind one NAT share an allowance; that bounds casual
    over-use, which is all an open demo needs — it is not a security control.
    """
    cap = _demo_limit()
    key, today = _client(request), date.today().isoformat()
    db = SessionLocal()
    try:
        row = _usage(db, key, today)
        used = row.count if row else 0
        if used >= cap:
            return False, 0
        if row:
            row.count += 1
        else:
            db.add(ScenarioDemoUsage(ip_address=key, date=today, count=1))
        db.commit()
        return True, cap - used - 1
    finally:
        db.close()


def _remaining(request) -> int:
    db = SessionLocal()
    try:
        row = _usage(db, _client(request), date.today().isoformat())
        return max(0, _demo_limit() - (row.count if row else 0))
    finally:
        db.close()


# ---------------------------------------------------------------------- pages

# Pages redirect (anonymous → /login, signed in without access → the demo);
# data routes answer 401/403. Scans are the publisher's artwork and sit behind
# the tighter grant.
_page = require_entitlement("scenarios", no_access_url="/scenarios/demo")
_api = require_entitlement("scenarios", api=True)
_scans = require_entitlement("scenarios.scans", api=True)


def _ctx(request: Request, user, section_page: str, **extra) -> dict:
    """Template context: ruleschat's base context (nav, login state, the
    entitlement set) plus the section's own flags. active_page marks the
    top-nav item; section_page the sub-nav item (see section.html)."""
    from app.api.chat import get_base_context  # lazy: chat.py imports this module
    ctx = get_base_context(request, user)
    ctx.update(active_page="scenarios", section_page=section_page,
               section_access=has(user, "scenarios"))
    ctx.update(extra)
    return ctx


@router.get("/scenarios", name="scenarios", response_class=HTMLResponse)
async def scenarios_list(request: Request, user: User = Depends(_page)):
    with sqlite3.connect(f"file:{_DB}?mode=ro", uri=True) as con:
        extracted = [r[0] for r in con.execute("SELECT uid FROM scenario_facts")]
    return _templates.TemplateResponse("list.html", _ctx(
        request, user, "list",
        facets=_serve.facets(), extracted_uids=extracted,
        scans_allowed=has(user, "scenarios.scans"),
    ))


@router.get("/scenarios/chat", name="scenarios_chat", response_class=HTMLResponse)
async def scenarios_chat(request: Request, user: User = Depends(_page)):
    return _templates.TemplateResponse("chat.html", _ctx(
        request, user, "chat",
        models=_providers.models_for_dropdown(), pricing=_providers.pricing(),
        coverage=_tools.coverage(), demo=False, ws_path="/scenarios/ws",
    ))


@router.get("/scenarios/demo", name="scenarios_demo", response_class=HTMLResponse)
async def scenarios_demo(request: Request, user: User = Depends(get_current_user)):
    """Open to anyone — no login, capped per visitor per day."""
    return _templates.TemplateResponse("chat.html", _ctx(
        request, user, "demo",
        models=[_providers.demo_model()],
        pricing=_providers.pricing(), coverage=_tools.coverage(),
        demo=True, remaining=_remaining(request), daily_limit=_demo_limit(),
        imported_summary=_tools.imported_summary(),
        signed_in=user is not None, allowed=has(user, "scenarios"),
        ws_path="/scenarios/demo/ws", login_url="/login",
    ))


@router.get("/scenarios/design", name="scenarios_design", response_class=HTMLResponse)
async def scenarios_design(request: Request, user: User = Depends(get_current_user)):
    """How the collection was built and what is in it. Open to anyone.

    Explains the method and the measured accuracy; carries no card data, so it
    sits alongside the demo rather than behind the login.
    """
    return _templates.TemplateResponse("design.html", _ctx(
        request, user, "design", s=_tools.design_stats()))


# The flat siblings these pages used to live at. Old links and bookmarks keep
# working; the nav never emits them.
@router.get("/scenarios-demo", include_in_schema=False)
async def _old_demo_path():
    return RedirectResponse(url="/scenarios/demo", status_code=301)


@router.get("/scenarios-design", include_in_schema=False)
async def _old_design_path():
    return RedirectResponse(url="/scenarios/design", status_code=301)


@router.get("/scenarios/static/{path:path}", include_in_schema=False)
async def scenarios_static(path: str):
    """The section's own assets (scenarios.css). /static is ruleschat's tree,
    served straight by nginx, so the checkout's files get their own prefix."""
    root = _static.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(target)


# ----------------------------------------------------------------- data + ws

@router.get("/scenarios/api/list")
async def scenarios_api(request: Request, user: User = Depends(_api)):
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


# The card scans are the publisher's artwork: `scenarios.scans`, never the demo.
@router.get("/scenarios/api/view")
async def scenarios_view(uid: str, user: User = Depends(_scans)):
    return _card_pdf(uid, inline=True)


@router.get("/scenarios/api/export")
async def scenarios_export(uid: str, user: User = Depends(_scans)):
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
    # The handshake carries the same cookies as the page, so the same check
    # runs here too — otherwise the demo page could open the unmetered socket.
    user = await get_current_user(websocket, db)
    if not has(user, "scenarios"):
        await websocket.close(code=1008)
        return
    await _run(websocket, demo=False)


@router.websocket("/scenarios/demo/ws")
async def scenarios_demo_ws(websocket: WebSocket):
    await _run(websocket, demo=True)
