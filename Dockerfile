FROM python:3.10-slim

WORKDIR /app

# 1. 基礎環境與路徑設定 
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    U2NET_HOME=/app/.u2net

# 2. 安裝系統套件 
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 3. 安裝 PyTorch CPU 版 
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 4. 安裝其他依賴 (requirements.txt)
COPY requirements.txt .


RUN pip install --no-cache-dir -r requirements.txt

# 5. 預建資產目錄
RUN mkdir -p /app/media

# 6. 拷貝程式碼
COPY . .

# 7. 預載 rembg 
RUN python -c "from rembg import new_session; new_session()" || true

EXPOSE 8002

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8002"]