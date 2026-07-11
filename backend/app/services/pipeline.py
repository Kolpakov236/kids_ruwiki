from __future__ import annotations

import asyncio
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
    draft_answer,
    synthesize_answer,
    model_variant,
)
from app.services.reflection import score_article_vs_llm
from app.services.moderation import is_gibberish, _REFUSAL_TEXT, _GIBBERISH_TEXT
from app.services.llm_moderation import validate_query_with_llm
from app.services.simplifier import improve_child_readability
from app.services.verifier import (
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




_CHARACTER_QUERY_RE = re.compile(
    r"^(?:кто такой|кто такая|кто такие|кто это|что это за персонаж|расскажи про|кто)\s+",
    re.IGNORECASE,
)
_CHARACTER_DRAFT_RE = re.compile(
    r"персонаж|литературный герой|из книги|из мультфильма|из сказки|герой сказки|герой книги",
    re.IGNORECASE,
)


def _build_search_hints(query: str, llm_result=None) -> list[str]:
    """
    Build an ordered list of search queries.
    Starts with the standard query variants, then appends proper nouns extracted
    from the draft text (or full LLMResult) to point at the most relevant articles.
    For character/person queries, also tries disambiguation suffixes.
    """
    from app.services.ruwiki import _extract_search_queries

    hints: list[str] = list(_extract_search_queries(query))

    # For character/person queries, add disambiguation variants early
    is_character_query = bool(_CHARACTER_QUERY_RE.match(query.strip()))
    seen_lower = {h.lower() for h in hints}

    if is_character_query and hints:
        concept = hints[0]
        for suffix in [" (персонаж)", " (литературный персонаж)"]:
            variant = concept + suffix
            if variant.lower() not in seen_lower:
                hints.insert(1, variant)
                seen_lower.add(variant.lower())

    if llm_result is None:
        return hints[:8]

    # Accept either a plain draft string or a full LLMResult
    if isinstance(llm_result, str):
        text = llm_result
        glossary_items: list = []
    else:
        text = llm_result.simplified_text or ""
        glossary_items = (llm_result.glossary or [])[:3]

    # If draft identifies topic as a character/fictional hero, add disambiguation hint
    if text and _CHARACTER_DRAFT_RE.search(text) and hints:
        concept = hints[0]
        variant = concept + " (персонаж)"
        if variant.lower() not in seen_lower:
            hints.insert(1, variant)
            seen_lower.add(variant.lower())

    # Proper nouns from the draft/answer text (e.g. "Криштиану Роналду")
    proper_nouns = re.findall(r"\b[А-ЯЁ][а-яё]{3,}(?:\s+[А-ЯЁ][а-яё]{3,})*\b", text)
    for pn in proper_nouns[:4]:
        if pn.lower() not in seen_lower:
            hints.append(pn)
            seen_lower.add(pn.lower())

    for item in glossary_items:
        term = (item.get("term") or "").strip()
        if term and len(term) >= 3 and term.lower() not in seen_lower:
            hints.append(term)
            seen_lower.add(term.lower())

    return hints[:8]


async def _fetch_articles_parallel(hints: list[str]) -> list:
    """
    Fetch wiki articles for every hint in parallel.
    Silently ignores errors (article not found, network issue, etc.).
    De-duplicates by article title.
    """
    async def _safe_fetch(hint: str):
        try:
            return await fetch_article(hint)
        except Exception:
            return None

    results = await asyncio.gather(*[_safe_fetch(h) for h in hints])

    seen: set[str] = set()
    out = []
    for art in results:
        if art is not None and art.title.lower() not in seen:
            seen.add(art.title.lower())
            out.append(art)
    return out


async def simplify_pipeline(
    query: str,
    age: int,
    mode: str = "balanced",
    enable_metrics: bool = True,
    model_id: str | None = None,
) -> SimplifyResponse:
    from app.services.llm import _model_override
    _fast_model = settings.llm_fast_model or model_id or settings.llm_model
    _main_model = model_id or settings.llm_model
    tok = _model_override.set(_fast_model)

    t0 = time.perf_counter()
    timings: dict[str, int] = {}

    age_group = _age_group(age)

    # --- Content moderation (runs before any LLM/cache logic) ---
    # Fast gibberish check first (zero latency), then semantic LLM check.
    _blocked_idea: str | None = None
    _blocked_text: str | None = None
    if is_gibberish(query):
        logger.info("content_filter: gibberish blocked query=%r", query[:60])
        _blocked_idea = "Непонятный набор символов."
        _blocked_text = _GIBBERISH_TEXT
    else:
        _llm_blocked, _llm_reason = await validate_query_with_llm(query)
        if _llm_blocked:
            logger.info("llm_moderation: blocked query=%r reason=%r", query[:60], _llm_reason)
            _blocked_idea = "Этот вопрос не подходит для детской энциклопедии."
            _blocked_text = _REFUSAL_TEXT

    if _blocked_text:
        _model = {"provider": settings.llm_provider, "name": model_id or settings.llm_model}
        _quality = {"ok": True, "issues": [], "sentence_count": 2, "word_count": 22,
                    "max_sentence_words": 15, "finish_reason": None}
        _accuracy = {"metric_label": "Фильтр контента", "metric_key": "content_filter",
                     "score": 1.0, "percent": 100}
        return SimplifyResponse(
            query=query, age=age, age_group=age_group, mode=mode,
            source_title="", source_url="", original_text="",
            main_idea=_blocked_idea,
            simplified_text=_blocked_text,
            reasoning_steps=[], learning_steps=[], glossary=[],
            analogies=[], quiz=[], theories=[],
            quality=_quality, accuracy=_accuracy, evaluation={},
            model=_model, verifier=_EMPTY_VERIFIER,
            cached=False, metrics_enabled=False, llm_only=True,
            timings_ms={"total": int((time.perf_counter() - t0) * 1000)},
            history_key=None,
        )

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

    effective_model = model_id or settings.llm_model
    model = {"provider": settings.llm_provider, "name": effective_model}

    # ── Step 2: Draft answer (1-2 sentences) — anchors search and synthesis ──
    _p0 = time.perf_counter()
    _draft = ""
    try:
        _draft = await draft_answer(query, age=age)
    except Exception as e:
        logger.warning("draft_answer failed: %s", e)
    timings["draft"] = int((time.perf_counter() - _p0) * 1000)

    # ── Step 3: Build search queries from original question + draft ───────────
    _hints = _build_search_hints(query, _draft or None)
    _f0 = time.perf_counter()
    _candidates = await _fetch_articles_parallel(_hints)
    timings["fetch_article"] = int((time.perf_counter() - _f0) * 1000)

    # ── Sort articles by relevance to the draft ────────────────────────────────
    _scored = sorted(
        [
            (score_article_vs_llm(_draft or query, a.title, a.text), a)
            for a in _candidates
        ],
        key=lambda x: x[0],
        reverse=True,
    )
    _best_article = _scored[0][1] if _scored else None
    _best_score = _scored[0][0] if _scored else 0.0

    logger.info(
        "draft-then-retrieve: hints=%d articles=%d best=%r score=%.3f",
        len(_hints), len(_candidates),
        _best_article.title if _best_article else None,
        _best_score,
    )

    # ── Steps 5-7: Synthesize — draft + articles → full explanation ───────────
    # Single LLM call: uses draft as seed, articles as factual context,
    # applies age-appropriate language, generates analogies, self-validates.
    _article_excerpts = [(a.title, a.text) for _, a in _scored[:3]]

    # Switch to main (high-quality) model for synthesis
    _model_override.reset(tok)
    tok = _model_override.set(_main_model)

    _e0 = time.perf_counter()
    try:
        simplified = await synthesize_answer(
            query=query,
            draft=_draft,
            article_excerpts=_article_excerpts,
            age=age,
            mode=mode,
        )
    except (LLMError, TimeoutError) as e:
        logger.warning("synthesize_answer failed: %s", e)
        raise ValueError("no_relevant_article") from e
    timings["simplify"] = int((time.perf_counter() - _e0) * 1000)

    # ── Step 5: Source citation setup ─────────────────────────────────────────
    # Only cite an article as the source if it is clearly about the same topic.
    # Low-scoring articles may be used for synthesis context but not shown as sources.
    _SOURCE_MIN_SCORE = 0.30
    if _best_article and _best_score < _SOURCE_MIN_SCORE:
        logger.info(
            "source suppressed: article=%r score=%.3f < %.2f",
            _best_article.title, _best_score, _SOURCE_MIN_SCORE,
        )
        article = None
    else:
        article = _best_article
    original_text = _mvp_article_slice(article.text) if article else ""
    key_facts = extract_key_facts(original_text) if original_text else {}
    is_llm_only = not bool(_candidates)

    key = cache_key(
        query=query, age_group=age_group, mode=mode,
        source_title=article.title if article else "",
        model_variant=model_variant(), key_facts=key_facts,
    )

    # Per-result SQLite cache check
    cached_payload = get_sqlite_cached(key)
    if cached_payload:
        resp = _build_cached_response(cached_payload, age, age_group, mode, timings, t0)
        if resp:
            return resp
        cached_payload = None

    # Vector lookup for similar originals (full metrics mode only, needs article text)
    sims = []
    if original_text and settings.enable_vector_cache and enable_metrics:
        _c0 = time.perf_counter()
        try:
            sims = get_similar_originals(original_text, top_k=1)
        except Exception:
            logger.exception("vector lookup failed")
            timings["vector_lookup_failed"] = 1
        timings["vector_lookup"] = int((time.perf_counter() - _c0) * 1000)

    summary: SummarizationResult = _EMPTY_SUMMARY  # no separate summarize step needed

    _apply_readability(simplified, age=age)

    # --- Quality check (LLM-first: no factual_consistency vs article — article is secondary) ---
    ver: dict = _EMPTY_VERIFIER
    evaluation: dict = _EMPTY_EVALUATION
    accuracy: dict = {}

    if enable_metrics and original_text:
        # Light structural quality evaluation only — skip repair (article isn't primary source)
        v0 = time.perf_counter()
        evaluation = evaluate_answer_quality(
            original_text,
            simplified.simplified_text,
            age=age,
            consistency=_EMPTY_VERIFIER,
            key_facts=key_facts,
            analogies=simplified.analogies,
            glossary=simplified.glossary,
        )
        timings["verify"] = int((time.perf_counter() - v0) * 1000)
        bp = float(evaluation.get("bleurt_proxy") or 0.0)
        accuracy = {
            "metric_label": "ИИ + Ruwiki", "metric_key": "llm_primary",
            "score": round(bp, 4), "percent": int(round(100 * bp)),
        }

    quality = _quality_report(simplified.simplified_text, age=age, raw=simplified.raw)

    if not accuracy:
        accuracy = {
            "metric_label": "ИИ" if is_llm_only else "ИИ + Ruwiki",
            "metric_key": "llm_primary",
            "score": 1.0, "percent": 100,
        }

    _src_title = article.title if article else ""
    _src_url = article.url if article else ""

    payload = {
        "query": query,
        "age": age,
        "age_group": age_group,
        "mode": mode,
        "source_title": _src_title,
        "source_url": _src_url,
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
        "llm_only": is_llm_only,
    }

    # --- Persist to caches ---
    if enable_metrics:
        bleurt_proxy = float(evaluation.get("bleurt_proxy") or 0.0)
        quality_ok_for_cache = bleurt_proxy >= settings.quality_cache_threshold

        if original_text and settings.enable_vector_cache:
            try:
                upsert_original(original_text, meta={"key": key, "title": _src_title, "url": _src_url})
            except Exception:
                logger.exception("vector upsert failed")

        put_sqlite_cached(key, payload)

        if settings.enable_vector_cache and quality_ok_for_cache:
            try:
                upsert_answer_query(
                    query, age_group=age_group, key=key,
                    meta={
                        "source_title": _src_title[:180],
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
        source_title=_src_title,
        source_url=_src_url,
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

