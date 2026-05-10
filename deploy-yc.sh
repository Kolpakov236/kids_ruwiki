#!/bin/bash
# Скрипт для деплоя backend на Yandex Cloud Functions
# Требуется установленный yc CLI и настроенный профиль

set -e

echo "🚀 Деплой бэкенда Рувик Kids на Yandex Cloud Functions"

# Проверяем наличие yc CLI
if ! command -v yc &> /dev/null; then
    echo "❌ Yandex Cloud CLI (yc) не установлен"
    echo "Установите: https://cloud.yandex.ru/docs/cli/quickstart"
    exit 1
fi

# Проверяем авторизацию
if ! yc config list &> /dev/null; then
    echo "❌ Не авторизованы в Yandex Cloud"
    echo "Выполните: yc init"
    exit 1
fi

# Создаем временную директорию для деплоя
TEMP_DIR=$(mktemp -d)
echo "📁 Временная директория: $TEMP_DIR"

# Копируем backend
cp -r backend/* "$TEMP_DIR/"
cp backend/.env.example "$TEMP_DIR/.env" 2>/dev/null || true

# Создаем requirements.txt для Cloud Functions (минимальный набор)
cat > "$TEMP_DIR/requirements.txt" << EOF
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
pydantic-settings>=2.3
httpx>=0.27
python-dotenv>=1.0
beautifulsoup4>=4.12
lxml>=5.2
EOF

# Создаем файл для Yandex Cloud Functions
cat > "$TEMP_DIR/yandex-cloud-config.yaml" << EOF
runtime: python311
entrypoint: app.main.app
memory: 256MB
execution_timeout: 30s
environment:
  RUWIKI_API_BASE: "https://ru.ruwiki.ru/api/rest_v1"
  LLM_PROVIDER: "openai_compatible"
  LLM_MODEL: "yandexgpt-3"
  LLM_BASE_URL: "https://llm.api.cloud.yandex.net/foundationModels/v1"
  LLM_TIMEOUT_SECONDS: "120"
  LLM_MAX_INPUT_CHARS: "2500"
  LLM_TEMPERATURE: "0.35"
  LLM_NUM_CTX: "4096"
  LLM_NUM_PREDICT: "5000"
  ENABLE_LLM_REPAIR: "true"
  SQLITE_PATH: "/tmp/app.db"
  ENABLE_VECTOR_CACHE: "false"
  EMBEDDING_MODEL: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EOF

echo "📦 Подготовка завершена"

# Запрашиваем имя функции
read -p "Введите имя функции (по умолчанию: ruwiki-backend): " FUNCTION_NAME
FUNCTION_NAME=${FUNCTION_NAME:-ruwiki-backend}

# Запрашиваем API ключ
read -p "Введите API ключ Yandex AI Studio (оставьте пустым для использования переменной окружения): " API_KEY

if [ -n "$API_KEY" ]; then
    echo "LLM_API_KEY: '$API_KEY'" >> "$TEMP_DIR/yandex-cloud-config.yaml"
    echo "✅ API ключ добавлен в конфигурацию"
else
    echo "⚠️  API ключ не добавлен. Установите переменную окружения LLM_API_KEY в консоли Yandex Cloud."
fi

# Деплой
echo "🚀 Деплой функции $FUNCTION_NAME..."
yc serverless function create --name "$FUNCTION_NAME" 2>/dev/null || echo "Функция уже существует, обновляем..."

yc serverless function version create \
    --function-name "$FUNCTION_NAME" \
    --runtime python311 \
    --entrypoint app.main.app \
    --memory 256m \
    --execution-timeout 30s \
    --source-path "$TEMP_DIR" \
    --environment-from-file "$TEMP_DIR/yandex-cloud-config.yaml"

# Получаем URL функции
FUNCTION_URL=$(yc serverless function get --name "$FUNCTION_NAME" --format json | jq -r '.http_invoke_url' 2>/dev/null || echo "")

if [ -n "$FUNCTION_URL" ]; then
    echo "✅ Деплой завершен!"
    echo "🌐 URL функции: $FUNCTION_URL"
    echo ""
    echo "📝 Обновите frontend/app.js:"
    echo "function getDefaultBackendUrl() {"
    echo "  return \"$FUNCTION_URL\";"
    echo "}"
else
    echo "✅ Деплой завершен!"
    echo "🌐 Получите URL функции:"
    echo "yc serverless function get --name $FUNCTION_NAME"
fi

# Очистка
rm -rf "$TEMP_DIR"
echo "🧹 Временные файлы удалены"