# 使用輕量版 Python (基底)
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app
ENV U2NET_HOME=/app/.u2net
# 1. 安裝系統底層依賴
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# [新增] 指定模型存放的環境變數，這能確保所有 AI 工具都待在 /app 之下
ENV U2NET_HOME=/app/.u2net

# 2. 複製 requirements.txt 並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 預先下載去背模型 (這會直接載入 Image 的層級中)
RUN python -c "from rembg.session_factory import new_session; new_session('u2net')"

# 4. 複製所有程式碼
COPY . .

# 設定環境變數
ENV PYTHONUNBUFFERED=1

# 啟動指令
CMD ["python", "manage.py", "runserver", "0.0.0.0:8002"]