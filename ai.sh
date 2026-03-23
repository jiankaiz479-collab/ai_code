#!/bin/bash
set -euo pipefail

################################################################################
# ai_code Django 應用啟動腳本（Docker）
# - 載入 .env
# - 檢查必要變數
# - 檢查本機 DensePose 權重是否存在
# - 檢查 Docker 狀態
# - build image / 重建 container / 啟動服務
################################################################################

APP_IMAGE="${APP_IMAGE:-ai_code_app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai_code_container}"
APP_PORT_IN_CONTAINER="8002"
LOCAL_DENSEPOSE_WEIGHTS="$(pwd)/densepose_assets/model_final_162be9.pkl"

on_error() {
  local code=$?
  echo ""
  echo "❌ 啟動失敗（exit code: ${code}）"
  echo "💡 先看上方錯誤，或執行：docker logs -f ${CONTAINER_NAME}"
  exit "${code}"
}
trap on_error ERR

# 1) 載入 .env
if [ ! -f .env ]; then
  echo "❌ 錯誤：找不到 .env 檔案"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a
echo "✅ 已載入 .env"

# 2) 基礎變數檢查
if [ -z "${RUN_PORT:-}" ]; then
  echo "❌ 錯誤：.env 未設定 RUN_PORT"
  exit 1
fi

if ! [[ "${RUN_PORT}" =~ ^[0-9]+$ ]] || [ "${RUN_PORT}" -lt 1 ] || [ "${RUN_PORT}" -gt 65535 ]; then
  echo "❌ 錯誤：RUN_PORT 必須是 1-65535 的整數，當前值：${RUN_PORT}"
  exit 1
fi

if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "❌ 錯誤：.env 未設定 GOOGLE_API_KEY"
  exit 1
fi

echo "✅ RUN_PORT / GOOGLE_API_KEY 檢查通過"

# # 3) DensePose 設定檢查
# if [ "${ENABLE_DENSEPOSE:-false}" = "true" ]; then
#   if [ ! -f "${LOCAL_DENSEPOSE_WEIGHTS}" ]; then
#     echo "❌ 錯誤：找不到 DensePose 權重檔"
#     echo "   預期位置：${LOCAL_DENSEPOSE_WEIGHTS}"
#     exit 1
#   fi

#   if [ "$(stat -c%s "${LOCAL_DENSEPOSE_WEIGHTS}")" -lt 10000000 ]; then
#     echo "❌ 錯誤：DensePose 權重檔太小，可能不是有效模型"
#     echo "   檔案：${LOCAL_DENSEPOSE_WEIGHTS}"
#     exit 1
#   fi

#   echo "✅ DensePose 權重檢查通過"
# fi

# 4) Docker 檢查
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 錯誤：Docker 未安裝"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ 錯誤：Docker 服務未運行"
  exit 1
fi

echo "✅ Docker 檢查通過"

# 5) 建置映像
echo "🔨 開始建置映像：${APP_IMAGE}"
docker build -t "${APP_IMAGE}" .

# 6) 停止舊容器
echo "🛑 停止舊容器（若存在）"
docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true

# 7) 啟動新容器
echo "🚀 啟動新容器：${CONTAINER_NAME}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${RUN_PORT}:${APP_PORT_IN_CONTAINER}" \
  --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "$(pwd)/media:/app/media" \
  "${APP_IMAGE}"
  # -v "$(pwd)/densepose_assets:/app/densepose_assets" \




# 8) 完成提示
echo "-------------------------------------------------------"
echo "🎉 容器啟動成功"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "📊 查看日誌：docker logs -f ${CONTAINER_NAME}"
echo "🛑 停止容器：docker stop ${CONTAINER_NAME}"
echo "-------------------------------------------------------"

