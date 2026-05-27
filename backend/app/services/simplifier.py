from __future__ import annotations

import re
from typing import Any


def _split_long_sentences(text: str, max_words: int) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        words = p.split()
        if len(words) <= max_words:
            out.append(p)
            continue
        chunks = [words[i : i + max_words] for i in range(0, len(words), max_words)]
        out.extend([" ".join(c).strip(" ,;") + "." for c in chunks if c])
    return " ".join(out)


def _simple_lexicon(text: str) -> tuple[str, list[dict], list[str]]:
    rules = [
        ("гипоталамус", "отдел мозга"),
        ("митохондри", "энергетическая станция клетки"),
        ("фотосинтез", "как растения делают еду из света"),
        ("квантов", "очень маленький (на уровне частиц)"),
        ("суперпозици", "состояние, когда сразу несколько вариантов возможны"),
    ]
    glossary: list[dict] = []
    analogies: list[str] = []
    out = text
    for needle, repl in rules:
        if re.search(needle, out, flags=re.IGNORECASE):
            glossary.append({"term": needle, "definition": repl})
            analogies.append(f"{needle}: представь это как {repl}.")
            out = re.sub(needle, repl, out, flags=re.IGNORECASE)
    return out, glossary, analogies


def simplify_local(original_text: str, age: int) -> dict:
    max_words = 12 if age <= 8 else 15 if age <= 10 else 20 if age <= 12 else 25
    t = re.sub(r"[ \t]+\n", "\n", original_text).strip()
    t = _split_long_sentences(t, max_words=max_words)
    t, glossary, analogies = _simple_lexicon(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return {"text": t, "glossary": glossary, "analogies": analogies}


def improve_child_readability(text: str, age: int) -> dict:
    replacements = {
        r"основная физическая теория": "важное объяснение в физике",
        r"описывающая природу в масштабе атомов и субатомных частиц": "объясняет, как ведут себя атомы и частицы меньше атома",
        r"в масштабе атомов и субатомных частиц": "в мире атомов и очень маленьких частиц",
        r"субатомн\w* частиц\w*": "частиц меньше атома",
        r"квантовая химия": "раздел химии про маленькие частицы",
        r"квантовая теория поля": "сложная наука о частицах и полях",
        r"квантовая технология": "техника, которая использует правила маленьких частиц",
        r"квантовая информатика": "способ работать с информацией с помощью квантовых правил",
        r"классическая физика": "обычная физика",
        r"количественн\w* описан\w*": "точного объяснения с числами",
        r"аспекты природы": "явления природы",
        r"атомн\w* и субатомн\w* масштаб\w*": "очень маленьком мире",
    }

    out = text
    for pattern, repl in replacements.items():
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)

    glossary: list[dict] = []
    analogies: list[str] = []
    if re.search(r"\bквант", out, flags=re.IGNORECASE):
        glossary.append(
            {
                "term": "квантовая механика",
                "definition": "раздел физики про то, как ведут себя атомы и очень маленькие частицы",
            }
        )
        analogies.append("Это похоже на игру с особыми правилами: в маленьком мире предметы ведут себя не так, как мяч или книга.")

    out = re.sub(r"\*\*(.*?)\*\*", r"\1", out)
    out = re.sub(r"__(.*?)__", r"\1", out)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)
    out = re.sub(r"(?m)^\s*[-*+]\s+", "", out)
    out = re.sub(r"<[^>]+>", "", out)
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return {"text": out, "glossary": glossary, "analogies": analogies}


def build_extractive_fallback(original_text: str, age: int, key_facts: dict[str, Any] | None = None) -> dict:
    """Fast fallback when LLM is unavailable.

    It is intentionally extractive: keep factual anchors and simplify sentence boundaries
    instead of inventing a free paraphrase. This guarantees a usable response on timeouts.
    """
    facts = key_facts or {}
    required = [str(x).strip() for x in (facts.get("required_terms") or []) if str(x).strip()]
    formulas = [str(x).strip() for x in (facts.get("formulas") or []) if str(x).strip()]
    anchors = [*formulas[:8], *required[:18]]

    normalized = re.sub(r"\s+", " ", original_text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", normalized) if s.strip()]

    selected: list[str] = []
    selected_keys: set[str] = set()
    for anchor in anchors:
        key = anchor.lower().replace("ё", "е")
        for sentence in sentences:
            s_key = sentence.lower().replace("ё", "е")
            parts = [p for p in key.split() if len(p) >= 3]
            if key in s_key or (len(parts) >= 2 and all(p in s_key for p in parts)):
                clean = sentence[:320].strip()
                if clean and clean.lower() not in selected_keys:
                    selected.append(clean)
                    selected_keys.add(clean.lower())
                break
        if len(selected) >= 8:
            break

    if not selected:
        selected = sentences[:6]

    max_words = 9 if age <= 8 else 13 if age <= 11 else 18
    simple = _split_long_sentences(" ".join(selected), max_words=max_words)
    simple = re.sub(r"\s+", " ", simple).strip()
    if simple and not re.search(r"[.!?…]$", simple):
        simple += "."

    intro = "Главная мысль: это тема, где важно сохранить точные слова и обозначения из статьи."
    text = f"{intro} {simple}"
    if formulas:
        text += " Важно запомнить формулу или обозначение: " + ", ".join(formulas[:4]) + "."
    text += " Хочешь узнать это на простом примере из жизни?"

    glossary = [{"term": t, "definition": "важный термин из статьи"} for t in required[:5]]
    analogies = ["Это похоже на карту: сначала держим главные обозначения, а потом объясняем дорогу простыми словами."]
    quiz = [
        {"question": "Какой главный термин встретился в статье?", "answer": required[0] if required else "Посмотри на главную мысль."},
        {"question": "Какая формула или единица была важной?", "answer": formulas[0] if formulas else "В этой теме важнее термины, чем формулы."},
        {"question": "Почему нельзя выкидывать точные слова?", "answer": "Потому что они держат смысл статьи."},
    ]
    return {
        "main_idea": intro,
        "simplified_text": text,
        "theories": [],
        "reasoning_steps": [
            "LLM не успела ответить, поэтому включён быстрый локальный режим.",
            "Ответ собран из предложений статьи, где есть обязательные термины и формулы.",
            "Синтаксис упрощён, но факты и обозначения сохранены.",
        ],
        "learning_steps": [
            "Сначала находим главный термин.",
            "Затем смотрим важные обозначения и формулы.",
            "Потом читаем короткое объяснение.",
            "В конце проверяем себя вопросами.",
        ],
        "glossary": glossary,
        "analogies": analogies,
        "quiz": quiz,
    }

