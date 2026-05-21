from __future__ import annotations

import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.schemas import HealthResponse, RatingRequest, RatingResponse, SimplifyRequest, SimplifyResponse
from app.settings import settings
from app.services.auth_service import get_optional_user_id
from app.services.cache import clear_sqlite_cache, save_rating
from app.services.chat_service import save_chat_message
from app.services.llm import LLMError
from app.services.log_service import log_usage
from app.services.pipeline import simplify_pipeline

router = APIRouter()


def _is_llm_request(e: Exception) -> bool:
    req = getattr(e, "request", None)
    if req is None:
        return False
    try:
        url = str(req.url)
    except Exception:
        return False
    base = str(settings.llm_base_url or "").rstrip("/")
    return (base and base in url) or ("llm.api.cloud.yandex.net" in url) or ("/chat/completions" in url)


@router.post("/simplify", response_model=SimplifyResponse)
async def simplify(
    req: SimplifyRequest,
    user_id: Optional[int] = Depends(get_optional_user_id),
) -> SimplifyResponse:
    t0 = time.perf_counter()
    try:
        result = await simplify_pipeline(
            query=req.query,
            age=req.age,
            mode=req.mode,
            enable_metrics=req.enable_metrics,
            model_id=req.model_id,
        )
    except ValueError as e:
        latency = int((time.perf_counter() - t0) * 1000)
        log_usage("simplify", user_id=user_id, chat_id=req.chat_id, query=req.query,
                  age=req.age, mode=req.mode, latency_ms=latency, success=False, error_text=str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LLMError as e:
        latency = int((time.perf_counter() - t0) * 1000)
        log_usage("simplify", user_id=user_id, chat_id=req.chat_id, query=req.query,
                  age=req.age, mode=req.mode, latency_ms=latency, success=False, error_text=str(e))
        raise HTTPException(status_code=502, detail=_public_llm_error(str(e))) from e
    except httpx.ConnectError as e:
        if _is_llm_request(e):
            raise HTTPException(status_code=502, detail="llm_provider_unavailable") from e
        url = str(getattr(getattr(e, "request", None), "url", "unknown_url"))
        raise HTTPException(status_code=502, detail=f"upstream_connect_error:{url}") from e
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response is not None else ""
        url = str(e.request.url) if e.request is not None else "unknown_url"
        raise HTTPException(status_code=502, detail=f"upstream_http_{e.response.status_code}:{url}:{body}") from e
    except httpx.TimeoutException as e:
        if _is_llm_request(e):
            raise HTTPException(status_code=504, detail="llm_provider_timeout") from e
        url = str(getattr(getattr(e, "request", None), "url", "unknown_url"))
        raise HTTPException(status_code=504, detail=f"upstream_timeout:{url}") from e
    except Exception as e:
        latency = int((time.perf_counter() - t0) * 1000)
        log_usage("simplify", user_id=user_id, query=req.query, age=req.age, mode=req.mode,
                  latency_ms=latency, success=False, error_text=f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    latency = int((time.perf_counter() - t0) * 1000)

    # Save to chat history if user is logged in and chat_id provided
    if user_id and req.chat_id:
        try:
            response_dict = result.model_dump()
            save_chat_message(req.chat_id, "assistant", req.query, response_dict)
        except Exception:
            pass  # Chat save failures are non-critical

    log_usage(
        "simplify",
        user_id=user_id,
        chat_id=req.chat_id,
        query=req.query,
        age=req.age,
        mode=req.mode,
        model=result.model.get("name") if result.model else None,
        latency_ms=latency,
        cached=result.cached,
    )
    return result


@router.post("/rate", response_model=RatingResponse)
async def rate(
    req: RatingRequest,
    user_id: Optional[int] = Depends(get_optional_user_id),
) -> RatingResponse:
    try:
        save_rating(history_key=req.history_key, stars=req.stars, comment=req.comment)
        log_usage("rate", user_id=user_id, extras={"history_key": req.history_key, "stars": req.stars})
        return RatingResponse(ok=True, message="Спасибо за оценку!")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    api_key = settings.llm_api_key or settings.gemini_api_key or settings.google_api_key
    return HealthResponse(
        status="ok",
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key_configured=bool(api_key),
        cache_enabled=settings.enable_vector_cache,
        available_models=settings.available_models,
        vk_enabled=settings.vk_enabled,
        yandex_enabled=settings.yandex_oauth_enabled,
    )


@router.delete("/cache")
async def clear_cache() -> dict:
    deleted = clear_sqlite_cache()
    return {"status": "ok", "deleted": deleted}


def _public_llm_error(detail: str) -> str:
    if detail.startswith("llm_api_400"):
        body = detail[len("llm_api_400:"):].strip()
        return f"LLM API вернул ошибку 400 (неверный запрос). Проверьте LLM_API_KEY и LLM_MODEL в .env. Ответ API: {body[:300]}"
    if detail.startswith("llm_api_401") or detail.startswith("llm_api_403"):
        return "Ошибка авторизации LLM API (401/403). Проверьте LLM_API_KEY в backend/.env."
    if detail.startswith("llm_api_429"):
        return "Превышен лимит запросов к LLM API (429). Подождите немного и повторите."
    if detail.startswith("llm_api_"):
        code = detail.split(":")[0].replace("llm_api_", "")
        return f"LLM API вернул ошибку {code}. Подробности в логах сервера."
    if detail.startswith("gemini_response_truncated"):
        return "Ответ модели был обрезан. Увеличьте LLM_NUM_PREDICT и повторите запрос."
    if detail.startswith("gemini_response_blocked"):
        return "Gemini заблокировал ответ по правилам безопасности. Попробуйте другую формулировку темы."
    if detail.startswith("gemini_api_error:400"):
        return f"Gemini отклонил запрос: {detail[:700]}"
    if detail.startswith("gemini_returned_invalid_json"):
        return "Модель вернула поврежденный JSON. Повторите запрос или увеличьте LLM_NUM_PREDICT."
    if detail.startswith("quality_gate_failed"):
        return "Ответ не прошёл проверку качества. Повторите запрос."
    if detail.startswith("ruwiki_fetch_failed"):
        return f"Не удалось получить статью Рувики: {detail}. Попробуйте точное название статьи."
    if detail == "gemini_api_key_required":
        return "Не найден API-ключ Gemini. Укажите LLM_API_KEY в backend/.env."
    return detail
