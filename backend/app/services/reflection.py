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

MAX_LOOP_ATTEMPTS = 3          # kept for legacy; generate-then-retrieve doesn't use a loop
ARTICLE_OK_THRESHOLD = 0.50    # relevance score above which article is accepted immediately
ARTICLE_MIN_THRESHOLD = 0.25   # below this score article is always rejected
ARTICLE_ACCEPT_THRESHOLD = 0.35  # min score for article to beat the LLM-only answer


@dataclass
class ValidationResult:
    ok: bool
    reason: str
    score: float = 1.0


# ---------------------------------------------------------------------------
# Article validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Generate-then-retrieve scoring
# ---------------------------------------------------------------------------

def score_article_vs_llm(llm_text: str, article_title: str, article_text: str) -> float:
    """
    How well does this article match the LLM-generated preliminary answer?

    Measures key-word overlap between the LLM text and the article content.
    High score → article covers the same topic the LLM answered about.
    Bonus when the article title itself appears in the LLM text (strong signal).
    """
    from app.services.ruwiki import _RU_PREPOSITIONS

    if not llm_text or not article_text:
        return 0.0

    # Stem (first 6 chars) word sets, ignoring prepositions and short tokens
    llm_words = set(
        w[:6] for w in re.findall(r"\w{4,}", llm_text.lower())
        if w not in _RU_PREPOSITIONS
    )
    if not llm_words:
        return 0.0

    art_content = (article_title + " " + article_text[:3000]).lower()
    art_words = set(w[:6] for w in re.findall(r"\w{4,}", art_content))

    overlap = len(llm_words & art_words) / len(llm_words)

    # Strong bonus: article title stem appears in LLM text
    title_stem = re.sub(r"\s+", "", article_title.lower())[:8]
    title_bonus = 0.20 if title_stem and title_stem in llm_text.lower() else 0.0

    score = min(1.0, overlap + title_bonus)
    logger.debug(
        "score_article_vs_llm: title=%r overlap=%.3f title_bonus=%.2f → %.3f",
        article_title, overlap, title_bonus, score,
    )
    return round(score, 4)


# ---------------------------------------------------------------------------
# Article validation (used for optional extra checks)
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
