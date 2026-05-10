# AI Code Frontend Integration Guide 

更新日期: 2026-05-10

## 1. Container 與 Port

- 服務運行於 Docker container
- Container 內部 Port: 8002
- 對外 Port: 由 .env 的 RUN_PORT 決定
- 前端 Base URL:
  - http://localhost:{RUN_PORT}

---

## 2. API 總覽

### 2.1 Remove Background
- Method: POST
- Path: /virtual_try_on/clothes/remove_bg
- Request Content-Type: multipart/form-data
- 必填欄位:
  - clothes_image (file)
- Response Content-Type:
  - multipart/form-data; boundary=bg_removal_boundary

流程步驟:
1. 檢查 clothes_image 是否存在
2. 檢查 content_type 是否為 image/*
3. 執行去背
4. 執行風格分析
5. 回傳 multipart（JSON + PNG）

HTTP 狀態碼:
- 200: 成功
- 400: 缺少必要圖片
- 415: 非圖片格式
- 422: 圖片清晰度不足（若啟用清晰度檢查）
- 500: 去背或分析失敗

業務錯誤碼 (message):
- 1200: 成功
- 1400: 缺少輸入圖片
- 1415: 檔案格式錯誤
- 1422: 圖片清晰度不足
- 1500: 去背或系統錯誤
- 1501: 風格分析失敗

成功回應範例（multipart/form-data）:
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

<binary image data>
--bg_removal_boundary--

---

### 2.2 Virtual Try-On
- Method: POST
- Path: /virtual_try_on/fitting/generate
- Request Content-Type: multipart/form-data
- 必填欄位:
  - model_image (file)
  - garment_images (file array)
  - data (json string，需含 garments 陣列資訊)
- Response Content-Type:
  - multipart/mixed; boundary=frame_boundary

流程步驟:
1. 驗證 model_image、garment_images、data
2. 驗證 garment_images 與 data.garments 數量一致
3. 執行 garment 分析
4. 執行 try-on 合成
5. 回傳 multipart（JSON + PNG）

HTTP 狀態碼:
- 200: 成功
- 400: 參數錯誤 / JSON 格式錯誤 / 缺檔 / 數量不一致
- 422: 未偵測到人體
- 500: 分析或合成失敗

業務錯誤碼 (message):
- 2200: 成功
- 2400: 參數錯誤
- 2422: 偵測不到人體
- 2500: AI 分析失敗
- 2501: AI 合成或系統錯誤

成功回應範例（multipart/mixed）:
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

<binary image data>
--frame_boundary--

---

### 2.3 3D 物理試穿重建 (Tripo image-to-3D)
- Method: POST
- Path: /virtual_try_on/fitting/tryon_3d_physics
- Request Content-Type: multipart/form-data
- 必填欄位:
  - model_image (file)
- 選填欄位:
  - data (json string，含 model_info / garments，未來物理布料模擬 / 尺寸換算會用到)
  - prompt (string，自然語言指引 Tripo 生成)
  - negative_prompt (string，反向引導詞，最多 255 字)
  - texture_quality (string，"standard" | "detailed"，預設 standard)
  - face_limit (int，限制輸出面數)
  - pbr (bool，"1/true/yes" 開啟，預設 true)
  - style (string，風格名稱)
  - refine (bool，是否啟用 200k 面精修，預設依 .env TRIPO_ENABLE_REFINE)
  - refine_face_limit (int，refine 階段的面數)
- 除錯開關 (.env)：
  - TRIPO_DEBUG_MOCK=true → 不呼叫 Tripo API，直接回傳 media/tripo/<TRIPO_MOCK_GLB_NAME>（預設 model3d_2ce2ec84.glb），用於前端聯調省積分
  - 回應格式與真實流程完全一致（前端不需要區分）
- Response Content-Type:
  - multipart/mixed; boundary=frame_boundary（JSON + .glb 二進位）

流程步驟:
1. 驗證 model_image / data JSON
2. 上傳圖片至 Tripo 取得 file_token
3. 建立 image_to_model 任務（帶入 prompt 等進階參數）
4. 輪詢任務狀態直到完成
5. （選）建立 Refine 任務並輪詢取得精修模型
6. 下載 .glb 並落地到 media/tripo/
7. 回傳 multipart（JSON + GLB 二進位）

HTTP 狀態碼:
- 200: 成功
- 400: 缺少 model_image / data JSON 解析失敗
- 402: Tripo 積分不足
- 415: 上傳檔案空白或格式不支援
- 422: 重建失敗（姿勢過於極端 / 肢體不全 / 模型過於複雜 / 內容違規）
- 429: 3D 服務忙碌中（上游速率限制）
- 500: 3D 解析服務負載過重或崩潰 / Tripo 流程崩潰

業務錯誤碼 (message):
- 4200: 成功
- 4400: 缺少輸入參數 / 參數格式錯誤
- 4410: 積分不足，請儲值
- 4415: 檔案格式不支援或檔案空白
- 4422: 重建失敗（姿勢過於極端、肢體不全、模型過於複雜、內容違規）
- 4429: 服務忙碌或超出速率限制，請稍後再試
- 4500: 3D 解析服務負載過重或崩潰

成功回應範例（multipart/mixed）:
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

Tripo 上游錯誤對應重點:
- 上游 429 / code=2000：超過生成速率限制 → 後端回傳 message=4429（HTTP 429）
- 上游 400 / code=2002：不支援的任務類型 → 後端回傳 message=4500
- 上游 400 / code=2003：輸入檔案為空或被防火牆拒絕 → 後端回傳 message=4415（HTTP 415）
- 上游 400 / code=2004：不支援的檔案格式 → 後端回傳 message=4415（HTTP 415）
- 上游 400 / code=2008：輸入違反內容政策 → 後端回傳 message=4422（HTTP 422）
- 上游 403 / code=2010：Tripo 積分不足 → 後端回傳 message=4410（HTTP 402，前端提示儲值）
- 上游 400 / code=2015：模型版本已棄用 → 後端回傳 message=4500
- 上游 400 / code=2018：模型過於複雜無法重網格 → 後端回傳 message=4422
- 任何輪詢逾時 / 下載失敗 / 未捕獲例外：後端回傳 message=4500

錯誤回應格式（任何失敗情境一律回 application/json）:
{
  "code": 422,                      // HTTP 狀態碼
  "message": "4422",                // 業務錯誤碼（字串）
  "debug_info": {
    "error_detail": "[poll] Reconstruction rejected (code=2018): ..."
  }
}

備註：
- 後端 log 會同步輸出 `❌ [G4] 失敗 message=4xxx http=xxx detail=...`，可在伺服器 log 直接搜尋 message 碼定位問題
- error_detail 格式為 `[stage] 描述 (code=Tripo原始碼): 訊息`，stage 可為 upload / create_task / refine / poll / download

---

## 3. 前端欄位規格

### 3.1 Remove Background Request
- 欄位: clothes_image
- 型別: file
- 備註: 建議 jpg/png/webp，圖像需清晰且主體完整

### 3.2 Try-On Request
- 欄位: model_image
- 型別: file

- 欄位: garment_images
- 型別: file array
- 備註: 可多件；順序應與 data.garments 對齊

- 欄位: data
- 型別: string（JSON）
- 備註: Try-On 與 3D 重建共用此結構
- 建議結構:
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

---

## 4. 前端解析重點

1. 回傳是 multipart，不是單一 JSON
2. 先解析 JSON part 拿 code/message，再處理 image part
3. message 請以字串處理（例如 "1200"、"2422"）
4. HTTP 非 200 時，優先顯示 message 對應文案
5. 若有 debug_info.suggest，可在開發模式顯示

---

## 5. 前端錯誤提示對照表

### 5.1 Remove Background
- 1400: 缺少衣服圖片，請重新上傳
- 1415: 僅支援 JPG/PNG/WEBP，請更換格式
- 1422: 圖片太模糊，請上傳清晰照片
- 1500: 去背失敗，請稍後再試
- 1501: 風格分析失敗，請稍後再試
- HTTP 500 且無 message: 系統忙碌中，請稍後再試

### 5.2 Virtual Try-On
- 2400: 參數不完整或格式錯誤，請檢查輸入
- 2422: 未偵測到人物，請上傳清楚的人像照
- 2500: 服飾分析服務暫時不可用，請稍後再試
- 2501: 試穿合成失敗，請稍後再試
- HTTP 500 且無 message: 系統忙碌中，請稍後再試

### 5.3 3D 物理試穿重建
- 4400: 缺少必要參數，請重新上傳 model_image 或檢查 data JSON
- 4410: 積分不足，請前往儲值頁面後再試
- 4415: 圖片格式不支援或檔案損毀，請改用 JPG/PNG
- 4422: 姿勢過於極端或肢體不全，請改上傳全身、四肢清楚的正面照
- 4429: 服務忙碌中（已達速率限制），請稍後再試
- 4500: 3D 服務暫時不可用，請稍後再試
- HTTP 500 且無 message: 系統忙碌中，請稍後再試

備註：對應 Tripo 上游錯誤的處理請參考 §2.3 的 Tripo 上游錯誤對應重點。

---

## 6. 前端實作建議（最小流程）

1. 組 multipart/form-data 發送請求
2. 解析回傳 boundary
3. 讀取 JSON part，拿 code / message / data
4. 讀取 image part，顯示結果圖
5. 按 message 做錯誤提示與重試行為

---

## 7. 快速檢查清單

- Base URL 是否使用 RUN_PORT
- Path 是否正確
- 欄位名稱是否完全一致
- garment_images 與 data.garments 數量是否一致
- 是否有正確解析 multipart 的 JSON 與圖片兩個 part

