from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SimplifyRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    age: int = Field(default=10, ge=8, le=14)
    mode: Literal["simple", "balanced", "detailed"] = "balanced"


class SimplifyResponse(BaseModel):
    query: str
    age: int
    mode: str
    source_title: str
    source_url: str
    original_text: str
    main_idea: str
    simplified_text: str
    glossary: list[dict]
    analogies: list[str]
    quiz: list[dict]
    quality: dict
    model: dict[str, str]
    verifier: dict
    cached: bool
    timings_ms: dict[str, int]


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    api_key_configured: bool
    cache_enabled: bool

