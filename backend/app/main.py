from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.routes.simplify import router as simplify_router
from app.services.db import init_db


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Chrome: preflight к localhost может требовать Access-Control-Allow-Private-Network."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app = FastAPI(title="Ruwik Kids", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PrivateNetworkAccessMiddleware)

app.include_router(simplify_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()

