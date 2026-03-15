import os
import json
import logging
from PIL import Image
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

# 請根據你的目錄結構確認 import 路徑
from .services.processing import AIProcessor

logger = logging.getLogger(__name__)

# ==========================================
# 1. 去背功能 (Remove Background)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        clothes_image = request.FILES.get('clothes_image')
        
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
            processor = AIProcessor()
            logger.info(f"🔄 [RemoveBg] 啟動流水線: {clothes_image.name}")
            
            result = processor.remove_background(clothes_image)
            
            if result.get('success'):
                file_name = result.get('file_name')
                file_path = os.path.join(settings.MEDIA_ROOT, file_name)
                
                if not os.path.exists(file_path):
                    logger.error(f"❌ [RemoveBg] 文件生成失敗: {file_path}")
                    tools_status = result.get('tools_status', {})
                    tools_status['file_save'] = 'fail'
                    return JsonResponse({
                        "code": 500,
                        "message": "Internal Server Error: 文件生成失敗",
                        "tools_status": tools_status,
                        "debug_info": {"error_type": "FileNotFoundError", "detail": f"File not found: {file_name}"}
                    }, status=500)
                
                analysis_data = {
                    "code": 200,
                    "message": result.get('message', "Processing Success"),
                    "tools_status": result.get('tools_status', {}),
                    "data": {
                        "file_name": file_name,
                        "file_format": "PNG",
                        "style_analysis": result.get('style_analysis', {})
                    }
                }
                
                if result.get('error_details'):
                    analysis_data['error_details'] = result['error_details']
                                
                boundary = 'bg_removal_boundary'
                response_body = []
                
                response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
                response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
                response_body.append(b'Content-Type: application/json\r\n\r\n')
                response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
                response_body.append(b'\r\n')
                
                response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
                response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\n'.encode('utf-8'))
                response_body.append(b'Content-Type: image/png\r\n\r\n')
                
                with open(file_path, 'rb') as f:
                    response_body.append(f.read())
                
                response_body.append(b'\r\n')
                response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
                
                logger.info(f"✅ [RemoveBg] 處理完成並回傳")
                return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')
                
            else:
                error_code = result.get('code', 422)
                logger.warning(f"⚠️ [RemoveBg] 處理失敗 (code={error_code})")
                return JsonResponse({
                    "code": error_code,
                    "message": result.get('message', "Image processing failed"),
                    "tools_status": result.get('tools_status', {}),
                    "debug_info": result.get('debug_info', {})
                }, status=error_code)

        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型運算失敗",
                "tools_status": {"rembg_engine": "error", "opencv_masking": "error", "gemini_consultant": "error"},
                "debug_info": {"error_type": "RuntimeError", "detail": str(e)}
            }, status=500)

# ==========================================
# 2. 虛擬試穿 (Virtual Try-On)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        model_image = request.FILES.get('model_image')
        garment_image = request.FILES.get('clothes_image')

        try:
            data_str = request.POST.get('data')
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            data = {}

        if not model_image or not garment_image:
            logger.warning("⚠️ [TryOn] 缺少圖片")
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 缺少必要圖片",
                "tools_status": {
                    "rembg": "not_started",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started"
                }
            }, status=400)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動流水線控制...")
            
            tools_status = {
                "rembg": "running",
                "opencv_smoothing": "success", 
                "gemini_consultant": "not_started", 
                "gemini_model": "not_started",
                "densepose": "skipped"
            }

            # --- 步驟 1: 去背 ---
            result_bg = processor.remove_background(garment_image)
            if not result_bg.get('success'):
                return JsonResponse(result_bg, status=result_bg.get('code', 422))
            
            tools_status["rembg"] = "success"
            clean_path = os.path.join(settings.MEDIA_ROOT, result_bg.get('file_name'))
            
            pil_cloth = Image.open(clean_path).convert("RGBA")

            # --- 步驟 2: 視覺提取 ---
            hex_color = processor._get_dominant_color(pil_cloth)
            texture_swatch = processor._create_texture_swatch(pil_cloth)
            
            # --- 步驟 3: 技術分析 ---
            tools_status["gemini_consultant"] = "running"
            analysis_res = processor.analyze_garment(pil_cloth)
            
            garment_description = analysis_res.get('description')
            tools_status["gemini_consultant"] = analysis_res.get('gemini_consultant', "success")

            # --- 步驟 4: 虛擬試穿 ---
            tools_status["gemini_model"] = "running"
            result_tryon = processor.virtual_try_on(
                model_image=model_image,
                clean_clothes_path=clean_path,
                hex_color=hex_color,
                texture_swatch=texture_swatch,
                garment_description=garment_description,
                model_info=data.get('model_info', {}),
                garment_info=data.get('garment_info', {})
            )
            
            if not result_tryon.get('success'):
                error_code = result_tryon.get('code', 422)
                return JsonResponse({
                    "code": error_code,
                    "message": result_tryon.get('message', "Virtual try-on failed"),
                    "tools_status": result_tryon.get('tools_status', tools_status),
                    "debug_info": result_tryon.get('debug_info', {})
                }, status=error_code)
            
            tools_status["gemini_model"] = "success"
            tryon_filename = result_tryon.get('tryon_result_filename')
            tryon_path = os.path.join(settings.MEDIA_ROOT, tryon_filename)

            # ========== 3. 構建成功的 Multipart 響應 ==========
            analysis_data = {
                "code": 200,
                "message": "Success",
                "tools_status": tools_status,
                "data": {
                    "file_name": tryon_filename,
                    "file_format": "PNG",
                    "style_analysis": {
                        "tech_spec": garment_description,
                        "hex_color": hex_color
                    }
                }
            }

            boundary = 'frame_boundary'
            response_body = []
            
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{tryon_filename}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            with open(tryon_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            logger.info(f"✅ [TryOn] 合成流水線完成")
            return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')

        except Exception as e:
            logger.error(f"❌ [TryOn] 發生系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500, 
                "message": "Internal Server Error",
                "debug_info": {"detail": str(e)}
            }, status=500)