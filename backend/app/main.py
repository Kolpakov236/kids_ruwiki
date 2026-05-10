from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.simplify import router as simplify_router
from app.services.db import init_db

app = FastAPI(title="Ruwik Kids", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simplify_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()

