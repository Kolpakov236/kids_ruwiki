# Развертывание (кратко)

Проект состоит из двух частей:

- `backend/` — FastAPI API
- `frontend/` — статический сайт

## Backend

Подойдёт любой хостинг с Python 3.11+ (Railway/Render/Fly/VM/контейнер).

Минимальные переменные окружения:

```env
LLM_PROVIDER=openai_compatible
LLM_MODEL=your-model-name
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=your_api_key_here
```

Проверка:

- `GET /health`
- `POST /simplify`

## Frontend

Это статические файлы. Подойдёт любой статический хостинг (GitHub Pages, Nginx, S3 и т.д.).

В интерфейсе можно указать URL бэкенда в поле “API сервера”.