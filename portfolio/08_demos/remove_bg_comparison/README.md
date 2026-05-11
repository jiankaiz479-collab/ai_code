# 去背升級對照樣本

> 收集 iPhone 原拍照（input）與升級後去背結果（output），作為推甄 demo 與每次升級的回歸測試集。

## 資料夾結構

```
remove_bg_comparison/
├── originals/    # iPhone 原檔（input）
└── results/      # 去背結果 PNG（output）
```

## 命名規範

### originals
保留 iPhone 原檔名（例如 `IMG_3188.JPG`），方便對照 metadata。

### results
```
{原檔名 stem}_{變體}.png
```
例如：
- `IMG_3188_legacy.png`（舊版 rembg + u2net_human_seg 結果）
- `IMG_3188_robust.png`（升級版 isnet + matting + LCC + erode 結果）
- `IMG_3188_robust_v2.png`（未來如果有 v2 再加）

## 樣本對應表

| ID | 場景 | 難度 | 預期表現 |
|---|---|---|---|
| IMG_3188 | 黑 T-shirt + 灰白磁磚 | ⭐⭐ 簡單 | 應 100% 成功 |
| IMG_3184 | 白色帽 T + 鞋子地板雜物 | ⭐⭐⭐⭐ 中等 | LCC 應擋掉雜物 |
| IMG_3185 | 白色短褲 + 木桌 + 衣架 | ⭐⭐⭐⭐⭐ 困難 | 預期會失敗或黏到衣架 |

## 規則

- ❌ **不要刪除已有的對照圖**，是長期回歸測試集
- ❌ **不要覆蓋同名檔**，要保留歷史
- ✅ 每次升級新增 `*_robust_vN.png` 命名
- ✅ 圖片不上 git（已加入 .gitignore，太大）
