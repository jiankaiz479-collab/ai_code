# 升級對照紀錄

> 每次升級的 before / after 圖檔對照表與分析。
> 規則：**檔案不刪、不覆蓋**，舊版長期保留作為回歸測試集。
>
> 圖檔存放位置：`portfolio/08_demos/remove_bg_comparison/`（[README](remove_bg_comparison/README.md)）
> - `originals/` — 輸入原圖
> - `results/` — 每個版本的去背結果
>
> 本檔保留作為「文字版」對照紀錄（即使圖檔遺失，分析與學習仍可查閱）。

---

## 對照組 #1 — 暗光複雜場景（地上躺）

| 項目 | 舊版（baseline） | Robust v1 |
|---|---|---|
| 檔名 | `modules_0055e839.png` | `processed_5abbb07d.png` |
| 路徑（已失效） | ~~`media/modules_0055e839.png`~~ ⚠️ 已刪 | ~~`media/processed_5abbb07d.png`~~ ⚠️ 已刪 |
| 應放路徑（未來規範） | `portfolio/08_demos/remove_bg_comparison/results/{IMG_X}_legacy.png` | `portfolio/08_demos/remove_bg_comparison/results/{IMG_X}_robust.png` |
| 來源 endpoint | `/fitting/modules`（人像標準化） | `/clothes/remove_bg`（衣服去背 robust 模式） |
| 檔案大小 | 432 KB | 1.8 MB |
| 產出時間 | 2026-05-10 22:50 | 2026-05-11 00:00 |
| 輸入照片 | iPhone 14 Plus 拍，3024×4032，ISO 640，凌晨 12:34 拍攝，水泥地 + 金屬櫃 + 塑膠袋背景，蜷曲側躺姿勢 | 同上（同一張原圖） |
| rembg 模型 | `u2net_human_seg`（舊版預設） | `isnet-general-use` |
| 後處理 | 無（只 crop bbox） | alpha matting + 最大連通區域 + erosion 1px |
| HTTP 回應 | 200（假成功） | 200（仍是假成功 — 預期 1423 但沒撞到閾值） |

### 觀察到的問題（兩版**都有**）

| 失敗模式 | 舊版表現 | Robust v1 表現 |
|---|---|---|
| 背景沒去乾淨 | ⚠️ 部分背景殘留 + 金屬櫃殘影 | ⚠️ 幾乎整張保留，只有邊緣淡化漸層 |
| 棕色鞋子殘留 | ⚠️ 有 | ✅ 被 LCC 過濾掉了 |
| 白邊 fringe | ⚠️ 有 | ⚠️ 變成粉紅 / 橘色漸層暈染（alpha matting 軟邊緣 + 周圍牆面顏色） |
| 蒙版破洞 | ⚠️ 胸口有 | （無，但因為幾乎沒去背所以也沒洞） |
| 系統能否偵測失敗 | ❌ 回 200 假成功 | ❌ 也回 200 假成功（蒙版面積剛好沒撞到 95% 閾值） |

### 真實的失敗原因

兩版都失敗，但**失敗方式不同**：
- **舊版**：rembg 真的做了去背，但邊緣判定不準（保留鞋子、破洞）
- **Robust v1**：rembg 對這張**極暗 + 複雜背景**的圖判定「整張幾乎都是前景」，所以蒙版面積接近 100%，幾乎沒去任何東西

### 從這個對照學到的事

1. **isnet 對「複雜暗光場景」不見得比 u2net 好** — 它太保守，全部留下來
2. **我設的 `mask_coverage_max=0.95` 閾值太寬鬆** — 90% 的「假去背」可以通過
3. **alpha matting 軟邊緣** 在背景顏色強烈時（粉紅塑膠袋 + 金屬反光）會產生奇怪的色彩漸層
4. **這張圖根本不該做去背** — 應該在更上層用「姿勢預檢查」直接擋下

### 後續修正方向（對應 roadmap v2）

- 把 `REMOVE_BG_MASK_COVERAGE_MAX` 從 `0.95` 收緊到 `0.85`（v2 順手做）
- 加 Gemini 預檢「這張是不是衣服」 → 這張圖是人躺著，會被擋下（v2）
- 加品質檢查：亮度太低 → 直接擋（v2 規劃中）
- 試 `REMOVE_BG_ALPHA_MATTING=false`（硬邊緣，避免色彩漸層暈染）
- 長期：對 `/fitting/modules` 加姿勢預檢查

---

## 對照組 #2 — 黑 T-shirt + 灰白磁磚（簡單基準）

| 項目 | Robust v1 |
|---|---|
| 原圖 | `originals/IMG_3188.JPG` |
| 結果 | `results/IMG_3188_robust.png` |
| 來源 endpoint | `/clothes/remove_bg`（robust） |
| 輸入照片 | iPhone 14 Plus 原檔，4032×3024，EXIF 完整，黑色短袖 T-shirt 平放在灰白磁磚地 |
| 預測難度 | ⭐⭐ 簡單（高對比、單色背景） |

### 結果評估

- ✅ 背景完全去除
- ✅ 邊緣乾淨，無 fringe
- ✅ 無破洞、無殘留
- ✅ 符合預期 — 高對比 case 是 robust 的基本盤

---

## 對照組 #3 — 白色帽 T + 棉褲 + 雜物地板（中等難度）

| 項目 | Robust v1 |
|---|---|
| 原圖 | `originals/IMG_3184.JPG` |
| 結果 | `results/IMG_3184_robust.png` |
| 來源 endpoint | `/clothes/remove_bg`（robust） |
| 輸入照片 | iPhone 14 Plus 原檔，淺色帽 T + 棉褲攤平，地板上有雜物干擾 |
| 預測難度 | ⭐⭐⭐⭐ 中等（低對比 + 雜物） |

### 結果評估

- ✅ 背景完全去除（雜物全擋）
- ✅ 邊緣無 fringe、無漸層暈染
- ✅ 即使白衣 vs 淺色地板（低對比）也分得乾淨
- 🎯 **超出預期** — LCC（最大連通區域）成功擋掉所有不相連的雜物

### 從這組學到的事

LCC 在這類「衣服是唯一主體 + 雜物獨立散落」的場景非常有效。
這是 robust 升級相對舊版 u2net_human_seg 最明顯的進步點。

---

## 對照組 #4 — 白色短褲 + 木桌 + 粉紅衣架（困難）

| 項目 | Robust v1 |
|---|---|
| 原圖 | `originals/IMG_3185.JPG` |
| 結果 | `results/IMG_3185_robust.png` |
| 來源 endpoint | `/clothes/remove_bg`（robust） |
| 輸入照片 | iPhone 14 Plus 原檔，白色短褲掛在粉紅衣架上，木桌背景 |
| 預測難度 | ⭐⭐⭐⭐⭐ 困難（衣架實體連接衣服） |

### 結果評估

- ✅ 背景（木桌）完全去除
- ✅ 邊緣乾淨
- ⚠️ **粉紅衣架被一起保留** — 因為衣架穿在褲頭，與短褲在像素上是**連通的**
- ✅ 符合預期 — LCC 的原理本來就無法分離連通元件

### 真實的失敗原因

LCC（Largest Connected Component）只能過濾**不相連**的雜物。
衣架插在褲頭、與布料像素相連 → 整個被視為同一個前景區域。

### 改善方向（v2 候選）

1. **形狀分析**：偵測細長條狀區域（衣架特徵）後遮罩
2. **SAM 2 + 類別提示**：用 "clothing" 類別讓模型理解語義邊界
3. **拍照規範**：建議使用者拍照前**先把衣服從衣架取下平鋪** — 最低成本解法
4. **後處理用 Gemini 二次裁切**：請 LLM 識別並回傳衣架 bbox 後挖空

---

## 四組測試總結

| 對照組 | 樣本 | 場景 | 預測 | 實測 | 差距 |
|---|---|---|---|---|---|
| #1 | modules_0055e839 | 暗光複雜場景（人物躺地）| — | ❌ 假成功（兩版都失敗）| 模型對極端 case 過於保守 |
| #2 | IMG_3188 | 黑 T + 磁磚 | ⭐⭐ | ✅ 完美 | 符合 |
| #3 | IMG_3184 | 白帽 T + 雜物地板 | ⭐⭐⭐⭐ | ✅ 完美 | **超出預期** |
| #4 | IMG_3185 | 白短褲 + 衣架 | ⭐⭐⭐⭐⭐ | ⚠️ 衣架殘留 | 符合（LCC 限制）|

**結論**：
- robust 流程在「衣服 + 不相連雜物」（#2、#3）表現極佳
- 「衣服 + 相連物件」（#4 衣架）是已知限制，需 v3 形狀分析解決
- 「極端場景 + 非衣服輸入」（#1）要等 v2 Gemini 預檢擋下

---

## 對照組命名規範（未來新增時遵守）

```
對照組 #N — <描述>
原圖位置:   portfolio/08_demos/remove_bg_comparison/originals/<IMG_X>.JPG
舊版結果:   portfolio/08_demos/remove_bg_comparison/results/<IMG_X>_legacy.png
新版結果:   portfolio/08_demos/remove_bg_comparison/results/<IMG_X>_robust.png
未來新版:   portfolio/08_demos/remove_bg_comparison/results/<IMG_X>_robust_v2.png
（不要刪、不要覆蓋；圖檔不上 git，僅 md 紀錄會上）
```
