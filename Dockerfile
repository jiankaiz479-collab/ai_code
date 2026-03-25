FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/app/.u2net \
    FORCE_CUDA=0 \
    FVCORE_CACHE=/tmp

# System dependencies
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
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python tooling + CPU PyTorch
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
      torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cpu

# [暫時註解] Install detectron2 and DensePose from official source
RUN git clone --depth 1 https://github.com/facebookresearch/detectron2.git /app/detectron2 && \
    pip install --no-cache-dir -e /app/detectron2 --no-build-isolation && \
    pip install --no-cache-dir -e /app/detectron2/projects/DensePose --no-build-isolation

# [暫時註解] Verify DensePose
RUN python -c "import os; import detectron2; from densepose import add_densepose_config; cfg='/app/detectron2/projects/DensePose/configs/densepose_rcnn_R_50_FPN_s1x.yaml'; assert os.path.exists(cfg), cfg; print('detectron2+densepose ok')"

# App dependencies (現在包含所有 DensePose 的 Python 工具)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# DensePose weights directory
RUN mkdir -p /app/densepose_assets

# App source
COPY . .

# Preload rembg model
RUN python -c "from rembg import new_session; new_session()" || true

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import django; print('ok')" || exit 1

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]