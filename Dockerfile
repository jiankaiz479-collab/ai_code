# 1. 基底映像檔
FROM python:3.10-slim

# 2. 設定環境變數
# U2NET_HOME: 強制指定 rembg 模型下載到 /app/.u2net，避免權限問題
ENV PYTHONUNBUFFERED=1 \
    U2NET_HOME=/app/.u2net

# 3. 工作目錄
WORKDIR /app

# 4. 安裝套件 (利用 Cache 加速)
COPY requirements.txt .
# 記得 requirements.txt 裡要有: rembg, opencv-python-headless
RUN pip install --no-cache-dir -r requirements.txt

# 5. [關鍵一步] 預先下載 u2net 模型
# 因為 rembg 的 remove() 預設是用 'u2net'
# 我們在 Build 階段就把它下載下來，這樣容器啟動後去背會超快！
RUN python -c "from rembg.session_factory import new_session; new_session('u2net')"

# 6. 複製其餘程式碼
COPY . .

# 7. 開放 Port
EXPOSE 8000

# 8. 啟動 Django
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]