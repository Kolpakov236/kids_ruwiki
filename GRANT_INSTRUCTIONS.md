# Инструкция по использованию гранта SourceCraft для Yandex AI Studio

## 🎯 Что такое грант SourceCraft?

SourceCraft предоставляет грант в размере **6 000 рублей** на использование сервисов Yandex Cloud на срок **180 дней**. Грант можно использовать для Yandex AI Studio (Foundation Models).

## 📋 Шаги для активации гранта

### 1. Активация гранта в SourceCraft
1. Перейдите в настройки организации в SourceCraft
2. Найдите раздел "Сервисные подключения"
3. Создайте новое сервисное подключение к Yandex Cloud
4. Активируйте переключатель "Активировать грант от SourceCraft"
5. Следуйте инструкциям для привязки аккаунта Yandex Cloud

### 2. Настройка Yandex Cloud
1. Авторизуйтесь в [консоли Yandex Cloud](https://console.cloud.yandex.ru)
2. Убедитесь, что у вас есть облако и каталог
3. Активируйте сервис "Yandex Foundation Models"

### 3. Получение API ключа
1. В консоли Yandex Cloud перейдите в раздел "Service accounts"
2. Создайте сервисный аккаунт для приложения
3. Назначьте роли: `ai.languageModels.user`
4. Создайте авторизованный ключ для сервисного аккаунта
5. Сохраните ключ в безопасном месте

### 4. Настройка модели в Yandex AI Studio
1. Перейдите в [Yandex AI Studio](https://console.cloud.yandex.ru/foundation-models)
2. Выберите модель `yandexgpt-3` (самая экономичная для текста)
3. Скопируйте endpoint URL: `https://llm.api.cloud.yandex.net/foundationModels/v1`

## 🔧 Настройка проекта

### 1. Обновление .env файла
Замените в `backend/.env`:
```env
LLM_PROVIDER=openai_compatible
LLM_MODEL=yandexgpt-3
LLM_BASE_URL=https://llm.api.cloud.yandex.net/foundationModels/v1
LLM_API_KEY=ваш_ключ_здесь
```

### 2. Проверка работы
```bash
cd backend
python -c "from app.settings import settings; print(f'Provider: {settings.llm_provider}'); print(f'Model: {settings.llm_model}')"
```

## 💰 Расчет стоимости

### Тарифы YandexGPT-3 (на ноябрь 2024):
- **Входные токены**: 0.5 ₽ за 1K токенов
- **Выходные токены**: 0.5 ₽ за 1K токенов

### Средний запрос в Рувик Kids:
- **Вход**: ~500 токенов (статья + инструкция)
- **Выход**: ~300 токенов (объяснение + термины + вопросы)
- **Итого**: ~800 токенов = **0.4 ₽ за запрос**

### Возможности гранта:
- **Грант**: 6 000 ₽
- **Запросов за грант**: ~15 000
- **При 100 запросах в день**: хватит на 150 дней

## 🚀 Деплой на Yandex Cloud Functions

### Автоматический деплой:
```bash
chmod +x deploy-yc.sh
./deploy-yc.sh
```

### Ручной деплой:
```bash
cd backend
yc serverless function create --name ruwiki-backend
yc serverless function version create \
  --function-name ruwiki-backend \
  --runtime python311 \
  --entrypoint app.main.app \
  --memory 256m \
  --execution-timeout 30s \
  --source-path . \
  --environment "LLM_API_KEY=ваш_ключ,LLM_PROVIDER=openai_compatible,LLM_MODEL=yandexgpt-3,LLM_BASE_URL=https://llm.api.cloud.yandex.net/foundationModels/v1"
```

## 🔍 Проверка работы

1. **Проверка healthcheck**:
   ```
   GET https://your-function-url/health
   ```
   Должен вернуть: `{"provider":"openai_compatible","model":"yandexgpt-3","api_key_configured":true}`

2. **Тестовый запрос**:
   ```bash
   curl -X POST https://your-function-url/simplify \
     -H "Content-Type: application/json" \
     -d '{"query":"Фотосинтез","age":10,"mode":"balanced"}'
   ```

## ⚠️ Важные моменты

1. **Безопасность ключа**: Никогда не коммитьте API ключ в репозиторий
2. **Лимиты гранта**: Грант действует 180 дней, неиспользованные средства сгорают
3. **Мониторинг расхода**: Настройте алерты в Yandex Cloud Monitoring
4. **Резервный провайдер**: В `.env.example` оставлены настройки для Gemini как запасного варианта

## 📞 Поддержка

- **Документация SourceCraft**: https://sourcecraft.dev/portal/docs/ru/sourcecraft/concepts/grant
- **Yandex Cloud поддержка**: https://cloud.yandex.ru/docs/support
- **Yandex AI Studio документация**: https://cloud.yandex.ru/docs/foundation-models

## 🎯 Готовность к демонстрации заказчику

После настройки гранта и деплоя:
1. ✅ Фронтенд работает на SourceCraft Sites
2. ✅ Бэкенд работает на Yandex Cloud Functions
3. ✅ Интеграция с Yandex AI Studio настроена
4. ✅ Грант активирован, расход контролируется
5. ✅ Сервис полностью функционален и готов к использованию

Проект представляет собой полноценный MVP образовательного сервиса с профессиональным интерфейсом, облачной инфраструктурой и экономичной моделью использования AI.