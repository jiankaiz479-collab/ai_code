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

# Python tooling + CPU PyTorch + detectron2 runtime deps
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
      torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
      cython \
      pycocotools \
      termcolor \
      yacs \
      tabulate \
      cloudpickle \
      matplotlib \
      tqdm \
      omegaconf \
      fvcore \
      iopath

# Install detectron2 and DensePose from official source
RUN git clone --depth 1 https://github.com/facebookresearch/detectron2.git /app/detectron2 && \
    pip install --no-cache-dir -e /app/detectron2 --no-build-isolation && \
    pip install --no-cache-dir -e /app/detectron2/projects/DensePose --no-build-isolation

# Verify DensePose is importable and config exists
RUN python -c "import os; import detectron2; from densepose import add_densepose_config; cfg='/app/detectron2/projects/DensePose/configs/densepose_rcnn_R_50_FPN_s1x.yaml'; assert os.path.exists(cfg), cfg; print('detectron2+densepose ok')"

# App dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# DensePose weights directory
RUN mkdir -p /app/densepose_assets

# Try downloading DensePose weights during build; do not fail build if blocked
RUN python - <<'PY' || true
import os
import urllib.request

output_path = "/app/densepose_assets/model_final_162be9.pkl"
urls = [
    "https://dl.fbaipublicfiles.com/densepose/densepose_rcnn_R_50_FPN_s1x/165712039/model_final_162be9.pkl",
    "https://dl.fbaipublicfiles.com/densepose/densepose_rcnn_R_50_FPN_s1x/164832416/model_final_162be9.pkl",
    "https://huggingface.co/DeepGraphLearning/densepose/resolve/main/model_final_162be9.pkl",
]

def is_valid(path):
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 10 * 1024 * 1024:
        return False
    with open(path, "rb") as file_obj:
        header = file_obj.read(5)
    return not header.startswith(b"<")

for url in urls:
    try:
        print(f"downloading: {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=300) as response, open(output_path, "wb") as file_obj:
            file_obj.write(response.read())
        if is_valid(output_path):
            print(f"ok: {output_path} ({os.path.getsize(output_path) // 1024 // 1024}MB)")
            break
        print("invalid file, try next")
        os.remove(output_path)
    except Exception as exc:
        print(f"failed: {exc}")
        if os.path.exists(output_path):
            os.remove(output_path)
else:
    print("warning: all sources failed, mount weights at runtime")
PY

# App source
COPY . .

# Preload rembg model
RUN python -c "from rembg import new_session; new_session()" || true

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import django; print('ok')" || exit 1

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]