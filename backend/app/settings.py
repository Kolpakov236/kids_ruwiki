from __future__ import annotations

import secrets
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ruwiki_site_base: str = Field(default="https://ruwiki.ru", validation_alias="RUWIKI_SITE_BASE")
    ruwiki_rest_api_base: str = Field(
        default="https://ruwiki.ru/api/rest_v1",
        validation_alias=AliasChoices("RUWIKI_REST_API_BASE", "RUWIKI_API_BASE"),
    )

    # LLM — defaults point to Yandex AI Studio
    llm_provider: str = "openai_compatible"
    llm_model: str = "yandexgpt-5-pro"
    llm_base_url: str = "https://llm.api.cloud.yandex.net/v1"
    llm_api_key: str | None = None
    gemini_api_key: str | None = None
    google_api_key: str | None = None
    llm_timeout_seconds: float = 120.0
    llm_max_input_chars: int = 5000
    llm_temperature: float = 0.35
    llm_num_ctx: int = 8192
    llm_num_predict: int = 3000
    enable_llm_repair: bool = True

    # Yandex / OpenAI-compatible gateway extras
    openai_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_PROJECT", "YANDEX_FOLDER_ID"),
    )
    yandex_api_key: str | None = None
    yandex_model: str | None = None

    # Caching
    sqlite_path: str = "./data/app.db"
    chroma_path: str = "./data/chroma"
    enable_vector_cache: bool = True
    quality_cache_threshold: float = Field(default=0.60, validation_alias="QUALITY_CACHE_THRESHOLD")
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # Auth
    secret_key: str = Field(default_factory=lambda: secrets.token_hex(32), validation_alias="SECRET_KEY")
    jwt_expire_days: int = 30
    frontend_url: str = Field(default="http://127.0.0.1:8000", validation_alias="FRONTEND_URL")

    # OAuth — VK
    vk_client_id: str = Field(default="", validation_alias="VK_CLIENT_ID")
    vk_client_secret: str = Field(default="", validation_alias="VK_CLIENT_SECRET")

    # OAuth — Yandex
    yandex_client_id: str = Field(default="", validation_alias="YANDEX_CLIENT_ID")
    yandex_client_secret: str = Field(default="", validation_alias="YANDEX_CLIENT_SECRET")

    # Database — leave empty to use SQLite; set to a postgres:// URL for PostgreSQL
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    # Chats
    max_chats_per_user: int = 20

    @property
    def available_models(self) -> list[dict]:
        if "yandex" in self.llm_base_url or self.llm_provider == "openai_compatible":
            return [
                {"id": "yandexgpt-5-pro", "label": "YandexGPT 5 Pro", "description": "Умная модель"},
                {"id": "yandexgpt-5-lite", "label": "YandexGPT 5 Lite", "description": "Быстрая модель"},
            ]
        if self.llm_provider == "gemini":
            return [
                {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "description": "Быстрая модель"},
                {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "description": "Умная модель"},
            ]
        return [{"id": self.llm_model, "label": self.llm_model, "description": "Текущая модель"}]

    @property
    def vk_enabled(self) -> bool:
        return bool(self.vk_client_id and self.vk_client_secret)

    @property
    def yandex_oauth_enabled(self) -> bool:
        return bool(self.yandex_client_id and self.yandex_client_secret)


settings = Settings()
