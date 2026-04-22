
################################################################################
# ai_code 總指揮腳本 (V7.0 - 整合 setup_3d.sh 版本)
################################################################################
BASE_DIR=$(pwd)
APP_IMAGE="${APP_IMAGE:-ai-code-app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-container}"
RUN_PORT="${RUN_PORT:-8002}"

# 路徑定義
ENGINE_DIR="./ai_app/services/3d_engines"
PIFU_SRC_DIR="$ENGINE_DIR/pifuhd"

on_error() {
  echo "❌ 啟動失敗。請檢查設定、網路連線或磁碟空間。"
  exit 1
}
trap on_error ERR

echo "======================================================="
echo "🛡️  正在進行系統全面檢查與初始化..."
echo "======================================================="

# ---------------------------------------------------------
# 1) 執行 3D 環境配置 (呼叫 setup_3d.sh)
# ---------------------------------------------------------
echo "🔍 [1/4] 檢查 3D 運作環境..."
if [ -f "./setup_3d.sh" ]; then
    # 執行你寫好的 setup_3d.sh，它會處理 venv 建立、套件安裝、語法修正
    bash ./setup_3d.sh
else
    echo "❌ 錯誤：找不到 setup_3d.sh，請確保該腳本與 ai.sh 在同一個資料夾。"
    exit 1
fi

# ---------------------------------------------------------
# 2) 檢查 3D 模型權重檔
# ---------------------------------------------------------
# (這部分保留在 ai.sh 是因為它屬於「資產檢查」而非「環境配置」)
FINAL_CHECKPOINT="$ENGINE_DIR/checkpoints/pifuhd_final.pdb"
if [ ! -f "$FINAL_CHECKPOINT" ]; then
    echo "🔍 [2/4] 狀態：缺少模型權重。準備啟動下載..."
    mkdir -p "$ENGINE_DIR/checkpoints"
    pushd "$PIFU_SRC_DIR" > /dev/null
        sh ./scripts/download_trained_model.sh || true
    popd > /dev/null
    
    TEMP_FILE="$PIFU_SRC_DIR/checkpoints/pifuhd.pt"
    if [ -f "$TEMP_FILE" ]; then
        mv "$TEMP_FILE" "$FINAL_CHECKPOINT"
        echo "✅ 權重配置成功！"
    fi
else
    echo "✅ [2/4] 狀態：PIFuHD 權重檔已就緒。"
fi

# ---------------------------------------------------------
# 3) 檢查並建置 Docker 映像檔 (2D 服務)
# ---------------------------------------------------------
if [[ "$(docker images -q ${APP_IMAGE} 2> /dev/null)" == "" ]]; then
    echo "🔍 [3/4] 狀態：未偵測到 Docker 映像。正在建置..."
    docker build -t "${APP_IMAGE}" .
else
    echo "✅ [3/4] 狀態：Docker 映像已存在，跳過建置。"
fi

# ---------------------------------------------------------
# 4) 啟動 Docker 容器
# ---------------------------------------------------------
echo "🔍 [4/4] 狀態：正在啟動 2D 影像服務..."
if [ ! -f .env ]; then
  echo "❌ 錯誤：找不到 .env 檔案。"
  exit 1
fi
set -a; source .env; set +a

docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true


docker run -d \
  --name "${CONTAINER_NAME}" \
  --network my_network \
  -p "${RUN_PORT}:8002" \
  --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "$(pwd)/media:/app/media" \
  -e U2NET_HOME=/app/.u2net \
  "${APP_IMAGE}"

echo "======================================================="
echo "🎉 全系統配置完成！"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "======================================================="

