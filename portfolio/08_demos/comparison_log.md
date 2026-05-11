# 升級對照紀錄

> 每次升級的 before / after 圖檔對照表與分析。
> 規則：**檔案不刪、不覆蓋**，舊版長期保留作為回歸測試集。
>
> 圖檔存放位置：`portfolio/08_demos/remove_bg_comparison/`（[README](remove_bg_comparison/README.md)）
> - `originals/` — 輸入原圖
> - `results/` — 去背結果，按版本分資料夾：
>   - `results/legacy/*_legacy.png` — 舊版（u2net_human_seg）
>   - `results/v1/*_robust.png` — Robust v1
>   - `results/v2/...` — 未來版本依此類推
>
> 本檔保留作為「文字版」對照紀錄（即使圖檔遺失，分析與學習仍可查閱）。

---

## 對照組 #1 — 黑 T-shirt + 灰白磁磚（簡單基準）

| 項目 | 舊版（legacy） | Robust v1 |
|---|---|---|
| 原圖 | `originals/IMG_3188.JPG` | 同上 |
| 結果 | `results/legacy/IMG_3188_legacy.png` | `results/v1/IMG_3188_robust.png` |
| rembg 模型 | `u2net_human_seg` | `isnet-general-use` |
| 後處理 | 無 | alpha matting + LCC + erosion 1px |

### 結果對比

| 失敗模式 | 舊版表現 | Robust v1 表現 |
|---|---|---|
| 背景去除 | ⚠️ **邊緣有灰色背景殘留**（人物模型不擅長抓衣服輪廓） | ✅ 完全乾淨 |
| 邊緣 fringe | ⚠️ 有 | ✅ 無 |
| 破洞 | （無）| ✅ 無 |

### 學到的事

即使是「簡單基準」（高對比、單色背景），**舊版人物模型仍會把衣服邊緣判錯**。Robust v1 換通用模型 + 後處理立刻解決。

---

## 對照組 #2 — 白色帽 T + 棉褲 + 雜物地板（中等難度）

| 項目 | 舊版（legacy） | Robust v1 |
|---|---|---|
| 原圖 | `originals/IMG_3184.JPG` | 同上 |
| 結果 | `results/legacy/IMG_3184_legacy.png` | `results/v1/IMG_3184_robust.png` |
| rembg 模型 | `u2net_human_seg` | `isnet-general-use` |

### 結果對比

| 失敗模式 | 舊版表現 | Robust v1 表現 |
|---|---|---|
| 主體保留 | ❌ **白色帽 T 整件被刪掉** | ✅ 完整保留 |
| 雜物處理 | ⚠️ **只留下鞋子和散落小物**（人腳被誤判為主體）| ✅ 雜物全部被 LCC 過濾掉 |
| 邊緣 | ⚠️ 邊角有煙霧狀殘影 | ✅ 乾淨 |

### 為什麼舊版會這樣

`u2net_human_seg` 訓練資料是「人 + 衣服」一起出現的場景。當輸入「平鋪的衣服 + 鞋」時，模型把**最像「人腳」的鞋當成主體**，反而把白色衣服當成背景刪掉。

**這是「模型 domain 不匹配」最戲劇化的例子** —— 證明 v1 換 `isnet-general-use` 是必要的。

---

## 對照組 #3 — 白色短褲 + 木桌 + 粉紅衣架（困難）

| 項目 | 舊版（legacy） | Robust v1 |
|---|---|---|
| 原圖 | `originals/IMG_3185.JPG` | 同上 |
| 結果 | `results/legacy/IMG_3185_legacy.png` | `results/v1/IMG_3185_robust.png` |
| rembg 模型 | `u2net_human_seg` | `isnet-general-use` |

### 結果對比

| 失敗模式 | 舊版表現 | Robust v1 表現 |
|---|---|---|
| 背景（木桌）去除 | ✅ 有去掉 | ✅ 有去掉 |
| 衣架處理 | ⚠️ 衣架還在 | ⚠️ 衣架還在 |
| 邊緣品質 | ⚠️ 偏粗糙 | ✅ 乾淨 |

### 兩版都解不掉的問題

衣架穿在褲頭，與短褲在像素上**連通** → LCC（最大連通區域）原理上無法分離連通元件，兩版都會把衣架一起保留。

### 改善方向（roadmap v3）

1. **形狀分析**：偵測細長條狀區域（衣架特徵）後遮罩
2. **SAM 2 + 類別提示**：用 "clothing" 類別讓模型理解語義邊界
3. **拍照規範**：建議使用者拍照前**先把衣服從衣架取下平鋪**（最低成本）
4. **後處理用 Gemini 二次裁切**：請 LLM 識別並回傳衣架 bbox 後挖空

---

## 三組測試總結

| 對照組 | 樣本 | 場景 | 舊版 | Robust v1 |
|---|---|---|---|---|
| #1 | IMG_3188 | 黑 T + 磁磚 | ⚠️ 邊緣有殘留 | ✅ 完美 |
| #2 | IMG_3184 | 白帽 T + 雜物 | ❌ **整件衣服被誤刪** | ✅ 完美 |
| #3 | IMG_3185 | 白短褲 + 衣架 | ⚠️ 邊緣粗糙 + 衣架在 | ⚠️ 衣架在（邊緣乾淨）|

### 結論

- **舊版 + 人物模型**對「衣服去背」根本不適用（#2 直接刪掉衣服只留鞋）
- **Robust v1 + 通用模型 + 後處理**：常見場景（#1、#2）表現極佳；連通物件（#3）仍有限制
- **下一步**：連通物件（#3）→ roadmap v3 形狀分析

---

## 對照組命名規範（未來新增時遵守）

```
對照組 #N — <描述>
原圖位置:   portfolio/08_demos/remove_bg_comparison/originals/<IMG_X>.JPG
舊版結果:   portfolio/08_demos/remove_bg_comparison/results/legacy/<IMG_X>_legacy.png
v1 結果:    portfolio/08_demos/remove_bg_comparison/results/v1/<IMG_X>_robust.png
v2 結果:    portfolio/08_demos/remove_bg_comparison/results/v2/<IMG_X>_robust.png
（不要刪、不要覆蓋；圖檔不上 git，僅 md 紀錄會上）
```
