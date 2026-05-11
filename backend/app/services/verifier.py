from __future__ import annotations

import re
from functools import lru_cache

from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter


@lru_cache(maxsize=1)
def _nlp():
    segmenter = Segmenter()
    emb = NewsEmbedding()
    ner_tagger = NewsNERTagger(emb)
    return segmenter, ner_tagger


def _flex_norm(s: str) -> str:
    s = s.strip().lower().replace("ё", "е")
    s = re.sub(r"[-‑–—]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _ner_entities(text: str) -> set[str]:
    segmenter, ner_tagger = _nlp()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)
    ents: set[str] = set()
    for span in doc.spans:
        if span.type in {"PER", "LOC", "ORG"}:
            t = span.text.strip()
            if len(t) >= 3:
                ents.add(re.sub(r"\s+", " ", t))
    return ents


_RU_STOP = frozenset(
    """
    который которая которое которые которых которым которой этот эта это эти этого этих этой этом
    такой такая такое такие было были быть есть был была его её их он она они оно
    при для над под или что как где когда все всё всего только ещё уже тоже
    очень более менее без про со из от до если то не ни да нет там тут этот этих
    также между после перед потому однако одной одного первый второй через
    другой другая другие некоторые многие всегда никогда иногда однако лишь
    """.split()
)

# Markers of analogies and concrete examples — good signs in children's text
_EXAMPLE_MARKERS = re.compile(
    r"например|представь|похоже|как будто|как если бы|словно|это как|"
    r"можно сравнить|вспомни|допустим|для примера|скажем|"
    r"похож[а-я]* на|как [а-яё]+,|как в",
    re.IGNORECASE,
)

# Engagement markers — questions and imperatives increase child engagement
_ENGAGEMENT = re.compile(
    r"\?|знаешь|хочешь узнать|а знаешь|представь себе|попробуй|вспомни|подумай",
    re.IGNORECASE,
)


def _significant_terms(text: str, limit: int = 48) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{5,}", text)
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        lw = w.lower().replace("ё", "е")
        if lw in _RU_STOP:
            continue
        if lw not in seen:
            seen.add(lw)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def _technical_phrases(text: str, limit: int = 32) -> list[str]:
    def script(word: str) -> str:
        if re.search(r"[А-Яа-яЁё]", word):
            return "ru"
        if re.search(r"[A-Za-z]", word):
            return "en"
        return "other"

    raw_words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)
    words = [w for w in raw_words if len(w) >= 3 and w.lower().replace("ё", "е") not in _RU_STOP]
    phrases: list[str] = []
    seen: set[str] = set()
    for n in (2, 3):
        for i in range(0, max(0, len(words) - n + 1)):
            parts = words[i : i + n]
            scripts = {script(p) for p in parts if script(p) != "other"}
            if len(scripts) > 1:
                continue
            norm_parts = [p.lower().replace("ё", "е") for p in parts]
            phrase = " ".join(parts)
            key = " ".join(norm_parts)
            if key in seen:
                continue
            if any(len(p) >= 6 for p in norm_parts):
                seen.add(key)
                phrases.append(phrase)
            if len(phrases) >= limit:
                return phrases
    return phrases


def _formulas_and_units(text: str) -> list[str]:
    patterns = [
        r"\b[A-Za-zА-Яа-яЁё]\s*=\s*[^,.;\n]+",
        r"\b[A-Za-zА-Яа-яЁё]\s*[⁻−-]?\d+\b",
        r"\b(?:Гц|кГц|МГц|с\s*[⁻−-]?\s*1|секунд[ауы]?|вольт|ампер|ватт)\b",
        r"[∼≈~]",
        r"\bAC\b",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for m in re.findall(pattern, text, flags=re.IGNORECASE):
            s = re.sub(r"\s+", " ", str(m)).strip(" .;,")
            key = s.lower()
            if s and key not in seen:
                seen.add(key)
                out.append(s)
    return out


def extract_key_facts(text: str, limit_terms: int = 48) -> dict:
    terms = _significant_terms(text, limit=limit_terms)
    phrases = _technical_phrases(text, limit=32)
    formulas = _formulas_and_units(text)
    years = _years(text)

    required: list[str] = []
    seen: set[str] = set()
    for item in [*formulas, *years, *phrases, *terms]:
        s = re.sub(r"\s+", " ", str(item)).strip()
        key = _flex_norm(s)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        required.append(s)
        if len(required) >= limit_terms:
            break

    return {
        "required_terms": required,
        "technical_phrases": phrases,
        "formulas": formulas,
        "years": years,
        "reference_text": " ".join(required),
    }


def anchor_coverage(key_facts: dict, candidate: str) -> dict:
    anchors = [str(x) for x in (key_facts or {}).get("required_terms") or []]
    haystack = _flex_norm(candidate)
    kept: list[str] = []
    missing: list[str] = []
    for anchor in anchors:
        norm = _flex_norm(anchor)
        if not norm:
            continue
        parts = [p for p in norm.split() if len(p) >= 3]
        ok = norm in haystack or (len(parts) >= 2 and all(p in haystack for p in parts))
        (kept if ok else missing).append(anchor)
    total = len(kept) + len(missing)
    score = 1.0 if total == 0 else len(kept) / total
    return {
        "kept": len(kept),
        "total": total,
        "score": round(score, 4),
        "percent": round(score * 100, 1),
        "missing": missing[:24],
        "sample_kept": kept[:12],
    }


def _years(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b((?:1[0-9]{3}|20[0-2][0-9]))\b", text)))


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text.lower().replace("ё", "е"))


def _content_words(text: str) -> list[str]:
    words = []
    for w in _words(text):
        if len(w) < 3 or w in _RU_STOP:
            continue
        words.append(w)
    return words


def _lcs_len(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def _f1(overlap: int, pred_total: int, ref_total: int) -> float:
    if overlap <= 0 or pred_total <= 0 or ref_total <= 0:
        return 0.0
    precision = overlap / pred_total
    recall = overlap / ref_total
    return 2 * precision * recall / (precision + recall)


def rouge_scores(reference: str, candidate: str) -> dict:
    ref = _content_words(reference)
    cand = _content_words(candidate)
    if not ref or not cand:
        return {"rouge_1": 0.0, "rouge_l": 0.0}

    ref_counts: dict[str, int] = {}
    for w in ref:
        ref_counts[w] = ref_counts.get(w, 0) + 1

    overlap = 0
    used = ref_counts.copy()
    for w in cand:
        if used.get(w, 0) > 0:
            overlap += 1
            used[w] -= 1

    lcs = _lcs_len(cand, ref)
    return {
        "rouge_1": round(_f1(overlap, len(cand), len(ref)), 4),
        "rouge_l": round(_f1(lcs, len(cand), len(ref)), 4),
    }


def simplicity_score(text: str, age: int) -> float:
    """Measures how simple the language is for the target age group.

    Focuses on sentence length and vocabulary complexity — the primary
    quality signal for children's content.
    """
    sentences = [x for x in re.split(r"(?<=[.!?…])\s+", text.strip()) if x.strip()]
    words = _words(text)
    if not sentences or not words:
        return 0.0

    avg_sentence = len(words) / len(sentences)
    max_target = 8 if age <= 8 else 11 if age <= 11 else 16
    # Gentle penalty: up to half the target over limit → 0 score
    sentence_score = max(0.0, 1.0 - max(0.0, avg_sentence - max_target) / (max_target * 1.5))

    long_threshold = 7 if age <= 8 else 10 if age <= 11 else 13
    long_words = [w for w in words if len(w) > long_threshold]
    vocab_score = max(0.0, 1.0 - len(long_words) / max(1, len(words)) * 2.0)

    return round(0.55 * sentence_score + 0.45 * vocab_score, 4)


def example_quality_score(text: str, analogies: list[str] | None = None) -> float:
    """Measures quality and density of analogies and concrete examples.

    For children's content this is more important than literal fact retention:
    a good analogy teaches better than a preserved encyclopedia sentence.
    """
    analogy_list = analogies or []

    # Count example markers in the text
    marker_hits = len(_EXAMPLE_MARKERS.findall(text))
    words = _words(text)
    word_count = max(1, len(words))

    # Normalize: expect ~1 marker per 40 words
    density_score = min(1.0, marker_hits / max(1, word_count / 40))

    # External analogies provided by the model
    has_analogies = 1.0 if len(analogy_list) >= 2 else 0.6 if len(analogy_list) == 1 else 0.2

    # Engagement (questions and imperatives)
    engagement = 1.0 if _ENGAGEMENT.search(text) else 0.65

    return round(0.45 * density_score + 0.35 * has_analogies + 0.20 * engagement, 4)


def term_clarity_score(text: str, glossary: list[dict] | None = None) -> float:
    """Measures how well terms are explained in context.

    Checks that each glossary term appears in the simplified text
    AND that there is an explanatory phrase nearby ("это", "то есть", "значит").
    """
    if not glossary:
        return 0.75  # Neutral: no terms to check
    lower = text.lower().replace("ё", "е")
    explain_pattern = re.compile(r"это|то есть|значит|называется|называют|является|представляет")

    explained = 0
    for entry in glossary:
        term = str(entry.get("term") or "").strip().lower().replace("ё", "е")
        if not term or len(term) < 3:
            continue
        if term not in lower:
            continue
        # Find term position and check for explanation marker within ±80 chars
        pos = lower.find(term)
        context = lower[max(0, pos - 80) : pos + len(term) + 80]
        if explain_pattern.search(context):
            explained += 1
        else:
            explained += 0.5  # Term present but not explicitly explained
    return round(min(1.0, explained / len(glossary)), 4) if glossary else 0.75


def _readability_naturalness(text: str, age: int) -> float:
    """Legacy wrapper kept for compatibility — now calls simplicity_score."""
    return simplicity_score(text, age)


def evaluate_answer_quality(
    original: str,
    simplified: str,
    age: int,
    consistency: dict | None = None,
    key_facts: dict | None = None,
    analogies: list[str] | None = None,
    glossary: list[dict] | None = None,
) -> dict:
    """Main quality evaluation.

    Primary signal: simplicity + example quality (child-oriented).
    Secondary signal: faithfulness (factual grounding, kept lower weight).
    """
    facts = key_facts or extract_key_facts(original)
    coverage = anchor_coverage(facts, simplified)
    rouge = rouge_scores(facts.get("reference_text") or original, simplified)
    faithfulness = float((consistency or {}).get("score") or 0.0)

    simp = simplicity_score(simplified, age)
    examples = example_quality_score(simplified, analogies)
    clarity = term_clarity_score(simplified, glossary)

    # New bleurt_proxy: child-focused metrics dominate
    # Faithfulness kept as a small guard (we still don't want hallucinations)
    bleurt_proxy = round(
        0.45 * simp
        + 0.30 * examples
        + 0.15 * clarity
        + 0.10 * faithfulness,
        4,
    )

    return {
        "rouge_1": rouge["rouge_1"],
        "rouge_l": rouge["rouge_l"],
        "bleurt_proxy": bleurt_proxy,
        "simplicity": simp,
        "example_quality": examples,
        "term_clarity": clarity,
        "faithfulness": faithfulness,
        "key_terms": coverage,
        "targets": {
            "bleurt_proxy": 0.70,
            "simplicity": 0.65,
            "example_quality": 0.55,
        },
        "ok": simp >= 0.55 and examples >= 0.45 and bleurt_proxy >= 0.55,
        "note": (
            "Метрики ориентированы на детский контент: простота изложения (45%) "
            "и качество примеров/аналогий (30%) важнее дословного сохранения фактов."
        ),
    }


def _entity_preserved(entity: str, simplified: str) -> bool:
    e = _flex_norm(entity)
    if len(e) < 3:
        return True
    h = _flex_norm(simplified)
    if e in h:
        return True
    parts = [p for p in e.split() if len(p) >= 3]
    if len(parts) >= 2:
        return all(p in h for p in parts)
    return False


def factual_consistency(original: str, simplified: str) -> dict:
    """Secondary metric: basic factual grounding check.

    Kept as a guard against hallucinations but no longer dominates the
    overall quality score (faithfulness weight dropped to 10% in bleurt_proxy).
    """
    ner_orig = _ner_entities(original)
    kept_ent = 0
    missing: list[str] = []
    for e in sorted(ner_orig):
        if _entity_preserved(e, simplified):
            kept_ent += 1
        else:
            missing.append(e)

    n_ent = len(ner_orig)
    entity_ratio = 1.0 if n_ent == 0 else kept_ent / n_ent

    ys_orig = _years(original)
    ys_simp = set(_years(simplified))
    kept_y = sum(1 for y in ys_orig if y in ys_simp)
    n_y = len(ys_orig)
    year_ratio = 1.0 if n_y == 0 else kept_y / n_y

    terms = _significant_terms(original)
    h_low = simplified.lower().replace("ё", "е")
    kept_t = sum(1 for w in terms if w.lower().replace("ё", "е") in h_low)
    n_t = len(terms)
    term_ratio = 1.0 if n_t == 0 else kept_t / n_t

    if n_ent > 0:
        score = 0.62 * entity_ratio + 0.18 * year_ratio + 0.20 * term_ratio
    elif n_y > 0:
        score = 0.55 * year_ratio + 0.45 * term_ratio
    elif n_t > 0:
        score = term_ratio
    else:
        score = 1.0

    score = max(0.0, min(1.0, float(score)))

    breakdown = {
        "named_entities": {
            "kept": kept_ent,
            "total": n_ent,
            "percent": round(100 * entity_ratio, 1) if n_ent else None,
        },
        "years": {
            "kept": kept_y,
            "total": n_y,
            "percent": round(100 * year_ratio, 1) if n_y else None,
        },
        "term_anchors": {
            "kept": kept_t,
            "total": n_t,
            "percent": round(100 * term_ratio, 1) if n_t else None,
        },
        "weights_note": "Вторичная метрика: защита от галлюцинаций (вес 10% в bleurt_proxy).",
    }

    return {
        "score": score,
        "missing": sorted(missing),
        "breakdown": breakdown,
    }
