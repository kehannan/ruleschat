"""HTTP endpoint for the in-process MCP bridge (see app/asl/mcp_bridge.py).

Only the CLI subprocesses on this host should reach it. nginx forwards every
path to uvicorn, so requests carrying its proxy headers are refused; the
per-call token is the actual credential.
"""
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.asl import mcp_bridge

router = APIRouter()

_PROXY_HEADERS = ("x-real-ip", "x-forwarded-for")


def _from_proxy(request: Request) -> bool:
    return any(h in request.headers for h in _PROXY_HEADERS)


@router.post("/internal/mcp/{token}", include_in_schema=False)
async def mcp_post(token: str, request: Request):
    if _from_proxy(request):
        return Response(status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)
    batch = body if isinstance(body, list) else [body]
    replies = []
    for msg in batch:
        if not isinstance(msg, dict):
            continue
        try:
            # Tools can block (retrieval, attack resolution) — keep them off
            # the event loop the CLI's own /api/ask request is waiting on.
            reply: Optional[dict] = await run_in_threadpool(mcp_bridge.handle, token, msg)
        except KeyError:
            return Response(status_code=404)
        if reply is not None:
            replies.append(reply)
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if isinstance(body, list) else replies[0])


@router.get("/internal/mcp/{token}", include_in_schema=False)
async def mcp_get(token: str):
    # No server-initiated stream; Streamable HTTP clients accept 405 here.
    return Response(status_code=405)


@router.delete("/internal/mcp/{token}", include_in_schema=False)
async def mcp_delete(token: str):
    return Response(status_code=405)
