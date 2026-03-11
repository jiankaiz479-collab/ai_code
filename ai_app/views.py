import os
import logging
import cv2
import numpy as np
import json
from urllib.parse import quote
from django.conf import settings
from django.http import JsonResponse, FileResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

logger = logging.getLogger(__name__)

# ==========================================
#  1. 去背功能 (Remove Background)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        clothes_image = request.FILES.get('clothes_image')
        
        # --- 錯誤處理 1: 未上傳檔案 (400) ---
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

        # --- 錯誤處理 2: 檔案格式不符 (415) ---
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
            
            # 執行 AI 處理 - processing 會返回完整的 tools_status
            result = processor.remove_background(clothes_image)
            
            # --- 情況 A: 去背成功 (回傳 Multipart) ---
            if result.get('success'):
                file_name = result.get('file_name')
                file_path = os.path.join(settings.MEDIA_ROOT, file_name)
                
                # 檢查文件是否真的存在
                if not os.path.exists(file_path):
                    logger.error(f"❌ [RemoveBg] 文件生成失敗: {file_path}")
                    
                    # 使用 processing 返回的 tools_status，添加 file_save 狀態
                    tools_status = result.get('tools_status', {})
                    tools_status['file_save'] = 'fail'
                    
                    return JsonResponse({
                        "code": 500,
                        "message": "Internal Server Error: 文件生成失敗",
                        "tools_status": tools_status,
                        "debug_info": {
                            "error_type": "FileNotFoundError",
                            "detail": f"Expected file not found: {file_name}"
                        }
                    }, status=500)
                
                logger.info(f"✅ [RemoveBg] 處理成功: {file_name}")
                
                # 构建基础响应数据（严格按照 API 文档）
                analysis_data = {
                    "code": 200,
                    "message": result.get('message', "Processing Success"),
                    "tools_status": result.get('tools_status', {}),
                    "data": {
                        "file_name": file_name,
                        "file_format": "PNG",
                        "style_analysis": result.get('style_analysis', {})
                        # ❌ 删除: "top_colors": result.get('top_colors')
                    }
                }
                
                # ✅ 只有当 processing 返回了 error_details 时才动态添加
                if result.get('error_details'):
                    analysis_data['error_details'] = result['error_details']
                                
                boundary = 'bg_removal_boundary'
                response_body = []
                
                # Part 1: analysis (JSON)
                response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
                response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
                response_body.append(b'Content-Type: application/json\r\n\r\n')
                response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
                response_body.append(b'\r\n')
                
                # Part 2: processed_image (Binary)
                response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
                response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\n'.encode('utf-8'))
                response_body.append(b'Content-Type: image/png\r\n\r\n')
                
                try:
                    with open(file_path, 'rb') as f:
                        response_body.append(f.read())
                except Exception as e:
                    logger.error(f"❌ [RemoveBg] 讀取文件失敗: {str(e)}")
                    
                    # 使用 processing 返回的 tools_status，添加 file_read 狀態
                    tools_status = result.get('tools_status', {})
                    tools_status['file_read'] = 'fail'
                    
                    return JsonResponse({
                        "code": 500,
                        "message": "Internal Server Error: 無法讀取處理後的圖片",
                        "tools_status": tools_status,
                        "debug_info": {
                            "error_type": "FileReadError",
                            "detail": str(e)
                        }
                    }, status=500)
                
                response_body.append(b'\r\n')
                response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
                
                return HttpResponse(
                    b''.join(response_body),
                    content_type=f'multipart/form-data; boundary={boundary}'
                )
                
            # --- 情況 B: 邏輯失敗 ---
            else:
                # 直接使用 processing 返回的所有數據
                error_code = result.get('code', 422)
                
                logger.warning(f"⚠️ [RemoveBg] 處理失敗 (code={error_code}): {result.get('message')}")
                return JsonResponse({
                    "code": error_code,
                    "message": result.get('message', "Image processing failed"),
                    "tools_status": result.get('tools_status', {}),
                    "debug_info": result.get('debug_info', {})
                }, status=error_code)

        # --- 情況 C: 系統崩潰 (500) ---
        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型運算失敗",
                "tools_status": {
                    "rembg_engine": "error",
                    "opencv_masking": "error",
                    "gemini_consultant": "error"
                },
                "debug_info": {
                    "error_type": "RuntimeError",
                    "detail": str(e)
                }
            }, status=500)



# ==========================================
# [功能 2] 虚拟试穿 (独立功能，不继承去背状态)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        model_image = request.FILES.get('model_image')
        garment_image = request.FILES.get('clothes_image')

        try:
            data_str = request.POST.get('data')
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ [TryOn] JSON 解析失败: {e}")
            data = {}

        if not model_image or not garment_image:
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 缺少必要图片",
                "tools_status": {
                    "rembg": "not_started",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started",
                    "densepose": "not_started",
                    "controlnet": "not_started"
                },
                "debug_info": {
                    "error_type": "MissingFileError",
                    "suggest": "Please upload both model_image and garment_image."
                }
            }, status=400)

        if not model_image.content_type.startswith('image/') or not garment_image.content_type.startswith('image/'):
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 上传非图片档",
                "tools_status": {
                    "rembg": "not_started",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started",
                    "densepose": "not_started",
                    "controlnet": "not_started"
                },
                "debug_info": {
                    "error_type": "InvalidFormatError",
                    "suggest": "Only JPG/PNG/WEBP files are accepted."
                }
            }, status=415)

        processor = AIProcessor()
        clothes_category = data.get('clothes_category', 'cloth')
        model_info = data.get('model_info', {})
        garment_info = data.get('garment_info', {})

        tools_status = {
            "rembg": "not_started",
            "opencv_smoothing": "not_started",
            "gemini_consultant": "not_started",
            "gemini_model": "not_started",
            "densepose": "not_started",
            "controlnet": "not_started"
        }

        try:
            # Step 1: 衣服去背
            garment_result = processor.remove_clothes_background(garment_image)
            tools_status.update(garment_result.get("tools_status", {}))
            if not garment_result.get("success"):
                code = garment_result.get("code", 422)
                return JsonResponse({
                    "code": code,
                    "message": garment_result.get("message", "Garment background removal failed"),
                    "tools_status": tools_status,
                    "debug_info": garment_result.get("debug_info", {})
                }, status=code)

            clean_clothes_path = garment_result.get("file_path")
            if not clean_clothes_path or not os.path.exists(clean_clothes_path):
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 去背衣服文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {"error_type": "FileNotFoundError"}
                }, status=500)

            # Step 2: 模特兒去背
            model_result = processor.remove_model_background(model_image)
            if "rembg" in model_result.get("tools_status", {}):
                tools_status["rembg"] = model_result["tools_status"]["rembg"]

            if not model_result.get("success"):
                code = model_result.get("code", 422)
                return JsonResponse({
                    "code": code,
                    "message": model_result.get("message", "Model background removal failed"),
                    "tools_status": tools_status,
                    "debug_info": model_result.get("debug_info", {})
                }, status=code)

            clean_model_path = model_result.get("file_path")
            if not clean_model_path or not os.path.exists(clean_model_path):
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 去背模特文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {"error_type": "FileNotFoundError"}
                }, status=500)

            # Step 3: DensePose
            pose_result = processor.extract_densepose_map(clean_model_path)
            if "densepose" in pose_result.get("tools_status", {}):
                tools_status["densepose"] = pose_result["tools_status"]["densepose"]

            if not pose_result.get("success"):
                code = pose_result.get("code", 422)
                return JsonResponse({
                    "code": code,
                    "message": pose_result.get("message", "Pose extraction failed"),
                    "tools_status": tools_status,
                    "debug_info": pose_result.get("debug_info", {})
                }, status=code)

            pose_map_path = pose_result.get("pose_map_path")
            if not pose_map_path or not os.path.exists(pose_map_path):
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: DensePose 文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {"error_type": "FileNotFoundError"}
                }, status=500)

            # Step 4: 合成（只合成）
            tryon_result = processor.virtual_try_on(
                clean_model_path=clean_model_path,
                clean_clothes_path=clean_clothes_path,
                pose_map_path=pose_map_path,
                clothes_category=clothes_category,
                model_info=model_info,
                garment_info=garment_info
            )

            if "gemini_model" in tryon_result.get("tools_status", {}):
                tools_status["gemini_model"] = tryon_result["tools_status"]["gemini_model"]

            if not tryon_result.get("success"):
                code = tryon_result.get("code", 422)
                return JsonResponse({
                    "code": code,
                    "message": tryon_result.get("message", "Virtual try-on failed"),
                    "tools_status": tools_status,
                    "debug_info": tryon_result.get("debug_info", {})
                }, status=code)

            tryon_filename = tryon_result.get('tryon_result_filename')
            tryon_path = tryon_result.get('file_path')
            if not tryon_filename or not tryon_path or not os.path.exists(tryon_path):
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 试穿结果文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {"error_type": "FileNotFoundError"}
                }, status=500)

            analysis_data = {
                "code": 200,
                "message": "Success",
                "tools_status": tools_status,
                "data": {
                    "file_name": tryon_filename,
                    "file_format": "PNG"
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

            return HttpResponse(
                b''.join(response_body),
                content_type=f'multipart/form-data; boundary={boundary}'
            )

        except Exception as e:
            logger.error(f"❌ [TryOn] 系统错误: {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型运算失败",
                "tools_status": tools_status,
                "debug_info": {
                    "error_type": "RuntimeError",
                    "detail": str(e)
                }
            }, status=500)