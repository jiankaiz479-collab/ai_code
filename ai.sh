#!/bin/bash
set -euo pipefail

################################################################################
# ai_code Django 應用啟動腳本 (V4.0 純 Docker 自動化版)
################################################################################

APP_IMAGE="${APP_IMAGE:-ai-code-app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-container}"
RUN_PORT="${RUN_PORT:-8002}"

# 本地資產路徑 (掛載用)
TARGET_DP_DIR="$(pwd)/densepose_assets"
TARGET_SMPL_DIR="$(pwd)/smpl_assets"

on_error() {
  echo "❌ 啟動失敗。請確認 .env 中的 ID 正確，且網路連線正常。"
  exit 1
}
trap on_error ERR

echo "-------------------------------------------------------"
echo "🌟 啟動全自動部署流程 (純 Docker 模式)..."
echo "-------------------------------------------------------"

# 1) 載入並檢查 .env
if [ ! -f .env ]; then
  echo "❌ 錯誤：找不到 .env 檔案。"
  exit 1
fi
set -a; source .env; set +a
echo "✅ 已載入 .env 設定"

# ------------------------------------------------------------------------------
# 🚀 2) 雲端資源檢查與下載 (主機免安裝 Python 工具)
# ------------------------------------------------------------------------------
echo "🔍 正在檢查模型資產..."

# A. 下載 DensePose 權重
if [ ! -f "${TARGET_DP_DIR}/model_final_162be9.pkl" ]; then
    echo "🚚 發現缺少 DensePose 權重，啟動臨時下載容器..."
    mkdir -p "${TARGET_DP_DIR}"
    # 使用 python:3.11-slim 鏡像執行下載，跑完自動銷毀 (--rm)
    docker run --rm \
      -v "${TARGET_DP_DIR}:/downloads" \
      python:3.11-slim \
      bash -c "pip install gdown && gdown --folder ${DENSEPOSE_FOLDER_ID} -O /downloads"
    echo "✅ DensePose 權重下載成功"
fi

# B. 下載 SMPL 模型
if [ ! -d "${TARGET_SMPL_DIR}/smpl" ]; then
    echo "🚚 正在下載 SMPL 模型..."
    mkdir -p "${TARGET_SMPL_DIR}"
    
    # 用臨時容器下載
    docker run --rm -v "${TARGET_SMPL_DIR}:/downloads" python:3.11-slim \
      bash -c "pip install gdown && gdown --folder ${SMPL_FOLDER_ID} -O /downloads"
    
    # 🚀 關鍵：確保下載下來的內容是在 smpl 子資料夾內
    # 如果 gdown 直接把檔案丟在 smpl_assets，我們手動幫它歸位
    if [ ! -d "${TARGET_SMPL_DIR}/smpl" ]; then
        mkdir -p "${TARGET_SMPL_DIR}/smpl"
        mv ${TARGET_SMPL_DIR}/*.pkl ${TARGET_SMPL_DIR}/smpl/ 2>/dev/null || true
    fi

    # 執行更名 (對齊 smplx 套件預設檔名)
    echo "🔧 執行檔名標準化..."
    mv ${TARGET_SMPL_DIR}/smpl/basicmodel_neutral_*.pkl ${TARGET_SMPL_DIR}/smpl/SMPL_NEUTRAL.pkl 2>/dev/null || true
    mv ${TARGET_SMPL_DIR}/smpl/basicmodel_f_*.pkl ${TARGET_SMPL_DIR}/smpl/SMPL_FEMALE.pkl 2>/dev/null || true
    mv ${TARGET_SMPL_DIR}/smpl/basicmodel_m_*.pkl ${TARGET_SMPL_DIR}/smpl/SMPL_MALE.pkl 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 3) 啟動主要的 Django Container
# ------------------------------------------------------------------------------
echo "🔨 正在建置主應用映像 (Dockerfile)..."
docker build -t "${APP_IMAGE}" .

echo "🛑 清理舊容器..."
docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "🚀 啟動 AI 應用服務..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${RUN_PORT}:8002" \
  --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "${TARGET_DP_DIR}:/app/densepose_assets" \
  -v "${TARGET_SMPL_DIR}:/app/smpl_assets" \
  -v "$(pwd)/media:/app/media" \
  "${APP_IMAGE}"

echo "-------------------------------------------------------"
echo "🎉 部署完成！"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "📊 日誌查看：docker logs -f ${CONTAINER_NAME}"
echo "-------------------------------------------------------"  