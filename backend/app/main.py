from __future__ import annotations

import base64
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.routes.auth import router as auth_router
from app.routes.chats import router as chats_router
from app.routes.simplify import router as simplify_router
from app.routes.tts import router as tts_router
from app.services.db import init_db

# YC Functions: code is always at /function/code/; locally: backend/app/ → ../../frontend
_CF_FRONTEND = pathlib.Path("/function/code/frontend")
_LOCAL_FRONTEND = pathlib.Path(__file__).parent.parent.parent / "frontend"
FRONTEND_DIR = _CF_FRONTEND if _CF_FRONTEND.exists() else _LOCAL_FRONTEND


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app = FastAPI(title="Ruwiki Kids", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PrivateNetworkAccessMiddleware)

app.include_router(simplify_router)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(tts_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/debug/fs")
def debug_fs():
    import os
    cf = pathlib.Path("/function/code")
    return {
        "frontend_dir": str(FRONTEND_DIR),
        "frontend_exists": FRONTEND_DIR.exists(),
        "frontend_files": sorted(os.listdir(FRONTEND_DIR)) if FRONTEND_DIR.exists() else [],
        "cf_root": sorted(os.listdir(cf)) if cf.exists() else "no /function/code",
    }


# Serve frontend static files only in local dev (in Cloud Functions StaticFiles at "/"
# intercepts all routes including API ones due to Starlette mount priority)
_in_cloud_functions = pathlib.Path("/function/code").exists()
if FRONTEND_DIR.exists() and not _in_cloud_functions:
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")


def handler(event, context):
    """Yandex Cloud Functions entry point — wraps FastAPI via httpx.ASGITransport."""
    import asyncio
    import json as _json
    import httpx

    # event["path"] = route template ("/{path+}"), real URL is in event["url"]
    raw_url = event.get("url") or "/"
    if not raw_url.startswith("/"):
        raw_url = "/" + raw_url
    if raw_url.endswith("?"):
        raw_url = raw_url[:-1]

    path_only = raw_url.split("?")[0]
    if path_only in ("/ping", "/debug-event"):
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": _json.dumps({"pong": True, "event": event}),
            "isBase64Encoded": False,
        }

    init_db()

    method = event.get("httpMethod", "GET")
    raw_headers = event.get("headers") or {}
    body_raw = event.get("body") or ""
    is_b64 = event.get("isBase64Encoded", False)

    body_bytes = base64.b64decode(body_raw) if is_b64 else body_raw.encode()

    url = raw_url
    req_headers = {k: v for k, v in raw_headers.items()}

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://yc.local") as client:
            return await client.request(method.upper(), url, headers=req_headers, content=body_bytes)

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(_call())
    finally:
        loop.close()

    resp_headers = dict(response.headers)
    content_type = resp_headers.get("content-type", "")
    is_binary = content_type and not content_type.startswith(
        ("text/", "application/json", "application/xml", "application/javascript")
    )

    if is_binary:
        return {
            "statusCode": response.status_code,
            "headers": resp_headers,
            "body": base64.b64encode(response.content).decode(),
            "isBase64Encoded": True,
        }

    return {
        "statusCode": response.status_code,
        "headers": resp_headers,
        "body": response.text,
        "isBase64Encoded": False,
    }
