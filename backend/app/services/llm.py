from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.settings import settings


@dataclass
class LLMResult:
    main_idea: str
    simplified_text: str
    reasoning_steps: list[str]
    learning_steps: list[str]
    glossary: list[dict[str, str]]
    analogies: list[str]
    quiz: list[dict[str, str]]
    raw: dict[str, Any]


class LLMError(RuntimeError):
    pass


def model_variant() -> str:
    return f"{settings.llm_provider}:{settings.llm_model}:fact-anchored-v1"


def _system_prompt() -> str:
    return (
        "Ты редактор детской энциклопедии и опытный учитель. "
        "Твоя задача — превращать сложный энциклопедический текст в ясное объяснение для детей 6-14 лет. "
        "Сохраняй только факты из исходного текста. Главный приоритет — не потерять ключевые термины, формулы, единицы измерения, даты, имена и причинно-следственные связи. "
        "Упрощай синтаксис, но не выкидывай научные понятия. "
        "Имена людей, названия стран и организаций, важные даты и годы должны попасть в упрощённый текст так же, как в источнике "
        "(им можно слегка сопроводить простым пояснением, но не заменять выдуманными названиями). "
        "Если указаны интересы ребёнка — используй их только для понятных аналогий и примеров; новые факты из головы не придумывай. "
        "Перед финальным текстом последовательно разберись: что главное, какие якорные факты нельзя потерять, "
        "как объяснить простыми словами и как связать один пример с интересами без искажения смысла. "
        "Пиши живо и спокойно. Без Markdown. Ответь только валидным JSON."
    )


def _age_style(age: int) -> str:
    if age <= 8:
        return "очень короткие предложения до 8 слов, самые простые слова, примеры из игр, сказок и дома"
    if age <= 11:
        return "короткие предложения до 10-12 слов, простые термины с пояснениями, один яркий пример"
    return "ясный подростковый стиль 12-14 лет, базовые научные понятия с живыми аналогиями, без канцелярита"


def _mode_style(mode: str) -> str:
    if mode == "simple":
        return "максимально простое объяснение с бытовыми словами и минимумом деталей"
    if mode == "detailed":
        return "подробное объяснение с большим числом фактов, но без сложного языка"
    return "сбалансированное объяснение: понятно, коротко и достаточно информативно"


def _mode_lengths(mode: str) -> tuple[str, str, str]:
    if mode == "simple":
        return (
            "5-8 коротких предложений",
            "4-5 шагов",
            "3-4 пункта",
        )
    if mode == "detailed":
        return (
            "10-14 предложений",
            "7-9 шагов",
            "6-8 пунктов",
        )
    return (
        "8-12 предложений",
        "6-8 шагов",
        "5-7 пунктов",
    )


def _output_schema_text() -> str:
    return (
        '{"main_idea":"...",'
        '"simplified_text":"...",'
        '"reasoning_steps":["шаг размышления 1","..."],'
        '"learning_steps":["что узнаём шаг 1","..."],'
        '"glossary":[{"term":"...","definition":"..."}],'
        '"analogies":["..."],'
        '"quiz":[{"question":"...","answer":"..."}]}'
    )


def _personalization_block(interest_topics: list[str], child_notes: str) -> str:
    topics = [t.strip() for t in interest_topics if t and str(t).strip()][:10]
    notes = (child_notes or "").strip()
    lines: list[str] = []
    if topics:
        lines.append(
            "Интересы ребёнка (обязательно используй минимум одну бытовую аналогию или пример из этой области; "
            "факты про тему статьи только из текста ниже): "
            + ", ".join(topics)
            + "."
        )
    if notes:
        lines.append("Коротко о ребёнке (тон и примеры, без диагнозов и ярлыков): " + notes + ".")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _simplify_user_prompt(
    text: str,
    age: int,
    mode: str,
    interest_topics: list[str],
    child_notes: str,
    key_facts: dict | None = None,
) -> str:
    extra = _personalization_block(interest_topics, child_notes)
    slen, rlen, llen = _mode_lengths(mode)
    facts = key_facts or {}
    required_terms = [str(x) for x in facts.get("required_terms") or []][:48]
    formulas = [str(x) for x in facts.get("formulas") or []][:16]
    fact_block = ""
    if required_terms:
        fact_block += "ОБЯЗАТЕЛЬНЫЕ ТЕРМИНЫ И ФАКТЫ: " + "; ".join(required_terms) + ".\n"
    if formulas:
        fact_block += "ОБЯЗАТЕЛЬНЫЕ ФОРМУЛЫ / ОБОЗНАЧЕНИЯ / ЕДИНИЦЫ: " + "; ".join(formulas) + ".\n"
    return (
        f"Возраст: {age} лет.\n"
        f"Стиль: {_age_style(age)}.\n"
        f"Режим: {_mode_style(mode)}.\n"
        + (extra if extra else "")
        + fact_block
        + "Сначала последовательно размышляй (поле reasoning_steps). Структура рассуждения:\n"
        "1) Перечисли обязательные термины, формулы, обозначения, единицы, имена и числа, которые нельзя потерять.\n"
        "2) Составь план так, чтобы каждый обязательный термин появился в simplified_text дословно или почти дословно.\n"
        "3) Сохрани определения из оригинала: можно укоротить фразу, но нельзя менять смысл.\n"
        "4) Только после фактов добавь одну короткую аналогию; аналогия не заменяет термины и формулы.\n"
        f"Поле learning_steps: {llen} коротких шагов «что узнаём по порядку» для ребёнка.\n"
        "Перефразируй текст так, будто объясняешь тему после школы.\n"
        "Требования к качеству:\n"
        f"- В simplified_text: {slen}, но если обязательных терминов много, лучше 12-16 предложений, чем потеря фактов.\n"
        "- Сначала главная идея, затем важные детали и связь между ними.\n"
        "- Каждый сложный термин сразу объясни простыми словами рядом.\n"
        "- Замени канцелярит обычной речью, но сами научные термины сохраняй.\n"
        "- Имена, даты, места и числа из статьи сохраняй; не подменяй вымышленными названиями.\n"
        "- Формулы, обозначения и единицы измерения перепиши дословно, если они есть в обязательном списке.\n"
        "- Ожидаемый ROUGE-1 по обязательным терминам должен быть не ниже 0.60: проверь себя перед ответом.\n"
        "- Не добавляй фактов вне исходного текста.\n"
        "- Если интересы указаны: минимум одна ясная аналогия из этой области (без новых фактов про тему).\n"
        "- Если интересов нет: одна жизненная аналогия из школьного или домашнего опыта.\n"
        "- 3 вопроса мини-викторины с короткими ответами.\n"
        "- Глоссарий: 3-5 самых нужных терминов.\n"
        f"- reasoning_steps: {rlen}, каждый пункт — одно законченное наблюдение на русском.\n"
        "- analogies: 2-3 короткие строки (разные углы), хотя бы одна про интересы, если они есть.\n"
        "- В simplified_text используй 2-3 уместных эмодзи для визуальных акцентов (например 🔍, 💡, 🚀), но не перегружай.\n"
        "- В конце simplified_text добавь один вопрос-вовлечение: «Хочешь узнать...» или «А знаешь ли ты...?».\n"
        "- Персонализация: не больше одной метафоры из интересов ребёнка, чтобы украшение не вытеснило факты.\n"
        "- Без Markdown, без **жирного**, без #, без HTML.\n"
        "- В simplified_text не используй маркированные списки и кавычки-ёлочки.\n"
        f"Верни строго JSON: {_output_schema_text()}\n"
        "Текст:\n"
        f"{text}"
    )


def _repair_user_prompt(original: str, simplified: str, age: int, missing: list[str]) -> str:
    return json.dumps(
        {
            "task": "repair_simplification_fact_loss",
            "age": age,
            "missing_entities_or_facts": missing,
            "rules": [
                "Вставь в simplified_text каждый missing item — дословно или почти дословно из original_text.",
                "Если missing item — формула, обозначение или единица измерения, перепиши его дословно.",
                "Если не помещается в одно предложение, добавь отдельное короткое предложение.",
                "Сохрани детский стиль и простые слова; не добавляй новых фактов кроме возвращённых элементов.",
                "Не удаляй уже удачные части ответа.",
                "Отвечай тем же JSON schema.",
            ],
            "output_schema": {
                "main_idea": "string",
                "simplified_text": "string",
                "reasoning_steps": ["string"],
                "learning_steps": ["string"],
                "glossary": [{"term": "string", "definition": "string"}],
                "analogies": ["string"],
                "quiz": [{"question": "string", "answer": "string"}],
            },
            "original_text": original,
            "current_simplified_text": simplified,
        },
        ensure_ascii=False,
    )


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
        data.get("main_idea")
        or data.get("mainIdea")
        or data.get("summary")
        or ""
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
    quiz = [
        {
            "question": _clean_text(x.get("question", "")),
            "answer": _clean_text(x.get("answer", "")),
        }
        for x in quiz
        if isinstance(x, dict) and (x.get("question") or x.get("answer"))
    ][:3]

    reasoning_steps = _normalize_str_list(
        data.get("reasoning_steps")
        or data.get("reasoning")
        or data.get("thought_steps"),
        max_items=8,
    )
    learning_steps = _normalize_str_list(
        data.get("learning_steps")
        or data.get("steps")
        or data.get("lesson_steps"),
        max_items=10,
    )

    return LLMResult(
        main_idea=main_idea,
        simplified_text=text,
        reasoning_steps=reasoning_steps,
        learning_steps=learning_steps,
        glossary=glossary,
        analogies=analogies,
        quiz=quiz,
        raw=raw,
    )


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
    # Yandex AI Studio OpenAI-compatible endpoint is under /v1.
    # Many configs use Foundation Models base (/foundationModels/v1) — normalize it.
    if ("llm.api.cloud.yandex.net" in base) and ("/foundationModels/v1" in base):
        base = base.replace("/foundationModels/v1", "/v1")

    # Support both forms: base="https://.../v1" or base="https://..."
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    headers = {}
    if settings.llm_api_key:
        # Yandex API keys use "Api-Key". IAM tokens use "Bearer".
        if "llm.api.cloud.yandex.net" in base or str(settings.llm_model).startswith("gpt://"):
            headers["Authorization"] = f"Api-Key {settings.llm_api_key}"
        else:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    if settings.openai_project:
        headers["OpenAI-Project"] = settings.openai_project
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_num_predict,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
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
            "analogies": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "quiz": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING"},
                        "answer": {"type": "STRING"},
                    },
                    "required": ["question", "answer"],
                },
            },
        },
        "required": [
            "main_idea",
            "simplified_text",
            "reasoning_steps",
            "learning_steps",
            "glossary",
            "analogies",
            "quiz",
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


async def _chat(messages: list[dict[str, str]]) -> LLMResult:
    if settings.llm_provider == "ollama":
        raw = await _ollama_chat(messages)
    elif settings.llm_provider == "openai_compatible":
        raw = await _openai_compatible_chat(messages)
    elif settings.llm_provider == "gemini":
        try:
            raw = await _gemini_chat(messages)
        except httpx.TimeoutException as e:
            raise LLMError("llm_provider_timeout") from e
    else:
        raise LLMError(f"unsupported_llm_provider:{settings.llm_provider}")

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
                main_idea="",
                simplified_text=partial_text,
                reasoning_steps=[],
                learning_steps=[],
                glossary=[],
                analogies=[],
                quiz=[],
                raw=raw,
            )
        if str(e).startswith("llm_returned_malformed_json"):
            cleaned = re.sub(r"^\s*\{?\s*\"?simplified_text\"?\s*:\s*\"?", "", content, flags=re.DOTALL)
            cleaned = re.sub(r"\"?\s*,?\s*\"?glossary\"?.*$", "", cleaned, flags=re.DOTALL).strip()
            if cleaned:
                return LLMResult(
                    main_idea="",
                    simplified_text=cleaned,
                    reasoning_steps=[],
                    learning_steps=[],
                    glossary=[],
                    analogies=[],
                    quiz=[],
                    raw=raw,
                )
        return LLMResult(
            main_idea="",
            simplified_text=content,
            reasoning_steps=[],
            learning_steps=[],
            glossary=[],
            analogies=[],
            quiz=[],
            raw=raw,
        )
    return _normalize_result(data, raw)


async def simplify_with_llm(
    original_text: str,
    age: int,
    mode: str,
    interest_topics: list[str] | None = None,
    child_notes: str = "",
    key_facts: dict | None = None,
) -> LLMResult:
    text = original_text[: settings.llm_max_input_chars].strip()
    return await _chat(
        [
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": _simplify_user_prompt(
                    text,
                    age,
                    mode,
                    interest_topics or [],
                    child_notes,
                    key_facts,
                ),
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

