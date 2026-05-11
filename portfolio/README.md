# Portfolio — Virtual Try-On AI Service

> 本資料夾用途：**推甄資工/AI 研究所**的作品集素材匯整。
> 內容是專案的「思考軌跡」與「設計決策」，不是流水帳。

---

## 📌 一句話介紹專案

> 整合 **rembg（CV）+ Gemini（VLM）+ Tripo3D（3D 生成）** 三種異質 AI 服務的虛擬試穿後端，把使用者一張人像照轉換成「3D 試穿模型」的完整 pipeline。

- 開發期間：**2026-01-18 ~ 2026-05-10（4 個月，230 commits）**
- 角色：個人專案 / 後端 + AI 整合
- 技術棧：Django · Python · Docker · Gemini API · Tripo3D API · rembg

---

## 📂 檔案導覽

| 檔案 | 內容 | 推甄面試會用到 |
|---|---|---|
| [01_problem.md](01_problem.md) | 問題定義、使用者痛點、為何做這個 | ✅ 開場 |
| [02_architecture.md](02_architecture.md) | 系統架構、資料流、分層設計 | ✅ 必講 |
| [03_ai_choices.md](03_ai_choices.md) | AI 服務選型決策（Gemini / Tripo / rembg） | ⭐ AI 所必講 |
| [04_prompt_design.md](04_prompt_design.md) | Prompt engineering 與負向 prompt 實戰 | ⭐ AI 所必講 |
| [05_error_design.md](05_error_design.md) | 業務錯誤碼設計、上游錯誤映射 | ✅ 顯工程力 |
| [06_bugs_and_fixes.md](06_bugs_and_fixes.md) | 卡關故事（待累積） | 🔥 教授最愛問 |
| [07_metrics.md](07_metrics.md) | 量化成果（速度、成本、成功率） | ✅ 提數字 |
| [08_demos/](08_demos/) | 截圖、影片、before/after | ✅ 面試 demo |
| [99_self_intro.md](99_self_intro.md) | 自傳/讀書計畫草稿 | ✅ 書面審查 |

---

## 🎯 我會在面試怎麼講這個專案（30 秒版）

> 「我做了一個虛擬試穿系統，把『人像照 → 3D 試穿模型』的流程，用三種不同 AI 串接起來：rembg 做 CV 級別的去背、Gemini 做圖像理解與合成、Tripo3D 做 3D 重建。
>
> 過程中我特別關注三件事：**第一**，AI 服務的選型——為什麼選 Tripo 而不是 Meshy？因為 Tripo 的 `image_to_model` 有 `texture_alignment` 參數可以精準對齊紋理；**第二**，Prompt 設計——我寫了一段針對『寫實還原』場景的長 prompt + 負向 prompt 去避開卡通化；**第三**，工程細節——做了 6 段式錯誤碼把上游 Tripo 的 8 種錯誤映射成前端可以使用的業務碼，還做了一個 Mock 模式讓開發時不花積分。
>
> 上線後一張人像生成 3D 模型約 90 秒，後來透過參數調優降到 35 秒。」

---

## ⚠️ 使用本資料夾的守則

- 寫**思考過程**，不寫流水帳
- 寫**「為什麼」**，不只寫「做了什麼」
- 數字 > 形容詞（「快了 60%」勝過「跑得更順」）
- 每個檔案的目標：**面試官 30 秒能抓到重點**

---

更新日期: 2026-05-10
