from __future__ import annotations

import asyncio
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
    """Yandex Cloud Functions entry point — wraps FastAPI ASGI app."""
    init_db()

    method = event.get("httpMethod", "GET")
    path = event.get("path", "/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    qs = event.get("queryStringParameters") or {}
    raw_headers = event.get("headers") or {}
    body_raw = event.get("body") or ""
    is_b64 = event.get("isBase64Encoded", False)

    body_bytes = base64.b64decode(body_raw) if is_b64 else body_raw.encode()
    query_string = "&".join(f"{k}={v}" for k, v in qs.items()).encode()
    headers = [(k.lower().encode(), v.encode()) for k, v in raw_headers.items()]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "root_path": "",
        "headers": headers,
        "server": ("functions.yandexcloud.net", 443),
        "scheme": "https",
    }

    status_code = 200
    resp_headers: dict = {}
    body_chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(message):
        nonlocal status_code, resp_headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            resp_headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    asyncio.run(app(scope, receive, send))

    response_body = b"".join(body_chunks)
    content_type = resp_headers.get("content-type", "")
    is_binary = content_type and not content_type.startswith(
        ("text/", "application/json", "application/xml", "application/javascript")
    )

    if is_binary:
        return {
            "statusCode": status_code,
            "headers": resp_headers,
            "body": base64.b64encode(response_body).decode(),
            "isBase64Encoded": True,
        }

    return {
        "statusCode": status_code,
        "headers": resp_headers,
        "body": response_body.decode("utf-8", errors="replace"),
        "isBase64Encoded": False,
    }
