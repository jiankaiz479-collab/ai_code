# Virtual Try-On AI Service

> Django 後端 AI 服務，負責「衣服去背 + 風格分析」「2D 虛擬試穿合成」「3D 人物重建」三大流程。
> 對接：前端（multipart 上傳照片） ↔ Django ↔ Gemini / RemBG / Tripo3D。

更新日期: 2026-05-10

---

## 目錄

1. [專案概覽](#1-專案概覽)
2. [快速啟動](#2-快速啟動)
3. [目錄結構](#3-目錄結構)
4. [環境變數](#4-環境變數)
5. [API 規格](#5-api-規格)
   - [5.1 去背 Remove Background](#51-去背-remove-background)
   - [5.2 2D 虛擬試穿 Virtual Try-On](#52-2d-虛擬試穿-virtual-try-on)
   - [5.3 3D 物理試穿重建 Reconstruct 3D](#53-3d-物理試穿重建-reconstruct-3d)
   - [5.4 人像標準化 Reconstruct Modules](#54-人像標準化-reconstruct-modules)
6. [錯誤回應格式 & Log 規範](#6-錯誤回應格式--log-規範)
7. [Tripo 上游錯誤映射表](#7-tripo-上游錯誤映射表)
8. [前端整合指南](#8-前端整合指南)
9. [開發者守則](#9-開發者守則)
10. [常見問題 FAQ](#10-常見問題-faq)

---

## 1. 專案概覽

### 解決什麼問題
讓使用者上傳一張人像照 + 一張或多張衣服照，自動完成：
- ✂️ 去背 → 取出乾淨衣物 PNG
- 👕 2D 試穿 → AI 合成「人穿衣服」的照片
- 🧍 3D 重建 → 把人轉成 .glb 3D 模型（未來給物理布料模擬使用）

### 技術棧
| 層 | 用什麼 |
|---|---|
| Web 框架 | Django 4.x |
| 去背 | [rembg](https://github.com/danielgatis/rembg)（U2-Net） |
| AI 合成 | Google Gemini（`gemini-3-pro-image-preview`） |
| 3D 重建 | [Tripo3D](https://www.tripo3d.ai/) `image_to_model` |
| 部署 | Docker（內部 port 8002） |

### 高階資料流
```
前端
  │  multipart/form-data
  ▼
Django (port 8002)
  │
  ├── /clothes/remove_bg        ──► rembg ──► Gemini 風格分析
  ├── /fitting/generate         ──► Gemini 試穿合成
  ├── /fitting/tryon_3d_physics ──► Tripo 上傳 → 任務 → 輪詢 → 下載 .glb
  └── /fitting/modules          ──► rembg 模特兒去背 → 1024×1024 方正標準化（3D 預處理）
```

---

## 2. 快速啟動

### 2.1 環境需求
- Docker（建議）；或 Python 3.10+
- Tripo API Key（[官方申請](https://www.tripo3d.ai/)）
- Google API Key（Gemini）

### 2.2 一鍵啟動（推薦）
```bash
bash ai.sh
```

`ai.sh` 會自動：
1. 檢查 Docker image 是否存在，沒有就 `docker build`
2. 讀取 `.env`
3. 停掉舊的 `ai-container`、起新容器
4. 掛載 `ai_app/`（程式碼熱更新）與 `media/`（產出物落地）
5. 對外 port 由 `.env` 的 `RUN_PORT` 決定（預設 8002）

**前置條件**：
- 已安裝 Docker
- 已建立 Docker network：`docker network create my_network`（首次執行才需要）
- 已準備好 `.env`（填好 `TRIPO_API_KEY` / `GOOGLE_API_KEY`）

### 2.3 手動 Docker（不用 ai.sh 時）
```bash
docker build -t ai-code-app .
docker run -d --name ai-container --network my_network \
  -p 8002:8002 --env-file .env \
  -v "$(pwd)/ai_app:/app/ai_app" \
  -v "$(pwd)/media:/app/media" \
  ai-code-app
```

### 2.4 直接本機跑（無 Docker，除錯用）
```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8002
```

### 2.5 驗證服務啟動
```bash
# 預期：HTTP 405（POST-only 端點對 GET 回 405，代表服務有起來）
curl -i http://localhost:8002/virtual_try_on/clothes/remove_bg
```

### 2.6 第一支可跑的測試（去背）
```bash
curl -X POST http://localhost:8002/virtual_try_on/clothes/remove_bg \
  -F "clothes_image=@tests/sample_shirt.jpg" \
  -o response.bin
# 開啟 response.bin 應看到 multipart 內含 JSON + PNG 二進位
```

---

## 3. 目錄結構

```
ai_code/
├── ai_app/
│   ├── views.py             # ★ 4 個 API endpoint 入口
│   ├── urls.py              # 路由
│   ├── services/
│   │   ├── processing.py    # ★ AI 處理核心（去背、Gemini、Tripo 全部在這）
│   │   └── interfaces.py    # 抽象介面
│   ├── models.py            # ORM model（目前少用）
│   └── migrations/
├── cv_testing_site/         # Django 專案設定
│   ├── settings.py
│   └── urls.py
├── media/                   # 落地檔案區
│   ├── tripo/               # ★ 生成的 3D GLB 存放點（含 mock GLB）
│   └── evidence_*/          # 實驗證據檔
├── .env                     # ★ 環境變數（不入 git）
├── Dockerfile
├── requirements.txt
├── manage.py
└── README.md                # 本檔
```

> ★ 標示：接手時最常修改的檔案。

---

## 4. 環境變數

完整 `.env` 範例與說明：

### 4.1 Django 設定
| 變數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `SECRET_KEY` | ✅ | - | Django 簽章金鑰，正式環境**必換** |
| `DEBUG` | ❌ | False | 正式請保持 False |
| `ALLOWED_HOSTS` | ❌ | localhost,127.0.0.1,* | 允許的 Host |
| `RUN_PORT` | ❌ | 8002 | 服務對外 port |
| `DATABASE_URL` | ❌ | sqlite:///db.sqlite3 | DB 連線 |
| `DJANGO_SETTINGS_MODULE` | ❌ | cv_testing_site.settings | 固定 |

### 4.2 AI / 第三方
| 變數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `U2NET_HOME` | ❌ | /app/.u2net | rembg 模型快取路徑 |
| `GOOGLE_API_KEY` | ✅ | - | Gemini API key |
| `GEMINI_MODEL_NAME` | ❌ | gemini-2.5-flash-image | 試穿合成模型 |
| `GEMINI_CONSULTANT_MODEL` | ❌ | gemini-2.5-flash | 顏色判斷/品管模型 |

### 4.3 Tripo 3D API
| 變數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `TRIPO_API_KEY` | ✅ | - | Tripo 金鑰（**會扣錢**） |
| `TRIPO_BASE_URL` | ❌ | https://api.tripo3d.ai/v2/openapi | API 根網址 |
| `TRIPO_UPLOAD_TIMEOUT` | ❌ | 60 | 上傳 timeout（秒） |
| `TRIPO_TASK_TIMEOUT` | ❌ | 60 | 建任務 timeout |
| `TRIPO_POLL_TIMEOUT` | ❌ | 30 | 單次輪詢 timeout |
| `TRIPO_DOWNLOAD_TIMEOUT` | ❌ | 120 | 下載 .glb timeout |
| `TRIPO_POLL_MAX_SECONDS` | ❌ | 600 | 輪詢總上限 |
| `TRIPO_POLL_INTERVAL` | ❌ | 5 | 輪詢間隔 |
| `TRIPO_ENABLE_REFINE` | ❌ | false | 是否啟用 200k 面 Refine 精修（**會額外扣 1 次積分**） |

### 4.4 Tripo 生成預設參數
| 變數 | 預設 | 說明 |
|---|---|---|
| `TRIPO_TEXTURE_QUALITY` | detailed | 紋理品質：standard / detailed |
| `TRIPO_FACE_LIMIT` | 100000 | 面數上限 |
| `TRIPO_PBR` | true | 啟用 PBR 材質 |
| `TRIPO_MODEL_VERSION` | v3.1-20260211 | Tripo 模型版本 |
| `TRIPO_TEXTURE_ALIGNMENT` | original_image | 紋理對齊：original_image / geometry |
| `TRIPO_GEOMETRY_QUALITY` | detailed | 幾何品質：standard / detailed |
| `TRIPO_REFINE_FACE_LIMIT` | 200000 | Refine 階段面數 |

### 4.5 ⚠️ Debug Mock（省積分開關）
| 變數 | 預設 | 說明 |
|---|---|---|
| `TRIPO_DEBUG_MOCK` | false | **true=不呼叫 Tripo API**，直接回傳 mock GLB（前端聯調用） |
| `TRIPO_MOCK_GLB_NAME` | model3d_2ce2ec84.glb | mock 用的 GLB 檔名（位於 `media/tripo/`） |

> ⚠️ **正式環境部署前務必確認 `TRIPO_DEBUG_MOCK=false`**，否則所有用戶會收到同一個假模型。

---

## 5. API 規格

### 共通約定
- **Base URL**：`http://<host>:<RUN_PORT>`
- **Request Content-Type**：一律 `multipart/form-data`
- **Response 成功**：`multipart/mixed` 或 `multipart/form-data`（JSON + 二進位）
- **Response 失敗**：一律 `application/json`
- **message 是字串**（例如 `"1200"`、`"4422"`），不是整數
- **code 是 HTTP 狀態碼**（200/400/422/500…）

---

### 5.1 去背 Remove Background

| 項目 | 值 |
|---|---|
| Method | `POST` |
| Path | `/virtual_try_on/clothes/remove_bg` |
| Request | `multipart/form-data` |
| Response | `multipart/form-data; boundary=bg_removal_boundary` |

#### 輸入欄位
| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `clothes_image` | file | ✅ | 衣服照片，建議 JPG/PNG/WEBP，主體清晰 |

#### 處理流程
1. 檢查 `clothes_image` 存在
2. 檢查 content_type 為 `image/*`
3. rembg 執行去背
4. Gemini 執行風格 / 顏色分析
5. 回傳 multipart（JSON + PNG）

#### 成功回應
```
--bg_removal_boundary
Content-Disposition: form-data; name="analysis"
Content-Type: application/json

{
  "code": 200,
  "message": "1200",
  "data": {
    "file_name": "cleaned_garment.png",
    "file_format": "PNG",
    "style_analysis": {
      "clothes_category": "clothing",
      "style_name": ["Casual"],
      "color_name": ["Red"]
    }
  }
}

--bg_removal_boundary
Content-Disposition: form-data; name="processed_image"; filename="processed.png"
Content-Type: image/png

<binary png data>
--bg_removal_boundary--
```

#### 錯誤碼
| message | HTTP | 觸發情境 | 前端建議文案 |
|---|---|---|---|
| 1200 | 200 | 成功 | - |
| 1400 | 400 | 缺少 clothes_image | 缺少衣服圖片，請重新上傳 |
| 1415 | 415 | 非圖片格式 | 僅支援 JPG/PNG/WEBP |
| 1422 | 422 | 圖片清晰度不足 | 圖片太模糊，請上傳清晰照片 |
| 1500 | 500 | 去背失敗 | 去背失敗，請稍後再試 |
| 1501 | 500 | 風格分析失敗 | 風格分析失敗，請稍後再試 |

#### curl 範例
```bash
curl -X POST http://localhost:8002/virtual_try_on/clothes/remove_bg \
  -F "clothes_image=@shirt.jpg" \
  -o response.bin
```

---

### 5.2 2D 虛擬試穿 Virtual Try-On

| 項目 | 值 |
|---|---|
| Method | `POST` |
| Path | `/virtual_try_on/fitting/generate` |
| Request | `multipart/form-data` |
| Response | `multipart/mixed; boundary=frame_boundary` |

#### 輸入欄位
| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `model_image` | file | ✅ | 人物模特照（一張） |
| `garment_images` | file[] | ✅ | 衣服照（可多件） |
| `data` | string (JSON) | ✅ | 衣服資訊；`garments` 陣列長度必須等於 `garment_images` 數量 |

`data` 結構：
```json
{
  "model_info": {
    "user_height": 170,
    "user_waistline": 80
  },
  "garments": [
    {
      "clothes_category": "clothing",
      "garment_info": {
        "clothes_arm_length": 60,
        "clothes_shoulder_width": 42
      }
    }
  ]
}
```

#### 處理流程
1. 驗證 `model_image` / `garment_images` / `data`
2. 驗證 `garment_images` 與 `data.garments` 數量一致
3. Gemini 執行衣服分析
4. Gemini 執行試穿合成
5. 回傳 multipart（JSON + PNG）

#### 成功回應
```
--frame_boundary
Content-Type: application/json

{
  "code": 200,
  "message": "2200",
  "data": {
    "file_name": "try_on_outfit_1234abcd.png",
    "file_format": "PNG",
    "items_processed": 2
  }
}

--frame_boundary
Content-Type: image/png
Content-Disposition: attachment; filename="try_on_outfit_1234abcd.png"

<binary png data>
--frame_boundary--
```

#### 錯誤碼
| message | HTTP | 觸發情境 | 前端建議文案 |
|---|---|---|---|
| 2200 | 200 | 成功 | - |
| 2400 | 400 | 參數錯誤 / JSON 格式錯 / 缺檔 / 數量不一致 | 參數不完整或格式錯誤，請檢查輸入 |
| 2422 | 422 | 未偵測到人體 | 未偵測到人物，請上傳清楚的人像照 |
| 2500 | 500 | AI 分析失敗 | 服飾分析服務暫時不可用 |
| 2501 | 500 | AI 合成或系統錯誤 | 試穿合成失敗，請稍後再試 |

#### curl 範例
```bash
curl -X POST http://localhost:8002/virtual_try_on/fitting/generate \
  -F "model_image=@model.jpg" \
  -F "garment_images=@shirt.jpg" \
  -F "garment_images=@pants.jpg" \
  -F 'data={"model_info":{"user_height":170,"user_waistline":80},"garments":[{"clothes_category":"clothing"},{"clothes_category":"clothing"}]}' \
  -o tryon.bin
```

---

### 5.3 3D 物理試穿重建 Reconstruct 3D

| 項目 | 值 |
|---|---|
| Method | `POST` |
| Path | `/virtual_try_on/fitting/tryon_3d_physics` |
| Request | `multipart/form-data` |
| Response | `multipart/mixed; boundary=frame_boundary`（JSON + .glb 二進位） |

#### 輸入欄位
| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `model_image` | file | ✅ | - | 人物照（全身正面為佳） |
| `data` | string (JSON) | ❌ | - | 同 5.2 結構，未來物理布料模擬 / 尺寸換算會用到 |
| `prompt` | string | ❌ | （內建寫實還原 prompt） | 自然語言指引 Tripo 生成 |
| `negative_prompt` | string | ❌ | （內建反向詞） | 反向引導詞，最多 255 字 |
| `texture_quality` | string | ❌ | `.env` 設定 | `standard` / `detailed` |
| `face_limit` | int | ❌ | `.env` 設定 | 限制輸出面數 |
| `pbr` | bool | ❌ | `.env` 設定 | `1/true/yes` 開啟 |
| `style` | string | ❌ | - | 風格名稱 |
| `refine` | bool | ❌ | `TRIPO_ENABLE_REFINE` | 是否啟用 Refine 精修 |
| `refine_face_limit` | int | ❌ | 200000 | Refine 階段面數 |

#### 處理流程

**正常模式（`TRIPO_DEBUG_MOCK=false`）：**
```
接收圖片 → 解析 data JSON
  → Step1: 上傳圖片到 Tripo（取得 file_token）
  → Step2: 建立 image_to_model 任務（取得 task_id）        💰 扣積分
  → Step3: 輪詢任務狀態（1~3 分鐘）
  → Step3.5: (可選) 建 Refine 任務 + 輪詢                  💰 再扣積分
  → Step4: 下載 .glb
  → Step5: 存到 media/tripo/model3d_<uuid>.glb
  → 回傳 multipart（JSON + .glb）
```

**Mock 模式（`TRIPO_DEBUG_MOCK=true`）：**
```
接收圖片 → 解析 data JSON
  → 跳過 Tripo API
  → 直接讀 media/tripo/<TRIPO_MOCK_GLB_NAME>
  → 回傳 multipart（JSON + .glb）
```
回傳格式與正常模式**完全一致**，前端不需要區分。

#### 成功回應
```
--frame_boundary
Content-Type: application/json

{
  "code": 200,
  "message": "4200",
  "data": {
    "file_name": "model3d_1a2b3c4d.glb",
    "file_format": "GLB"
  }
}

--frame_boundary
Content-Type: model/gltf-binary
Content-Disposition: attachment; filename="model3d_1a2b3c4d.glb"

<binary glb data>
--frame_boundary--
```

#### 錯誤碼
| message | HTTP | 觸發情境 | 前端建議文案 |
|---|---|---|---|
| 4200 | 200 | 成功 | - |
| 4400 | 400 | 缺少 model_image / data JSON 解析失敗 | 缺少必要參數，請重新上傳 |
| 4410 | 402 | Tripo 積分不足 | 積分不足，請前往儲值頁面 |
| 4415 | 415 | 上傳檔案空白或格式不支援 | 圖片格式不支援，請改用 JPG/PNG |
| 4422 | 422 | 姿勢過於極端 / 肢體不全 / 內容違規 / 模型過於複雜 | 請改上傳全身、四肢清楚的正面照 |
| 4429 | 429 | 3D 服務忙碌（上游速率限制） | 服務忙碌中，請稍後再試 |
| 4500 | 500 | 服務崩潰、輪詢逾時、未捕獲例外 | 系統忙碌中，請稍後再試 |

#### curl 範例
```bash
# 基本（用 .env 預設參數）
curl -X POST http://localhost:8002/virtual_try_on/fitting/tryon_3d_physics \
  -F "model_image=@person.jpg" \
  -o result.bin

# 帶參數
curl -X POST http://localhost:8002/virtual_try_on/fitting/tryon_3d_physics \
  -F "model_image=@person.jpg" \
  -F "prompt=realistic 3D character, A-pose" \
  -F "face_limit=50000" \
  -F "refine=false" \
  -o result.bin
```

---

### 5.4 人像標準化 Reconstruct Modules

把人像照「去背 + 置中 + 留白 + 壓成 1024×1024 方正圖」，作為 3D 重建前置預處理使用（讓 Tripo 輸入更穩定）。

| 項目 | 值 |
|---|---|
| Method | `POST` |
| Path | `/virtual_try_on/fitting/modules` |
| Request | `multipart/form-data` |
| Response | `multipart/mixed; boundary=frame_boundary` |

#### 輸入欄位
| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `model_image` | file | ✅ | 人物照（建議全身、肢體完整） |

#### 處理流程
1. 接收 `model_image`
2. rembg 去背
3. `compose_square_portrait()` 置中 + 5% 上下留白 + 壓成 1024×1024
4. 偵測是否肢體完整（不完整 → 4xx）
5. 存檔 `media/modules_<uuid>.png`
6. 回傳 multipart（JSON + PNG）

#### 成功回應
```
--frame_boundary
Content-Type: application/json

{
  "code": 200,
  "message": "3200",
  "data": {
    "file_name": "modules_1a2b3c4d.png",
    "file_format": "PNG"
  }
}

--frame_boundary
Content-Type: image/png
Content-Disposition: attachment; filename="modules_1a2b3c4d.png"

<binary png data>
--frame_boundary--
```

#### 錯誤碼
| message | HTTP | 觸發情境 | 前端建議文案 |
|---|---|---|---|
| 3200 | 200 | 成功 | - |
| 3400 | 400 | 缺少 model_image | 缺少模特圖片，請重新上傳 |
| 3422 | 422 | 偵測到肢體不完整 / 無法標準化 | 請改上傳全身、四肢清楚的正面照 |
| 3500 | 500 | 例外崩潰 | 系統忙碌中，請稍後再試 |

#### curl 範例
```bash
curl -X POST http://localhost:8002/virtual_try_on/fitting/modules \
  -F "model_image=@person.jpg" \
  -o normalized.bin
```

#### 與 5.3 的差異
- **5.4 modules**：只做 2D 預處理（PNG），不呼叫 Tripo，**不花錢**
- **5.3 tryon_3d_physics**：完整 3D 流程，呼叫 Tripo 產生 .glb，**會扣積分**
- 建議流程：先用 5.4 把人像標準化，再把結果丟給 5.3 生成 3D（提高成功率）

---

## 6. 錯誤回應格式 & Log 規範

### 6.1 錯誤回應 JSON 格式（任何 API 失敗時統一格式）
```json
{
  "code": 422,
  "message": "4422",
  "debug_info": {
    "error_detail": "[poll] Reconstruction rejected (code=2018): mesh too complex"
  }
}
```

| 欄位 | 說明 |
|---|---|
| `code` | HTTP 狀態碼（數字） |
| `message` | 業務錯誤碼字串（前端依此顯示文案） |
| `debug_info.error_detail` | 開發用詳細訊息，格式 `[stage] 描述 (code=上游碼): 訊息` |

`stage` 可為：`upload` / `create_task` / `refine` / `poll` / `download`。

### 6.2 Log 命名規則
每個 endpoint 有自己的標記（方便 grep）：

| 標記 | 對應 endpoint |
|---|---|
| `[G1]` | 去背 RemoveBgView |
| `[G3]` | 2D 試穿 TryCombineView |
| `[G4]` | 3D 重建 Reconstruct_3D |

### 6.3 Log 內容
僅輸出**狀態**，不輸出細節（檔名、size、token、耗時等已精簡）。

**3D 重建正常流程 log：**
```
--- [G4] 接收到 3D 重建請求 (Tripo) ---
📥 [G4] data JSON 解析成功
✅ [G4] 圖片載入成功
🚀 [G4] Step1: 上傳圖片至 Tripo...
✅ [G4] Step1 完成
🚀 [G4] Step2: 建立 image_to_model 任務...
✅ [G4] Step2 完成
⏳ [G4] Step3: 輪詢任務...
✅ [G4] Step3 完成
🚀 [G4] Step4: 下載 .glb...
✅ [G4] Step4 完成
✅ [G4] Step5 存檔完成
🎉 [G4] 3D 重建完成
```

**Mock 流程 log：**
```
--- [G4] 接收到 3D 重建請求 (Tripo) ---
✅ [G4] 圖片載入成功
🧪 [G4] Mock 模式啟用，跳過 Tripo API
✅ [G4] Mock GLB 載入成功
🎉 [G4] 3D 重建完成 (MOCK)
```

**錯誤 log（一律帶 message 碼，方便搜尋）：**
```
❌ [G4] 失敗 message=4422 http=422 detail=[poll] Reconstruction rejected (code=2018): ...
💥 [G4] 3D 流程崩潰 (message=4500): Server crash or heavy load: ...
```

---

## 7. Tripo 上游錯誤映射表

3D 重建內部會把 Tripo 原始錯誤映射為自家 4xxx 碼：

| Tripo HTTP | Tripo code | 含義 | → 本服務 message | → HTTP |
|---|---|---|---|---|
| 429 | 2000 | 超過速率限制 | 4429 | 429 |
| 400 | 2002 | 任務類型不支援（設定錯誤） | 4500 | 500 |
| 400 | 2003 | 輸入檔案為空 | 4415 | 415 |
| 400 | 2004 | 不支援的檔案格式 | 4415 | 415 |
| 400 | 2008 | 輸入違反內容政策 | 4422 | 422 |
| 403 | 2010 | 積分不足 | 4410 | 402 |
| 400 | 2015 | 模型版本已棄用 | 4500 | 500 |
| 400 | 2018 | 模型過於複雜無法重網格 | 4422 | 422 |
| - | - | 輪詢逾時 / 下載失敗 / 例外 | 4500 | 500 |

實作位置：[ai_app/services/processing.py](ai_app/services/processing.py) `AIProcessor._map_tripo_error()`

---

## 8. 前端整合指南

### 8.1 解析重點
1. **回傳是 multipart**，不是單一 JSON
2. 先解析 JSON part 拿 `code` / `message`，再處理二進位 part
3. `message` 請以**字串**處理（`"1200"`、`"4422"`）
4. HTTP 非 200 時，回應是純 JSON（不是 multipart），優先依 `message` 顯示文案
5. `debug_info.error_detail` 僅在開發模式顯示

### 8.2 最小實作流程
1. 組 `multipart/form-data` 發送請求
2. 從 `Content-Type` 取 boundary
3. 依 boundary 切 part
4. 讀 JSON part → `code` / `message` / `data`
5. 讀 image/glb part → 顯示或下載
6. 按 `message` 決定 UI 行為（重試 / 提示 / 跳儲值）

### 8.3 快速檢查清單
- [ ] Base URL 是否帶上 `RUN_PORT`
- [ ] Path 拼寫是否正確
- [ ] 欄位名稱是否完全一致（大小寫敏感）
- [ ] `garment_images` 與 `data.garments` 數量是否一致
- [ ] 是否正確處理 multipart 的兩個 part
- [ ] 失敗時是否切到 JSON 解析路徑

---

## 9. 開發者守則

### 9.1 省積分
**開發 / 聯調 / 跑 UI 測試** → `.env` 設定 `TRIPO_DEBUG_MOCK=true`
**真的要看新照片生成的 3D 樣子** → 改 `false`，記得跑完馬上改回 `true`

### 9.2 編號規則
- 業務碼前綴：`1xxx`=去背、`2xxx`=試穿、`4xxx`=3D 重建（`3xxx` 保留給未來模組）
- Log 標記：`[G1]`=去背、`[G3]`=試穿、`[G4]`=3D
- GLB 落地命名：`model3d_<8碼 hex>.glb` 放在 `media/tripo/`

### 9.3 不要做這些事
- ❌ Commit `.env`（已在 `.gitignore`）
- ❌ 在 `main` 分支留 `TRIPO_DEBUG_MOCK=true` 上 production
- ❌ 直接修改 `model3d_2ce2ec84.glb`（這是 mock 預設檔，會影響聯調）
- ❌ 把 `prompt` / `negative_prompt` 預設值搬到 .env（很長，留在程式裡比較好維護）

### 9.4 接 Tripo 新版本
要升級 `TRIPO_MODEL_VERSION` 時：
1. 先在開發環境改 `.env` 試跑
2. 確認新版接受的參數是否相容（看 Tripo 官方文件）
3. 若 P1 系列需注意：**不支援** `quad` / `smart_low_poly` / `generate_parts` / `geometry_quality`

---

## 10. 常見問題 FAQ

### Q1. 啟動後 4 個 endpoint 都回 500
→ 99% 是 `.env` 沒填或有錯。檢查 `TRIPO_API_KEY` / `GOOGLE_API_KEY`。

### Q2. 3D 重建一直回 4410
→ Tripo 積分用完。短期解：`TRIPO_DEBUG_MOCK=true` 切到 mock 模式。長期解：去 Tripo 後台儲值。

### Q3. 3D 重建一直回 4422
→ 上傳的照片**姿勢過於極端或肢體不全**。建議：全身正面照、A-pose、肢體完整可見。

### Q4. 3D 重建跑超久
→ 正常 1~3 分鐘。若超過 `TRIPO_POLL_MAX_SECONDS`（預設 600 秒）會 4500。可調大但通常代表 Tripo 在排隊。

### Q5. log 看不到輪詢進度
→ 進度 log 已精簡掉，只看 step 開始/完成。要看詳細進度請看 `processing.py` 的 `tripo_poll_task`。

### Q6. 前端拿到 multipart 解析不出來
→ 多半是用了 `response.json()` 直接解。要先拿 `Content-Type` 的 boundary，照 multipart 規範切 part。失敗回應才是純 JSON。

### Q7. mock GLB 想換成別的模特
→ 把 `.env` 的 `TRIPO_MOCK_GLB_NAME` 改成 `media/tripo/` 底下任一個 `.glb` 檔名。

### Q8. 為什麼 Refine 預設關閉？
→ Refine 會**額外扣一次積分**，做開發測試不必要。要看高品質成品再開。

---

## 附錄：相關連結
- [Tripo3D 官方文件](https://platform.tripo3d.ai/docs)
- [Gemini API](https://ai.google.dev/)
- [rembg](https://github.com/danielgatis/rembg)
