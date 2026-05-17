FROM python:3.10-slim

WORKDIR /app

# 1. 基礎環境設定
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/.u2net

# 2. 安裝系統套件
# 這裡維持不變，但 Docker 只要看過這層沒變，就不會重新 apt-get
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential wget \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 3. 安裝 PyTorch CPU 版 (利用 Docker 快取)
# 只要這行沒改，Docker 就不會重新下載好幾百 MB 的 Torch
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 4. 安裝專案依賴
# [智慧檢查點]：我們先 COPY requirements.txt 
# 如果你只改了 python 程式碼，沒動到 requirements.txt，Docker 就會跳過這一整層的安裝！
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 建立必要目錄
RUN mkdir -p /app/media

# 6. 拷貝程式碼 (放到越後面越好，因為程式碼最常改動)
# 注意：因為有 .dockerignore，所以不會拷貝到 venv 那些垃圾檔案了！
COPY . .

# 7. 智慧預載 rembg 模型 (包含預設與人像優化版)
# 先檢查模型是否已存在，不存在才透過 curl 下載，大幅加快容器冷啟動速度
RUN mkdir -p ${U2NET_HOME} && \
    if [ ! -f "${U2NET_HOME}/u2net_human_seg.onnx" ]; then \
        echo "Downloading u2net_human_seg.onnx..." && \
        curl -L -o ${U2NET_HOME}/u2net_human_seg.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx; \
    fi && \
    if [ ! -f "${U2NET_HOME}/u2net.onnx" ]; then \
        echo "Downloading u2net.onnx..." && \
        curl -L -o ${U2NET_HOME}/u2net.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx; \
    fi

EXPOSE ${RUN_PORT:-8002}

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:${RUN_PORT:-8002}"]