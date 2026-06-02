"""
Content moderation for a children's encyclopedia.

_is_adult_content(query) returns True if the query contains explicit,
sexual, or otherwise inappropriate material for minors.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Pattern: Russian obscenity + sexual terms the user must not see answered
# ---------------------------------------------------------------------------
# Using prefix/root matching because Russian is heavily inflected.
# Ordered from most specific to most general to avoid false positives.

_ADULT_RE = re.compile(
    r"(?:"
    # ── Explicit Russian mat ───────────────────────────────────────────────
    r"хуй|хуя|хую|хуем|хуев|хуйн|xyй|"         # хуй and forms
    r"пизд|пёзд|пиzд|"                           # пизда, пиздец, etc.
    r"блядь|бляди|блядей|блядск|"
    r"ёбан|ебан|ёбат|ебат|ёб(?:ать|ёт|и\b)|"
    r"залупа|мудак|мудил|мандавошк|"
    r"сперм|"
    # ── Childish / euphemistic terms (user-listed) ────────────────────────
    r"письк[иауей]|письк\w*|"                    # письки, писька
    r"попк[иуей]\b|"                             # попки, попку — NOT попка (=parrot)
    r"сиськ|сись\b|"                             # сиськи, сиська
    r"пипис|пипи\b|"                             # пиписька
    # ── Anatomical / sexual terms ─────────────────────────────────────────
    r"вагин|"                                    # вагина, вагины…
    r"пенис|"
    r"клитор|"
    r"мастурб|онани|"
    r"трахат|трахнут|трахает|"
    r"порн(?:о|ография|охаб)|"
    r"проститут|эскорт\b|"
    r"инцест|педофил|зоофил|"
    r"оргазм|эрекц|"
    r"половой акт|половые органы|"
    r"секс(?:уальн|уальн)?(?:ый|ого|ому|ым|ое|ая|ой|ую|ые|ых|ым|ыми)?\b|"
    # ── Obvious transliterations / leet-speak ─────────────────────────────
    r"cock\b|pussy\b|fuck|porn|dick\b|ass\b|sex\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)

# Whitelist: these words *contain* a blocked root but are safe in context.
# Check before flagging.
_WHITELIST_RE = re.compile(
    r"\b(?:"
    r"сексология|сексолог|"            # scientific field
    r"проститутка_однодневка|"         # not a real word, just in case
    r"секстант|"                       # navigation instrument
    r"сексот"                          # NKVD informer — historical term
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def is_adult_content(query: str) -> bool:
    """
    Return True if the query contains content inappropriate for children.
    Fast regex check — no LLM call needed.
    """
    q = (query or "").strip()
    if not q:
        return False
    # Safe terms that happen to share a root with flagged words
    if _WHITELIST_RE.search(q):
        return False
    return bool(_ADULT_RE.search(q))


# ---------------------------------------------------------------------------
# Child-friendly refusal message
# ---------------------------------------------------------------------------

_REFUSAL_TEXT = (
    "😊 Привет! Этот вопрос не подходит для детской энциклопедии — "
    "здесь мы рассказываем о науке, природе и интересных открытиях. "
    "Попробуй спросить про динозавров, чёрные дыры, вулканы или как работает мозг — "
    "гарантирую, будет намного интереснее! 🌍🚀"
)
