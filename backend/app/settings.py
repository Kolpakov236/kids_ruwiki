from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ruwiki can occasionally block some server IPs on certain subdomains.
    # Keep separate bases to allow fallback between hosts.
    ruwiki_site_base: str = Field(default="https://ruwiki.ru", validation_alias="RUWIKI_SITE_BASE")
    ruwiki_rest_api_base: str = Field(
        default="https://ruwiki.ru/api/rest_v1",
        validation_alias=AliasChoices("RUWIKI_REST_API_BASE", "RUWIKI_API_BASE"),
    )

    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_api_key: str | None = None
    gemini_api_key: str | None = None
    google_api_key: str | None = None
    llm_timeout_seconds: float = 120.0
    llm_max_input_chars: int = 5000
    llm_temperature: float = 0.35
    llm_num_ctx: int = 8192
    llm_num_predict: int = 3000
    enable_llm_repair: bool = True

    # For OpenAI-compatible gateways that require a project/folder header (e.g. Yandex AI Studio).
    openai_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_PROJECT", "YANDEX_FOLDER_ID"),
    )

    yandex_api_key: str | None = None
    yandex_model: str | None = None

    sqlite_path: str = "./data/app.db"
    chroma_path: str = "./data/chroma"
    enable_vector_cache: bool = False

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


settings = Settings()

