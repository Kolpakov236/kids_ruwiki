from __future__ import annotations

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

    # Only save to the answer vector cache when bleurt_proxy meets this threshold.
    # This ensures the semantic cache only returns high-quality answers.
    quality_cache_threshold: float = Field(default=0.60, validation_alias="QUALITY_CACHE_THRESHOLD")

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


settings = Settings()
