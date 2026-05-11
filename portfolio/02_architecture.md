# 02. 系統架構

## 高階資料流

```
                    ┌─────────────────┐
                    │   前端 (隊友)    │
                    └────────┬────────┘
                             │ multipart/form-data
                             ▼
        ┌───────────────────────────────────────┐
        │      Django Backend  (port 8002)      │
        │  ┌─────────────────────────────────┐  │
        │  │       ai_app/views.py (路由層)    │  │
        │  └───────────┬─────────────────────┘  │
        │              │                         │
        │  ┌───────────▼─────────────────────┐  │
        │  │  services/processing.py (核心)    │  │
        │  │  ┌────────────────────────────┐ │  │
        │  │  │ AIProcessor (整合 3 種 AI) │ │  │
        │  │  └─┬──────────┬──────────┬───┘ │  │
        │  └────┼──────────┼──────────┼─────┘  │
        └───────┼──────────┼──────────┼────────┘
                ▼          ▼          ▼
        ┌──────────┐ ┌──────────┐ ┌────────┐
        │  rembg   │ │  Gemini  │ │ Tripo  │
        │ (本地CV) │ │ (Google) │ │  (3D)  │
        └──────────┘ └──────────┘ └────────┘
```

---

## 分層設計（為什麼這樣切？）

### 第 1 層：URL 路由 (`urls.py`)
- 只負責 path → view 對應
- **不放邏輯**
- **設計理由**：未來換 web framework（例如改 FastAPI）只要重寫這層

### 第 2 層：View 入口 (`views.py`)
- 接收 multipart 請求、驗證參數、組裝 multipart 回應
- 呼叫 services 層的 AI 處理
- **不直接 call 第三方 API**
- **設計理由**：View 應該對使用者體驗負責（錯誤訊息、HTTP 狀態），不該知道 Gemini 怎麼用

### 第 3 層：AI Services (`services/processing.py`)
- 集中所有 AI 呼叫邏輯
- 每個 AI service 一組方法群（`tripo_*`, `gemini_*`, `remove_background`）
- **不直接接觸 HTTP request/response**
- **設計理由**：未來要 CLI 化、或被別的服務呼叫，這層可以直接重用

### 第 4 層：抽象介面 (`services/interfaces.py`)
- 定義 `ImageProcessingInterface` 抽象類別
- **設計理由**：未來要做 unit test 時可以 mock，也方便換掉某個 AI 服務（例如 Gemini → Claude）

---

## API 端點設計

| Endpoint | 用途 | 對應業務碼前綴 |
|---|---|---|
| `/clothes/remove_bg` | 衣服去背 + 風格分析 | `1xxx` |
| `/fitting/generate` | 2D 虛擬試穿合成 | `2xxx` |
| `/fitting/modules` | 人像標準化（3D 預處理） | `3xxx` |
| `/fitting/tryon_3d_physics` | 3D 重建 | `4xxx` |

> **設計亮點**：業務碼前綴 = 模組編號，看到 `4422` 就知道是「3D 模組的姿勢失敗」。

---

## 為什麼選 Django 而不是 FastAPI？

| | Django | FastAPI |
|---|---|---|
| 適合場景 | 完整 web 服務 | 純 API |
| 內建工具 | ORM, Admin, Auth | 較少 |
| 我熟悉程度 | ✅ 高 | ⚠️ 中 |
| 推甄考量 | 我已會 Django，**時間花在 AI 整合**比較有意義 | 重學會花時間 |

**選擇**：Django。專案的價值在 AI pipeline，不在 web framework 本身。

---

## 為什麼用 Docker？

1. **隔離環境**：rembg 需要特定版本的 PyTorch + ONNX runtime，本機裝亂環境會崩
2. **一鍵啟動**：`bash ai.sh` 就好，隊友（前端）不用懂 Python 環境
3. **未來部署**：直接 push 到雲端（GCP / AWS ECS）就能跑

---

## 部署架構（目前）

```
本機 / 開發機
  └─ Docker Container (ai-container)
        ├─ port 8002 對外
        ├─ /app/ai_app (mount 程式碼，熱更新)
        ├─ /app/media (mount 產出物)
        └─ network: my_network (供前端容器連接)
```

未來上線會考慮：
- 反向代理（Nginx）
- 多 worker（gunicorn / uvicorn）
- 雲端物件儲存（取代 local media/）
- Tripo task 改非同步（Celery + Redis），避免 long polling 卡死

---

---

## 架構演進：Strategy Pattern 重構（2026-05-10 決策）

### 觸發點
專案前期只支援「網路上找來的乾淨產品圖」（白底、置中、低解析度）。準備上線時意識到使用者實際會用**手機現場拍**——EXIF 旋轉、12MP 巨大解析度、廣色域、複雜背景、強陰影、暗光雜訊——舊邏輯完全沒處理這些。

> ⚠️ 假設修正：原本以為「HEIC 格式」是首要問題，但使用者用 iPhone 14 Plus 實拍出來其實是 JPEG（可設定），所以真正的優先序是 **EXIF rotation > 解析度 > 色彩 profile > 格式正規化**。詳見 [06_bugs_and_fixes.md 故事 0](06_bugs_and_fixes.md)。

### 設計選擇

| 選項 | 優點 | 缺點 |
|---|---|---|
| A) 直接改 `remove_background()` | 簡單 | 改壞了線上掛掉 |
| B) 寫第二支 API endpoint | 隔離 | 前端要切換 path |
| C) **Strategy Pattern + 工廠** | 舊邏輯零改動、env 切換 | 多一層抽象 |

**選擇：C**

**理由**：
1. 舊邏輯**完全不動**（已驗證可用，不冒險）
2. 用 `.env` 變數 `REMOVE_BG_VERSION=legacy|robust` 切換實作
3. 未來 `/fitting/modules` 也要同樣升級，**這層抽象可以共用**（DRY 原則）
4. 失敗時可快速 rollback：改 env、重啟即可

### 設計細節

```python
# 抽象介面：定義「做什麼」
class ImagePreprocessor(ABC):
    @abstractmethod
    def preprocess(self, image: Image) -> ProcessResult:
        ...

# 兩個實作
class LegacyRemoveBg(ImagePreprocessor):
    """包裝現有 remove_background()，零改動"""

class RobustRemoveBg(ImagePreprocessor):
    """新版：HEIC + EXIF + resize + 品質檢查 + rembg(可換模型) + OpenCV rescue"""

# 工廠（讀 env 決定回哪個）
def get_preprocessor() -> ImagePreprocessor:
    version = os.getenv("REMOVE_BG_VERSION", "legacy")
    return RobustRemoveBg() if version == "robust" else LegacyRemoveBg()
```

### 共通基礎建設（為下一波 /modules 升級鋪路）

`services/utils/image_io.py` 收 HEIC / EXIF / resize 共通工具，**`/fitting/modules` 升級時直接 import 用**。

### 學到的設計觀念

1. **可演進性 > 簡潔性**：多一層抽象換到「敢動」的勇氣是值得的
2. **舊功能是資產**：能跑的舊邏輯**比新邏輯有價值**（已驗證 > 未驗證）
3. **env 變數是「便宜的 feature flag」**：不需要動到 LaunchDarkly 那種重量級工具

### 推甄面試 30 秒講法（Strategy Pattern 故事版）

> 「上線前我發現一個現實問題：之前測試都用網路上抓的乾淨圖，但使用者實際會用手機拍——HEIC 格式、EXIF 旋轉、複雜背景，舊邏輯完全沒處理。
>
> 我選擇用 **Strategy Pattern** 重構，而不是直接改舊 code。我定義一個 `ImagePreprocessor` 抽象介面，舊邏輯包成 `LegacyRemoveBg`、新邏輯寫成 `RobustRemoveBg`，用 `.env` 變數切換。
>
> 這樣做有三個好處：第一，舊邏輯零改動，已驗證的功能不冒風險；第二，新邏輯出 bug 改一個 env 就能 rollback；第三，下一個要升級的 `/fitting/modules` 端點可以**直接共用同一套抽象**，不重複造輪子。」

---

## 推甄面試 30 秒架構講法

> 「我把後端切成 4 層：URL 路由、View 入口、AI Services、抽象介面。
> 最關鍵的是第 3 層 AIProcessor，它集中管理三個異質 AI——rembg 做本地 CV、Gemini 做雲端 VLM、Tripo 做 3D 生成。
> 為什麼這樣切？因為這三個 AI 的呼叫方式完全不同：rembg 是同步函式呼叫、Gemini 是一次性 HTTP、Tripo 要長輪詢——但我用一致的回傳格式 `(result, status, code, error)` 把它們包起來，上層 View 就不用管底下是哪一個 AI 在做事。」
