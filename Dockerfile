# 1. 基底
FROM python:3.10-slim

# 2. 工作目錄
WORKDIR /app

# (原本報錯的 apt-get 整段刪除，我們不需要了！)

# 3. 安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製程式碼
COPY . .

# 5. 開放 Port
EXPOSE 8000

# 6. 啟動
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]