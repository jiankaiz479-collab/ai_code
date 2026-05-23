## 日期
2026-05-21

## 簡短描述
紀錄 go/bkg-remove 專案的架構決議與下一步實作計畫。

## 決議摘要
- 採用混合式背景處理架構：使用 Celery 作為可靠的背景任務平台，task 內結合 `asyncio` 處理 I/O（Tripo/Gemini），必要時在 task 內用 `ProcessPoolExecutor` / `ThreadPoolExecutor` 做短暫 CPU 加速。 
- GPU 推論採用專用 worker（每個 worker 綁定一個 GPU），或把模型拆成獨立模型服務以避免 fork 引發的 GPU 問題。

## 為何選 Celery
- 需要任務持久化、重試、失敗歸檔與監控。
- 支援水平擴展並能在 worker 內混合使用 sync/async 與 executor。

## 立即可做的改善（優先順序）
1. `mask_cleaner`：新增 mask 後處理模組（morphology + small-component removal + gaussian feather），能立即降低白色殘留/小雜點問題。
2. Celery scaffold：建立 Redis broker 的 Celery 範例（包含示例 task、Docker Compose與 README）。
3. 生產化：分配 GPU worker、設定監控（Flower/Prometheus）與資源限制。

## 實作計畫（短期執行項）
- Step 1：加入 `mask_cleaner.py`，在 pipeline 產出 mask 後呼叫，並把中間結果存到 `REMOVE_BG_FAIL_SAMPLES_DIR`。 
- Step 2：把整個 pipeline 包成 Celery task（`remove_bg_pipeline`），task 內用 `asyncio` 做 HTTP；在需要時用 `ProcessPoolExecutor` 執行 CPU-heavy 清理。
- Step 3：測試樣本回饋調參，設定 `REMOVE_BG_VERSION` 作為切換開關以便快速回滾/比對。

## 待辦 / 決策點
- 決定要我先實作：
  - A) `mask_cleaner` 並整合到 `robust_v2` pipeline（快速見效）
  - B) scaffold Celery（較多工作，但進入生產級工作流程）

## 備註
- Env 與目前 pipeline 參考：`.env` 中的 `REMOVE_BG_VERSION`、`REMOVE_BG_FAIL_SAMPLES_DIR`、以及 `REMOVE_BG_AUTO_FIX_EXPOSURE` 等設定。

紀錄者：助理（自動化記錄）
