"""
Reflection validators for the search-and-answer pipeline.

validate_article  — heuristic check: is this article relevant to the query?
validate_answer   — heuristic check: does the generated answer address the query?

Both are pure-CPU (no LLM calls) so they don't add network latency to the loop.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_LOOP_ATTEMPTS = 3          # max reflection iterations before accepting best result
ARTICLE_OK_THRESHOLD = 0.50    # relevance score above which article is accepted immediately
ARTICLE_MIN_THRESHOLD = 0.25   # below this score article is always rejected


@dataclass
class ValidationResult:
    ok: bool
    reason: str
    score: float = 1.0


# ---------------------------------------------------------------------------
# Article validation
# ---------------------------------------------------------------------------

def validate_article(query: str, title: str, text: str) -> ValidationResult:
    """
    Check whether the article is relevant to the query using the relevance scorer.
    Scores all query variants and takes the best.
    """
    from app.services.ruwiki import _extract_search_queries, _relevance_score

    variants = _extract_search_queries(query)
    best = 0.0
    best_v = variants[0] if variants else query
    for v in variants:
        s = _relevance_score(v, title, text[:4000])
        if s > best:
            best = s
            best_v = v

    logger.debug("validate_article: query=%r title=%r best_variant=%r score=%.3f", query, title, best_v, best)

    if best >= ARTICLE_OK_THRESHOLD:
        return ValidationResult(ok=True, reason=f"score={best:.2f}", score=best)
    return ValidationResult(ok=False, reason=f"score_too_low={best:.2f}", score=best)


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------

# At least two capitalised Russian words in a row (proper name / "Лионель Месси")
_PROPER_NOUN_RU = re.compile(
    r"\b[А-ЯЁ][а-яё]{2,}(?:[-\s][А-ЯЁ][а-яё]{2,})+\b"
)
# Causal language expected in "почему" answers
_CAUSAL_RU = re.compile(
    r"потому\s+что|так\s+как|из[-\s]за|вследствие|поэтому|причин[аыу]?\b|объясняется",
    re.IGNORECASE,
)


def validate_answer(query: str, simplified_text: str) -> ValidationResult:
    """
    Heuristic check: does the generated answer actually address what was asked?

    Checks:
    - Minimum length
    - "кто" queries → answer should name specific people/objects
    - "почему" queries → answer should contain causal language
    - Topic coverage: key concept words should appear in the answer
    """
    from app.services.ruwiki import _extract_concept, _RU_PREPOSITIONS

    text = simplified_text or ""
    if len(text) < 60:
        return ValidationResult(ok=False, reason="answer_too_short")

    q = query.strip().lower()

    # "кто ..." → should name specific entities
    if q.startswith("кто "):
        if not _PROPER_NOUN_RU.search(simplified_text):
            return ValidationResult(ok=False, reason="who_query_no_named_entities")

    # "почему ..." → should contain causal reasoning
    if q.startswith("почему "):
        if not _CAUSAL_RU.search(text):
            return ValidationResult(ok=False, reason="why_query_no_causation")

    # Topic coverage: key concept words from the query should appear in the answer
    concept = _extract_concept(query)
    c_words = [w for w in re.findall(r"\w{4,}", concept.lower()) if w not in _RU_PREPOSITIONS]
    if c_words:
        text_lower = text.lower()
        matched = sum(1 for w in c_words if w[:6] in text_lower)
        cov = matched / len(c_words)
        if cov < 0.25:
            return ValidationResult(ok=False, reason=f"off_topic:coverage={cov:.0%}", score=cov)

    return ValidationResult(ok=True, reason="ok")
