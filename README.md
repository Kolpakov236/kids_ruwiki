# Рувик Kids — Образовательный сервис для детей

Образовательный веб-сервис, который превращает сложные статьи Рувики в понятные объяснения для детей 8-14 лет. Сервис находит статью, выделяет главную мысль, объясняет термины простыми словами, добавляет пример из жизни и мини-викторину для закрепления.

## 🚀 Быстрый старт (MVP для заказчика)

Сервис готов к демонстрации заказчику как полноценный MVP:

### 🌐 Онлайн-демо
- **Frontend**: https://folomkin-ivan.sourcecraft.site/ruwiki-explain (после публикации)
- **Backend**: Требуется развертывание на Yandex Cloud Functions или аналогичном сервисе

### ✨ Ключевые возможности MVP
- **Профессиональный интерфейс** — современный дизайн, адаптивная верстка
- **Три режима объяснения**: очень просто, сбалансированно, подробнее
- **Структурированный результат**: главная мысль, простой текст, термины, примеры и вопросы
- **Озвучивание результата** и копирование текста
- **Проверка качества ответа**: защита от обрезанных и неполных генераций
- **Кэширование успешных ответов** в SQLite
- **Поддержка Yandex AI Studio** через грант (экономно)

## 🏗️ Архитектура

```
├── frontend/           # Статический сайт (HTML/CSS/JS)
│   ├── index.html     # Главная страница
│   ├── styles.css     # Стили
│   └── app.js         # Логика фронтенда
├── backend/           # FastAPI приложение
│   ├── app/          # Основной код
│   ├── requirements.txt
│   └── .env          # Конфигурация
└── .sourcecraft/     # Конфигурация SourceCraft
    ├── sites.yaml    # Публикация фронтенда
    └── ci.yaml       # CI/CD конфигурация
```

## 🚀 Развертывание

### 1. Публикация фронтенда (SourceCraft Sites)
Фронтенд автоматически публикуется на SourceCraft Sites при пуше в ветку `main`.
Сайт будет доступен по адресу: `https://folomkin-ivan.sourcecraft.site/ruwiki-explain`

### 2. Развертывание бэкенда
Бэкенд требует развертывания на облачной платформе:

#### Вариант A: Yandex Cloud Functions (рекомендуется с грантом)
```bash
cd backend
# Установите Yandex Cloud CLI
yc serverless function create --name ruwiki-backend
yc serverless function version create \
  --function-name ruwiki-backend \
  --runtime python311 \
  --entrypoint app.main.app \
  --memory 256m \
  --execution-timeout 30s \
  --source-path . \
  --environment "LLM_API_KEY=your_key,LLM_PROVIDER=openai_compatible,..."
```

#### Вариант B: Любой хостинг с поддержкой Python
- Railway, Render, Fly.io, PythonAnywhere
- Требуется Python 3.11+ и установка зависимостей

### 3. Настройка Yandex AI Studio
1. Получите грант SourceCraft на Yandex Cloud
2. Активируйте Yandex Foundation Models в консоли
3. Создайте API-ключ для доступа к моделям
4. Обновите `backend/.env`:
   ```env
   LLM_PROVIDER=openai_compatible
   LLM_MODEL=yandexgpt-3
   LLM_BASE_URL=https://llm.api.cloud.yandex.net/foundationModels/v1
   LLM_API_KEY=ваш_ключ_здесь
   ```

### 4. Настройка фронтенда
После развертывания бэкенда обновите URL в `frontend/app.js`:
```javascript
function getDefaultBackendUrl() {
  return "https://your-deployed-backend-url.here";
}
```

## 🔧 Локальная разработка

### Backend (FastAPI)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# или .venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Откройте `http://127.0.0.1:8000/docs` для доступа к API документации.

### Frontend
```bash
cd frontend
python -m http.server 5173
```
Откройте `http://127.0.0.1:5173`. По умолчанию frontend обращается к `http://127.0.0.1:8000`.

## 📊 API

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

## 🧠 Интеграция с LLM

`backend/app/services/llm.py` содержит единый интерфейс для моделей:
- `openai_compatible` — Yandex AI Studio, OpenAI, Mistral, etc.
- `gemini` — Gemini через Google AI Studio API
- `ollama` — бесплатный локальный запуск

Бизнес-логика в `pipeline.py` не зависит от конкретного провайдера.

## 🔄 Пайплайн обработки

1. Получает статью Рувики через MediaWiki API
2. Нормализует входной текст и выбирает фрагмент для объяснения
3. Проверяет кэш с учетом темы, возраста, режима, статьи и версии модели
4. Запрашивает у модели строгий JSON с главным тезисом, объяснением, терминами, примером и вопросами
5. Проверяет, не был ли ответ обрезан или заблокирован провайдером
6. Прогоняет результат через локальный readability guard
7. Проверяет сохранность ключевых сущностей через Natasha NER
8. При необходимости запускает repair-cycle
9. Сохраняет только прошедший проверку результат

## 💰 Экономия с Yandex AI Studio грантом

- **Грант SourceCraft**: 6 000 ₽ на 180 дней
- **YandexGPT-3**: ~0.5 ₽ за 1K токенов
- **Средний запрос**: ~500 токенов вход + 300 токенов выход = ~0.4 ₽
- **При гранте**: ~15 000 запросов бесплатно

## 🎯 Подготовка к демонстрации заказчику

1. **Разверните бэкенд** на Yandex Cloud Functions
2. **Обновите frontend/app.js** с публичным URL бэкенда
3. **Проверьте здоровье системы**: `GET /health` должен показывать настроенный API-ключ
4. **Очистите кэш** кнопкой в интерфейсе или запросом `DELETE /cache`
5. **Протестируйте демо-темы**: `Квантовая механика`, `Фотосинтез`, `Древний Египет`, `Черная дыра`
6. **Подготовьте скринкаст** или живую демонстрацию

## 📝 Лицензия

MIT License. Сервис использует статьи из Рувики (CC BY-SA 3.0).

## 🤝 Вклад в проект

1. Форкните репозиторий
2. Создайте ветку для фичи (`git checkout -b feature/amazing-feature`)
3. Закоммитьте изменения (`git commit -m 'Add amazing feature'`)
4. Запушьте ветку (`git push origin feature/amazing-feature`)
5. Откройте Pull Request

## 📞 Контакты

Для вопросов по развертыванию или использованию гранта:
- SourceCraft документация: https://sourcecraft.dev/portal/docs
- Yandex Cloud документация: https://cloud.yandex.ru/docs

---
*Проект готов к демонстрации заказчику как полноценный MVP образовательного сервиса.*