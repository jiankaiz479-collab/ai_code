# 03. AI 服務選型決策

> 這個檔案是給推甄教授看的「AI 選型思考」。教授看到「我用了 Gemini」不會驚訝，但會問「**為什麼**選 Gemini？」——這份文件回答這些問題。

---

## 三個 AI 模組的角色定位

| 模組 | 任務 | 選型 |
|---|---|---|
| 去背 | 把衣服 / 人物從背景切出來 | **rembg (U2-Net)** |
| 圖像理解 + 合成 | 看圖判斷風格、合成試穿 | **Gemini 2.5 Flash Image** |
| 3D 重建 | 從一張人像照生 3D 模型 | **Tripo3D image_to_model** |

---

## 決策 1：去背為什麼選 rembg 而不是 Gemini？

| 方案 | 優點 | 缺點 |
|---|---|---|
| rembg (U2-Net) | 本地、免費、毫秒級 | 模型固定，無法調 prompt |
| Gemini「請去背這張圖」 | 可微調指令 | 慢、每次扣 token、結果不穩定 |
| Remove.bg API | 商業 API，效果好 | 收費、加密綁定外部服務 |

**選擇**：rembg
**理由**：
1. 去背是「定義明確」的任務，不需要 LLM 級別的理解能力
2. **每張圖呼叫一次 Gemini 太貴**，去背可能一次上傳要做 5~10 張衣服圖
3. 本地跑 = 不被 Google 限流，不擔心離線

**取捨我能接受的代價**：失去 prompt 彈性。但對「去背」這個任務來說，模型固定其實是優點（結果可預期）。

---

## 決策 2：圖像分析 + 合成為什麼選 Gemini？

候選：Gemini 2.5 Flash Image / GPT-4o / Claude 3.5 Sonnet vision / Stable Diffusion + ControlNet

| 方案 | 試穿合成能力 | 多模態理解 | 成本 | 速度 |
|---|---|---|---|---|
| Gemini Flash Image | ✅ 直接出圖 | ✅ 強 | ✅ 便宜 | ✅ 快 |
| GPT-4o | ❌ 不能直接出圖（要走 DALL·E） | ✅ 強 | ❌ 貴 | ⚠️ 中 |
| Claude Sonnet | ❌ 不能出圖 | ✅ 強 | ❌ 貴 | ⚠️ 中 |
| SD + ControlNet | ✅ 強 | ❌ 需自己拼理解流程 | ⚠️ GPU 成本 | ❌ 慢 |

**選擇**：Gemini 2.5 Flash Image
**理由**：
1. **它是目前少數能「看圖 + 生圖」一氣呵成的雲端模型**，不用拼兩個服務
2. 價格比 GPT-4o 系列便宜很多（學生專題經費敏感）
3. 試過 `gemini-3-pro-image-preview` 與 `gemini-2.5-flash-image`，後者品質夠用、便宜很多
4. 多模型分工：
   - `GEMINI_MODEL_NAME=gemini-2.5-flash-image` 做試穿合成
   - `GEMINI_CONSULTANT_MODEL=gemini-2.5-flash` 做顏色判斷與品管

**關鍵設計**：把「圖像理解」和「圖像生成」拆成兩個 Gemini 呼叫，**用便宜的 model 做輕量任務**——這是省成本的核心技巧。

---

## 決策 3：3D 重建為什麼選 Tripo？

候選：Tripo3D / Meshy / TripoSR / OpenAI Shape-E / 自訓 NeRF

| 方案 | 一張圖能跑？ | 紋理品質 | 成本 | 部署難度 |
|---|---|---|---|---|
| **Tripo3D** | ✅ | ✅ 高（PBR） | 💰 雲端付費 | ✅ 純 API |
| Meshy | ✅ | ✅ 高 | 💰 雲端付費 | ✅ 純 API |
| TripoSR (open source) | ✅ | ⚠️ 中 | ✅ 免費 | ❌ 要自己佈 GPU |
| Shape-E (OpenAI) | ✅ | ❌ 低 | ✅ 免費 | ⚠️ 中 |
| 自訓 NeRF | ❌ 需多張圖 | ✅ 高 | ✅ 免費 | ❌ 極難 |

**選擇**：Tripo3D
**理由**（這部分是 Tripo > Meshy 的關鍵差異）：
1. Tripo 支援 `texture_alignment` 參數，可以選 `original_image`（紋理嚴格對齊原圖）或 `geometry`（幾何優先）
2. Tripo 的 `image_to_model` 在「人物」類別表現比 Meshy 穩
3. Tripo 有 `Refine` 兩階段：先 draft 拿快速結果，再 refine 拿高品質——對開發流程友善
4. 多版本（v2.5 / v3.0 / v3.1 / P1）可選，便於成本/品質 trade-off

**為什麼不用 open source 的 TripoSR？**
- 推甄專題時間有限，自己佈 GPU 跑 + 寫推論邏輯 → 太多時間花在 infra
- 商業 API 讓我把時間花在「**怎麼用好 AI**」而不是「怎麼讓 AI 跑起來」

---

## 決策 4：人像標準化為什麼自己做（不交給 AI）？

問題：人像照進 Tripo 前，需要先「去背 + 置中 + 1024×1024」。

候選：
- A) 全交給 Tripo（讓它自己處理）
- B) 用 Gemini「幫我把人物置中」
- C) **自己用 PIL/OpenCV 寫**

**選擇**：C
**理由**：
1. **這是有明確輸出規格的任務**（1024×1024，5% 留白）—— 寫死 code 比 AI 穩定 100 倍
2. AI 做不可預測的事情（有可能生出 1023×1024 或加奇怪邊框）
3. 跑得快（毫秒級）+ 不花錢

**設計原則**：**AI 用在「無法用規則描述」的任務上，能寫 code 解決的就不要叫 AI。**

---

## 決策 5：Tripo 模型版本選 v3.1-20260211

Tripo 提供：
- `v1.3` (已棄用)
- `v1.4`
- `v2.0` ~ `v2.5`
- **`v3.0` / `v3.1`** ← 我選
- `Turbo-v1.0`（快速版，犧牲品質）
- `P1`（低面數專用）

**選擇**：`v3.1-20260211`
**理由**：
- v3.0+ 才支援 `geometry_quality` 參數（控制幾何精度）
- 比 v2.5 在「衣服紋理」表現好
- P1 低面數雖快，但**不支援部件分割 / 四邊形網格**，未來想做布料模擬會卡

---

## 決策 6：是否啟用 Tripo Refine 兩階段？

Tripo 流程：
```
image_to_model (draft) → refine_model (200k 面精修)
```

`Refine` 會額外扣一次積分。

**設計**：用 `.env` 變數 `TRIPO_ENABLE_REFINE` 控制，**預設關閉**。
**理由**：
1. Draft 階段（100k 面）對「2D 預覽」已經夠用
2. 真的要做物理模擬時再開 Refine
3. 開發測試階段如果預設開啟，**錢會燒得很快**

---

## 推甄面試 1 分鐘 AI 選型講法

> 「這個專案我刻意挑了三個**互補的 AI**：
>
> rembg 做去背——選它是因為去背是定義明確的任務，不需要 LLM 級別的理解，本地跑省成本；
>
> Gemini 做圖像理解和合成——因為它是少數能『看圖 + 生圖』一氣呵成的模型，而且我做了**多模型分工**，用便宜的 flash 做顏色判斷，貴的 flash-image 才做合成；
>
> Tripo 做 3D 重建——選它而不選 Meshy，是因為 Tripo 有 `texture_alignment` 參數可以嚴格對齊原圖紋理，這對『試穿』場景很重要——使用者上傳一件紅衣服，3D 出來不能變橘色。
>
> 整體設計原則是：**AI 用在不能寫 code 解決的事，能寫 code 的不要交給 AI**。」
