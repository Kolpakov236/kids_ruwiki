from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx

from app.settings import settings

# Per-request model override (set by pipeline when model_id is specified)
_model_override: ContextVar[str | None] = ContextVar("model_override", default=None)

logger = logging.getLogger(__name__)


@dataclass
class SummarizationResult:
    condensed_text: str
    core_concept: str
    key_terms: list[dict[str, str]]
    key_dates: list[str]
    key_names: list[str]
    key_numbers: list[str]
    raw: dict[str, Any]


@dataclass
class LLMResult:
    main_idea: str
    simplified_text: str
    reasoning_steps: list[str]
    learning_steps: list[str]
    glossary: list[dict[str, str]]
    analogies: list[str]
    quiz: list[dict[str, str]]
    theories: list[dict[str, str]]
    raw: dict[str, Any]


class LLMError(RuntimeError):
    pass


def model_variant() -> str:
    return f"{settings.llm_provider}:{settings.llm_model}:two-step-v2"


# ---------------------------------------------------------------------------
# Prompt builders — Step 1: Summarization
# ---------------------------------------------------------------------------

def _summarize_system_prompt() -> str:
    return (
        "Ты аналитик, который готовит материал для детского педагога. "
        "Из статьи нужно извлечь не просто список фактов, а концептуальную структуру: "
        "что это такое, как это работает (механизм/процесс), почему это происходит, "
        "какие причинно-следственные связи ключевые. "
        "Сохрани все конкретные факты: термины с определениями, даты, имена, числа, формулы, единицы. "
        "Дополнительно укажи: в чём главная сложность объяснения этой темы детям, "
        "какое самое частое неверное представление о ней. "
        "Ничего не добавляй от себя — только то, что есть в тексте статьи. "
        "Если в тексте нет ответа на какой-то аспект — не пытайся придумать, используй только факты из статьи. "
        "Не используй внешние знания. Ответь только валидным JSON."
    )


def _summarize_user_prompt(text: str, query: str) -> str:
    return (
        f"Тема: {query}\n\n"
        "Проанализируй статью и заполни JSON-структуру ниже.\n\n"
        "condensed_text: связный текст, сохраняющий ВСЕ факты + причинно-следственные связи + механизм работы. "
        "Убери вводные фразы и воду, но не теряй ни одного конкретного факта.\n"
        "core_concept: одно предложение — суть явления и как оно работает.\n"
        "core_mechanism: 2-3 предложения — пошаговое описание механизма/процесса.\n"
        "main_misconception: типичное ошибочное представление об этой теме.\n"
        "hardest_to_explain: что труднее всего объяснить ребёнку и почему.\n"
        "key_terms: [{\"term\": ..., \"definition\": ...}] — термины с определениями.\n"
        "key_dates: [\"год: что произошло\"]\n"
        "key_names: [\"имя: кто это\"]\n"
        "key_numbers: [\"число + единица: что означает\"]\n\n"
        "Формат ответа (строго JSON):\n"
        '{"condensed_text":"...","core_concept":"...","core_mechanism":"...",'
        '"main_misconception":"...","hardest_to_explain":"...",'
        '"key_terms":[{"term":"...","definition":"..."}],'
        '"key_dates":["..."],"key_names":["..."],"key_numbers":["..."]}\n\n'
        f"Текст статьи:\n{text}"
    )


# ---------------------------------------------------------------------------
# Prompt builders — Step 2: Simplification
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    return (
        "Ты опытный педагог и автор детских научно-популярных книг. "
        "Твоя задача — написать объяснение так, чтобы ребёнок не просто запомнил слова, "
        "а понял, КАК это работает. Качественная планка: после прочтения ребёнок должен "
        "суметь объяснить тему своими словами и предсказать, что случится в новой ситуации с этим явлением.\n"
        "Принципы, которых придерживайся строго:\n"
        "1. НЕ ИСТОРИЯ, А МЕХАНИЗМ. Никаких «учёные долго искали», «в XIX веке открыли», "
        "«слово происходит от...» — если факт не объясняет КАК явление работает, его нет в тексте. "
        "История открытия и биографии учёных в simplified_text запрещены.\n"
        "2. КОНКРЕТНОЕ РАНЬШЕ АБСТРАКТНОГО. Начинай с поведения явления, которое можно представить — "
        "не с определения и не с даты.\n"
        "3. МЕХАНИЗМ ПОШАГОВО. Объясняй не «что это», а «что происходит шаг за шагом прямо сейчас».\n"
        "4. АНАЛОГИЯ ПРОВЕРЯЕТ ПОНИМАНИЕ. Хорошая аналогия позволяет правильно ответить на вопрос "
        "«что будет, если...». Плохая — просто похожа внешне. Выбирай аналогию, которая работает. "
        "При формировании аналогий всегда учитывай возрастную группу: для младшего возраста (6-8 лет) "
        "аналогии только из повседневного опыта ребёнка — игрушки, мультфильмы, прогулки, еда дома. "
        "Не используй аналогии про вождение, деньги или взрослый труд для детей до 9 лет.\n"
        "5. БЕЗ ПУСТЫХ ФРАЗ. Не пиши «это очень интересно» или «учёные долго исследовали» — "
        "каждое предложение несёт конкретную информацию.\n"
        "6. ТЕРМИНЫ НЕЛЬЗЯ ВЫБРАСЫВАТЬ. Каждый научный термин должен появиться с объяснением "
        "рядом — в скобках или через тире.\n"
        "7. ТЫ — ДЕТСКАЯ ЭНЦИКЛОПЕДИЯ. Если пользователь спрашивает «какая ты модель», «кто тебя создал», "
        "«ты ChatGPT?» или любые похожие вопросы про ИИ и технологии — не отвечай на этот вопрос напрямую. "
        "Вместо этого верни JSON где simplified_text содержит: «Я — Ruwiki Explain, детская энциклопедия! "
        "Я здесь, чтобы объяснять науку, природу и всё интересное. Лучше спроси меня про динозавров, "
        "чёрные дыры или как работает мозг 🚀», а main_idea — пустую строку.\n"
        "Пиши без Markdown. Ответь только валидным JSON."
    )


def _age_instructions(age: int) -> str:
    if age <= 8:
        return (
            "ВОЗРАСТ 6-8 ЛЕТ. Правила:\n"
            "- Предложения не длиннее 7 слов.\n"
            "- Только слова, которые ребёнок знает из дома, мультиков, улицы.\n"
            "- Каждый новый термин объясни через знакомый предмет: не «молекула», а «молекула — "
            "это крохотный кусочек, меньше пылинки».\n"
            "- Обращайся к ребёнку напрямую: «ты», «тебе», «посмотри».\n"
            "- Для аналогий используй РАЗНЫЕ приёмы — не повторяй одну формулу:\n"
            "  • Сравни с игрой или игрушкой: «это как конструктор Lego»\n"
            "  • Сравни с едой или природой: «это как дерево с ветками»\n"
            "  • Используй риторический вопрос: «Ты видел, как мыльный пузырь лопается?»\n"
            "  • Расскажи мини-историю: «Однажды учёный увидел, что яблоко падает...»\n"
            "- ЗАПРЕЩЕНО: шаблон «Представь, что ты [персонаж/профессия]» — это скучно и не работает для всех тем.\n"
            "- Перед выбором аналогии задай себе вопрос: «Видел ли ребёнок 6-8 лет это в своей жизни?» Если нет — выбери другую.\n"
            "- Аналогии ТОЛЬКО из повседневного опыта ребёнка: игрушки, мультики, прогулки, еда дома, школа.\n"
            "- НЕ используй аналогии: вождение автомобиля, деньги и финансы, алкоголь, взрослый труд, жарка мяса на костре.\n"
            "- Не больше 2 новых понятий за раз."
        )
    if age <= 11:
        return (
            "ВОЗРАСТ 9-11 ЛЕТ. Правила:\n"
            "- Предложения до 12 слов.\n"
            "- Термины с немедленным пояснением: «фотосинтез — то, как растения делают еду из света».\n"
            "- Опирайся на знания школьной программы (сложение, умножение, части тела, погода, страны).\n"
            "- Можно один сравнительно сложный пример, если сначала дать простой.\n"
            "- Задавай риторические вопросы: «Ты замечал, что...?»"
        )
    return (
        "ВОЗРАСТ 12-14 ЛЕТ. Правила:\n"
        "- Ясный, прямой стиль без лишних слов.\n"
        "- Научные термины можно использовать, но с чётким определением при первом появлении.\n"
        "- Можно ссылаться на физику, химию, биологию на уровне 6-8 класса.\n"
        "- Аналогии из технологий, спорта, современной жизни — они близки этому возрасту.\n"
        "- Избегай покровительственного тона; пиши как для умного сверстника."
    )


def _mode_instructions(mode: str) -> str:
    if mode == "micro":
        return (
            "РЕЖИМ: ЧЕРНОВИК. simplified_text — ровно 1-2 предложения: только суть явления, "
            "без истории, без вступлений. Остальные поля — минимальны."
        )
    if mode == "simple":
        return (
            "РЕЖИМ: ПРОСТО. 6-9 предложений. Объясни главную идею + как это работает + одна аналогия. "
            "Всё второстепенное убрать, но ответ должен ПОЛНОСТЬЮ отвечать на вопрос."
        )
    if mode == "detailed":
        return (
            "РЕЖИМ: ПОДРОБНО. 15-20 предложений. Включи все важные факты, причинно-следственные связи, "
            "исторический контекст (если нужен), примеры из жизни. 2-3 аналогии, полный глоссарий."
        )
    return (
        "РЕЖИМ: СБАЛАНСИРОВАННО. 12-16 предложений. "
        "Структура: (1) что это такое, (2) как работает механизм пошагово, "
        "(3) почему это важно / где встречается в жизни, (4) одна сильная аналогия, "
        "(5) интересный факт — ТОЛЬКО если он есть в предоставленном контексте статьи. "
        "Ответ должен ПОЛНОСТЬЮ раскрывать вопрос — не обрывай на середине."
    )


def _output_schema_text() -> str:
    return (
        '{"main_idea":"...",'
        '"simplified_text":"...",'
        '"reasoning_steps":["..."],'
        '"learning_steps":["..."],'
        '"glossary":[{"term":"...","definition":"..."}],'
        '"analogies":["..."],'
        '"quiz":[{"question":"...","answer":"...","choices":["...","...","...","..."]}],'
        '"theories":[{"title":"...","text":"..."}]}'
    )


def _simplify_user_prompt(
    text: str,
    age: int,
    mode: str,
    key_facts: dict | None = None,
    summary: SummarizationResult | None = None,
    query: str = "",
    retry_context: str = "",
) -> str:
    facts = key_facts or {}
    required_terms = [str(x) for x in facts.get("required_terms") or []][:48]
    formulas = [str(x) for x in facts.get("formulas") or []][:16]

    if summary:
        for item in summary.key_terms:
            t = str(item.get("term") or "").strip()
            if t and t not in required_terms:
                required_terms.append(t)
        for n in summary.key_numbers:
            s = str(n).strip()
            if s and s not in formulas:
                formulas.append(s)

    fact_block = ""
    if required_terms:
        fact_block += "ТЕРМИНЫ, КОТОРЫЕ ДОЛЖНЫ ПОЯВИТЬСЯ В ТЕКСТЕ: " + "; ".join(required_terms[:48]) + ".\n"
    if formulas:
        fact_block += "ФОРМУЛЫ / ЕДИНИЦЫ / ОБОЗНАЧЕНИЯ (сохрани дословно): " + "; ".join(formulas[:16]) + ".\n"

    # Extract extra context from summary
    mechanism_hint = ""
    misconception_hint = ""
    if summary:
        raw = summary.raw or {}
        core_mech = str(raw.get("core_mechanism") or "").strip()
        misconception = str(raw.get("main_misconception") or "").strip()
        hardest = str(raw.get("hardest_to_explain") or "").strip()
        if core_mech:
            mechanism_hint = f"МЕХАНИЗМ (обязательно объясни): {core_mech}\n"
        if misconception:
            misconception_hint = f"ЧАСТОЕ ЗАБЛУЖДЕНИЕ (опровергни мягко): {misconception}\n"
        if hardest:
            misconception_hint += f"ТРУДНЕЕ ВСЕГО ОБЪЯСНИТЬ: {hardest}\n"

    reasoning_guide = (
        "ШАГ 1 — Что здесь самое важное для понимания? "
        "Сформулируй одно предложение — суть явления.\n"
        "ШАГ 2 — Как это работает пошагово? Перечисли 3-5 шагов механизма.\n"
        "ШАГ 3 — Какое типичное заблуждение детей об этой теме? "
        "Как в объяснении его аккуратно исправить?\n"
        "ШАГ 4 — Выбери ОДНУ аналогию, которая работает как тест: "
        "если понял аналогию — можешь предсказать поведение оригинала. "
        "Выбирай из: вода/трубы/насос, электрический ток, строительство, игра с правилами, "
        "кулинария/смешивание, живой организм, транспортная сеть, склад/почта. "
        "Объясни в reasoning_steps, ПОЧЕМУ именно эта аналогия — лучшая для этой темы.\n"
        "ШАГ 5 — Спланируй структуру simplified_text:\n"
        "  а) КРЮЧОК (1-2 предл.): удивительный факт о том, КАК явление себя ведёт прямо сейчас — "
        "без определений, без истории открытия, без имён учёных.\n"
        "  б) МЕХАНИЗМ (3-5 предл.): что происходит шаг за шагом.\n"
        "  в) СМЫСЛ (1-2 предл.): зачем это нужно, где встречается в жизни.\n"
        "  г) АНАЛОГИЯ (1-3 предл.): та самая, из шага 4 — она должна ОБЪЯСНЯТЬ, не украшать.\n"
        "  д) ВОПРОС (1 предл.): «А ты знаешь...?» или «Хочешь узнать, почему...?»"
    )

    quiz_guide = (
        "quiz — РОВНО 3 вопроса-теста с вариантами ответов. Каждый вопрос:\n"
        "  • question: конкретный вопрос на ПОНИМАНИЕ (не память)\n"
        "    Q1: на механизм — «Почему...» или «Что происходит, когда...»\n"
        "    Q2: на применение — «Где встречается...» или «Что изменится, если...»\n"
        "    Q3: на объяснение — «Как бы ты объяснил...» или «Что значит...»\n"
        "  • answer: ОДНА СТРОКА — правильный ответ. ОН ДОЛЖЕН БЫТЬ ТОЧНОЙ КОПИЕЙ (дословно) одного из вариантов в choices. Не используй синонимы, не меняй порядок слов.\n"
        "  • choices: РОВНО 4 варианта ответа — 1 правильный (answer) + 3 неправильных но похожих.\n"
        "    Варианты короткие (до 8 слов). Перемешай правильный среди других случайно.\n"
        "Формат каждого элемента: {\"question\":\"...\",\"answer\":\"...\",\"choices\":[\"...\",\"...\",\"...\",\"...\"]}"
    )

    glossary_guide = (
        "glossary — 3-5 терминов. Каждое определение:\n"
        "  - заканчивается конкретным примером из жизни\n"
        "  - написано на языке указанного возраста\n"
        "  - нет круговых определений (X — это когда X)"
    )

    analogies_guide = (
        "analogies — 2-3 строки, каждая начинается с «Представь...» или «Это как...». "
        "Они должны помогать ПРЕДСКАЗЫВАТЬ поведение явления, а не просто звучать красиво. "
        "Разные аналогии — разные углы зрения на ту же идею."
    )

    # Detect if the query asks about causes, extinction, disappearance — force theories
    _theories_triggers = (
        "куда", "почему", "зачем", "откуда", "вымер", "исчез", "погиб", "пропал",
        "причин", "теори", "версий", "версия", "гипотез", "что случилось", "как погибли",
        "как вымерли", "как исчезли",
    )
    query_lower = (query or "").lower()
    theories_required = any(t in query_lower for t in _theories_triggers)

    theories_guide = (
        "theories — массив объектов {\"title\": \"...\", \"text\": \"...\"}. "
        + (
            "ОБЯЗАТЕЛЬНО заполни: запрос пользователя касается причин, исчезновения или события — "
            "добавь 2-4 основные версии/теории (например: падение астероида, вулканы, изменение климата и т.д.). "
            if theories_required else
            "Заполни если тема допускает несколько конкурирующих объяснений "
            "(вымирание, катастрофы, исторические загадки, происхождение чего-либо). "
        )
        + "Каждая теория: короткий заголовок + 1-3 предложения для ребёнка. "
        "Если конкурирующих объяснений нет — верни пустой массив []."
    )

    query_line = f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ: {query}\n\n" if query else ""

    retry_block = ""
    if retry_context:
        retry_block = (
            f"⚠️ ПОВТОРНАЯ ПОПЫТКА: {retry_context}.\n"
            "Предыдущий результат не отвечал на запрос пользователя. "
            "В simplified_text фокусируйся КОНКРЕТНО на том, что спросил пользователь, "
            "а не на общем описании темы.\n\n"
        )

    return (
        f"{_age_instructions(age)}\n\n"
        f"{_mode_instructions(mode)}\n\n"
        + query_line
        + retry_block
        + fact_block
        + mechanism_hint
        + misconception_hint
        + "\n--- РАССУЖДЕНИЕ (reasoning_steps) ---\n"
        + reasoning_guide
        + "\n\n--- ТРЕБОВАНИЯ К ВЫХОДНЫМ ПОЛЯМ ---\n"
        + quiz_guide + "\n"
        + glossary_guide + "\n"
        + analogies_guide + "\n"
        + theories_guide + "\n"
        "main_idea — одно предложение, суть темы, понятная без контекста.\n"
        "learning_steps — 4-6 шагов «что узнаём»: коротко, по порядку, для ребёнка.\n"
        "В simplified_text: 2-3 эмодзи для акцентов (🔍 💡 🌱 и т.п.). "
        "Без Markdown, без звёздочек, без списков с дефисами.\n"
        "В конце simplified_text добавь строку: «Читай подробнее в энциклопедии: [название статьи]» — "
        "используй точное название статьи из переменной «ТЕКСТ ДЛЯ ОБРАБОТКИ».\n\n"
        f"Верни строго JSON: {_output_schema_text()}\n\n"
        "--- ТЕКСТ ДЛЯ ОБРАБОТКИ ---\n"
        f"{text}"
    )


def _repair_user_prompt(original: str, simplified: str, age: int, missing: list[str]) -> str:
    return json.dumps(
        {
            "task": "repair_simplification",
            "age": age,
            "missing_items": missing,
            "rules": [
                "Вставь каждый missing item в simplified_text дословно или почти дословно.",
                "Если это формула или единица измерения — перепиши дословно.",
                "Сохрани детский стиль и структуру: крючок → механизм → смысл → аналогия → вопрос.",
                "Не удаляй уже удачные части. Не добавляй новых фактов кроме возвращённых.",
                "Отвечай тем же JSON schema.",
            ],
            "output_schema": _output_schema_text(),
            "original_text": original,
            "current_simplified_text": simplified,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _extract_json(content: str) -> dict[str, Any]:
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not match:
            raise LLMError("llm_returned_non_json")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"llm_returned_malformed_json:{e}") from e


def _extract_partial_simplified_text(content: str) -> str:
    match = re.search(r'"simplified_text"\s*:\s*"((?:\\.|[^"\\])*)', content, flags=re.DOTALL)
    if not match:
        return ""
    raw = match.group(1)
    try:
        return json.loads(f'"{raw}"').strip()
    except json.JSONDecodeError:
        return raw.replace('\\"', '"').replace("\\n", "\n").strip()


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"```(?:\w+)?|```", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.*?)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_(?!_)(.*?)_(?!_)", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_str_list(value: Any, max_items: int = 10, max_len: int = 280) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for x in value:
        s = _clean_text(x)
        if not s:
            continue
        if len(s) > max_len:
            s = s[: max_len - 1].rstrip() + "…"
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _normalize_result(data: dict[str, Any], raw: dict[str, Any]) -> LLMResult:
    main_idea = _clean_text(
        data.get("main_idea") or data.get("mainIdea") or data.get("summary") or ""
    ).strip()
    text = _clean_text(
        data.get("simplified_text")
        or data.get("simplifiedText")
        or data.get("simplified")
        or data.get("answer")
        or data.get("result")
        or data.get("text")
        or ""
    ).strip()
    if isinstance(data.get("output"), dict):
        output = data["output"]
        text = text or _clean_text(
            output.get("simplified_text")
            or output.get("simplifiedText")
            or output.get("simplified")
            or output.get("answer")
            or output.get("result")
            or output.get("text")
            or ""
        ).strip()
    if not text:
        raise LLMError(f"llm_returned_empty_text:{json.dumps(data, ensure_ascii=False)[:500]}")
    if text.lstrip().startswith("{") and "simplified_text" in text:
        try:
            nested = _extract_json(text)
            nested_text = _clean_text(nested.get("simplified_text"))
            if nested_text:
                text = nested_text
        except LLMError:
            nested_text = _extract_partial_simplified_text(text)
            if nested_text:
                text = nested_text

    if not main_idea:
        first_sentence = re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)[0].strip()
        main_idea = first_sentence[:220]

    glossary = data.get("glossary") or []
    if not isinstance(glossary, list):
        glossary = []
    glossary = [
        {"term": _clean_text(x.get("term", "")), "definition": _clean_text(x.get("definition", ""))}
        for x in glossary
        if isinstance(x, dict) and (x.get("term") or x.get("definition"))
    ]

    analogies = data.get("analogies") or []
    if not isinstance(analogies, list):
        analogies = []
    analogies = [_clean_text(x) for x in analogies if _clean_text(x)][:5]

    quiz = data.get("quiz") or data.get("questions") or []
    if not isinstance(quiz, list):
        quiz = []
    parsed_quiz = []
    for x in quiz:
        if not isinstance(x, dict):
            continue
        if not (x.get("question") or x.get("answer")):
            continue
        raw_choices = x.get("choices") or []
        choices = [_clean_text(c) for c in raw_choices if _clean_text(c)][:4] if isinstance(raw_choices, list) else []
        item: dict = {
            "question": _clean_text(x.get("question", "")),
            "answer": _clean_text(x.get("answer", "")),
        }
        if choices:
            item["choices"] = choices
        parsed_quiz.append(item)
        if len(parsed_quiz) >= 3:
            break
    quiz = parsed_quiz

    reasoning_steps = _normalize_str_list(
        data.get("reasoning_steps") or data.get("reasoning") or data.get("thought_steps"),
        max_items=8,
    )
    learning_steps = _normalize_str_list(
        data.get("learning_steps") or data.get("steps") or data.get("lesson_steps"),
        max_items=10,
    )

    raw_theories = data.get("theories") or []
    if not isinstance(raw_theories, list):
        raw_theories = []
    theories = [
        {"title": _clean_text(x.get("title", "")), "text": _clean_text(x.get("text", ""))}
        for x in raw_theories
        if isinstance(x, dict) and (x.get("title") or x.get("text"))
    ][:4]

    return LLMResult(
        main_idea=main_idea,
        simplified_text=text,
        reasoning_steps=reasoning_steps,
        learning_steps=learning_steps,
        glossary=glossary,
        analogies=analogies,
        quiz=quiz,
        theories=theories,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _is_yandex(base: str) -> bool:
    return "llm.api.cloud.yandex.net" in base or str(settings.llm_model).startswith("gpt://")


async def _ollama_chat(messages: list[dict[str, str]]) -> dict[str, Any]:
    url = f"{settings.llm_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": settings.llm_temperature,
            "num_ctx": settings.llm_num_ctx,
            "num_predict": settings.llm_num_predict,
        },
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, follow_redirects=True) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()


async def _openai_compatible_chat(messages: list[dict[str, str]]) -> dict[str, Any]:
    base = settings.llm_base_url.rstrip("/")
    if ("llm.api.cloud.yandex.net" in base) and ("/foundationModels/v1" in base):
        base = base.replace("/foundationModels/v1", "/v1")

    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    is_yandex = _is_yandex(base)

    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Api-Key {settings.llm_api_key}" if is_yandex else f"Bearer {settings.llm_api_key}"
    if settings.openai_project:
        headers["x-folder-id" if is_yandex else "OpenAI-Project"] = settings.openai_project

    effective_model = _model_override.get() or settings.llm_model
    # YandexGPT requires full URI: gpt://<folder_id>/<model>/latest
    if is_yandex and not effective_model.startswith("gpt://") and settings.openai_project:
        effective_model = f"gpt://{settings.openai_project}/{effective_model}/latest"

    payload: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_num_predict,
    }
    # response_format=json_object is NOT supported by Yandex Foundation Models;
    # JSON is enforced via the system prompt ("Ответь только валидным JSON").
    if not is_yandex:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        r = await client.post(url, json=payload)
        if not r.is_success:
            body = r.text[:600]
            logger.error("LLM API %s %s: %s", r.status_code, url, body)
            raise LLMError(f"llm_api_{r.status_code}:{body}")
        return r.json()


def _gemini_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "main_idea": {"type": "STRING"},
            "simplified_text": {"type": "STRING"},
            "reasoning_steps": {"type": "ARRAY", "items": {"type": "STRING"}},
            "learning_steps": {"type": "ARRAY", "items": {"type": "STRING"}},
            "glossary": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "term": {"type": "STRING"},
                        "definition": {"type": "STRING"},
                    },
                    "required": ["term", "definition"],
                },
            },
            "analogies": {"type": "ARRAY", "items": {"type": "STRING"}},
            "quiz": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING"},
                        "answer": {"type": "STRING"},
                        "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["question", "answer", "choices"],
                },
            },
            "theories": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "text": {"type": "STRING"},
                    },
                    "required": ["title", "text"],
                },
            },
        },
        "required": [
            "main_idea", "simplified_text", "reasoning_steps",
            "learning_steps", "glossary", "analogies", "quiz", "theories",
        ],
    }


async def _gemini_chat(messages: list[dict[str, str]]) -> dict[str, Any]:
    api_key = settings.llm_api_key or settings.gemini_api_key or settings.google_api_key
    if not api_key:
        raise LLMError("gemini_api_key_required")

    url = f"{settings.llm_base_url.rstrip('/')}/models/{settings.llm_model}:generateContent"
    params = {"key": api_key}
    system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system").strip()
    contents = [
        {
            "role": "user" if m.get("role") != "assistant" else "model",
            "parts": [{"text": m.get("content", "")}],
        }
        for m in messages
        if m.get("role") != "system"
    ]
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": settings.llm_temperature,
            "maxOutputTokens": settings.llm_num_predict,
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(),
        },
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, follow_redirects=True) as client:
        r = await client.post(url, params=params, json=payload)
        if r.status_code >= 400:
            raise LLMError(f"gemini_api_error:{r.status_code}:{r.text[:500]}")
        return r.json()


def _gemini_finish_reason(raw: dict[str, Any]) -> str:
    candidates = raw.get("candidates")
    if isinstance(candidates, list) and candidates:
        return str(candidates[0].get("finishReason") or "")
    return ""


def _raise_for_incomplete_response(raw: dict[str, Any]) -> None:
    if settings.llm_provider == "gemini":
        reason = _gemini_finish_reason(raw)
        if reason == "MAX_TOKENS":
            raise LLMError("gemini_response_truncated:increase_LLM_NUM_PREDICT")
        if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            raise LLMError(f"gemini_response_blocked:{reason.lower()}")


def _content_from_response(raw: dict[str, Any]) -> str:
    if "message" in raw and isinstance(raw["message"], dict):
        return str(raw["message"].get("content", ""))
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {})
        return str(msg.get("content", ""))
    candidates = raw.get("candidates")
    if isinstance(candidates, list) and candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        if isinstance(parts, list):
            text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
            if text:
                return text
    raise LLMError("llm_response_has_no_content")


async def _call_provider(messages: list[dict[str, str]]) -> dict[str, Any]:
    if settings.llm_provider == "ollama":
        return await _ollama_chat(messages)
    if settings.llm_provider == "openai_compatible":
        return await _openai_compatible_chat(messages)
    if settings.llm_provider == "gemini":
        try:
            return await _gemini_chat(messages)
        except httpx.TimeoutException as e:
            raise LLMError("llm_provider_timeout") from e
    raise LLMError(f"unsupported_llm_provider:{settings.llm_provider}")


async def _chat(messages: list[dict[str, str]]) -> LLMResult:
    raw = await _call_provider(messages)
    _raise_for_incomplete_response(raw)
    content = _content_from_response(raw).strip()
    try:
        data = _extract_json(content)
    except LLMError as e:
        if settings.llm_provider == "gemini":
            raise LLMError(f"gemini_returned_invalid_json:{str(e)}") from e
        if not content:
            raise
        partial_text = _extract_partial_simplified_text(content)
        if partial_text:
            return LLMResult(
                main_idea="", simplified_text=partial_text,
                reasoning_steps=[], learning_steps=[],
                glossary=[], analogies=[], quiz=[], theories=[], raw=raw,
            )
        if str(e).startswith("llm_returned_malformed_json"):
            cleaned = re.sub(r"^\s*\{?\s*\"?simplified_text\"?\s*:\s*\"?", "", content, flags=re.DOTALL)
            cleaned = re.sub(r"\"?\s*,?\s*\"?glossary\"?.*$", "", cleaned, flags=re.DOTALL).strip()
            if cleaned:
                return LLMResult(
                    main_idea="", simplified_text=cleaned,
                    reasoning_steps=[], learning_steps=[],
                    glossary=[], analogies=[], quiz=[], theories=[], raw=raw,
                )
        return LLMResult(
            main_idea="", simplified_text=content,
            reasoning_steps=[], learning_steps=[],
            glossary=[], analogies=[], quiz=[], theories=[], raw=raw,
        )
    return _normalize_result(data, raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def summarize_article(text: str, query: str) -> SummarizationResult:
    """Step 1: Extract conceptual structure and all key facts from the article."""
    truncated = text[: settings.llm_max_input_chars].strip()
    messages = [
        {"role": "system", "content": _summarize_system_prompt()},
        {"role": "user", "content": _summarize_user_prompt(truncated, query)},
    ]
    try:
        raw = await _call_provider(messages)
        content = _content_from_response(raw).strip()
        data = _extract_json(content)

        condensed = _clean_text(data.get("condensed_text") or "").strip()
        core_concept = _clean_text(data.get("core_concept") or "").strip()

        raw_terms = data.get("key_terms") or []
        key_terms = [
            {"term": _clean_text(x.get("term", "")), "definition": _clean_text(x.get("definition", ""))}
            for x in raw_terms
            if isinstance(x, dict)
        ][:20]

        key_dates = [str(x).strip() for x in (data.get("key_dates") or []) if str(x).strip()][:12]
        key_names = [str(x).strip() for x in (data.get("key_names") or []) if str(x).strip()][:16]
        key_numbers = [str(x).strip() for x in (data.get("key_numbers") or []) if str(x).strip()][:16]

        if not condensed or len(condensed) < 50:
            condensed = truncated

        # Attach extra fields to raw so pipeline can use them for prompt enrichment
        raw["core_mechanism"] = _clean_text(data.get("core_mechanism") or "").strip()
        raw["main_misconception"] = _clean_text(data.get("main_misconception") or "").strip()
        raw["hardest_to_explain"] = _clean_text(data.get("hardest_to_explain") or "").strip()

        logger.info(
            "summarize_article: condensed %d→%d chars, terms=%d, dates=%d, names=%d",
            len(truncated), len(condensed), len(key_terms), len(key_dates), len(key_names),
        )
        return SummarizationResult(
            condensed_text=condensed,
            core_concept=core_concept,
            key_terms=key_terms,
            key_dates=key_dates,
            key_names=key_names,
            key_numbers=key_numbers,
            raw=raw,
        )
    except Exception as exc:
        logger.warning("summarize_article failed (%s), falling back to raw text", exc)
        return SummarizationResult(
            condensed_text=truncated,
            core_concept="",
            key_terms=[],
            key_dates=[],
            key_names=[],
            key_numbers=[],
            raw={},
        )


async def simplify_with_llm(
    original_text: str,
    age: int,
    mode: str,
    key_facts: dict | None = None,
    summary: SummarizationResult | None = None,
    query: str = "",
    retry_context: str = "",
) -> LLMResult:
    """Step 2: Turn the structured summary into a clear, deep explanation for children."""
    input_text = original_text
    if summary and summary.condensed_text and len(summary.condensed_text) >= 50:
        input_text = summary.condensed_text

    text = input_text[: settings.llm_max_input_chars].strip()
    return await _chat(
        [
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": _simplify_user_prompt(text, age, mode, key_facts, summary, query, retry_context),
            },
        ]
    )


async def repair_with_llm(original_text: str, simplified_text: str, age: int, missing: list[str]) -> LLMResult:
    original = original_text[: settings.llm_max_input_chars].strip()
    return await _chat(
        [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _repair_user_prompt(original, simplified_text, age, missing)},
        ]
    )


_RANKING_PROMPT_RE = re.compile(
    r"^(?:кто|что|какой|какая|какое|какие)\s+"
    r"(?:самый\s+|самая\s+|самое\s+|самые\s+)?"
    r"(?:лучший|лучшая|лучшее|лучшие|"
    r"величайший|величайшая|величайшее|величайшие|"
    r"известный|известная|известные|знаменитый|знаменитая|знаменитые|"
    r"популярный|популярная|популярные|богатый|богатые|"
    r"умный|умная|умные|быстрый|быстрая|быстрые|"
    r"сильный|сильная|сильные|главный|главная|главные)\b",
    re.IGNORECASE | re.UNICODE,
)


def _llm_only_user_prompt(query: str, age: int, mode: str) -> str:
    is_ranking = bool(_RANKING_PROMPT_RE.match(query.strip()))

    if is_ranking:
        context_note = (
            "ВАЖНО: Это вопрос о рейтинге или достижениях. "
            "Назови 2-3 конкретных человека (или объекта/страны), которые считаются лучшими в этой категории. "
            "Для каждого — конкретные достижения, рекорды, награды. "
            "НЕ объясняй что такое эта профессия или категория вообще. "
            "НЕ начинай с определения «Футболист — это...». "
            "Начни сразу с имён: «Криштиану Роналду и Лионель Месси считаются...».\n\n"
        )
    else:
        context_note = (
            "Ответь ТОЧНО на вопрос пользователя, опираясь на свои знания. "
            "Не описывай тему вообще — давай конкретный ответ на то, что спрошено. "
            "Если вопрос «каких X больше всего» — назови конкретный тип X, а не что такое X.\n\n"
        )

    theories_note = (
        "theories — если тема допускает несколько конкурирующих объяснений "
        "(вымирание, катастрофы, исторические загадки) — добавь 2-4 теории. Иначе []."
    )
    return (
        f"{_age_instructions(age)}\n\n"
        f"{_mode_instructions(mode)}\n\n"
        + context_note
        + "--- ТРЕБОВАНИЯ К ВЫХОДНЫМ ПОЛЯМ ---\n"
        "main_idea — одно предложение, суть темы.\n"
        "simplified_text — ПОЛНОЕ объяснение для ребёнка. Следуй режиму выше (число предложений). "
        "Структура: что это → механизм → где встречается → аналогия → интересный факт. 2-3 эмодзи.\n"
        "glossary — 3-5 ключевых терминов с определениями для указанного возраста.\n"
        "analogies — 2-3 аналогии начинающихся с «Представь...» или «Это как...».\n"
        "quiz — 3 вопроса на понимание (не память).\n"
        "reasoning_steps — ход рассуждений (как ты пришёл к этому объяснению).\n"
        "learning_steps — 4-6 шагов «что узнаём».\n"
        f"{theories_note}\n\n"
        f"Верни строго JSON: {_output_schema_text()}\n\n"
        f"ВОПРОС: {query}"
    )


async def answer_without_article(query: str, age: int, mode: str = "balanced") -> LLMResult:
    """Answer a question using LLM general knowledge when no wiki article was found."""
    return await _chat(
        [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _llm_only_user_prompt(query, age, mode)},
        ]
    )


async def draft_answer(query: str, age: int) -> str:
    """Step 2 of the pipeline: generate a minimal 1-2 sentence direct answer."""
    result = await answer_without_article(query, age=age, mode="micro")
    return result.simplified_text


def _synthesis_user_prompt(
    query: str,
    draft: str,
    article_context: str,
    age: int,
    mode: str,
) -> str:
    age_instr = _age_instructions(age)
    mode_instr = _mode_instructions(mode)

    draft_block = (
        f"ЧЕРНОВИК ОТВЕТА (прямая суть, 1–2 предложения):\n{draft}\n\n"
        if draft else ""
    )

    if article_context:
        ctx_block = (
            f"КОНТЕКСТ ИЗ ЭНЦИКЛОПЕДИИ:\n{article_context}\n\n"
            "⚠️ СТРОГОЕ ПРАВИЛО — ЗАПРЕТ НА ВЫДУМКУ:\n"
            "• Добавляй конкретные числа, даты, научные названия ТОЛЬКО из предоставленного контекста статьи.\n"
            "• Если контекст противоречит черновику — доверяй контексту.\n"
            "• СТРОГО ЗАПРЕЩЕНО добавлять любые факты, которых нет ни в черновике, ни в контексте.\n"
            "• Если в статье нет информации по какому-то аспекту вопроса — НЕ додумывай и НЕ используй свои знания. Просто пропусти этот аспект.\n"
            "• Если для ответа требуется информация, которой нет в материалах, — не фантазируй, сократи ответ.\n"
            "• «Интересный факт» — ТОЛЬКО если он есть в контексте статьи. Иначе пропусти.\n\n"
        )
    else:
        ctx_block = (
            "КОНТЕКСТ ИЗ ЭНЦИКЛОПЕДИИ: пуст.\n"
            "⚠️ Статья не найдена — объясняй ТОЛЬКО общеизвестные научные факты.\n"
            "СТРОГО ЗАПРЕЩЕНО упоминать конкретные имена людей, заведения, адреса, даты или события, которые не можешь проверить.\n"
            "Лучше сократить ответ, чем добавить непроверенный факт.\n\n"
        )

    analogies_guide = (
        "analogies — выдели 1–3 ключевых понятия из объяснения. "
        "Для каждого — одна короткая аналогия из быта, игр, еды, транспорта или природы. "
        "Формат строки: «Термин: аналогия (одно предложение)». "
        "Аналогия должна помогать предсказывать поведение явления, а не просто звучать похоже. "
        "Не повторяй банальные сравнения. Если объяснение и так предельно ясное — верни []."
    )

    validation_block = (
        "ПЕРЕД ФИНАЛИЗАЦИЕЙ ПРОВЕРЬ:\n"
        "1. Правдоподобность: нет ли ложных фактов, вымысла, противоречий с наукой?\n"
        "   Если да — удали или замени на информацию из черновика/контекста.\n"
        "2. Доступность: каждое предложение понятно ребёнку указанного возраста?\n"
        "   Если нет — перепиши короче, добавь бытовой пример.\n\n"
    )

    _theories_triggers = (
        "куда", "почему", "зачем", "откуда", "вымер", "исчез", "погиб", "пропал",
        "причин", "теори", "версий", "версия", "гипотез", "что случилось",
    )
    theories_required = any(t in query.lower() for t in _theories_triggers)
    theories_guide = (
        "theories — "
        + (
            "ОБЯЗАТЕЛЬНО 2-4 теории/версии (тема касается причин или исчезновения). "
            if theories_required else
            "если тема допускает конкурирующие объяснения — добавь теории. Иначе []."
        )
        + " Каждая: короткий заголовок + 1-3 предложения для ребёнка."
    )

    return (
        f"{age_instr}\n\n"
        f"{mode_instr}\n\n"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {query}\n\n"
        + draft_block
        + ctx_block
        + validation_block
        + "ПРАВИЛА ДЛЯ simplified_text:\n"
        "• ОБЯЗАТЕЛЬНО полностью раскрой вопрос — не обрывай на середине, не давай слишком короткий ответ.\n"
        "• Структура: что это → как работает (механизм) → где встречается/зачем нужно → интересный факт.\n"
        "• Предложения до 12 слов. Если нужно сказать больше — используй несколько предложений.\n"
        "• Сложные термины — сразу поясняй бытовым примером в том же предложении.\n"
        "• Не используй «во-первых», «во-вторых», «как уже было сказано».\n"
        "• Пиши живо, как будто объясняешь другу. Риторические вопросы приветствуются.\n"
        "• 2-3 эмодзи для акцентов. Без Markdown.\n"
        "• В конце simplified_text добавь строку: «Источник: [название статьи]» — "
        "используй точное название из контекста энциклопедии выше. Если контекст пуст — не добавляй.\n\n"
        "--- ТРЕБОВАНИЯ К ВЫХОДНЫМ ПОЛЯМ ---\n"
        "main_idea — одно предложение, суть темы (не более 20 слов).\n"
        "simplified_text — ПОЛНОЕ объяснение для ребёнка. Следуй режиму (см. выше) — не менее указанного числа предложений.\n"
        + analogies_guide + "\n"
        "glossary — 3-5 терминов с определениями для указанного возраста.\n"
        "quiz — 3 вопроса на понимание механизма (не на память).\n"
        "reasoning_steps — ход рассуждений (как ты пришёл к этому объяснению).\n"
        "learning_steps — 4-6 шагов «что узнаём».\n"
        f"{theories_guide}\n\n"
        f"Верни строго JSON: {_output_schema_text()}"
    )


async def synthesize_answer(
    query: str,
    draft: str,
    article_excerpts: list[tuple[str, str]],
    age: int,
    mode: str = "balanced",
) -> LLMResult:
    """
    Step 5-7 of the pipeline: synthesize a full structured answer for children
    from a draft + article context. Includes analogy generation and self-validation
    instructions in the prompt.
    """
    article_context = ""
    for title, text in article_excerpts[:3]:
        chunk = text[:1400].strip()
        article_context += f"\n--- «{title}» ---\n{chunk}\n"

    return await _chat([
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": _synthesis_user_prompt(
                query=query,
                draft=draft,
                article_context=article_context.strip(),
                age=age,
                mode=mode,
            ),
        },
    ])


async def enrich_answer_with_articles(
    question: str,
    llm_answer: LLMResult,
    article_excerpts: list[tuple[str, str]],
    age: int,
    mode: str = "balanced",
) -> LLMResult:
    """
    Enrich the simplified_text of a direct LLM answer with specific facts
    from Ruwiki articles.

    KEY DESIGN: only simplified_text is updated. All structured fields
    (glossary, analogies, quiz, theories, learning_steps, reasoning_steps)
    are taken unchanged from the original LLM answer so they stay correctly
    focused on the actual question, not on the article topic.

    Falls back to the original LLM answer on any error.
    """
    if not article_excerpts:
        return llm_answer

    excerpts_text = ""
    for title, text in article_excerpts[:3]:
        chunk = text[:1400].strip()
        excerpts_text += f"\n--- «{title}» ---\n{chunk}\n"

    age_note = (
        "Простые короткие предложения (до 7 слов)." if age <= 8 else
        "Предложения до 12 слов, школьная лексика." if age <= 11 else
        "Ясный точный стиль, научные термины с определением."
    )

    prompt = (
        f"ВОПРОС: «{question}»\n\n"
        f"ТЕКУЩИЙ ОТВЕТ (правильный — не переписывать полностью):\n{llm_answer.simplified_text}\n\n"
        f"СПРАВОЧНЫЕ МАТЕРИАЛЫ ИЗ RUWIKI:{excerpts_text}\n"
        "─────────────────────────────────\n"
        "ЗАДАЧА: Улучши ТОЛЬКО текст ответа, НО строго по правилам:\n"
        "1. Добавляй только те числа/даты/названия, которые есть в предоставленных статьях.\n"
        "2. Если в статьях нет уточняющей информации — не добавляй ничего, оставь текст как есть.\n"
        "3. СТРОГО ЗАПРЕЩЕНО вносить любые факты, отсутствующие в статьях.\n"
        "4. Если статья содержит информацию, противоречащую текущему ответу — исправь в пользу статьи.\n"
        "5. Ответ ДОЛЖЕН отвечать на вопрос, а не описывать тему вообще.\n"
        f"6. Стиль: {age_note}\n\n"
        "Верни ТОЛЬКО улучшенный текст ответа — без JSON, без заголовков, без пояснений."
    )

    try:
        raw = await _call_provider([
            {
                "role": "system",
                "content": (
                    "Ты редактор детских текстов. Улучшай ответы, добавляя конкретные факты "
                    "из справочных материалов. Не меняй основную тему и не переписывай полностью."
                ),
            },
            {"role": "user", "content": prompt},
        ])
        enriched_text = _content_from_response(raw).strip()

        # Strip any accidental JSON/markdown wrapping
        if enriched_text.startswith("```"):
            enriched_text = re.sub(r"^```\w*\n?", "", enriched_text)
            enriched_text = re.sub(r"\n?```$", "", enriched_text)
        enriched_text = _clean_text(enriched_text)

        if not enriched_text or len(enriched_text) < 40:
            return llm_answer

        # Preserve ALL structured fields from the original correct answer.
        # Only simplified_text is updated with article facts.
        return LLMResult(
            main_idea=llm_answer.main_idea,
            simplified_text=enriched_text,
            reasoning_steps=llm_answer.reasoning_steps,
            learning_steps=llm_answer.learning_steps,
            glossary=llm_answer.glossary,
            analogies=llm_answer.analogies,
            quiz=llm_answer.quiz,
            theories=llm_answer.theories,
            raw=llm_answer.raw,
        )
    except Exception as e:
        logger.warning("enrich_answer_with_articles failed (%s) — using original", e)
        return llm_answer
