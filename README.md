# Рувик Kids

Образовательный веб-сервис, который превращает сложные статьи Рувики в понятные объяснения для детей 8-14 лет. Сервис находит статью, выделяет главную мысль, объясняет термины простыми словами, добавляет пример из жизни и мини-викторину для закрепления.

## Возможности

- Перефразирование статей Рувики под возраст ребенка.
- Три режима объяснения: очень просто, сбалансированно, подробнее.
- Структурированный результат: главная мысль, простой текст, термины, примеры и вопросы.
- Озвучивание результата и копирование текста.
- Проверка качества ответа: защита от обрезанных и неполных генераций.
- Кэширование успешных ответов в SQLite.
- Поддержка Gemini 2.5 Flash, Ollama и OpenAI-compatible endpoints.

## Быстрый старт

### 1) Backend (FastAPI)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Откройте `http://127.0.0.1:8000/docs`.

По умолчанию backend использует Gemini 2.5 Flash через Google AI Studio API. Создайте `backend/.env` по примеру `backend/.env.example` и укажите API-ключ:

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
LLM_API_KEY=your_google_ai_studio_api_key
LLM_TIMEOUT_SECONDS=120
LLM_MAX_INPUT_CHARS=5000
LLM_TEMPERATURE=0.35
LLM_NUM_CTX=8192
LLM_NUM_PREDICT=3000
ENABLE_LLM_REPAIR=true
ENABLE_VECTOR_CACHE=false
```

Вместо `LLM_API_KEY` можно использовать `GEMINI_API_KEY` или `GOOGLE_API_KEY`.

Для локального запуска через Ollama можно переключить провайдер без изменения кода:

```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:3b-instruct
LLM_BASE_URL=http://localhost:11434
LLM_API_KEY=
```

Для LM Studio, vLLM, OpenRouter и других OpenAI-compatible endpoint:

```env
LLM_PROVIDER=openai_compatible
LLM_MODEL=your-model-name
LLM_BASE_URL=http://localhost:1234
LLM_API_KEY=
```

### 2) Frontend

```powershell
cd frontend
python -m http.server 5173
```

Откройте `http://127.0.0.1:5173`. По умолчанию frontend обращается к `http://127.0.0.1:8000`.

## API

- `POST /simplify` — упрощает статью по теме, возрасту и режиму.
- `GET /health` — показывает провайдера модели, имя модели и наличие API-ключа.
- `DELETE /cache` — очищает SQLite-кэш и историю генераций.

Пример запроса:

```json
{
  "query": "Квантовая механика",
  "age": 10,
  "mode": "balanced"
}
```

`mode` принимает значения `simple`, `balanced` или `detailed`.

## Переменные окружения

См. `backend/.env.example`.

## Архитектура LLM

`backend/app/services/llm.py` содержит единый интерфейс для моделей:

- `gemini` — Gemini 2.5 Flash через Google Generative Language API;
- `ollama` — бесплатный локальный запуск;
- `openai_compatible` — быстрый переход на LM Studio, vLLM, OpenRouter, Mistral, Yandex-compatible proxy или облачные модели;
- бизнес-логика в `pipeline.py` не зависит от конкретного провайдера.

## Пайплайн

1. Получает статью Рувики.
2. Нормализует входной текст и выбирает фрагмент для объяснения.
3. Проверяет кэш с учетом темы, возраста, режима, статьи и версии модели.
4. Запрашивает у модели строгий JSON с главным тезисом, объяснением, терминами, примером и вопросами.
5. Проверяет, не был ли ответ обрезан или заблокирован провайдером.
6. Прогоняет результат через локальный readability guard.
7. Проверяет сохранность ключевых сущностей через Natasha NER.
8. При необходимости запускает repair-cycle.
9. Сохраняет только прошедший проверку результат.

## Подготовка к демонстрации

1. Убедитесь, что `GET /health` показывает выбранного провайдера и настроенный API-ключ.
2. Очистите кэш кнопкой в интерфейсе или запросом `DELETE /cache`.
3. Проверьте демо-темы: `Квантовая механика`, `Фотосинтез`, `Древний Египет`, `Черная дыра`.
4. Если модель возвращает неполный ответ, увеличьте `LLM_NUM_PREDICT` и повторите запрос.

