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
                    "message": result.get('message', "Processing Success"),  # 使用动态消息
                    "tools_status": result.get('tools_status', {}),
                    "error_details": result.get('error_details'),  # 新增：失败原因
                    "data": {
                        "file_name": file_name,
                        "file_format": "PNG",
                        "style_analysis": result.get('style_analysis', {}),
                        "top_colors": result.get('top_colors')
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
# [功能 2] 虚拟试穿 (独立功能，不继承去背状态)
# ==========================================
# ==========================================
# [功能 2] 虚拟试穿 (独立功能，不继承去背状态)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # ========== 接收文件 ==========
        model_image = request.FILES.get('model_image')
        garment_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        # ========== 接收 JSON 数据 ==========
        try:
            # 尝试从 request.POST 获取 JSON 字符串
            data_str = request.POST.get('data')
            if data_str:
                data = json.loads(data_str)
            else:
                # 如果没有 data 字段,使用空字典
                data = {}
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ [TryOn] JSON 解析失败: {e}")
            data = {}

        # ========== 验证必要参数 ==========
        if not model_image or not garment_image:
            logger.warning("⚠️ [TryOn] 缺少图片")
            return JsonResponse({
                "code": 400,
                "message": "Bad Request: 缺少必要图片",
                "tools_status": {
                    "rembg": "not_started",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started"
                },
                "debug_info": {
                    "error_type": "MissingFileError",
                    "suggest": "Please upload both model_image and garment_image."
                }
            }, status=400)

        # 检查文件格式
        if not model_image.content_type.startswith('image/') or not garment_image.content_type.startswith('image/'):
            logger.warning("⚠️ [TryOn] 不支援的档案格式")
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 上传非图片档",
                "tools_status": {
                    "rembg": "not_started",
                    "opencv_smoothing": "not_started",
                    "gemini_consultant": "not_started",
                    "gemini_model": "not_started"
                },
                "debug_info": {
                    "error_type": "InvalidFormatError",
                    "suggest": "Only JPG/PNG/WEBP files are accepted."
                }
            }, status=415)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 启动正式合成流水线...")
            
            # ========== 提取参数 ==========
            clothes_category = data.get('clothes_category', 'cloth')
            model_info = data.get('model_info', {})
            garment_info = data.get('garment_info', {})
            
            # 1. 取得去背素材
            result_bg = processor.remove_background(garment_image)
            
            if not result_bg.get('success'):
                error_code = result_bg.get('code', 422)
                logger.warning(f"⚠️ [TryOn] 去背失败 (code={error_code})")
                return JsonResponse({
                    "code": error_code,
                    "message": result_bg.get('message', "Background removal failed"),
                    "tools_status": result_bg.get('tools_status', {}),
                    "debug_info": result_bg.get('debug_info', {})
                }, status=error_code)
            
            clean_path = os.path.join(settings.MEDIA_ROOT, result_bg.get('file_name'))
            
            # 检查去背文件是否存在
            if not os.path.exists(clean_path):
                logger.error(f"❌ [TryOn] 去背文件不存在: {clean_path}")
                tools_status = result_bg.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 去背文件生成失败",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "stage": "preprocessing",
                        "detail": f"Background removed file not found: {result_bg.get('file_name')}"
                    }
                }, status=500)
            
            # 2. 调用 AI 合成结果（传入尺寸数据，不传颜色矩阵）
            result_tryon = processor.virtual_try_on(
                model_image=model_image,
                clean_clothes_path=clean_path,
                clothes_category=clothes_category,
                model_info=model_info,
                garment_info=garment_info
            )
            
            if not result_tryon.get('success'):
                error_code = result_tryon.get('code', 422)
                logger.warning(f"⚠️ [TryOn] 合成失败 (code={error_code}): {result_tryon.get('message')}")
                return JsonResponse({
                    "code": error_code,
                    "message": result_tryon.get('message', "Virtual try-on failed"),
                    "tools_status": result_tryon.get('tools_status', {}),
                    "debug_info": result_tryon.get('debug_info', {})
                }, status=error_code)
            
            # 检查输出文件名称
            model_filename = result_tryon.get('model_image_filename')
            tryon_filename = result_tryon.get('tryon_result_filename')
            
            if not model_filename or not tryon_filename:
                logger.error(f"❌ [TryOn] 返回结果缺少文件名称")
                tools_status = result_tryon.get('tools_status', {})
                tools_status['result_validation'] = 'fail'
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 处理结果不完整",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "IncompleteResultError",
                        "detail": "Missing output filenames"
                    }
                }, status=500)
            
            model_path = os.path.join(settings.MEDIA_ROOT, model_filename)
            tryon_path = os.path.join(settings.MEDIA_ROOT, tryon_filename)
            
            # 检查文件是否存在
            if not os.path.exists(model_path):
                logger.error(f"❌ [TryOn] 模特图片不存在: {model_path}")
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 模特图片文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "detail": f"Model image not found: {model_filename}"
                    }
                }, status=500)
            
            if not os.path.exists(tryon_path):
                logger.error(f"❌ [TryOn] 试穿结果不存在: {tryon_path}")
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_check'] = 'fail'
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 试穿结果文件缺失",
                    "tools_status": tools_status,
                    "debug_info": {
                        "error_type": "FileNotFoundError",
                        "detail": f"Try-on result not found: {tryon_filename}"
                    }
                }, status=500)
            
            # 构建成功的响应
            analysis_data = {
                "code": 200,
                "message": "Success",
                "tools_status": result_tryon.get('tools_status', {}),
                "data": {
                    "file_name": tryon_filename,
                    "file_format": "PNG"
                }
            }
            
            # 构建 multipart/form-data 响应
            boundary = 'frame_boundary'
            response_body = []
            
            # Part 1: analysis (JSON)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json.dumps(analysis_data, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            # Part 2: try_on_result (Binary)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{tryon_filename}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            try:
                with open(tryon_path, 'rb') as f:
                    response_body.append(f.read())
            except Exception as e:
                logger.error(f"❌ [TryOn] 读取试穿结果失败: {str(e)}")
                tools_status = result_tryon.get('tools_status', {})
                tools_status['file_read'] = 'fail'
                return JsonResponse({
                    "code": 500,
                    "message": "Internal Server Error: 无法读取试穿结果",
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
            
            logger.info(f"✅ [TryOn] 合成成功并返回 multipart/form-data")
            return response

        except Exception as e:
            logger.error(f"❌ [TryOn] 发生系统错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型运算失败",
                "tools_status": {
                    "rembg": "error",
                    "opencv_smoothing": "error",
                    "gemini_consultant": "error",
                    "gemini_model": "error"
                },
                "debug_info": {
                    "error_type": "RuntimeError",
                    "detail": str(e)
                }
            }, status=500)