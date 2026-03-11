#!/bin/bash
set -euo pipefail

################################################################################
# ai_code Django 應用啟動腳本（Docker）
# - 載入 .env
# - 檢查必要變數與 DensePose 設定
# - 檢查 Docker 狀態
# - build image / 重建 container / 啟動服務
################################################################################

# 可由外部環境覆蓋
APP_IMAGE="${APP_IMAGE:-ai_code_app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai_code_container}"
APP_PORT_IN_CONTAINER="8002"

# 失敗時提示
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

# 3) DensePose 設定檢查（改為彈性）
if [ "${ENABLE_DENSEPOSE:-false}" = "true" ]; then
  echo "ℹ️ ENABLE_DENSEPOSE=true，開始檢查 DensePose 設定"

  # DENSEPOSE_CFG 可留空：自動偵測
  if [ -z "${DENSEPOSE_CFG:-}" ]; then
    echo "⚠️ DENSEPOSE_CFG 未設定，嘗試自動偵測..."
    AUTO_CFG="$(python -c "import os,detectron2; p=os.path.dirname(detectron2.__file__); c=os.path.join(os.path.dirname(p),'projects','DensePose','configs','densepose_rcnn_R_50_FPN_s1x.yaml'); print(c if os.path.isfile(c) else '')" 2>/dev/null || true)"
    if [ -n "${AUTO_CFG}" ]; then
      DENSEPOSE_CFG="${AUTO_CFG}"
      export DENSEPOSE_CFG
      echo "✅ 自動偵測到 DENSEPOSE_CFG：${DENSEPOSE_CFG}"
    else
      echo "❌ 錯誤：找不到 DensePose cfg，請在 .env 設定 DENSEPOSE_CFG"
      exit 1
    fi
  fi

  # DENSEPOSE_CFG 若是本地路徑（非 URL），必須存在
  if [[ "${DENSEPOSE_CFG}" != http://* ]] && [[ "${DENSEPOSE_CFG}" != https://* ]]; then
    if [ ! -f "${DENSEPOSE_CFG}" ]; then
      echo "❌ 錯誤：DENSEPOSE_CFG 本地檔案不存在：${DENSEPOSE_CFG}"
      echo "💡 若是相對路徑，請確認你在專案根目錄執行此腳本"
      exit 1
    fi
    echo "✅ DensePose cfg 檔案存在：${DENSEPOSE_CFG}"
  else
    echo "⚠️ DENSEPOSE_CFG 是 URL：${DENSEPOSE_CFG}"
  fi

  # DENSEPOSE_WEIGHTS 改為可選：有填才檢查
  if [ -n "${DENSEPOSE_WEIGHTS:-}" ]; then
    if [[ "${DENSEPOSE_WEIGHTS}" != http://* ]] && [[ "${DENSEPOSE_WEIGHTS}" != https://* ]]; then
      if [ ! -f "${DENSEPOSE_WEIGHTS}" ]; then
        echo "❌ 錯誤：DENSEPOSE_WEIGHTS 本地檔案不存在：${DENSEPOSE_WEIGHTS}"
        exit 1
      fi
      echo "✅ DensePose weights 檔案存在：${DENSEPOSE_WEIGHTS}"
    else
      echo "⚠️ DENSEPOSE_WEIGHTS 是 URL：${DENSEPOSE_WEIGHTS}"
    fi
  else
    echo "ℹ️ 未設定 DENSEPOSE_WEIGHTS，將使用程式預設/自動下載邏輯"
  fi

  echo "✅ DensePose 設定檢查通過"
else
  echo "ℹ️ ENABLE_DENSEPOSE!=true，略過 DensePose 檢查"
fi

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

# 7) 啟動新容器（掛載專案目錄，支援熱更新）
echo "🚀 啟動新容器：${CONTAINER_NAME}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${RUN_PORT}:${APP_PORT_IN_CONTAINER}" \
  -v "$(pwd):/app" \
  --env-file .env \
  -e DENSEPOSE_CFG="${DENSEPOSE_CFG:-}" \
  "${APP_IMAGE}" \
  python manage.py runserver 0.0.0.0:${APP_PORT_IN_CONTAINER}

# 8) 完成提示
echo "-------------------------------------------------------"
echo "🎉 容器啟動成功"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "📊 查看日誌：docker logs -f ${CONTAINER_NAME}"
echo "🛑 停止容器：docker stop ${CONTAINER_NAME}"
echo "-------------------------------------------------------"