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
                
                # 直接使用 processing 返回的數據
                analysis_data = {
                    "code": 200,
                    "message": "Processing Success",
                    "tools_status": result.get('tools_status', {}),
                    "data": {
                        "file_name": file_name,
                        "file_format": "PNG",
                        "style_analysis": result.get('style_analysis', {}),
                        "rgb_matrix": result.get('rgb_matrix')
                    }
                }
                
                boundary = 'bg_removal_boundary'
                response_body = []
                
                # Part 1: analysis (JSON)
                response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
                response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
                response_body.append(b'Content-Type: application/json\r\n\r\n')
                response_body.append(json.dumps(analysis_data, ensure_ascii=False).encode('utf-8'))
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
#  2. 虛擬試穿 (Virtual Try-On)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        model_image = request.FILES.get('model_image')
        garment_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not garment_image:
            logger.warning("⚠️ [TryOn] 缺少圖片")
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 缺少必要圖片",
                "tools_status": {
                    "rembg_engine": "not_started",
                    "opencv_masking": "not_started",
                    "gemini_consultant": "not_started",
                    "tryon_engine": "not_started",
                    "color_adjustment": "not_started"
                },
                "debug_info": {
                    "error_type": "MissingFileError",
                    "suggest": "Please upload both model_image and garment_image."
                }
            }, status=400)

        # 检查文件格式
        if not model_image.content_type.startswith('image/') or not garment_image.content_type.startswith('image/'):
            logger.warning("⚠️ [TryOn] 不支援的檔案格式")
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 上傳非圖片檔",
                "tools_status": {
                    "rembg_engine": "not_started",
                    "opencv_masking": "not_started",
                    "gemini_consultant": "not_started",
                    "tryon_engine": "not_started",
                    "color_adjustment": "not_started"
                },
                "debug_info": {
                    "error_type": "InvalidFormatError",
                    "suggest": "Only JPG/PNG/WEBP files are accepted."
                }
            }, status=415)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動正式合成流水線...")
            
            # 1. 取得去背素材 - processing 會返回完整的 tools_status
            result_bg = processor.remove_background(garment_image)
            
            if not result_bg.get('success'):
                error_code = result_bg.get('code', 422)
                logger.warning(f"⚠️ [TryOn] 去背失敗 (code={error_code})")
                
                # 直接使用 processing 返回的 tools_status
                return JsonResponse({
                    "code": error_code,
                    "message": result_bg.get('message', "Background removal failed"),
                    "tools_status": result_bg.get('tools_status', {}),
                    "debug_info": result_bg.get('debug_info', {})
                }, status=error_code)
            
            clean_path = os.path.join(settings.MEDIA_ROOT, result_bg.get('file_name'))
            
            # 檢查去背文件是否存在
            if not os.path.exists(clean_path):
                logger.error(f"❌ [TryOn] 去背文件不存在: {clean_path}")
                
                # 使用 processing 返回的 tools_status，添加 file_check 狀態
                tools_status = result_bg.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 去背文件生成失敗",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "stage": "preprocessing",
                        "detail": f"Background removed file not found: {result_bg.get('file_name')}"
                    }
                }, status=500)
            
            color_matrix = result_bg.get('rgb_matrix')
            
            # 2. 取得 AI 合成結果 - processing 會返回完整的 tools_status
            result_tryon = processor.virtual_try_on(
                model_image, 
                clean_path, 
                color_matrix
            )
            
            if not result_tryon.get('success'):
                error_code = result_tryon.get('code', 422)
                logger.warning(f"⚠️ [TryOn] 合成失敗 (code={error_code}): {result_tryon.get('message')}")
                
                # 直接使用 processing 返回的 tools_status
                return JsonResponse({
                    "code": error_code,
                    "message": result_tryon.get('message', "Virtual try-on failed"),
                    "tools_status": result_tryon.get('tools_status', {}),
                    "debug_info": result_tryon.get('debug_info', {})
                }, status=error_code)
            
            # 檢查輸出文件名稱
            model_filename = result_tryon.get('model_image_filename')
            tryon_filename = result_tryon.get('tryon_result_filename')
            
            if not model_filename or not tryon_filename:
                logger.error(f"❌ [TryOn] 回傳結果缺少文件名稱")
                
                # 使用 processing 返回的 tools_status，添加 result_validation 狀態
                tools_status = result_tryon.get('tools_status', {})
                tools_status['result_validation'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 處理結果不完整",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "IncompleteResultError",
                        "detail": "Missing output filenames"
                    }
                }, status=500)
            
            model_path = os.path.join(settings.MEDIA_ROOT, model_filename)
            tryon_path = os.path.join(settings.MEDIA_ROOT, tryon_filename)
            
            # 檢查文件是否存在
            if not os.path.exists(model_path):
                logger.error(f"❌ [TryOn] 模特圖片不存在: {model_path}")
                
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 模特圖片文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "detail": f"Model image not found: {model_filename}"
                    }
                }, status=500)
            
            if not os.path.exists(tryon_path):
                logger.error(f"❌ [TryOn] 試穿結果不存在: {tryon_path}")
                
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 試穿結果文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "detail": f"Try-on result not found: {tryon_filename}"
                    }
                }, status=500)
            
            # 构建成功的响应 - 直接使用 processing 返回的數據
            analysis_data = {
                "code": 200,
                "message": "OK: 虛擬試穿成功",
                "tools_status": result_tryon.get('tools_status', {}),
                "data": {
                    "model_image": model_filename,
                    "try_on_result": tryon_filename,
                    "file_format": "PNG",
                    "style_analysis": result_bg.get('style_analysis', {})
                }
            }
            
            # 构建 multipart/form-data 响应
            boundary = 'try_on_boundary'
            response_body = []
            
            # Part 1: analysis (JSON)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json.dumps(analysis_data, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            # Part 2: model_image (Binary)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="model_image"; filename="{model_filename}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            try:
                with open(model_path, 'rb') as f:
                    response_body.append(f.read())
            except Exception as e:
                logger.error(f"❌ [TryOn] 讀取模特圖片失敗: {str(e)}")
                
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_read'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 無法讀取模特圖片",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileReadError",
                        "detail": str(e)
                    }
                }, status=500)
            response_body.append(b'\r\n')
            
            # Part 3: try_on_result (Binary)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="try_on_result"; filename="{tryon_filename}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            try:
                with open(tryon_path, 'rb') as f:
                    response_body.append(f.read())
            except Exception as e:
                logger.error(f"❌ [TryOn] 讀取試穿結果失敗: {str(e)}")
                
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_read'] = 'fail'
                
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 無法讀取試穿結果",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileReadError",
                        "detail": str(e)
                    }
                }, status=500)
            response_body.append(b'\r\n')
            
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            # 创建响应
            response = HttpResponse(
                b''.join(response_body),
                content_type=f'multipart/form-data; boundary={boundary}'
            )
            
            logger.info(f"✅ [TryOn] 合成成功並回傳 multipart/form-data")
            return response

        except Exception as e:
            logger.error(f"❌ [TryOn] 發生系統錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型運算失敗",
                "tools_status": {
                    "rembg_engine": "error",
                    "opencv_masking": "error",
                    "gemini_consultant": "error",
                    "tryon_engine": "error",
                    "color_adjustment": "error"
                },
                "debug_info": {
                    "error_type": "RuntimeError",
                    "detail": str(e)
                }
            }, status=500)