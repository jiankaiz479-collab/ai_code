#!/bin/bash
set -euo pipefail

################################################################################
# ai_code Django 應用啟動腳本 (V4.0 2D 純淨版)
################################################################################

APP_IMAGE="${APP_IMAGE:-ai-code-app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-container}"
RUN_PORT="${RUN_PORT:-8002}"

on_error() {
  echo "❌ 啟動失敗。請檢查 Docker 設定或網路連線。"
  exit 1
}
trap on_error ERR

echo "-------------------------------------------------------"
echo "🌟 啟動 2D 影像處理服務 (Docker 模式)..."
echo "-------------------------------------------------------"

# 1) 載入並檢查 .env
if [ ! -f .env ]; then
  echo "❌ 錯誤：找不到 .env 檔案。請確保包含 GEMINI_API_KEY 等設定。"
  exit 1
fi
set -a; source .env; set +a
echo "✅ 已載入 .env 設定"

# ------------------------------------------------------------------------------
# 2) 啟動主要的 Django Container
# ------------------------------------------------------------------------------
echo "🔨 正在建置主應用映像 (Dockerfile)..."
docker build -t "${APP_IMAGE}" .

echo "🛑 清理舊容器..."
docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "🚀 啟動 AI 應用服務 (Port: ${RUN_PORT})..."
# 移除所有與 densepose_assets 和 smpl_assets 相關的掛載 (-v)
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${RUN_PORT}:8002" \
  --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "$(pwd)/media:/app/media" \
  "${APP_IMAGE}"

echo "-------------------------------------------------------"
echo "🎉 2D 服務部署完成！"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "📊 日誌查看：docker logs -f ${CONTAINER_NAME}"
echo "-------------------------------------------------------"