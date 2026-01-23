#!/bin/bash

# 腳本說明：此腳本用於啟動 ai_core Django 應用，並自動處理 Docker 相關操作。
# 它會檢查 Docker 是否安裝並運行，然後構建 Docker 鏡像並啟動容器。
# 容器內的應用會運行在端口 8001，並映射到主機的 RUN_PORT 端口（從 .env 文件讀取）。
# 新增：支持熱重載，通過卷掛載主機目錄到容器。

# 載入環境變數從 .env 文件（如果存在）
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 檢查 RUN_PORT 是否設置
if [ -z "$RUN_PORT" ]; then
    echo "錯誤：.env 文件中未設置 RUN_PORT 變數。"
    exit 1
fi

# 檢查 Docker 是否安裝並運行
if ! command -v docker &> /dev/null; then
    echo "錯誤：Docker 未安裝。請先安裝 Docker。"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "錯誤：Docker 服務未運行。請啟動 Docker 服務。"
    exit 1
fi

echo "Docker 檢查通過，正在構建鏡像..."

# 構建 Docker 鏡像
docker build -t ai_core_app .

if [ $? -ne 0 ]; then
    echo "錯誤：Docker 鏡像構建失敗。"
    exit 1
fi

echo "鏡像構建成功，正在啟動容器（支持熱重載）..."

# 停止並刪除可能存在的舊容器
docker stop ai_core_container 2>/dev/null
docker rm ai_core_container 2>/dev/null

# 啟動新容器，映射端口 RUN_PORT:8001，掛載主機目錄支持熱重載，並設置環境變數，覆蓋默認命令讓應用監聽 8001
docker run -d \
    --name ai_core_container \
    -p $RUN_PORT:8002 \
    -v $(pwd):/app \
    --env-file .env \
    ai_core_app \
    python manage.py runserver 0.0.0.0:8002

if [ $? -eq 0 ]; then
    echo "容器啟動成功！應用現在運行在 http://localhost:$RUN_PORT"
    echo "熱重載已啟用：修改代碼後，服務器會自動重啟。"
    echo "要停止容器，請運行：docker stop ai_core_container"
    echo "要查看日誌，請運行：docker logs ai_core_container"
else
    echo "錯誤：容器啟動失敗。"
    exit 1
fi