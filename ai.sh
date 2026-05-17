
################################################################################
# ai_code 總指揮腳本 
################################################################################
BASE_DIR=$(pwd)
APP_IMAGE="${APP_IMAGE:-ai-code-app}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-container}"
RUN_PORT="${RUN_PORT:-8002}"



on_error() {
  echo "❌ 啟動失敗。請檢查設定、網路連線或磁碟空間。"
  exit 1
}
trap on_error ERR

echo "======================================================="
echo "🛡️  正在進行系統全面檢查與初始化..."
echo "======================================================="

# ---------------------------------------------------------
# 0) 自動下載服裝部位分割 ONNX 模型 (供 v3 Human Parsing 使用)
# ---------------------------------------------------------
mkdir -p models
if [ ! -f "models/u2net_cloth_seg.onnx" ]; then
    echo "⬇️ Downloading u2net_cloth_seg ONNX model (approx. 170MB)..."
    wget -O models/u2net_cloth_seg.onnx "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_cloth_seg.onnx"
    echo "✅ Model downloaded successfully."
fi


# ---------------------------------------------------------
# 3) 檢查並建置 Docker 映像檔 (2D 服務)
# ---------------------------------------------------------
if [ "$FORCE_BUILD" = true ]; then
    echo "🛠️ [1/2] 狀態：偵測到 --build 參數，強制重新建置 Docker 映像..."
    docker build -t "${APP_IMAGE}" .
elif [[ "$(docker images -q ${APP_IMAGE} 2> /dev/null)" == "" ]]; then
    echo "🔍 [1/2] 狀態：未偵測到 Docker 映像。正在建置..."
    docker build -t "${APP_IMAGE}" .
else
    echo "✅ [1/2] 狀態：Docker 映像已存在，跳過建置。"
fi

# ---------------------------------------------------------
# 3.5) 啟動 MinIO 儲存服務
# ---------------------------------------------------------
echo "🔍 [1.5/2] 狀態：正在啟動 MinIO 儲存服務..."
docker network create app-network >/dev/null 2>&1 || true

docker stop ai-minio >/dev/null 2>&1 || true
docker rm ai-minio >/dev/null 2>&1 || true

docker run -d \
  --name ai-minio \
  --network app-network \
  -p 9002:9002 \
  -p 10092:10092 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v "$(pwd)/minio_data:/data" \
  minio/minio server /data --address ":9002" --console-address ":10092"

echo "⏳ 正在等待 MinIO 啟動並建立 Bucket..."
sleep 3
docker run --rm --network app-network --entrypoint sh minio/mc -c "mc alias set myminio http://ai-minio:9002 minioadmin minioadmin && mc mb myminio/history-images --ignore-existing && mc anonymous set public myminio/history-images"

# ---------------------------------------------------------
# 4) 啟動 Docker 容器
# ---------------------------------------------------------
echo "🔍 [1/2] 狀態：正在啟動 2D 影像服務..."
if [ ! -f .env ]; then
  echo "❌ 錯誤：找不到 .env 檔案。"
  exit 1
fi
set -a; source .env; set +a

docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true


docker run -d \
  --name "${CONTAINER_NAME}" \
  --network app-network \
  -p "${RUN_PORT}:${RUN_PORT}" \
  --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "$(pwd)/cv_testing_site:/app/cv_testing_site" \
  -v "$(pwd)/media:/app/media" \
  -v "$(pwd)/models:/app/models" \
  -e U2NET_HOME=/app/.u2net \
  "${APP_IMAGE}"

echo "======================================================="
echo "🎉 全系統配置完成！"
echo "🔗 MinIO S3 API：http://localhost:9002"
echo "🔗 MinIO Console：http://localhost:10092 (登入: minioadmin / minioadmin)"
echo "🔗 服務位址：http://localhost:${RUN_PORT}"
echo "======================================================="
