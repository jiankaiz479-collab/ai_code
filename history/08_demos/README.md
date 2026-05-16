# 08. Demo 素材

> 推甄**書面審查 + 面試**都會用到的視覺素材。
> 現在不存以後找不到。

---

## 該存的素材清單

### A. before / after 對照
- [ ] **去背**：原圖 vs 去背後（建議 2~3 組，含失敗 case）
- [ ] **2D 試穿**：原始模特 + 衣服 → 試穿合成（不同類型衣服各一組）
- [ ] **3D 重建**：原圖 → 3D 模型截圖（最好 4 個角度：正、側、背、45°）
- [ ] **人像標準化**：raw 人像 → 1024×1024 標準化版本

### B. 失敗 case 截圖（**這個很重要**）
- [ ] 上傳半身照觸發 4422 的範例
- [ ] 紅衣變橘的 prompt 修正前後對比
- [ ] 卡通化的 Tripo 預設輸出 vs 加負向 prompt 後

### C. 系統截圖
- [ ] Postman / curl 呼叫 API 的截圖
- [ ] Server log 截圖（顯示業務碼追蹤）
- [ ] Docker 啟動畫面（`bash ai.sh` 輸出）

### D. 圖表
- [ ] 系統架構圖（可用 [02_architecture.md](../02_architecture.md) 的 ASCII 圖渲染）
- [ ] 資料流程圖
- [ ] 錯誤碼映射表（5_error_design.md 的內容做成圖）

### E. 歷史紀錄查詢 UI Demo
- [ ] 歷史紀錄 UI 截圖（待補，等開發完成後截圖放來這）
- [ ] 詳細狀態 JSON 檢視 Modal 截圖（待補）

### F. 影片（面試加分）
- [ ] 30 秒 demo 影片：上傳 → 處理 → 看到 3D 模型旋轉
- [ ] 60 秒架構解說（自己錄音 + 投影片）

---

## 推薦工具

| 用途 | 工具 |
|---|---|
| 截圖標記 | macOS 內建截圖 / Snipaste |
| 流程圖 | Excalidraw, Mermaid, draw.io |
| GLB 模型截圖 | Three.js viewer / Blender / [gltf-viewer](https://gltf-viewer.donmccurdy.com/) |
| 錄影 | OBS Studio / macOS QuickTime |
| 簡報 | Keynote / Google Slides |

---

## 命名規範（避免之後找不到）

```
08_demos/
├── before_after_rembg_01.png
├── before_after_rembg_02.png
├── before_after_tryon_shirt.png
├── before_after_3d_personA_front.png
├── before_after_3d_personA_side.png
├── failed_4422_half_body.png
├── prompt_compare_color_shift.png
├── architecture_v1.png
├── system_log_screenshot.png
└── demo_video_30s.mp4
```

---

## 面試 demo 順序建議（3 分鐘版）

1. **0:00-0:30** 講問題：使用者痛點、為什麼用 AI、為什麼要 3D
2. **0:30-1:00** 展示架構圖（一張）
3. **1:00-1:30** 跑一次完整流程：上傳人像 → 3D 模型
4. **1:30-2:00** 講「最得意的設計」：mock 模式 / 多模型分工 / prompt
5. **2:00-2:30** 講「卡關故事」：紅衣變橘 / 積分燒完
6. **2:30-3:00** 講「下一步」：物理布料模擬 / prompt 自動優化

---

> 接下來開發升級時記得**隨手存截圖到這個資料夾**，不要等推甄前才開始翻歷史照片。
