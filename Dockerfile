FROM python:3.11-slim

WORKDIR /app

# 基本環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/app/.u2net \
    FORCE_CUDA=0

# 系統依賴：OpenCV + Detectron2 編譯必備
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    ninja-build \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python build 工具 + CPU 版 torch
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
      torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir cython

# 先安裝 detectron2（需依賴已安裝好的 torch）
RUN pip install --no-cache-dir \
    'git+https://github.com/facebookresearch/detectron2.git' \
    --no-build-isolation

# 專案依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案
COPY . .

# 預下載 rembg 模型（失敗不阻斷 build）
RUN python -c "from rembg import new_session; new_session()" || true

EXPOSE 8002

# 簡易健康檢查：Django 可匯入即可
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import django; print('ok')" || exit 1

# 啟動：先 migrate 再 runserver（開發用）
CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]