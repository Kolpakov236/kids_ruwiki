from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Simplify
# ---------------------------------------------------------------------------

class SimplifyRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    age: int = Field(default=10, ge=6, le=14)
    mode: Literal["simple", "balanced", "detailed"] = "balanced"
    enable_metrics: bool = Field(default=True)
    chat_id: Optional[int] = None
    model_id: Optional[str] = None


class SimplifyResponse(BaseModel):
    query: str
    age: int
    age_group: str
    mode: str
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
    metrics_enabled: bool
    timings_ms: dict[str, int]
    history_key: str = ""


class RatingRequest(BaseModel):
    history_key: str = Field(min_length=1, max_length=256)
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class RatingResponse(BaseModel):
    ok: bool
    message: str


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    api_key_configured: bool
    cache_enabled: bool
    available_models: list[dict]
    vk_enabled: bool
    yandex_enabled: bool


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=80)
    birth_date: Optional[str] = None  # YYYY-MM-DD


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    email: Optional[str]
    display_name: str
    birth_date: Optional[str]
    avatar_url: Optional[str]
    age: int


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------

class ChatOut(BaseModel):
    id: int
    created_at: int
    title: str
    last_message_at: int


class ChatMessageOut(BaseModel):
    id: int
    created_at: int
    role: str
    query: str
    response: dict


class CreateChatResponse(BaseModel):
    chat_id: int
