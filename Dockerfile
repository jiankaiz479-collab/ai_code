# 使用輕量版 Python (基底)
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 1. 安裝系統底層依賴 (修正版)
# 使用 libgl1 取代舊的 libgl1-mesa-glx 以支援 Debian Bookworm
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 2. 複製 requirements.txt 並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 預先下載去背模型
# 這裡會下載 u2net 模型，避免啟動時才下載
RUN python -c "from rembg.session_factory import new_session; new_session('u2net')"

# 4. 複製所有程式碼
COPY . .

# 設定環境變數
ENV PYTHONUNBUFFERED=1

# 啟動指令
CMD ["python", "manage.py", "runserver", "0.0.0.0:8002"]