# 04. Prompt Engineering 實戰

> 這份文件記錄專案中所有「跟 AI 講話的設計」——對 AI 所教授來說，這是看你**會不會用 AI**最直接的證據。

---

## 我認知中的 Prompt Engineering 三層

| 層 | 做什麼 | 例子 |
|---|---|---|
| L1：能用 | 寫一句指令叫 AI 動作 | "把這張圖去背" |
| L2：能控 | 加入限制、輸出格式、負向指令 | "輸出 JSON，欄位 X/Y/Z，禁止解釋" |
| L3：能調 | 觀察失敗 case，反覆修詞拒絕特定錯誤 | "preserve identical facial features..." |

我在這個專案做到 **L3**。

---

## 案例 1：Tripo `image_to_model` 的寫實還原 prompt

### 痛點
Tripo 預設會「過度美化」人物（皮膚變塑膠感、臉變漫畫感、衣服顏色偏移）。這對「虛擬試穿」是致命的——使用者想看「自己穿這件衣服」，不是「一個跟我有點像的角色」。

### 設計
位置：[ai_app/services/processing.py:550-564](../ai_app/services/processing.py#L550)

**正向 Prompt**（指引「我要什麼」）：
```python
TRIPO_DEFAULT_PROMPT = (
    "photorealistic 3D character, exact replica of the input photo, "
    "preserve identical facial features, identity, hairstyle, skin tone, "
    "preserve exact clothing design, fabric color, patterns, logos, wrinkles, "
    "preserve body proportions and silhouette, sharp clean outline, "
    "realistic colors matching the source image, no color shift, "
    "high-fidelity texture, accurate detail reproduction, "
    "neutral A-pose, full body"
)
```

**負向 Prompt**（指引「我不要什麼」）：
```python
TRIPO_DEFAULT_NEGATIVE_PROMPT = (
    "cartoon, anime, stylized, deformed, distorted, blurry, "
    "oversmooth, plastic skin, color shift, saturated, washed out, "
    "extra limbs, missing limbs, asymmetric face, melted features, "
    "artistic interpretation, fantasy elements"
)
```

### 設計思路（4 個關鍵）

1. **重複關鍵詞強化語意**：`preserve identical / preserve exact / preserve body` 三次「preserve」是刻意的——Tripo 對某些詞權重比較敏感
2. **負向 prompt 比正向更重要**：列出所有失敗模式（卡通化、塑膠皮膚、偏色、肢體錯誤），等於告訴模型「不准走這些歪路」
3. **`neutral A-pose, full body`** 是給後續 3D 應用（綁骨架、布料模擬）鋪路的隱性需求
4. **`no color shift`** 是踩過坑後加上去的——曾經發生紅衣變橘色

### 學到的 prompt 規律
- **形容詞要疊**：`sharp clean outline` 比 `sharp outline` 結果穩定
- **負向詞要具體**：寫 `cartoon, anime, stylized` 比寫 `not realistic` 有效 10 倍
- **要懂模型偏好**：Tripo 對 `photorealistic` / `high-fidelity` 這類詞有強反應

---

## 案例 2：Gemini 試穿合成 prompt（在 processing.py 內）

### 痛點
Gemini 做「衣服試穿合成」會發生：
- 把衣服尺寸縮放成奇怪比例
- 把模特兒身材改變
- 把背景變掉

### 策略
**多輪 prompt + 結構化指令**（細節在 processing.py 的試穿合成函數，含參考圖角色定位）。

關鍵設計：
- 把 model_image 標為 "the person"
- 把 garment_image 標為 "the garment to be worn"
- 明確指令 "preserve the person's facial features and body proportions"
- 強調 "naturally drape the garment on the person"

---

## 案例 3：Gemini 顏色判斷 prompt（廉價模型分工）

### 設計
讓便宜的 `gemini-2.5-flash`（沒有 image 輸出能力，但有 vision）做：
- 衣服顏色判斷
- 衣服風格分類（Casual / Formal / Sport...）
- 品管：判斷一張合成圖是否「正常」

### 為什麼這樣設計？
> **用便宜的 model 做能力範圍內的事，貴的 model 只做它獨有的事**

例如：判斷一件衣服是紅色還是橘色，flash 就夠了；不需要 flash-image 也不需要 GPT-4。

### Prompt 風格
強制 JSON 輸出，例如：
```
Analyze this garment and respond ONLY in JSON format:
{
  "clothes_category": "<clothing|pants|accessory>",
  "style_name": ["<style1>", "<style2>"],
  "color_name": ["<color1>"]
}
Do not include any explanation.
```

**關鍵技巧**：`ONLY in JSON` + `Do not include any explanation` → 後端可以直接 `json.loads()` 不用 regex 解析。

---

## 案例 4：Mock 模式的「假 prompt」設計

開發時 `TRIPO_DEBUG_MOCK=true`，跳過 Tripo，但前後端流程要一致。

**設計**：直接回傳預存的 mock GLB，不真的呼叫 Tripo。這不是 prompt 但是「**省 prompt 成本**」的設計——讓開發迭代不會每次都燒積分。

---

## 我對 Prompt Engineering 的反思

### 我學到的
1. **負向 prompt 比正向 prompt 重要**：人腦想的是「我要什麼」，但 AI 失敗的方式很多——列出失敗模式比描述成功模式更有效
2. **同一個 prompt 不同模型反應差很多**：在 Gemini 有用的詞，在 Tripo 可能完全沒用
3. **Prompt 是工程，不是文學**：要可重現、可版本控（所以我把 prompt 放程式碼裡而不是 .env）
4. **Prompt 要踩過坑才知道怎麼寫**：上面這些 prompt 都是經過 10+ 次失敗 case 修出來的

### 我還想研究的（推甄方向）
- **自動 Prompt 優化**：能不能用一個小模型自動修 prompt？（例如 RLAIF / DSPy）
- **多模態 Prompt 對齊**：圖像 + 文字 prompt 各自貢獻多少？
- **Prompt vs Fine-tuning**：什麼時候該調 prompt，什麼時候該 fine-tune？

---

## 推甄面試 1 分鐘 Prompt 講法

> 「我在 Tripo 的部分做了**正向 + 負向**兩段 prompt。正向 prompt 反覆強調 `preserve identical/exact/body`，要 Tripo 嚴格還原原圖；負向 prompt 列舉了我踩過的所有坑——卡通化、塑膠皮膚、偏色、肢體錯誤——等於告訴模型『不准走這些歪路』。
>
> 還有一個我比較得意的設計：**多模型分工**。Gemini 有兩個版本，貴的 `flash-image` 只做合成，便宜的 `flash` 做顏色判斷和品管，後者強制輸出 JSON 不准解釋——這讓我可以直接 `json.loads()` 不用寫 regex parser。
>
> 推甄之後我想繼續研究 prompt 自動優化的問題，因為現在這些 prompt 都是手調的，能不能用 RL 自動修？」
