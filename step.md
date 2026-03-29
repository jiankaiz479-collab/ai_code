# AI Code Frontend Integration Guide 

更新日期: 2026-03-30

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

