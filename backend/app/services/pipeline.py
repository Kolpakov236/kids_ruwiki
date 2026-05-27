from __future__ import annotations

import logging
import re
import time

from app.schemas import SimplifyResponse
from app.settings import settings
from app.services.cache import (
    cache_key,
    get_similar_answer_key,
    get_similar_originals,
    get_sqlite_cached,
    log_history,
    put_sqlite_cached,
    upsert_answer_query,
    upsert_original,
)
from app.services.ruwiki import fetch_article
from app.services.llm import (
    LLMError,
    SummarizationResult,
    answer_without_article,
    model_variant,
    repair_with_llm,
    simplify_with_llm,
    summarize_article,
)
from app.services.simplifier import build_extractive_fallback, improve_child_readability
from app.services.verifier import (
    anchor_coverage,
    evaluate_answer_quality,
    extract_key_facts,
    factual_consistency,
)

logger = logging.getLogger(__name__)

_EMPTY_SUMMARY = SummarizationResult(
    condensed_text="", core_concept="", key_terms=[],
    key_dates=[], key_names=[], key_numbers=[], raw={},
)
_EMPTY_EVALUATION: dict = {
    "rouge_1": None, "rouge_l": None, "bleurt_proxy": None,
    "simplicity": None, "example_quality": None, "term_clarity": None,
    "faithfulness": None, "key_terms": {}, "ok": True,
}
_EMPTY_VERIFIER: dict = {"score": 1.0, "missing": [], "breakdown": {}}


class _LocalResult:
    def __init__(self, payload: dict):
        self.main_idea = payload.get("main_idea", "")
        self.simplified_text = payload.get("simplified_text", "")
        self.reasoning_steps = payload.get("reasoning_steps", [])
        self.learning_steps = payload.get("learning_steps", [])
        self.glossary = payload.get("glossary", [])
        self.analogies = payload.get("analogies", [])
        self.quiz = payload.get("quiz", [])
        self.theories = payload.get("theories", [])
        self.raw = {"provider": "local_extractive_fallback"}


def _age_group(age: int) -> str:
    if age <= 8:
        return "6-8"
    if age <= 11:
        return "9-11"
    return "12-14"


def _accuracy_report(verifier: dict) -> dict:
    score = float(verifier.get("score") or 0.0)
    pct = int(round(100 * max(0.0, min(1.0, score))))
    b = verifier.get("breakdown") or {}
    ne = b.get("named_entities") or {}
    yr = b.get("years") or {}
    ta = b.get("term_anchors") or {}

    parts: list[str] = []
    if ne.get("total"):
        parts.append(f"имена и места {ne.get('kept', 0)}/{ne.get('total')}")
    if yr.get("total"):
        parts.append(f"годы {yr.get('kept', 0)}/{yr.get('total')}")
    if ta.get("total"):
        parts.append(f"ключевые слова {ta.get('kept', 0)}/{ta.get('total')}")
    detail = " · ".join(parts) if parts else "проверка пропущена (быстрый режим)"

    return {
        "metric_label": "Качество объяснения",
        "metric_key": "explanation_quality",
        "score": round(score, 4),
        "percent": pct,
        "hint": (
            "Комбинированная метрика: простота изложения (45%), качество примеров и аналогий (30%), "
            "чёткость объяснения терминов (15%), фактическая точность (10%)."
        ),
        "breakdown": b,
        "detail_summary": detail,
    }


def _missing_quality_anchors(evaluation: dict, verifier: dict) -> list[str]:
    missing = []
    key_terms = (evaluation or {}).get("key_terms") or {}
    missing.extend(str(x) for x in key_terms.get("missing") or [])
    missing.extend(str(x) for x in (verifier or {}).get("missing") or [])
    out: list[str] = []
    seen: set[str] = set()
    for item in missing:
        s = item.strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= 24:
            break
    return out


_INCOMPLETE_ENDINGS = (
    ",", ":", ";", " и", " а", " но", " что", " которая", " который", " потому", " если",
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
    if max_sentence_words > (9 if age <= 8 else 13 if age <= 11 else 22):
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


def _summary_to_dict(summary: SummarizationResult) -> dict:
    return {
        "condensed_text": summary.condensed_text,
        "core_concept": summary.core_concept,
        "key_terms": summary.key_terms,
        "key_dates": summary.key_dates,
        "key_names": summary.key_names,
        "key_numbers": summary.key_numbers,
    }


def _build_cached_response(
    cached_payload: dict,
    age: int,
    age_group: str,
    mode: str,
    timings: dict,
    t0: float,
) -> SimplifyResponse | None:
    quality = _quality_report(cached_payload["simplified_text"], age=age)
    if not quality["ok"]:
        return None
    cached_payload["quality"] = cached_payload.get("quality") or quality
    total_ms = int((time.perf_counter() - t0) * 1000)
    timings["total"] = total_ms
    acc = cached_payload.get("accuracy") or _accuracy_report(cached_payload.get("verifier") or _EMPTY_VERIFIER)
    history_key = log_history(cached_payload, cached=True, latency_ms=total_ms, timings=timings)
    return SimplifyResponse(
        query=cached_payload["query"],
        age=cached_payload["age"],
        age_group=cached_payload.get("age_group") or age_group,
        mode=cached_payload.get("mode", mode),
        source_title=cached_payload["source_title"],
        source_url=cached_payload["source_url"],
        original_text=cached_payload["original_text"],
        main_idea=cached_payload.get("main_idea", ""),
        simplified_text=cached_payload["simplified_text"],
        reasoning_steps=cached_payload.get("reasoning_steps") or [],
        learning_steps=cached_payload.get("learning_steps") or [],
        glossary=cached_payload["glossary"],
        analogies=cached_payload["analogies"],
        quiz=cached_payload.get("quiz", []),
        theories=cached_payload.get("theories", []),
        quality=cached_payload["quality"],
        accuracy=acc,
        evaluation=cached_payload.get("evaluation") or _EMPTY_EVALUATION,
        model=cached_payload["model"],
        verifier=cached_payload.get("verifier") or _EMPTY_VERIFIER,
        cached=True,
        metrics_enabled=cached_payload.get("metrics_enabled", True),
        llm_only=cached_payload.get("llm_only", False),
        timings_ms=timings,
        history_key=history_key,
    )


async def _llm_only_pipeline(
    query: str,
    age: int,
    age_group: str,
    mode: str,
    model_id: str | None,
    timings: dict[str, int],
    t0: float,
) -> SimplifyResponse:
    """Answer a question using LLM general knowledge when no wiki article exists."""
    effective_model = model_id or settings.llm_model
    model = {"provider": settings.llm_provider, "name": effective_model}

    s0 = time.perf_counter()
    try:
        simplified = await answer_without_article(query, age=age, mode=mode)
    except (LLMError, TimeoutError) as e:
        logger.warning("LLM answer_without_article failed: %s", e)
        raise ValueError("no_relevant_article") from e
    timings["llm_only"] = int((time.perf_counter() - s0) * 1000)

    _apply_readability(simplified, age=age)
    quality = _quality_report(simplified.simplified_text, age=age, raw=simplified.raw)
    accuracy = {"metric_label": "ИИ без статьи", "metric_key": "llm_only", "score": 1.0, "percent": 100}

    total_ms = int((time.perf_counter() - t0) * 1000)
    timings["total"] = total_ms

    payload = {
        "query": query,
        "age": age,
        "age_group": age_group,
        "mode": mode,
        "source_title": "",
        "source_url": "",
        "original_text": "",
        "main_idea": simplified.main_idea,
        "simplified_text": simplified.simplified_text,
        "reasoning_steps": simplified.reasoning_steps,
        "learning_steps": simplified.learning_steps,
        "glossary": simplified.glossary,
        "analogies": simplified.analogies,
        "quiz": simplified.quiz,
        "theories": simplified.theories,
        "quality": quality,
        "accuracy": accuracy,
        "evaluation": _EMPTY_EVALUATION,
        "model": model,
        "verifier": _EMPTY_VERIFIER,
        "metrics_enabled": False,
        "llm_only": True,
    }
    history_key = log_history(payload, cached=False, latency_ms=total_ms, timings=timings)

    return SimplifyResponse(
        query=query,
        age=age,
        age_group=age_group,
        mode=mode,
        source_title="",
        source_url="",
        original_text="",
        main_idea=simplified.main_idea,
        simplified_text=simplified.simplified_text,
        reasoning_steps=simplified.reasoning_steps,
        learning_steps=simplified.learning_steps,
        glossary=simplified.glossary,
        analogies=simplified.analogies,
        quiz=simplified.quiz,
        theories=simplified.theories,
        quality=quality,
        accuracy=accuracy,
        evaluation=_EMPTY_EVALUATION,
        model=model,
        verifier=_EMPTY_VERIFIER,
        cached=False,
        metrics_enabled=False,
        llm_only=True,
        timings_ms=timings,
        history_key=history_key,
    )


async def simplify_pipeline(
    query: str,
    age: int,
    mode: str = "balanced",
    enable_metrics: bool = True,
    model_id: str | None = None,
) -> SimplifyResponse:
    from app.services.llm import _model_override
    tok = _model_override.set(model_id) if model_id else None

    t0 = time.perf_counter()
    timings: dict[str, int] = {}

    age_group = _age_group(age)

    # --- Vector answer cache (skip in fast mode to save latency) ---
    cached_payload = None
    if settings.enable_vector_cache and enable_metrics:
        c0 = time.perf_counter()
        try:
            match = get_similar_answer_key(query, age_group=age_group, threshold=0.92)
            timings["answer_cache_lookup"] = int((time.perf_counter() - c0) * 1000)
            if match:
                cached_payload = get_sqlite_cached(match["key"])
                if cached_payload:
                    cached_payload["cache_similarity"] = match["similarity"]
        except Exception:
            logger.exception("answer vector cache lookup failed")
            timings["answer_cache_lookup_failed"] = 1

    if cached_payload:
        resp = _build_cached_response(cached_payload, age, age_group, mode, timings, t0)
        if resp:
            return resp
        cached_payload = None

    # --- Fetch article ---
    a0 = time.perf_counter()
    try:
        article = await fetch_article(query)
    except ValueError as e:
        if "no_relevant_article" in str(e):
            timings["fetch_article"] = int((time.perf_counter() - a0) * 1000)
            return await _llm_only_pipeline(query, age, age_group, mode, model_id, timings, t0)
        raise
    timings["fetch_article"] = int((time.perf_counter() - a0) * 1000)
    original_text = _mvp_article_slice(article.text)
    key_facts = extract_key_facts(original_text)

    effective_model = model_id or settings.llm_model
    model = {"provider": settings.llm_provider, "name": effective_model}
    key = cache_key(
        query=query,
        age_group=age_group,
        mode=mode,
        source_title=article.title,
        model_variant=model_variant(),
        key_facts=key_facts,
    )

    # --- Exact SQLite cache lookup ---
    cached_payload = get_sqlite_cached(key)
    if cached_payload:
        resp = _build_cached_response(cached_payload, age, age_group, mode, timings, t0)
        if resp:
            return resp
        cached_payload = None

    # --- Vector lookup for similar originals (metrics mode only) ---
    sims = []
    if settings.enable_vector_cache and enable_metrics:
        c0 = time.perf_counter()
        try:
            sims = get_similar_originals(original_text, top_k=1)
        except Exception:
            logger.exception("vector lookup failed")
            timings["vector_lookup_failed"] = 1
        timings["vector_lookup"] = int((time.perf_counter() - c0) * 1000)

    # --- Step 1: Summarize article (full mode only) ---
    # In fast mode we skip summarization and go straight to simplification.
    summary: SummarizationResult = _EMPTY_SUMMARY
    if enable_metrics:
        sum0 = time.perf_counter()
        summary = await summarize_article(original_text, query)
        timings["summarize_article"] = int((time.perf_counter() - sum0) * 1000)
        logger.info(
            "pipeline step1 summarize: %dms, condensed %d→%d chars",
            timings["summarize_article"], len(original_text), len(summary.condensed_text),
        )

    # --- Step 2 (or Step 1 in fast mode): Simplify ---
    s0 = time.perf_counter()
    try:
        simplified = await simplify_with_llm(
            original_text,
            age=age,
            mode=mode,
            key_facts=key_facts,
            summary=summary if enable_metrics else None,
            query=query,
        )
    except (LLMError, TimeoutError) as e:
        logger.warning("LLM simplify failed, using local fallback: %s", e)
        timings["llm_fallback"] = 1
        simplified = _LocalResult(build_extractive_fallback(original_text, age=age, key_facts=key_facts))
    timings["simplify"] = int((time.perf_counter() - s0) * 1000)

    _apply_readability(simplified, age=age)

    # --- Quality verification (full mode only) ---
    ver: dict = _EMPTY_VERIFIER
    evaluation: dict = _EMPTY_EVALUATION
    accuracy: dict = {}

    if enable_metrics:
        v0 = time.perf_counter()
        ver = factual_consistency(original_text, simplified.simplified_text)
        timings["verify"] = int((time.perf_counter() - v0) * 1000)
        evaluation = evaluate_answer_quality(
            original_text,
            simplified.simplified_text,
            age=age,
            consistency=ver,
            key_facts=key_facts,
            analogies=simplified.analogies,
            glossary=simplified.glossary,
        )

        # Repair if quality below threshold
        if settings.enable_llm_repair and (ver["score"] < 0.80 or not evaluation["ok"]):
            s1 = time.perf_counter()
            missing = _missing_quality_anchors(evaluation, ver)
            simplified2 = await repair_with_llm(original_text, simplified.simplified_text, age=age, missing=missing)
            ver2 = factual_consistency(original_text, simplified2.simplified_text)
            evaluation2 = evaluate_answer_quality(
                original_text, simplified2.simplified_text, age=age,
                consistency=ver2, key_facts=key_facts,
                analogies=simplified2.analogies, glossary=simplified2.glossary,
            )
            timings["repair_simplify"] = int((time.perf_counter() - s1) * 1000)
            if evaluation2.get("bleurt_proxy", 0) >= evaluation.get("bleurt_proxy", 0):
                simplified = simplified2
                _apply_readability(simplified, age=age)
                ver = ver2
                evaluation = evaluation2

        evaluation = evaluate_answer_quality(
            original_text, simplified.simplified_text, age=age,
            consistency=ver, key_facts=key_facts,
            analogies=simplified.analogies, glossary=simplified.glossary,
        )
        accuracy = _accuracy_report(ver)
        # Update accuracy percent from bleurt_proxy (child-focused score)
        bp = float(evaluation.get("bleurt_proxy") or 0.0)
        accuracy["score"] = round(bp, 4)
        accuracy["percent"] = int(round(100 * bp))

    quality = _quality_report(simplified.simplified_text, age=age, raw=simplified.raw)

    if not accuracy:
        accuracy = {"metric_label": "Быстрый режим", "metric_key": "fast_mode", "score": 1.0, "percent": 100}

    payload = {
        "query": query,
        "age": age,
        "age_group": age_group,
        "mode": mode,
        "source_title": article.title,
        "source_url": article.url,
        "original_text": original_text,
        "main_idea": simplified.main_idea,
        "simplified_text": simplified.simplified_text,
        "reasoning_steps": simplified.reasoning_steps,
        "learning_steps": simplified.learning_steps,
        "glossary": simplified.glossary,
        "analogies": simplified.analogies,
        "quiz": simplified.quiz,
        "theories": simplified.theories,
        "quality": quality,
        "accuracy": accuracy,
        "evaluation": evaluation,
        "key_facts": {
            "required_terms": key_facts.get("required_terms", [])[:48],
            "formulas": key_facts.get("formulas", [])[:16],
        },
        "model": model,
        "verifier": ver,
        "similar": sims,
        "summarization": _summary_to_dict(summary),
        "metrics_enabled": enable_metrics,
    }

    # --- Persist to caches (only in full metrics mode with good quality) ---
    if enable_metrics:
        bleurt_proxy = float(evaluation.get("bleurt_proxy") or 0.0)
        quality_ok_for_cache = bleurt_proxy >= settings.quality_cache_threshold

        if settings.enable_vector_cache:
            try:
                upsert_original(original_text, meta={"key": key, "title": article.title, "url": article.url})
            except Exception:
                logger.exception("vector upsert failed")

        put_sqlite_cached(key, payload)

        if settings.enable_vector_cache and quality_ok_for_cache:
            try:
                upsert_answer_query(
                    query, age_group=age_group, key=key,
                    meta={
                        "source_title": article.title[:180],
                        "mode": mode,
                        "rouge_l": float(evaluation.get("rouge_l") or 0.0),
                        "bleurt_proxy": bleurt_proxy,
                    },
                )
            except Exception:
                logger.exception("answer vector cache upsert failed")
        elif settings.enable_vector_cache and not quality_ok_for_cache:
            logger.info(
                "skip answer vector cache: bleurt_proxy=%.3f < threshold=%.2f",
                bleurt_proxy, settings.quality_cache_threshold,
            )
    else:
        # Fast mode: still persist to SQLite so repeated exact requests use cache
        put_sqlite_cached(key, payload)

    if tok is not None:
        _model_override.reset(tok)

    total_ms = int((time.perf_counter() - t0) * 1000)
    timings["total"] = total_ms
    history_key = log_history(payload, cached=False, latency_ms=total_ms, timings=timings)

    return SimplifyResponse(
        query=query,
        age=age,
        age_group=age_group,
        mode=mode,
        source_title=article.title,
        source_url=article.url,
        original_text=original_text,
        main_idea=simplified.main_idea,
        simplified_text=simplified.simplified_text,
        reasoning_steps=simplified.reasoning_steps,
        learning_steps=simplified.learning_steps,
        glossary=simplified.glossary,
        analogies=simplified.analogies,
        quiz=simplified.quiz,
        theories=simplified.theories,
        quality=quality,
        accuracy=accuracy,
        evaluation=evaluation,
        model=model,
        verifier=ver,
        cached=False,
        metrics_enabled=enable_metrics,
        timings_ms=timings,
        history_key=history_key,
    )

