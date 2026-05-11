from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SimplifyRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    age: int = Field(default=10, ge=6, le=14)
    mode: Literal["simple", "balanced", "detailed"] = "balanced"
    interest_topics: list[str] = Field(default_factory=list, max_length=12)
    child_notes: str = Field(default="", max_length=280)


class SimplifyResponse(BaseModel):
    query: str
    age: int
    age_group: str
    mode: str
    interest_topics: list[str]
    child_notes: str
    source_title: str
    source_url: str
    original_text: str
    main_idea: str
    simplified_text: str
    reasoning_steps: list[str]
    learning_steps: list[str]
    glossary: list[dict]
    analogies: list[str]
    quiz: list[dict]
    quality: dict
    accuracy: dict
    evaluation: dict
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

