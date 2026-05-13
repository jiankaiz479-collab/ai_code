# 已知失敗 Case 集（known limitations）

> 收集「目前架構解不掉」的失敗樣本。每個 case 紀錄：失敗模式、為什麼擋不到、預計哪一版才能處理。
>
> 與 `remove_bg_comparison/` 的差別：
> - `remove_bg_comparison/` = 升級前後**都能跑**的對照組
> - `known_limitations/` = 升級後**仍跑不出來**的失敗樣本

## 命名規範

```
case_<NN>_<short_slug>_<input|result>.<ext>
```

- 例：`case_01_sunlit_floor_input.JPG`、`case_01_sunlit_floor_result.png`

## Case 清單

| # | 簡稱 | 場景 | 失敗模式 | 預計處理版本 |
|---|---|---|---|---|
| 01 | sunlit_floor | 白衣 + 強光直射 + 同色磁磚地 | LCC 把亮地板誤判成衣服一部分；強光區也讓 rembg 把上衣 / 下褲分成兩塊 | ✅ **已修復於 v2**（CLAHE clipLimit=3.0 局部抑制強光，完整 mask 從 9.7% → 47.2%，bbox 高度 834 → 1340） |
| 02 | blurry_unrelated | 嚴重模糊 + 拍到非衣服（牆角、銅板） | v1 Laplacian 應擋下，但若沒擋會跑出無意義輸出 | v1（已有但門檻可能需調整），或 v2 Gemini 預檢 |
| 03 | whiteboard_screen | 白板 + 筆電螢幕（不是衣服） | 純像素檢查抓不到「不是衣服」 | v2（Gemini 預檢「這是不是衣服」） |

## 規則

- ❌ 不要刪除已收檔的 case，是長期回歸測試集
- ✅ 修好的 case 仍保留，標註「已修復於 vN」
- ✅ 圖片不上 git（圖檔太大）
