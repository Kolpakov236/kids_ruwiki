"""
Content moderation for a children's encyclopedia.

Two checks:
  is_adult_content(query)  — profanity / 18+ topics
  is_gibberish(query)      — random character sequences, keyboard mashing

Both are pure-Python regex checks with zero latency (no LLM call).
"""
from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────────────────────
# 1. ADULT / PROFANITY FILTER
# ─────────────────────────────────────────────────────────────────────────────
# Strategy: root-prefix matching handles Russian inflection automatically.
# E.g., "пизд" catches пизда / пизды / пиздец / пиздёж / пиздюк / etc.

_ADULT_RE = re.compile(
    r"(?:"
    # ── Главные маты (основные корни) ─────────────────────────────────────
    r"хуй|хуя|хую|хуем|хуев|хуйн|хует|нахуй|похуй|нихуй|охуе|захуят|"
    r"отхуяр|хуесос|похуист|хуйл|"
    r"xyй|xuj|"                                        # транслитерации
    r"пизд|пёзд|пиzд|"                                 # пизда, пиздец, …
    r"ёбан|ёбат|ёбнут|еблан|ёбарь|ёбств|"
    r"ебан|ебат|ебнут|еблан|ебарь|ебств|"
    r"заёб|объёб|доёб|отъёб|наёб|"
    r"залупа|залупин|"
    r"блядь|бляди|блядей|блядск|блядун|блядств|"
    r"мудак|мудил|мудозвон|мудофл|"
    r"шлюх|давалк|"
    r"гандон|"
    r"пидор|педик|"                                    # гомофобные слуры — не для детей
    r"ублюдок|ублюдк|"
    r"мразь|"
    # ── Детские/эвфемистические (перечислены пользователем) ───────────────
    r"письк[иауей]?|"                                  # письки, писька
    r"попк[иуей]\b|"                                   # попки, попку (не попка = попугай)
    r"сиськ|сись\b|"                                   # сиськи, сиська
    r"пипис|пипи\b|"                                   # пиписька
    # ── Анатомические / сексуальные термины ───────────────────────────────
    r"вагин|"
    r"пенис|"
    r"клитор|"
    r"мастурб|онани|"
    r"трахат|трахнут|трахает|трахается|"
    r"дрочит|дрочер|дрочн|"
    r"порн(?:о|ография|охаб|уха)|"
    r"проститут|эскортниц|"
    r"инцест|педофил|зоофил|"
    r"оргазм|эрекц|"
    r"половой акт|половые органы|"
    r"анальн(?:ый|ого|ому|ым|ое|ая|ой|ую|ые|ых|ыми)|"
    # — «секс» — блокируем явные запросы, но «сексология» в whitelist ниже
    r"секс(?:ом|а\b|у\b|е\b|ом\b)|"                  # секса, сексу, сексе (не «сексуальный»)
    r"занятие сексом|заниматься сексом|"
    # ── Сленговые производные ─────────────────────────────────────────────
    r"срака|сраку|сраки|"
    r"жопа\w*|"                                        # жопа, жопный, жопник…
    r"ебать|ебёт|ебут|ебись|"
    r"ёбать|ёбёт|ёбут|ёбись|"
    r"сука\w{0,3}(?:\s|$)|"                            # сука как ругательство (не суках…)
    r"сучка\b|сучки\b|"
    r"срать|засрат|засери|обосрат|"
    # ── Английские / транслитерации ────────────────────────────────────────
    r"fuck|shit\b|cock\b|cunt\b|pussy\b|dick\b|"
    r"bitch\b|whore\b|slut\b|ass\b|boobs?\b|"
    r"porn|porno|nsfw"
    r")",
    re.IGNORECASE | re.UNICODE,
)

# Whitelist: слова, корни которых совпадают с матом, но безопасны в контексте.
_WHITELIST_RE = re.compile(
    r"\b(?:"
    r"секстант|секстина|сексология|сексолог|"    # наука, инструмент
    r"ублюдочный\s+алгоритм|"                    # техн. контекст (маловероятно)
    r"классика|гандон\w{4,}"                      # длинные производные от гандон маловероятны
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def is_adult_content(query: str) -> bool:
    """Return True if query contains material inappropriate for children."""
    q = (query or "").strip()
    if not q:
        return False
    if _WHITELIST_RE.search(q):
        return False
    return bool(_ADULT_RE.search(q))


# ─────────────────────────────────────────────────────────────────────────────
# 2. GIBBERISH / RANDOM-CHARACTERS FILTER
# ─────────────────────────────────────────────────────────────────────────────

_RU_VOWELS = frozenset("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
_EN_VOWELS = frozenset("aeiouAEIOU")
_ALL_VOWELS = _RU_VOWELS | _EN_VOWELS

# Й между двумя согласными — в русском языке невозможно (кроме edge cases на стыке морфем).
# «гкйш», «шкй» — типичный признак случайного набора.
_IMPOSSIBLE_YOT_RE = re.compile(
    r"[бвгджзклмнпрстфхцчшщ]й[бвгджзклмнпрстфхцчшщ]",
    re.IGNORECASE,
)

# Паттерны клавиатурного мэшинга — соседние клавиши подряд.
_KEYBOARD_ROW_RE = re.compile(
    r"(?:"
    # ── Русская раскладка, верхний ряд (ЙЦУКЕН) ──────────────────────────
    r"йцуке|цукен|укенг|кенгш|енгшщ|нгшщз|гшщзх|шщзхъ|"
    r"ъхзщш|хзщшг|зщшгн|щшгне|"              # справа налево
    # ── Средний ряд (ФЫВАПРОЛДЖ) ─────────────────────────────────────────
    r"фывап|ывапр|вапро|апрол|пролд|ролдж|"
    r"фыва|ывап|вапр|апро|прол|олдж|"         # 4-буквенные
    r"жджол|джолр|жолра|"                      # справа налево
    # ── Нижний ряд (ЯЧСМИТЬ) ─────────────────────────────────────────────
    r"ячсми|чсмит|смить|митьб|итьбю|"
    r"ясчми|юбьти|"                            # справа налево
    # ── Смешанные вертикальные паттерны ──────────────────────────────────
    r"фыавр|аывфп|уйцке|"
    # ── QWERTY ────────────────────────────────────────────────────────────
    r"qwert|werty|ertyu|rtyui|tyuio|yuiop|"
    r"asdfg|sdfgh|dfghj|fghjk|ghjkl|"
    r"zxcvb|xcvbn|cvbnm|"
    r"poiuy|lkjhg|mnbvc|"                      # QWERTY справа налево
    # ── Числовой ряд подряд ───────────────────────────────────────────────
    r"123456|234567|345678|456789|567890|"
    r"987654|876543|765432|654321"
    r")",
    re.IGNORECASE,
)

# Слова-исключения: короткие аббревиатуры (ДНК, ГДЗ, НАТО, СССР и т. д.)
# — в них мало гласных, но они легитимны.
_ABBREVIATION_RE = re.compile(r"^[А-ЯЁA-Z]{2,6}$")


def _token_is_gibberish(token: str) -> bool:
    """Heuristic: is this single alphabetic token random/nonsensical?"""
    if len(token) < 4:
        return False  # слишком короткое — не можем судить

    # Аббревиатуры заглавными — пропускаем
    if _ABBREVIATION_RE.match(token):
        return False

    t = token.lower()

    # Й между двумя согласными (шегкЙш, гкЙш, etc.)
    if _IMPOSSIBLE_YOT_RE.search(t):
        return True

    vowels = sum(1 for c in t if c in _ALL_VOWELS)

    # Полное отсутствие гласных в слове >= 4 букв
    if vowels == 0:
        return True

    # Очень низкая доля гласных в длинном токене (>= 7 букв)
    if len(t) >= 7 and vowels / len(t) < 0.15:
        return True

    # Подряд >= 6 согласных (вздрогнуть = 4 согл. — это нормально)
    max_cc = cur_cc = 0
    for c in t:
        if c in _ALL_VOWELS:
            cur_cc = 0
        else:
            cur_cc += 1
            if cur_cc > max_cc:
                max_cc = cur_cc
    if max_cc >= 6:
        return True

    # Клавиатурный мэшинг
    if _KEYBOARD_ROW_RE.search(t):
        return True

    # Повторяющийся 2-символьный паттерн 3+ раза подряд или в целом
    # («ыа» в «фыаввыафыа» → «ыа» встречается 3 раза → мэшинг)
    for i in range(len(t) - 1):
        bigram = t[i:i + 2]
        if t.count(bigram) >= 3:
            return True

    return False


def is_gibberish(query: str) -> bool:
    """
    Return True if the query is clearly random/meaningless input.

    Rules:
    - Keyboard-row mashing anywhere in the query
    - Most alphabetic tokens (4+ letters) are individually gibberish
    - Single-token query that is gibberish
    """
    q = (query or "").strip()
    if not q:
        return False

    # Быстрая проверка на keyboard-row во всём тексте
    if _KEYBOARD_ROW_RE.search(q):
        return True

    tokens = re.findall(r"[а-яёА-ЯЁa-zA-Z]+", q)
    meaningful = [t for t in tokens if len(t) >= 4]

    if not meaningful:
        # Только цифры / знаки — не наш профиль
        return False

    gibberish_flags = [_token_is_gibberish(t) for t in meaningful]
    gibberish_count = sum(gibberish_flags)

    if gibberish_count == 0:
        return False

    # Хотя бы один токен пойман по правилу "невозможный Й" — надёжный сигнал
    if any(
        _IMPOSSIBLE_YOT_RE.search(t.lower())
        for t, flag in zip(meaningful, gibberish_flags)
        if flag
    ):
        return True

    # Хотя бы один токен пойман по правилу "нет гласных вообще"
    if any(
        sum(1 for c in t if c in _ALL_VOWELS) == 0
        for t, flag in zip(meaningful, gibberish_flags)
        if flag
    ):
        return True

    # Большинство токенов (≥ 50%) — тарабарщина
    if gibberish_count / len(meaningful) >= 0.5:
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Response messages
# ─────────────────────────────────────────────────────────────────────────────

_REFUSAL_TEXT = (
    "😊 Привет! Этот вопрос не подходит для детской энциклопедии — "
    "здесь мы рассказываем о науке, природе и интересных открытиях. "
    "Попробуй спросить про динозавров, чёрные дыры, вулканы или как работает мозг — "
    "гарантирую, будет намного интереснее! 🌍🚀"
)

_GIBBERISH_TEXT = (
    "🤔 Кажется, это случайный набор символов — я не понял вопрос. "
    "Попробуй написать нормальным текстом! Например: «что такое чёрная дыра» "
    "или «куда делись динозавры». Я готов ответить! 📚"
)
