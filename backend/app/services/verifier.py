from __future__ import annotations

import re
from functools import lru_cache

from natasha import Doc, MorphVocab, NamesExtractor, NewsEmbedding, NewsNERTagger, Segmenter


@lru_cache(maxsize=1)
def _nlp():
    segmenter = Segmenter()
    emb = NewsEmbedding()
    ner_tagger = NewsNERTagger(emb)
    morph_vocab = MorphVocab()
    names_extractor = NamesExtractor(morph_vocab)
    return segmenter, ner_tagger, names_extractor


def _entities(text: str) -> set[str]:
    segmenter, ner_tagger, _ = _nlp()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)
    ents = set()
    for span in doc.spans:
        if span.type in {"PER", "LOC", "ORG"}:
            ents.add(span.text.strip().lower())
    return {re.sub(r"\s+", " ", e) for e in ents if len(e) >= 3}


def factual_consistency(original: str, simplified: str) -> dict:
    o = _entities(original)
    s = _entities(simplified)
    if not o:
        return {"score": 1.0, "missing": []}
    missing = sorted(list(o - s))
    score = 1.0 - (len(missing) / max(1, len(o)))
    return {"score": score, "missing": missing}

