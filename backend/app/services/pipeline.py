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
from app.services.llm import LLMError, model_variant, repair_with_llm, simplify_with_llm
from app.services.simplifier import build_extractive_fallback, improve_child_readability
from app.services.verifier import anchor_coverage, evaluate_answer_quality, extract_key_facts, factual_consistency

logger = logging.getLogger(__name__)


class _LocalResult:
    def __init__(self, payload: dict):
        self.main_idea = payload.get("main_idea", "")
        self.simplified_text = payload.get("simplified_text", "")
        self.reasoning_steps = payload.get("reasoning_steps", [])
        self.learning_steps = payload.get("learning_steps", [])
        self.glossary = payload.get("glossary", [])
        self.analogies = payload.get("analogies", [])
        self.quiz = payload.get("quiz", [])
        self.raw = {"provider": "local_extractive_fallback"}


def _normalize_interest_topics(topics: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in topics or []:
        s = str(t).strip()[:56]
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= 12:
            break
    return out


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

    detail = " · ".join(parts) if parts else "мало якорей для сравнения в фрагменте"

    return {
        "metric_label": "Достоверность к источнику",
        "metric_key": "faithfulness_composite",
        "score": round(score, 4),
        "percent": pct,
        "hint": (
            "Комбинированная метрика: совпадение имён и названий в тексте ответа, сохранение годов "
            "и ключевых терминов из статьи. Перефраз допустим, если суть и имена на месте."
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


async def simplify_pipeline(
    query: str,
    age: int,
    mode: str = "balanced",
    interest_topics: list[str] | None = None,
    child_notes: str = "",
) -> SimplifyResponse:
    t0 = time.perf_counter()
    timings: dict[str, int] = {}

    interest_topics = _normalize_interest_topics(interest_topics or [])
    child_notes = (child_notes or "").strip()[:280]
    age_group = _age_group(age)

    cached_payload = None
    if settings.enable_vector_cache:
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
        quality = _quality_report(cached_payload["simplified_text"], age=age)
        if not quality["ok"]:
            cached_payload = None
        else:
            cached_payload["quality"] = cached_payload.get("quality") or quality
            total_ms = int((time.perf_counter() - t0) * 1000)
            timings["total"] = total_ms
            acc = cached_payload.get("accuracy") or _accuracy_report(cached_payload["verifier"])
            log_history(cached_payload, cached=True, latency_ms=total_ms)
            return SimplifyResponse(
                query=cached_payload["query"],
                age=cached_payload["age"],
                age_group=cached_payload.get("age_group") or age_group,
                mode=cached_payload.get("mode", mode),
                interest_topics=cached_payload.get("interest_topics") or [],
                child_notes=cached_payload.get("child_notes") or "",
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
                quality=cached_payload["quality"],
                accuracy=acc,
                evaluation=cached_payload.get("evaluation") or {},
                model=cached_payload["model"],
                verifier=cached_payload["verifier"],
                cached=True,
                timings_ms=timings,
            )

    a0 = time.perf_counter()
    article = await fetch_article(query)
    timings["fetch_article"] = int((time.perf_counter() - a0) * 1000)
    original_text = _mvp_article_slice(article.text)
    key_facts = extract_key_facts(original_text)

    model = {"provider": settings.llm_provider, "name": settings.llm_model}
    key = cache_key(
        query=query,
        age_group=age_group,
        mode=mode,
        source_title=article.title,
        model_variant=model_variant(),
        interest_topics=interest_topics,
        child_notes=child_notes,
        key_facts=key_facts,
    )
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
        acc = cached_payload.get("accuracy") or {}
        if not acc:
            acc = _accuracy_report(cached_payload["verifier"])
        log_history(cached_payload, cached=True, latency_ms=total_ms)
        return SimplifyResponse(
            query=cached_payload["query"],
            age=cached_payload["age"],
            age_group=cached_payload.get("age_group") or age_group,
            mode=cached_payload.get("mode", mode),
            interest_topics=cached_payload.get("interest_topics") or [],
            child_notes=cached_payload.get("child_notes") or "",
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
            quality=cached_payload["quality"],
            accuracy=acc,
            evaluation=cached_payload.get("evaluation") or {},
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
    try:
        simplified = await simplify_with_llm(
            original_text,
            age=age,
            mode=mode,
            interest_topics=interest_topics,
            child_notes=child_notes,
            key_facts=key_facts,
        )
    except (LLMError, TimeoutError) as e:
        logger.warning("LLM failed, using local fallback: %s", e)
        timings["llm_fallback"] = 1
        simplified = _LocalResult(build_extractive_fallback(original_text, age=age, key_facts=key_facts))
    timings["simplify"] = int((time.perf_counter() - s0) * 1000)

    q0 = time.perf_counter()
    _apply_readability(simplified, age=age)
    timings["readability_guard"] = int((time.perf_counter() - q0) * 1000)

    v0 = time.perf_counter()
    ver = factual_consistency(original_text, simplified.simplified_text)
    timings["verify"] = int((time.perf_counter() - v0) * 1000)
    evaluation = evaluate_answer_quality(
        original_text,
        simplified.simplified_text,
        age=age,
        consistency=ver,
        key_facts=key_facts,
    )

    if settings.enable_llm_repair and (ver["score"] < 0.88 or not evaluation["ok"]):
        s1 = time.perf_counter()
        missing = _missing_quality_anchors(evaluation, ver)
        simplified2 = await repair_with_llm(original_text, simplified.simplified_text, age=age, missing=missing)
        ver2 = factual_consistency(original_text, simplified2.simplified_text)
        evaluation2 = evaluate_answer_quality(
            original_text,
            simplified2.simplified_text,
            age=age,
            consistency=ver2,
            key_facts=key_facts,
        )
        timings["repair_simplify"] = int((time.perf_counter() - s1) * 1000)
        if (evaluation2.get("key_terms", {}).get("score", 0) >= evaluation.get("key_terms", {}).get("score", 0)) and (
            ver2["score"] >= ver["score"]
        ):
            simplified = simplified2
            _apply_readability(simplified, age=age)
            ver = ver2
            evaluation = evaluation2

    quality = _quality_report(simplified.simplified_text, age=age, raw=simplified.raw)
    if not quality["ok"]:
        raise LLMError(f"quality_gate_failed:{','.join(quality['issues'])}")

    accuracy = _accuracy_report(ver)
    # Readability guard may alter text; refresh anchor coverage after all transformations.
    evaluation = evaluate_answer_quality(
        original_text,
        simplified.simplified_text,
        age=age,
        consistency=ver,
        key_facts=key_facts,
    )

    payload = {
        "query": query,
        "age": age,
        "age_group": age_group,
        "mode": mode,
        "interest_topics": interest_topics,
        "child_notes": child_notes,
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
    }

    if settings.enable_vector_cache:
        try:
            upsert_original(original_text, meta={"key": key, "title": article.title, "url": article.url})
        except Exception:
            logger.exception("vector upsert failed")
            timings["vector_upsert_failed"] = 1
    put_sqlite_cached(key, payload)
    if settings.enable_vector_cache:
        try:
            upsert_answer_query(
                query,
                age_group=age_group,
                key=key,
                meta={
                    "source_title": article.title[:180],
                    "mode": mode,
                    "rouge_l": float(evaluation.get("rouge_l") or 0.0),
                    "bleurt_proxy": float(evaluation.get("bleurt_proxy") or 0.0),
                },
            )
        except Exception:
            logger.exception("answer vector cache upsert failed")
            timings["answer_cache_upsert_failed"] = 1

    total_ms = int((time.perf_counter() - t0) * 1000)
    timings["total"] = total_ms
    log_history(payload, cached=False, latency_ms=total_ms)

    return SimplifyResponse(
        query=query,
        age=age,
        age_group=age_group,
        mode=mode,
        interest_topics=interest_topics,
        child_notes=child_notes,
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
        quality=quality,
        accuracy=accuracy,
        evaluation=evaluation,
        model=model,
        verifier=ver,
        cached=False,
        timings_ms=timings,
    )

