import os
import logging
import cv2
import numpy as np
import json
from urllib.parse import quote
from django.conf import settings
from django.http import JsonResponse, FileResponse,HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# 初始化日誌
logger = logging.getLogger(__name__)

# ==========================================
#  1. 去背功能 (Remove Background)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        # 检查是否有上传文件
        clothes_image = request.FILES.get('clothes_image')
        
        if not clothes_image:
            logger.warning("⚠️ [RemoveBg] 未上傳圖片")
            return JsonResponse({
                "code": 400,
                "message": "未上傳圖片"
            }, status=400)

        # 检查文件格式
        if not clothes_image.content_type.startswith('image/'):
            logger.warning(f"⚠️ [RemoveBg] 不支援的檔案格式: {clothes_image.content_type}")
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 上傳非圖片檔"
            }, status=415)

        try:
            processor = AIProcessor()
            logger.info(f"🔄 [RemoveBg] 開始執行去背流水線: {clothes_image.name}")
            
            # 调用处理，接收真实的 tools_status 和其他结果
            result = processor.remove_background(clothes_image)
            
            if result.get('success'):
                logger.info(f"✅ [RemoveBg] 處理完成，顏色矩陣: {result.get('rgb_matrix')}")
                
                # 构建 JSON 分析数据
                analysis_data = {
                    "code": 200,
                    "message": "OK: 去背成功",
                    "tools_status": result.get('tools_status')[0],  # 取出字典
                    "data": {
                        "file_name": result.get('file_name'),
                        "file_format": "PNG",
                        "rgb_matrix": result.get('rgb_matrix'),
                        "style_analysis": result.get('style_analysis')
                    }
                }
                
                # 读取处理后的图片
                file_path = os.path.join(settings.MEDIA_ROOT, result.get('file_name'))
                
                # 构建 multipart/form-data 响应
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
                response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{result.get("file_name")}"\r\n'.encode('utf-8'))
                response_body.append(b'Content-Type: image/png\r\n\r\n')
                
                with open(file_path, 'rb') as f:
                    response_body.append(f.read())
                
                response_body.append(b'\r\n')
                response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
                
                # 创建响应
                response = HttpResponse(
                    b''.join(response_body),
                    content_type=f'multipart/form-data; boundary={boundary}'
                )
                
                logger.info(f"✅ [RemoveBg] 回傳 multipart/form-data")
                return response
                
            else:
                # 失败情况：返回 JSON
                logger.warning(f"⚠️ [RemoveBg] 處理失敗: {result.get('message')}")
                code = result.get('code', 422)
                return JsonResponse({
                    "code": code,
                    "message": result.get('message'),
                    "tools_status": result.get('tools_status'),
                    "debug_info": result.get('debug_info')
                }, status=code)

        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": "AI 模型運算失敗"
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
                "message": "缺少圖片"
            }, status=400)

        # 检查文件格式
        if not model_image.content_type.startswith('image/') or not garment_image.content_type.startswith('image/'):
            logger.warning("⚠️ [TryOn] 不支援的檔案格式")
            return JsonResponse({
                "code": 415,
                "message": "Unsupported Media Type: 上傳非圖片檔"
            }, status=415)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動正式合成流水線...")
            
            # 1. 取得去背素材與顏色矩陣
            result_bg = processor.remove_background(garment_image)
            if not result_bg.get('success'):
                logger.warning(f"⚠️ [TryOn] 去背失敗")
                code = result_bg.get('code', 422)
                return JsonResponse({
                    "code": code,
                    "message": result_bg.get('message'),
                    "tools_status": result_bg.get('tools_status'),
                    "debug_info": result_bg.get('debug_info')
                }, status=code)
            
            clean_path = os.path.join(settings.MEDIA_ROOT, result_bg.get('file_name'))
            color_matrix = result_bg.get('rgb_matrix')
            
            # 2. 取得 AI 合成結果
            result_tryon = processor.virtual_try_on(
                model_image, 
                clean_path, 
                color_matrix
            )
            
            if not result_tryon.get('success'):
                logger.warning(f"⚠️ [TryOn] 合成失敗: {result_tryon.get('message')}")
                code = result_tryon.get('code', 422)
                return JsonResponse({
                    "code": code,
                    "message": result_tryon.get('message'),
                    "tools_status": result_tryon.get('tools_status'),
                    "debug_info": result_tryon.get('debug_info')
                }, status=code)
            
            # 构建成功的 multipart/form-data 响应
            analysis_data = {
                "code": 200,
                "message": "OK: 虛擬試穿成功",
                "tools_status": result_tryon.get('tools_status'),
                "data": {
                    "model_image": result_tryon.get('model_image_filename'),
                    "try_on_result": result_tryon.get('tryon_result_filename'),
                    "file_format": "PNG"
                }
            }
            
            # 读取两个图片文件
            model_path = os.path.join(settings.MEDIA_ROOT, result_tryon.get('model_image_filename'))
            tryon_path = os.path.join(settings.MEDIA_ROOT, result_tryon.get('tryon_result_filename'))
            
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
            response_body.append(f'Content-Disposition: form-data; name="model_image"; filename="{result_tryon.get("model_image_filename")}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            with open(model_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(b'\r\n')
            
            # Part 3: try_on_result (Binary)
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="try_on_result"; filename="{result_tryon.get("tryon_result_filename")}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            with open(tryon_path, 'rb') as f:
                response_body.append(f.read())
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
            logger.error(f"❌ [TryOn] 發生錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                "code": 500,
                "message": "Internal Server Error: AI 模型運算失敗"
            }, status=500) 