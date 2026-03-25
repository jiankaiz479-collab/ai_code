import os
import json
import logging
from PIL import Image, ImageEnhance
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

# 從自定義的服務層導入 AI 處理核心
from .services.processing import AIProcessor

# 初始化 Django 日誌記錄器
logger = logging.getLogger(__name__)

# ==========================================
# 1. 去背功能 (Remove Background)
# 此 View 負責處理單張衣服圖片的去背、磨皮及風格分析
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        # 從 POST 請求中獲取上傳的衣服圖片檔案
        clothes_image = request.FILES.get('clothes_image')
        
        # --- 基礎驗證：檢查檔案是否存在 ---
        if not clothes_image:
            logger.warning("⚠️ [RemoveBg] 未上傳圖片")
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 未上傳圖片",
                "tools_status": {
                    "rembg_engine": "not_started",
                    "opencv_masking": "not_started",
                    "gemini_consultant": "not_started"
                },
                "debug_info": {
                    "error_type": "MissingFileError",
                    "suggest": "Please upload an image file."
                }
            }, status=400)

        # --- 基礎驗證：檢查檔案格式是否為圖片 ---
        if not clothes_image.content_type.startswith('image/'):
            logger.warning(f"⚠️ [RemoveBg] 不支援格式: {clothes_image.content_type}")
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 僅支援圖片格式",
                "tools_status": {
                    "rembg_engine": "not_started",
                    "opencv_masking": "not_started",
                    "gemini_consultant": "not_started"
                },
                "debug_info": {
                    "error_type": "InvalidFormatError",
                    "suggest": "Only JPG/PNG/WEBP files are accepted."
                }
            }, status=415)

        try:
            # 初始化 AI 處理引擎
            processor = AIProcessor()
            logger.info(f"🔄 [RemoveBg] 啟動流水線: {clothes_image.name}")
            
            # 初始化各工具的執行狀態追蹤
            tools_status = {
                "rembg_engine": "running",
                "opencv_masking": "not_started",
                "gemini_consultant": "not_started"
            }

            # 1. 將上傳的檔案讀取並轉換為 PIL 影像物件，統一轉為 RGBA 模式處理透明度
            if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
            input_pil = Image.open(clothes_image).convert("RGBA")

            # 2. 執行 Rembg 去背處理 (呼叫外部 AI 模型)
            output_img, success, error = processor.remove_background(input_pil)
            if not success:
                tools_status["rembg_engine"] = "fail"
                return JsonResponse({"code": 422, "message": "去背失敗", "tools_status": tools_status}, status=422)
            tools_status["rembg_engine"] = "success"

            # 3. 執行清晰度檢測 (預防使用者上傳太模糊的圖片導致後續分析失真)
            is_clear, score, _ = processor.check_image_blur(output_img, threshold=50.0)
            if not is_clear:
                return JsonResponse({"code": 422, "message": f"圖片過於模糊 (Score: {round(score,1)})", "tools_status": tools_status}, status=422)

            # 4. 執行 OpenCV 磨皮處理 (減少布料反光與褶皺雜訊)
            tools_status["opencv_masking"] = "running"
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            smoothed_rgb, success, error = processor.smooth_fabric_with_opencv(rgb_img)
            if not success:
                tools_status["opencv_masking"] = "fail"
                return JsonResponse({"code": 422, "message": "磨皮處理失敗", "tools_status": tools_status}, status=422)
            tools_status["opencv_masking"] = "success"

            # 5. 合併回透明通道，並稍微增強對比度讓圖片看起來更清晰
            final_output = Image.merge('RGBA', (*smoothed_rgb.split(), a))
            final_output = ImageEnhance.Contrast(final_output).enhance(0.85)
            
            # 6. 生成唯一檔名並保存到伺服器媒體資料夾
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
            final_output.save(file_path, "PNG")

            # 7. 呼叫 Gemini 進行服裝風格與類別分析
            tools_status["gemini_consultant"] = "running"
            style_analysis, success, error = processor.analyze_clothing_style(file_path)
            tools_status["gemini_consultant"] = "success" if success else "fail"

            # ========== 構建 Multipart/form-data 響應 ==========
            # 第一部分：包含處理狀態與 Gemini 分析結果的 JSON 數據
            analysis_data = {
                "code": 200,
                "message": "Processing Success",
                "tools_status": tools_status,
                "data": {
                    "file_name": file_name,
                    "file_format": "PNG",
                    "style_analysis": style_analysis
                }
            }
            if not success:
                analysis_data["error_details"] = {"error_message": error}

            boundary = 'bg_removal_boundary'
            response_body = []
            
            # 寫入 JSON Part
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            # 寫入去背後的二進位圖片 Part
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            
            with open(file_path, 'rb') as f:
                response_body.append(f.read())
            
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            logger.info(f"✅ [RemoveBg] 處理完成並回傳")
            return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')

        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error",
                "tools_status": {"rembg_engine": "error", "opencv_masking": "error", "gemini_consultant": "error"},
                "debug_info": {"error_type": "RuntimeError", "detail": str(e)}
            }, status=500)

# ==========================================
# 2. 虛擬試穿 (Virtual Try-On)
# 此 View 負責接收模特兒與衣服，執行合成任務
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # ========== 1. 接收文件與額外 JSON 資料 ==========
        # 這裡直接拿到的就是 API 傳入的原始文件
        model_image = request.FILES.get('model_image')
        garment_image = request.FILES.get('clothes_image')

        try:
            data_str = request.POST.get('data')
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            data = {}

        # ========== 2. 基礎驗證 ==========
        if not model_image or not garment_image:
            logger.warning("⚠️ [TryOn] 缺少圖片")
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 缺少必要圖片",
                "tools_status": {
                    "rembg_people": "skipped", 
                    "densepose_analyzer": "skipped",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started"
                }
            }, status=400)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動流水線控制 (直接處理 Stream)...")
            
            tools_status = {
                "rembg_people": "skipped",
                "densepose_analyzer": "skipped",
                "opencv_smoothing": "success", 
                "gemini_consultant": "not_started", 
                "gemini_model": "not_started"
            }

            # 確保檔案指標在開頭
            if hasattr(model_image, 'seek'): model_image.seek(0)
            if hasattr(garment_image, 'seek'): garment_image.seek(0)

            # --- 步驟 3 & 4: 視覺特徵與 Gemini 分析 ---
            # 直接用 Image.open 讀取 UploadedFile 物件
            pil_cloth = Image.open(garment_image).convert("RGBA")
            hex_color = processor._get_dominant_color(pil_cloth)
            texture_swatch = processor._create_texture_swatch(pil_cloth)
            
            tools_status["gemini_consultant"] = "running"
            analysis_res = processor.analyze_garment(pil_cloth)
            garment_description = analysis_res.get('description', '')
            tools_status["gemini_consultant"] = "success"

            # --- 步驟 5: 執行最後的 VFX 合成 ---
            tools_status["gemini_model"] = "running"
            
            # 直接傳入原始的 model_image 與 garment_image 物件
            result_tryon = processor.virtual_try_on(
                model_image=model_image,        # 直接傳入文件物件
                garment_image=garment_image,    # 直接傳入文件物件
                hex_color=hex_color,
                texture_swatch=texture_swatch,
                garment_description=garment_description,
                model_info=data.get('model_info', {}),
                garment_info=data.get('garment_info', {})
            )
            
            if not result_tryon.get('success'):
                return JsonResponse(result_tryon, status=result_tryon.get('code', 422))
            
            tools_status["gemini_model"] = "success"
            tryon_filename = result_tryon.get('tryon_result_filename')
            tryon_path = os.path.join(settings.MEDIA_ROOT, tryon_filename)

            # ========== 3. 構建響應 ==========
            analysis_data = {
                "code": 200,
                "message": "Success",
                "tools_status": tools_status,
                "data": {
                    "file_name": tryon_filename,
                    "densepose_file": None,
                    "file_format": "PNG"
                }
            }

            boundary = 'frame_boundary'
            response_body = []
            
            # Part 1: JSON
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            # Part 2: Image
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n')
            response_body.append(f'Content-Disposition: attachment; filename="{tryon_filename}"\r\n\r\n'.encode('utf-8'))
            with open(tryon_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(b'\r\n')
            
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            logger.info(f"✅ [TryOn] 合成完成 (Stream 模式)。")
            return HttpResponse(
                b''.join(response_body), 
                content_type=f'multipart/mixed; boundary={boundary}'
            )

        except Exception as e:
            logger.error(f"❌ [TryOn] 系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({"code": 500, "message": "Internal Server Error"}, status=500)