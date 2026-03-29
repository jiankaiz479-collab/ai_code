# AI Code Steps

## #sym:RemoveBgView

位置: ai_app/views.py

流程:
1. 讀取上傳檔案 clothes_image
2. 驗證檔案存在與格式 (image/*)
3. 轉成 PIL RGBA
4. 呼叫 AIProcessor.remove_background 去背
5. 呼叫 AIProcessor.check_image_blur 做清晰度檢查
6. 呼叫 AIProcessor.smooth_fabric_with_opencv 磨皮
7. 合併 alpha 並做對比度增強
8. 儲存輸出檔案 (processed_*.png)
9. 呼叫 AIProcessor.analyze_clothing_style 進行風格分析
10. 回傳 multipart/form-data:
   - Part 1: analysis JSON
   - Part 2: processed_image PNG

主要 tools_status:
- rembg_engine
- opencv_masking
- gemini_consultant

常見錯誤碼:
- 400: 缺少圖片
- 415: 非圖片格式
- 422: 去背失敗 / 圖片模糊 / 磨皮失敗
- 500: 系統錯誤

---

## #sym:TryCombineView

位置: ai_app/views.py

流程:
1. 讀取 model_image 與 clothes_image
2. 解析 POST data(JSON 字串，可選)
3. 驗證必要檔案是否齊全
4. 讀取衣服圖為 PIL RGBA
5. 呼叫 analyze_garment 產生 garment_description
6. 呼叫 virtual_try_on 執行合成
7. 取得 tryon_result_filename
8. 回傳 multipart/mixed:
   - Part 1: analysis JSON
   - Part 2: try-on PNG

主要 tools_status:
- rembg_people
- densepose_analyzer
- gemini_consultant
- gemini_model

常見錯誤碼:
- 400: 缺少必要圖片
- 422: 合成失敗
- 500: 系統錯誤
