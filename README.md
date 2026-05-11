# Ruwiki Explain

Веб‑приложение: вводишь тему — получаешь объяснение того, **как это устроено и работает**, адаптированное под возраст ребёнка 6–14 лет. Никакой истории открытия — только механизм, аналогия, глоссарий и викторина.

## Возможности

- **Возрастные группы**: 6–8, 9–11, 12–14 лет — разные стиль, словарный запас и длина
- **3 режима**: очень просто / сбалансированно / подробнее
- **Двухшаговый пайплайн**: сначала модель анализирует механизм темы, потом пишет объяснение
- **Принципы объяснения**: конкретное до абстрактного, механизм пошагово, аналогия как тест понимания
- **Кэш ответов**: быстрый exact-cache в SQLite + семантический поиск через Chroma
- **Рейтинг**: пользователь ставит звёзды после объяснения, оценки хранятся в БД
- **Мини-игра**: пузырьки BubblePop пока идёт генерация
- **Озвучка** и копирование результата

## Архитектура

```
├── frontend/           # Статический сайт (HTML/CSS/JS)
│   ├── index.html     # Главная страница
│   ├── styles.css     # Стили
│   └── app.js         # Логика фронтенда
├── backend/           # FastAPI приложение
│   ├── app/
│   │   ├── services/
│   │   │   ├── llm.py       # Промпты и работа с моделью
│   │   │   ├── pipeline.py  # Бизнес-логика обработки
│   │   │   ├── cache.py     # SQLite + Chroma кэш
│   │   │   └── db.py        # Инициализация БД
│   │   ├── routes/
│   │   │   └── simplify.py  # HTTP-эндпоинты
│   │   ├── schemas.py
│   │   └── settings.py
│   ├── requirements.txt
│   └── .env.example
```

## Локальная разработка

### Backend (FastAPI)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Документация API: `http://127.0.0.1:8000/docs`

### Frontend
```bash
cd frontend
python -m http.server 5173
```
Откройте `http://127.0.0.1:5173`. По умолчанию фронтенд обращается к `http://127.0.0.1:8000`.

## Настройка

Создайте `backend/.env` на основе `backend/.env.example` и укажите провайдера и ключи.

Минимальный пример (Yandex AI Studio):
```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://llm.api.cloud.yandex.net/v1
LLM_API_KEY=your_api_key
OPENAI_PROJECT=your_folder_id
LLM_MODEL=yandexgpt-lite
```

## API

- `POST /simplify` — объяснение темы по возрасту и режиму
- `POST /rate` — сохранить оценку (звёзды + комментарий)
- `GET /health` — провайдер, модель, наличие ключа
- `DELETE /cache` — очистить SQLite-кэш и историю

Пример запроса:
```json
{
  "query": "Квантовая механика",
  "age": 10,
  "mode": "balanced",
  "enable_metrics": true
}
```
`mode`: `simple` / `balanced` / `detailed`.

## Пайплайн обработки

1. Получает статью Рувики через MediaWiki API
2. Проверяет семантический кэш (Chroma) по похожим запросам
3. Проверяет exact-cache (SQLite) по теме, возрасту, режиму и версии промпта
4. **Шаг 1 — анализ**: модель извлекает механизм явления, ключевые термины и типичные заблуждения
5. **Шаг 2 — объяснение**: модель пишет текст по структуре: крючок → механизм → смысл → аналогия → вопрос
6. Считает метрики достоверности к источнику (ROUGE, BLEURT-proxy)
7. При необходимости запускает repair-цикл для возврата пропущенных фактов
8. Сохраняет результат в SQLite и Chroma

## Интеграция с LLM

`backend/app/services/llm.py` поддерживает три провайдера:
- `openai_compatible` — любой OpenAI-совместимый endpoint (Yandex AI Studio, OpenAI, etc.)
- `gemini` — Google Gemini API
- `ollama` — локальный Ollama

## Лицензия

MIT License. Используются статьи из Рувики (CC BY-SA 3.0).
