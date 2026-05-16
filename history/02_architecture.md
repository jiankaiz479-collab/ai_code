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

## 架構演進：Fat Views → Service Layer 重構（2026-05-11）

### 觸發點
原本 view 同時負責 HTTP I/O **和**業務邏輯——例如 `TryCombineView.post()` 一個方法 200+ 行，混雜 multipart 解析、衣物分析、Gemini 合成、存檔、風格分析。

要做「2D 試穿 + 3D 重建 一條龍」新功能時，**唯一選擇是複製貼上**——這是工程上的 code smell。學長提醒「**該用 Service 層**」。

### 設計選擇

| 選項 | 優點 | 缺點 |
|---|---|---|
| A) 直接複製 2D + 3D 到新 view | 最快 | code 重複、改 bug 要改多處 |
| B) 把共用邏輯抽 helper 函式 | 中等 | 函式介面難設計、仍綁 view |
| **C) Service Layer** | 業務邏輯獨立、可重用 | 多一層架構 |

**選擇：C**

**理由**：
1. **業務邏輯不應綁 HTTP**：service 純 Python 物件進出，可被 view / Celery / CLI / 測試共用
2. **單一職責原則**：view 只做 HTTP，service 只做業務，AIProcessor 只做底層工具
3. **未來擴展容易**：要做更多組合 endpoint 時，只是「呼叫更多 service」

### 重構結果

新增 4 個 service 檔案：

| Service | 行數 | 職責 |
|---|---|---|
| `RemoveBgService` | 101 | 去背 + 風格分析（並行） |
| `TryOnService` | 141 | 2D 試穿（衣物分析 → 合成 → 風格分析） |
| `Reconstruct3DService` | 188 | 3D 重建（Tripo 全流程 + Mock） |
| `TryOn3DService` | 96 | **2D + 3D 一條龍**（純組合，零複製代碼） |

`views.py` 從 **705 行 → 393 行**（縮減 44%）。每個 view 變得只有 4 件事：

```python
def post(self, request):
    # ① 解析 multipart
    # ② 驗證
    # ③ result = SomeService().method(...)
    # ④ 成功 → multipart 回應；失敗 → JSON 錯誤
```

### Service Layer 的回報（同日完成「一條龍」endpoint）

`TryOn3DService.execute()` 核心邏輯**只有 6 行**：

```python
tryon = self.try_on.synthesize(model_image, garment_images, data)
if not tryon.ok:
    return TryOn3DResult(ok=False, code=tryon.code, ...)
recon = self.recon.reconstruct(tryon.image, options)
if not recon.ok:
    return TryOn3DResult(ok=False, code=recon.code, ...)
return TryOn3DResult(ok=True, glb_bytes=recon.glb_bytes, ...)
```

**零複製代碼**——純組合既有兩個 service。如果沒先做 Service Layer 重構，這個 endpoint 至少要寫 200+ 行（複製 2D 的全部 + 複製 3D 的全部）。

### 學到的設計觀念

1. **「Composition over duplication」**：新功能 = 既有 service 的組合，不要複製貼上
2. **業務邏輯不該依賴 framework**：service 不認識 `request` / `response`，所以 Django 換成 FastAPI 也不用重寫
3. **重構的「回報」要等到下一次擴展才看得到**：當下重構 view 看起來只是搬位置；下一次寫新 endpoint 時才感受到「省了多少時間」
4. **學長提醒的價值**：很多工程模式（Service Layer / Strategy / Composition）資深工程師會自然提出，趁早問

### 推甄面試 1 分鐘 Service Layer 講法

> 「我接手專案時 view 跟業務邏輯混在一起，一個 endpoint 200+ 行。學長提醒我用 Service Layer 模式——view 只做 HTTP I/O、service 做業務邏輯、底層工具做 AI API 呼叫。
>
> 我重構了 4 個 service：去背、2D 試穿、3D 重建，還有一個『2D + 3D 一條龍』。**重構後 views.py 從 705 行縮到 393 行。**
>
> 真正的回報在重構完當天就出現——產品需求要做『先 2D 試穿再做 3D』的新 endpoint，**我的 service 核心邏輯只有 6 行**，因為兩個既有 service 已經包好流程，新 view 只是組合它們。如果沒重構，這個新 endpoint 至少要寫 200+ 行複製貼上的 code。
>
> 這讓我體會到：**重構的價值不在當下，在下一次擴展時。**」

---

## 架構演進：LLM Router 雙軌路由（2026-05-13 決策，Roadmap v3）

### 觸發點
系統上線後發現使用者上傳的照片分兩大類：「平鋪/掛在衣架上」與「真人穿著照片」。
現有 `rembg` 在前者表現完美，但在後者會把整個人當前景切下。若換成服裝分割模型，在平鋪照上又會因找不到人體而失效。

### 設計選擇

| 選項 | 優點 | 缺點 |
|---|---|---|
| A) 前端加按鈕讓使用者選 | 最簡單 | 破壞 UX，使用者容易選錯 |
| B) 把圖同時丟給兩種模型 | 總會有一個成功 | 運算成本翻倍，且難以判斷要相信哪一個結果 |
| **C) LLM 路由 (Router) + 雙軌並行** | 零 UX 負擔、零運算浪費 | 需要擴充 v2 預檢層邏輯 |

**選擇：C**

### 設計細節
結合之前實作的 Strategy Pattern 與 Gemini 預檢層：
1. **擴充 Prompt**：在 v2 擋爛圖的 Prompt 中，要求 Gemini 多輸出一欄 `"presentation_mode": "worn_on_body" | "flat_lay" | "on_hanger"`。
2. **後端 Router**：
   ```python
   presentation = validation_result["presentation_mode"]
   if presentation in ["flat_lay", "on_hanger"]:
       # 情境 A：走舊路徑 (rembg)
       strategy = RobustRemoveBg()
   elif presentation == "worn_on_body":
       # 情境 B：走新路徑 (Human Parsing)，自動切分上下身
       strategy = HumanParsingRemoveBg()
   ```

### 附帶 UX 升級：全自動部位拆解
這套架構讓前端 API 達到「**無參數化**」。使用者傳全身照進來，`HumanParsingRemoveBg` 跑一次推論後，自動包裝出 `{"upper": 上衣圖, "lower": 褲子圖}` 回傳。

### 學到的設計觀念
1. **LLM 作為路由器 (Router)**：大語言模型不只是拿來聊天或分析，它也是極佳的「分類器」，能用極低成本導流傳統程式的走向。
2. **One Inference, Multiple Outputs**：如果模型一次能生出全身上下的部位標籤，就應該讓後端一次把它們全拆分好，而不是讓前端呼叫兩次 API 分別要求「我要上衣」跟「我要褲子」。

### 推甄面試 30 秒講法（LLM Router 版）
> 「我遇到一個難題：『平鋪圖』和『真人穿搭照』需要的去背模型完全不同。如果讓前端加按鈕讓使用者選，體驗很差；如果只用一個模型，必定有一種情境會失敗。
> 
> 我的解法是導入 **LLM Router**。因為我原本就有用 Gemini 做防呆預檢，我只改了一行 Prompt，讓 Gemini 順便幫我判斷這張圖是平鋪還是真人穿著。後端拿到 JSON 後，動態把平鋪圖導給 `rembg`、真人圖導給 `Human Parsing 模型`。這達成了『零額外成本、零前端改動』，完美解決了多情境的問題。」

---

## 推甄面試 30 秒架構講法

> 「我把後端切成 4 層：URL 路由、View 入口、AI Services、抽象介面。
> 最關鍵的是第 3 層 AIProcessor，它集中管理三個異質 AI——rembg 做本地 CV、Gemini 做雲端 VLM、Tripo 做 3D 生成。
> 為什麼這樣切？因為這三個 AI 的呼叫方式完全不同：rembg 是同步函式呼叫、Gemini 是一次性 HTTP、Tripo 要長輪詢——但我用一致的回傳格式 `(result, status, code, error)` 把它們包起來，上層 View 就不用管底下是哪一個 AI 在做事。」
