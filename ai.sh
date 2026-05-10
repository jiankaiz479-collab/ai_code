
################################################################################
# ai_code 總指揮腳本 (V7.0 - 整合 setup_3d.sh 版本)
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

