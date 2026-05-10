from __future__ import annotations

import re


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

