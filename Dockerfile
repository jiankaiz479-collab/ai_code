FROM python:3.10-slim

WORKDIR /app

# 1. 基礎環境與路徑設定
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/app/.u2net \
    FORCE_CUDA=0 \
    FVCORE_CACHE=/tmp \
    # 🚀 修正點：指向父目錄 /app/smpl_assets，避免 smplx 自動拼接成 smpl/smpl
    SMPL_MODEL_DIR=/app/smpl_assets


# 🚀 更新 3D 渲染補丁：改用 EGL 體系
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
    # --- 關鍵 EGL 補丁 (取代 osmesa) ---
    libegl1-mesa-dev \
    libgbm1 \
    libgl1-mesa-dri \
    # -------------------------------
    ninja-build ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
    
# 安裝 PyTorch CPU 版與最新的建置工具
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# [保留註解] 安裝 Detectron2 & DensePose
# RUN git clone --depth 1 https://github.com/facebookresearch/detectron2.git /app/detectron2 && \
#     pip install --no-cache-dir -e /app/detectron2 --no-build-isolation && \
#     pip install --no-cache-dir -e /app/detectron2/projects/DensePose --no-build-isolation

# 安裝其他依賴 (requirements.txt)
COPY requirements.txt .
# 🚀 關鍵修正：增加 --no-build-isolation 解決 chumpy 安裝時找不到 pip 的問題
RUN pip install --no-cache-dir --no-build-isolation -r requirements.txt

# 🚀 預建資產目錄 (結構對齊掛載路徑)
RUN mkdir -p /app/densepose_assets /app/smpl_assets /app/media

# 拷貝程式碼
COPY . .

# 預載 rembg (非必要，但能加快啟動)
# RUN python -c "from rembg import new_session; new_session()" || true

EXPOSE 8002

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]