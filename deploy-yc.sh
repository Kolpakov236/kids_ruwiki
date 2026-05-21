#!/bin/bash
# Деплой Ruwiki Kids Backend → Yandex Cloud Functions
# Использование:
#   1. yc config set service-account-key key.json
#   2. yc config set folder-id <folder_id>
#   3. bash deploy-yc.sh

set -euo pipefail

FUNCTION_NAME="${YC_FUNCTION_NAME:-ruwiki-backend}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/backend/.env"
TMP_SRC="/tmp/ruwiki-fn-src"

# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------
if ! command -v yc &>/dev/null; then
  echo "❌  YC CLI не найден. Установите: https://yandex.cloud/ru/docs/cli/quickstart"
  exit 1
fi

if ! yc config list &>/dev/null; then
  echo "❌  YC CLI не авторизован. Выполните:"
  echo "    yc config set service-account-key key.json"
  echo "    yc config set folder-id <folder_id>"
  exit 1
fi

YC_FOLDER_ID=$(yc config get folder-id 2>/dev/null || true)
if [ -z "$YC_FOLDER_ID" ]; then
  echo "❌  folder-id не задан. Выполните: yc config set folder-id <folder_id>"
  exit 1
fi

# ---------------------------------------------------------------------------
# Читаем .env (только если файл есть)
# ---------------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo "✅  Переменные загружены из backend/.env"
else
  echo "⚠️   backend/.env не найден — используем переменные окружения"
fi

# ---------------------------------------------------------------------------
# Валидация обязательных переменных
# ---------------------------------------------------------------------------
: "${LLM_API_KEY:?Задайте LLM_API_KEY в backend/.env или переменных окружения}"
: "${SECRET_KEY:?Задайте SECRET_KEY в backend/.env (openssl rand -hex 32)}"

LLM_PROVIDER="${LLM_PROVIDER:-openai_compatible}"
LLM_MODEL="${LLM_MODEL:-yandexgpt-5-lite}"
LLM_BASE_URL="${LLM_BASE_URL:-https://llm.api.cloud.yandex.net/v1}"
LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-120}"
LLM_MAX_INPUT_CHARS="${LLM_MAX_INPUT_CHARS:-2500}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.35}"
LLM_NUM_PREDICT="${LLM_NUM_PREDICT:-3000}"
ENABLE_LLM_REPAIR="${ENABLE_LLM_REPAIR:-true}"
YANDEX_FOLDER_ID="${YANDEX_FOLDER_ID:-$YC_FOLDER_ID}"

# Для YandexGPT нужен правильный base URL (не foundationModels)
if [[ "$LLM_BASE_URL" == *"foundationModels"* ]]; then
  echo "⚠️   Исправляю LLM_BASE_URL: foundationModels/v1 → /v1"
  LLM_BASE_URL="https://llm.api.cloud.yandex.net/v1"
fi

# Для YandexGPT заменяем полный URI на короткое имя модели
if [[ "$LLM_MODEL" == gpt://* ]]; then
  LLM_MODEL=$(echo "$LLM_MODEL" | sed 's|gpt://[^/]*/\([^/]*\).*|\1|')
  echo "⚠️   Преобразую модель URI → короткое имя: $LLM_MODEL"
fi

# ---------------------------------------------------------------------------
# Подготовка исходников (без .env и кеша)
# ---------------------------------------------------------------------------
echo ""
echo "📦  Подготовка исходников..."
rm -rf "$TMP_SRC"
cp -r "$SCRIPT_DIR/backend" "$TMP_SRC"
rm -f "$TMP_SRC/.env" "$TMP_SRC/.env.*"
rm -rf "$TMP_SRC/__pycache__" "$TMP_SRC/data" "$TMP_SRC/.venv"
find "$TMP_SRC" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$TMP_SRC" -name "*.pyc" -delete 2>/dev/null || true

# Use CF-specific requirements (no torch/chromadb/sentence-transformers)
if [ -f "$SCRIPT_DIR/backend/requirements.cf.txt" ]; then
  cp "$SCRIPT_DIR/backend/requirements.cf.txt" "$TMP_SRC/requirements.txt"
  echo "   Using requirements.cf.txt (without heavy ML packages)"
fi

# Include frontend so FastAPI can serve it as static files
if [ -d "$SCRIPT_DIR/frontend" ]; then
  cp -r "$SCRIPT_DIR/frontend" "$TMP_SRC/frontend"
  echo "   Included frontend/"
fi

echo "   Файлы для деплоя:"
ls "$TMP_SRC"

# ---------------------------------------------------------------------------
# Создаём функцию (если не существует)
# ---------------------------------------------------------------------------
echo ""
echo "🔍  Проверяю функцию '$FUNCTION_NAME'..."
if ! yc serverless function get --name="$FUNCTION_NAME" &>/dev/null; then
  echo "   Функция не найдена, создаю..."
  yc serverless function create --name="$FUNCTION_NAME"
  echo "   ✅ Функция создана"
else
  echo "   ✅ Функция уже существует"
fi

# ---------------------------------------------------------------------------
# Деплой версии
# ---------------------------------------------------------------------------
echo ""
echo "🚀  Деплой версии функции..."

ENV_ARGS=(
  --environment "LLM_PROVIDER=$LLM_PROVIDER"
  --environment "LLM_MODEL=$LLM_MODEL"
  --environment "LLM_BASE_URL=$LLM_BASE_URL"
  --environment "LLM_API_KEY=$LLM_API_KEY"
  --environment "LLM_TIMEOUT_SECONDS=$LLM_TIMEOUT_SECONDS"
  --environment "LLM_MAX_INPUT_CHARS=$LLM_MAX_INPUT_CHARS"
  --environment "LLM_TEMPERATURE=$LLM_TEMPERATURE"
  --environment "LLM_NUM_PREDICT=$LLM_NUM_PREDICT"
  --environment "ENABLE_LLM_REPAIR=$ENABLE_LLM_REPAIR"
  --environment "YANDEX_FOLDER_ID=$YANDEX_FOLDER_ID"
  --environment "RUWIKI_SITE_BASE=https://ruwiki.ru"
  --environment "RUWIKI_REST_API_BASE=https://ruwiki.ru/api/rest_v1"
  --environment "SQLITE_PATH=/tmp/data/app.db"
  --environment "CHROMA_PATH=/tmp/data/chroma"
  --environment "ENABLE_VECTOR_CACHE=false"
  --environment "SECRET_KEY=$SECRET_KEY"
  --environment "FRONTEND_URL=${FRONTEND_URL:-https://functions.yandexcloud.net}"
)

# OAuth (добавляем только если заданы в .env)
if [ -n "${VK_CLIENT_ID:-}" ];        then ENV_ARGS+=(--environment "VK_CLIENT_ID=$VK_CLIENT_ID"); fi
if [ -n "${VK_CLIENT_SECRET:-}" ];    then ENV_ARGS+=(--environment "VK_CLIENT_SECRET=$VK_CLIENT_SECRET"); fi
if [ -n "${YANDEX_CLIENT_ID:-}" ];    then ENV_ARGS+=(--environment "YANDEX_CLIENT_ID=$YANDEX_CLIENT_ID"); fi
if [ -n "${YANDEX_CLIENT_SECRET:-}" ]; then ENV_ARGS+=(--environment "YANDEX_CLIENT_SECRET=$YANDEX_CLIENT_SECRET"); fi

yc serverless function version create \
  --function-name="$FUNCTION_NAME" \
  --runtime="python311" \
  --entrypoint="app.main.handler" \
  --memory="512m" \
  --execution-timeout="120s" \
  --source-path="$TMP_SRC" \
  "${ENV_ARGS[@]}"

# ---------------------------------------------------------------------------
# Делаем функцию публичной
# ---------------------------------------------------------------------------
echo ""
echo "🌐  Открываю публичный доступ..."
if ! yc serverless function allow-unauthenticated-invoke --name="$FUNCTION_NAME" 2>&1; then
  echo "⚠️   Не удалось выставить публичный доступ автоматически."
  echo "     Сделайте это вручную в консоли YC:"
  echo "     https://console.yandex.cloud/functions → $FUNCTION_NAME → Доступ → Без токена"
fi

# ---------------------------------------------------------------------------
# API Gateway (поддержка sub-путей: /health, /simplify, /chats и т.д.)
# ---------------------------------------------------------------------------
GATEWAY_NAME="${YC_GATEWAY_NAME:-ruwiki-gateway}"
FUNCTION_ID=$(yc serverless function get --name="$FUNCTION_NAME" --format=json | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
SA_ID=$(python3 -c "import json; d=json.load(open('$SCRIPT_DIR/key.json')); print(d['service_account_id'])" 2>/dev/null || true)

echo ""
echo "🔗  Настройка API Gateway '$GATEWAY_NAME'..."

cat > /tmp/ruwiki-gateway.yaml <<GWEOF
openapi: 3.0.0
info:
  title: ruwiki-kids
  version: 1.0.0
paths:
  /:
    x-yc-apigateway-any-method:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: $FUNCTION_ID
        service_account_id: $SA_ID
  /{path+}:
    x-yc-apigateway-any-method:
      parameters:
        - name: path
          in: path
          required: false
          schema:
            type: string
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: $FUNCTION_ID
        service_account_id: $SA_ID
GWEOF

if ! yc serverless api-gateway get --name="$GATEWAY_NAME" &>/dev/null; then
  echo "   Создаю gateway..."
  yc serverless api-gateway create --name="$GATEWAY_NAME" --spec=/tmp/ruwiki-gateway.yaml
else
  echo "   Обновляю gateway..."
  yc serverless api-gateway update --name="$GATEWAY_NAME" --spec=/tmp/ruwiki-gateway.yaml
fi

GATEWAY_DOMAIN=$(yc serverless api-gateway get --name="$GATEWAY_NAME" --format=json | python3 -c "import sys,json; print(json.load(sys.stdin)['domain'])")
GATEWAY_URL="https://$GATEWAY_DOMAIN"
echo "   ✅ Gateway: $GATEWAY_URL"

# ---------------------------------------------------------------------------
# Итог
# ---------------------------------------------------------------------------
FUNCTION_URL="https://functions.yandexcloud.net/$FUNCTION_ID"

echo ""
echo "════════════════════════════════════════════════"
echo "✅  Деплой завершён!"
echo "🌐  Приложение: $GATEWAY_URL"
echo "🔧  Функция:    $FUNCTION_URL"
echo ""
echo "📝  Проверка:"
echo "    curl $GATEWAY_URL/health"
echo ""
echo "📝  Если используете OAuth, обновите FRONTEND_URL в backend/.env:"
echo "    FRONTEND_URL=$GATEWAY_URL"
echo "════════════════════════════════════════════════"

# Очистка
rm -rf "$TMP_SRC"
