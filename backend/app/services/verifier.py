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
            # Avoid artificial mixed-script n-grams like "ток alternating".
            if len(scripts) > 1:
                continue
            norm_parts = [p.lower().replace("ё", "е") for p in parts]
            phrase = " ".join(parts)
            key = " ".join(norm_parts)
            if key in seen:
                continue
            # Keep phrases that look like domain terms, not arbitrary glue.
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
    """Извлекает якоря, которые модель обязана сохранить.

    Это дешёвая локальная замена отдельного extractor-LLM: термины, устойчивые фразы,
    годы, формулы, обозначения и единицы. Именно эти якоря используются для ROUGE.
    """
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
    """Лёгкий ROUGE-1 / ROUGE-L без внешних зависимостей.

    В продукте без эталонного детского ответа reference = исходный фрагмент статьи.
    Это не «идеальный ROUGE», но полезный сигнал сохранения ключевых слов и порядка фактов.
    """
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


def _readability_naturalness(text: str, age: int) -> float:
    sentences = [x for x in re.split(r"(?<=[.!?…])\s+", text.strip()) if x.strip()]
    words = _words(text)
    if not sentences or not words:
        return 0.0

    avg_sentence = len(words) / len(sentences)
    max_target = 8 if age <= 8 else 11 if age <= 11 else 16
    sentence_score = max(0.0, 1.0 - max(0.0, avg_sentence - max_target) / max_target)

    long_words = [w for w in words if len(w) > (7 if age <= 8 else 10 if age <= 11 else 13)]
    vocab_score = max(0.0, 1.0 - len(long_words) / max(1, len(words)))

    has_engagement = 1.0 if re.search(r"[?]|знаешь|представь|похоже|как будто", text, flags=re.IGNORECASE) else 0.75
    return round(0.45 * sentence_score + 0.35 * vocab_score + 0.20 * has_engagement, 4)


def evaluate_answer_quality(
    original: str,
    simplified: str,
    age: int,
    consistency: dict | None = None,
    key_facts: dict | None = None,
) -> dict:
    facts = key_facts or extract_key_facts(original)
    coverage = anchor_coverage(facts, simplified)
    # ROUGE считаем не по всей статье, а по обязательным фактическим якорям.
    rouge = rouge_scores(facts.get("reference_text") or original, simplified)
    faithfulness = float((consistency or {}).get("score") or 0.0)
    naturalness = _readability_naturalness(simplified, age)

    # BLEURT требует отдельной тяжёлой модели; для локального продукта используем прозрачный proxy.
    bleurt_proxy = round(
        0.42 * naturalness
        + 0.28 * faithfulness
        + 0.18 * min(1.0, rouge["rouge_l"] / 0.65)
        + 0.12 * coverage["score"],
        4,
    )

    return {
        "rouge_1": rouge["rouge_1"],
        "rouge_l": rouge["rouge_l"],
        "bleurt_proxy": bleurt_proxy,
        "naturalness": naturalness,
        "key_terms": coverage,
        "targets": {
            "rouge_1": 0.65,
            "rouge_l": 0.65,
            "bleurt_proxy": 0.75,
            "key_terms": 0.72,
        },
        "ok": coverage["score"] >= 0.72 and bleurt_proxy >= 0.62 and faithfulness >= 0.55,
        "note": (
            "ROUGE считается к извлечённым фактическим якорям (термины, формулы, годы), а не ко всей статье; "
            "BLEURT-proxy — локальная оценка естественности, достоверности и связности без тяжёлой модели."
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
    """
    Оценка близости упрощённого текста к источнику без эталонного «золотого» пересказа.

    Раньше считался только пересечение NER по двум текстам — на упрощённом тексте NER
    часто «ломается», из‑за чего score завышал потери (типичный вид ~20%).

    Сейчас:
    - имена/места из оригинала ищутся в упрощённом тексте как подстроки (гибкая нормализация);
    - годы и крупные числа как якоря фактов;
    - пересечение значимых длинных слов (якоря терминов), без штрафа за перефраз при сохранении якорей.
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

    # Веса: сущности главное; годы и термины стабилизируют оценку, когда NER пустой или редкий.
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
        "weights_note": "Имена и места — основной сигнал; годы и ключевые слова дополняют картину.",
    }

    return {
        "score": score,
        "missing": sorted(missing),
        "breakdown": breakdown,
        "legacy_ner_only_note": "Оценка обновлена: совпадение сущностей проверяется по тексту ответа, не по повторному NER.",
    }
