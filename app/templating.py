"""One Jinja environment for every page, with the nav's state attached.

Each router used to create its own Jinja2Templates and build its own
context. Routes that skipped get_base_context (evals, IFT, demo, register)
rendered a nav missing whatever they hadn't passed — the Scenarios link, the
entitlement set, the demo toggle. A context processor runs for every
TemplateResponse, so the nav is complete no matter which route rendered the
page; routes still add their own keys on top.
"""
from fastapi import Request
from fastapi.templating import Jinja2Templates


def nav_context(request: Request) -> dict:
    """Login state, admin flag, entitlements, feature toggles — what base.html
    needs. Looked up from the session cookie so no route has to remember."""
    from app.api.chat import get_base_context, get_current_user_from_request  # lazy: chat imports this
    ctx = get_base_context(request, get_current_user_from_request(request))
    ctx.pop("request", None)
    return ctx


templates = Jinja2Templates(directory="templates", context_processors=[nav_context])
