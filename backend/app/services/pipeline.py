from __future__ import annotations

import logging
import re
import time

from app.schemas import SimplifyResponse
from app.settings import settings
from app.services.cache import (
    cache_key,
    get_similar_originals,
    get_sqlite_cached,
    log_history,
    put_sqlite_cached,
    upsert_original,
)
from app.services.ruwiki import fetch_article
from app.services.llm import LLMError, model_variant, repair_with_llm, simplify_with_llm
from app.services.simplifier import improve_child_readability
from app.services.verifier import factual_consistency

logger = logging.getLogger(__name__)


_INCOMPLETE_ENDINGS = (
    ",",
    ":",
    ";",
    " и",
    " а",
    " но",
    " что",
    " которая",
    " который",
    " потому",
    " если",
)


def _mvp_article_slice(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…])\s+", normalized)
    selected = []
    total = 0
    for part in parts:
        if not part:
            continue
        next_total = total + len(part) + 1
        if selected and (len(selected) >= 20 or next_total > settings.llm_max_input_chars):
            break
        selected.append(part)
        total = next_total
    return " ".join(selected)[: settings.llm_max_input_chars].strip()


def _quality_report(text: str, age: int, raw: dict | None = None) -> dict:
    normalized = re.sub(r"\s+", " ", text).strip()
    sentences = [x for x in re.split(r"(?<=[.!?…])\s+", normalized) if x.strip()]
    words = re.findall(r"\w+", normalized, flags=re.UNICODE)
    max_sentence_words = max((len(re.findall(r"\w+", s, flags=re.UNICODE)) for s in sentences), default=0)
    lower = normalized.lower()
    finish_reason = ""
    if raw:
        candidates = raw.get("candidates")
        if isinstance(candidates, list) and candidates:
            finish_reason = str(candidates[0].get("finishReason") or "")

    issues: list[str] = []
    if finish_reason == "MAX_TOKENS":
        issues.append("model_output_truncated")
    if len(normalized) < 140:
        issues.append("answer_too_short")
    if normalized and not re.search(r"[.!?…]$", normalized):
        issues.append("answer_has_no_final_punctuation")
    if any(lower.endswith(x) for x in _INCOMPLETE_ENDINGS):
        issues.append("answer_looks_cut_off")
    if len(sentences) < 4:
        issues.append("too_few_sentences")
    if max_sentence_words > (18 if age <= 10 else 26):
        issues.append("long_sentence_detected")

    critical = {"model_output_truncated", "answer_looks_cut_off", "answer_has_no_final_punctuation"}
    return {
        "ok": not critical.intersection(issues),
        "issues": issues,
        "sentence_count": len(sentences),
        "word_count": len(words),
        "max_sentence_words": max_sentence_words,
        "finish_reason": finish_reason or None,
    }


def _apply_readability(payload, age: int) -> None:
    readability = improve_child_readability(payload.simplified_text, age=age)
    payload.simplified_text = readability["text"]
    known_terms = {item.get("term", "").lower() for item in payload.glossary}
    for item in readability["glossary"]:
        if item["term"].lower() not in known_terms:
            payload.glossary.append(item)
            known_terms.add(item["term"].lower())
    for item in readability["analogies"]:
        if item not in payload.analogies:
            payload.analogies.append(item)


async def simplify_pipeline(query: str, age: int, mode: str = "balanced") -> SimplifyResponse:
    t0 = time.perf_counter()
    timings: dict[str, int] = {}

    a0 = time.perf_counter()
    article = await fetch_article(query)
    timings["fetch_article"] = int((time.perf_counter() - a0) * 1000)
    original_text = _mvp_article_slice(article.text)

    model = {"provider": settings.llm_provider, "name": settings.llm_model}
    key = cache_key(query=query, age=age, mode=mode, source_title=article.title, model_variant=model_variant())
    cached_payload = get_sqlite_cached(key)
    if cached_payload:
        quality = _quality_report(cached_payload["simplified_text"], age=age)
        if not quality["ok"]:
            cached_payload = None
        else:
            cached_payload["quality"] = cached_payload.get("quality") or quality
    if cached_payload:
        total_ms = int((time.perf_counter() - t0) * 1000)
        timings["total"] = total_ms
        log_history(cached_payload, cached=True, latency_ms=total_ms)
        return SimplifyResponse(
            query=cached_payload["query"],
            age=cached_payload["age"],
            mode=cached_payload.get("mode", mode),
            source_title=cached_payload["source_title"],
            source_url=cached_payload["source_url"],
            original_text=cached_payload["original_text"],
            main_idea=cached_payload.get("main_idea", ""),
            simplified_text=cached_payload["simplified_text"],
            glossary=cached_payload["glossary"],
            analogies=cached_payload["analogies"],
            quiz=cached_payload.get("quiz", []),
            quality=cached_payload["quality"],
            model=cached_payload["model"],
            verifier=cached_payload["verifier"],
            cached=True,
            timings_ms=timings,
        )

    c0 = time.perf_counter()
    sims = []
    if settings.enable_vector_cache:
        try:
            sims = get_similar_originals(original_text, top_k=1)
        except Exception:
            logger.exception("vector lookup failed")
            timings["vector_lookup_failed"] = 1
    timings["vector_lookup"] = int((time.perf_counter() - c0) * 1000)

    s0 = time.perf_counter()
    simplified = await simplify_with_llm(original_text, age=age, mode=mode)
    timings["simplify"] = int((time.perf_counter() - s0) * 1000)

    q0 = time.perf_counter()
    _apply_readability(simplified, age=age)
    timings["readability_guard"] = int((time.perf_counter() - q0) * 1000)

    v0 = time.perf_counter()
    ver = factual_consistency(original_text, simplified.simplified_text)
    timings["verify"] = int((time.perf_counter() - v0) * 1000)

    if settings.enable_llm_repair and ver["score"] < 0.90:
        s1 = time.perf_counter()
        simplified2 = await repair_with_llm(original_text, simplified.simplified_text, age=age, missing=ver["missing"])
        ver2 = factual_consistency(original_text, simplified2.simplified_text)
        timings["repair_simplify"] = int((time.perf_counter() - s1) * 1000)
        if ver2["score"] >= ver["score"]:
            simplified = simplified2
            _apply_readability(simplified, age=age)
            ver = ver2

    quality = _quality_report(simplified.simplified_text, age=age, raw=simplified.raw)
    if not quality["ok"]:
        raise LLMError(f"quality_gate_failed:{','.join(quality['issues'])}")

    payload = {
        "query": query,
        "age": age,
        "mode": mode,
        "source_title": article.title,
        "source_url": article.url,
        "original_text": original_text,
        "main_idea": simplified.main_idea,
        "simplified_text": simplified.simplified_text,
        "glossary": simplified.glossary,
        "analogies": simplified.analogies,
        "quiz": simplified.quiz,
        "quality": quality,
        "model": model,
        "verifier": ver,
        "similar": sims,
    }

    if settings.enable_vector_cache:
        try:
            upsert_original(original_text, meta={"key": key, "title": article.title, "url": article.url})
        except Exception:
            logger.exception("vector upsert failed")
            timings["vector_upsert_failed"] = 1
    put_sqlite_cached(key, payload)

    total_ms = int((time.perf_counter() - t0) * 1000)
    timings["total"] = total_ms
    log_history(payload, cached=False, latency_ms=total_ms)

    return SimplifyResponse(
        query=query,
        age=age,
        mode=mode,
        source_title=article.title,
        source_url=article.url,
        original_text=original_text,
        main_idea=simplified.main_idea,
        simplified_text=simplified.simplified_text,
        glossary=simplified.glossary,
        analogies=simplified.analogies,
        quiz=simplified.quiz,
        quality=quality,
        model=model,
        verifier=ver,
        cached=False,
        timings_ms=timings,
    )

