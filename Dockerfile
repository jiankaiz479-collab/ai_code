FROM python:3.11-slim

WORKDIR /app

# 基本環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/app/.u2net \
    FORCE_CUDA=0

# 系統依賴：OpenCV + Detectron2 / DensePose 編譯必備
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

# 安裝 detectron2
RUN pip install --no-cache-dir \
    "git+https://github.com/facebookresearch/detectron2.git" \
    --no-build-isolation

# 安裝 DensePose（重點）
RUN pip install --no-cache-dir \
    "git+https://github.com/facebookresearch/detectron2.git#subdirectory=projects/DensePose" \
    --no-build-isolation

# 專案依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案
COPY . .

# 預下載 rembg 模型（失敗不阻斷 build）
RUN python -c "from rembg import new_session; new_session()" || true

# 驗證 DensePose 可匯入
RUN python -c "from densepose import add_densepose_config; print('densepose ok')"

EXPOSE 8002

# 健康檢查：Django + DensePose
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import django; from densepose import add_densepose_config; print('ok')" || exit 1

# 啟動：先 migrate 再 runserver（開發用）
CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]