# AI Background Removal Service (AI 去背模組)

這是 "Virtual Try-On" 專案的 AI 核心模組，負責接收圖片並去除背景。
使用技術：Django, Rembg (U2-Net), Docker。

## 🚀 快速啟動 (Quick Start)

只要你有安裝 Docker，執行以下指令即可啟動服務：

### 1. 建置並啟動
```bash
docker build -t ai_service .
docker run -p 8001:8001 --env-file .env ai_service