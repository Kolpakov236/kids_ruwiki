from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from app.schemas import HealthResponse, SimplifyRequest, SimplifyResponse
from app.settings import settings
from app.services.cache import clear_sqlite_cache
from app.services.llm import LLMError
from app.services.pipeline import simplify_pipeline

router = APIRouter()


@router.post("/simplify", response_model=SimplifyResponse)
async def simplify(req: SimplifyRequest) -> SimplifyResponse:
    try:
        return await simplify_pipeline(query=req.query, age=req.age, mode=req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=_public_llm_error(str(e))) from e
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail="llm_provider_unavailable") from e
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response is not None else ""
        url = str(e.request.url) if e.request is not None else "unknown_url"
        raise HTTPException(status_code=502, detail=f"upstream_http_{e.response.status_code}:{url}:{body}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="llm_provider_timeout") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    api_key = settings.llm_api_key or settings.gemini_api_key or settings.google_api_key
    return HealthResponse(
        status="ok",
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key_configured=bool(api_key),
        cache_enabled=settings.enable_vector_cache,
    )


@router.delete("/cache")
async def clear_cache() -> dict:
    deleted = clear_sqlite_cache()
    return {"status": "ok", "deleted": deleted}


def _public_llm_error(detail: str) -> str:
    if detail.startswith("gemini_response_truncated"):
        return "Ответ модели был обрезан. Увеличьте LLM_NUM_PREDICT и повторите запрос."
    if detail.startswith("gemini_response_blocked"):
        return "Gemini заблокировал ответ по правилам безопасности. Попробуйте другую формулировку темы."
    if detail.startswith("gemini_api_error:400"):
        return f"Gemini отклонил запрос: {detail[:700]}"
    if detail.startswith("gemini_returned_invalid_json"):
        return "Модель вернула поврежденный JSON. Повторите запрос или увеличьте LLM_NUM_PREDICT."
    if detail.startswith("quality_gate_failed"):
        return "Ответ не прошел проверку качества: он выглядит неполным. Повторите запрос."
    if detail == "gemini_api_key_required":
        return "Не найден API-ключ Gemini. Укажите LLM_API_KEY, GEMINI_API_KEY или GOOGLE_API_KEY в backend/.env."
    return detail

