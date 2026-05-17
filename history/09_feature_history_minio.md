
# 09. 歷史紀錄與 MinIO 整合計畫

> 本文件記錄「歷史紀錄查詢與 MinIO 雲端儲存」功能的架構規劃與實作步驟。展現從前端 UI 設計、Mock 資料展示到後端真實 API 與雲端儲存串接的完整軟體工程交付流程。

## 📌 核心目標 (TL;DR)
建立可瀏覽處理歷史（去背 / 2D / 3D）的控制台：後端把原圖與縮圖上傳至 MinIO，寫入 `HistoryRecord`（含 `response_json`、`start_ts`、`end_ts`、`exec_time_ms`）；前端先做 Portfolio 靜態 Demo（快速展示），之後整合到 Django 使用真實 API。

---

## 🏗️ 架構與設定
- **MinIO S3 API 埠**：`9002`（host）→ container `9002`（徹底避開 9000 避免衝突）
- **MinIO Console 埠**：`10092`（host）→ container `10092`（徹底避開 9001）
- **啟動方式**：整合進 `ai.sh` / Dockerfile 啟動
- **Bucket 名稱**：`history-images`
- **使用者權限**：目前不限制，顯示全部歷史
- **歷史保留**：永久（不自動刪除）

---

## 🎯 前端 UI 設計（三層式佈局）
- **左側 Sidebar**：垂直按鈕列（切換去背 / 2D / 3D），選中狀態高亮；手機版可收合成上方 Tab，點選後立即過濾卡片。
- **中間 Main Grid**：Responsive Card Grid，卡片包含縮圖、操作類型 Badge、時間（YYYY‑MM‑DD HH:MM）、成功/失敗狀態。先用 Mock data 驗證 Layout 與互動。
- **中央 Modal 彈出視窗**：置中浮層，左側大圖、右側 Metadata（包含 `response_json`、start/end/exec_time），支援 Esc 鍵或點擊背景關閉。
- **視覺風格**：參考 Google Drive / Google Photos 的乾淨白底、卡片陰影、圓角與留白，避免過度複雜。
- **互動規則**：切換 Sidebar 只刷新 Grid（不刷新整頁）；點擊卡片才展開大圖；大圖與 Metadata 分欄呈現。

---

## 🚀 開發階段拆解

### 第一階段：Portfolio 靜態 Demo (快速展示)
> 目標：快速建立可用於面試或展示的 UI 雛形，確認視覺與動線。
1. 新增 Demo 檔案到 `portfolio/08_demos/`：
   - `history_demo.html`：HTML 範本，包含右側卡片格狀與 Modal
   - `history.css`：CSS 樣式（Grid, Card, Modal, Responsive）
   - `history.js`：JS 互動邏輯（Mock JSON 渲染、過濾、Modal 控制）
2. 行為驗證：右側顯示卡片格狀，點擊卡片開啟 Modal 顯示大圖與 Mock Metadata。
3. 交付物：無須啟動伺服器，用瀏覽器直接打開 HTML 即可展示的靜態頁面。

### 第二階段：Django 後端整合 (真實 API 與 MinIO)
> 目標：實作資料庫模型、串接 MinIO S3 儲存，並提供真實的 API 供前端呼叫。

1. **資料模型與 Migration**：
   - 在 `ai_app/models.py` 新增 `HistoryRecord` model。
   - 欄位：`id`, `operation`, `bucket`, `object_key`, `thumb_key`, `response_json`, `start_ts`, `end_ts`, `exec_time_ms`, `created_at`。

2. **後端服務串接 (Service Layer)**：
   - 修改 `remove_bg_service.py`、`try_on_service.py`、`try_on_3d_service.py`。
   - 任務完成時生成縮圖，呼叫 S3 客戶端上傳原圖與縮圖到 MinIO，取得 Object keys。
   - 寫入 `HistoryRecord` 資料庫。

3. **API 端點實作 (`views.py` & `urls.py`)**：
   - `GET /api/history/?operation=&page=&page_size=`：回傳分頁紀錄（含縮圖 URL 與基本 metadata）。
   - `GET /api/history/{id}/`：回傳單筆完整資料（含放大圖 URL 與 `response_json`）。

4. **Template & 靜態資源搬移**：
   - 將第一階段的 HTML/CSS/JS 搬移至 `ai_app/templates/` 與 `ai_app/static/`。
   - JS 改為 `fetch()` 真實 API 資料。

5. **系統設定 (`settings.py`)**：
   - 讀取 MinIO 環境變數（`MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`）。

---

## ✅ 驗收清單
- [x] **靜態展示**：能在瀏覽器直接打開 Demo HTML，網格佈局與彈出視窗互動正常。
- [x] **雲端儲存**：執行 AI 任務後，能進入 MinIO Console (`:10092`) 看到原圖與縮圖成功上傳。
- [x] **資料庫紀錄**：`HistoryRecord` 確實存入 `start_ts`、`end_ts`、耗時與完整的 `response_json`。
- [x] **前端整合**：Django 頁面成功透過 API 載入 MinIO 圖片與紀錄，過濾功能與詳細視窗皆呈現真實資料。