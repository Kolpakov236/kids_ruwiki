from __future__ import annotations

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

FRONTEND_DIR = pathlib.Path(__file__).parent.parent.parent / "frontend"


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


# Serve frontend static files (must be last so API routes take priority)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
