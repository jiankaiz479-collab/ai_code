import os
import logging
import cv2
import numpy as np
from urllib.parse import quote
from django.conf import settings
from django.http import JsonResponse, FileResponse
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
        clothes_image = request.FILES.get('clothes_image')
        
        if not clothes_image:
            logger.warning("⚠️ [RemoveBg] 未上傳圖片")
            return JsonResponse({"code": 400, "message": "未上傳圖片"}, status=400)

        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "不支援的檔案格式"}, status=415)

        try:
            processor = AIProcessor()
            logger.info(f"🔄 [RemoveBg] 開始執行去背流水線: {clothes_image.name}")
            
            # 【修正】配合 AIProcessor 接收 (路徑, 顏色矩陣)
            result_path, rgb_matrix = processor.remove_background(clothes_image)
            
            if not os.path.exists(result_path):
                return JsonResponse({"code": 500, "message": "檔案處理失敗"}, status=500)

            filename = os.path.basename(result_path)
            response = FileResponse(open(result_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            # 將 OpenCV 偵測到的顏色矩陣傳回 Header
            response['X-Color-Matrix'] = str(rgb_matrix)
            response['X-Message'] = 'Success'
            
            logger.info(f"✅ [RemoveBg] 回傳圖片並附帶顏色矩陣: {rgb_matrix}")
            return response

        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            return JsonResponse({"code": 500, "message": str(e)}, status=500)

# ==========================================
#  2. 虛擬試穿 (Virtual Try-On)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        model_image = request.FILES.get('model_image')
        # 為了除錯方便，兼容兩種可能的 key
        clothes_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not clothes_image:
            return JsonResponse({"code": 400, "message": "缺少參數"}, status=400)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動正式合成流水線...")
            
            # 1. 取得去背素材與顏色矩陣 (內部已包含 OpenCV 磨皮處理)
            clean_path, color_matrix = processor.remove_background(clothes_image)
            
            # 2. 取得 AI 合成結果 (使用色彩鎖定技術)
            result_path, ai_analysis = processor.virtual_try_on(
                model_image, 
                clean_path, 
                color_matrix
            )
            
            if not os.path.exists(result_path):
                raise FileNotFoundError("合成完成但找不到輸出檔案")

            # --- [正式輸出：直接回傳合成圖] ---
            filename = os.path.basename(result_path)
            response = FileResponse(open(result_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="tryon_{filename}"'
            
            # 依然在 Header 保留分析資訊與鎖定的顏色，方便後端追蹤
            safe_text = quote(ai_analysis, safe='/:, =.') 
            response['X-AI-Analysis'] = safe_text
            response['X-Color-Locked'] = str(color_matrix)

            logger.info(f"✅ [TryOn] 合成成功並回傳單圖")
            return response

        except Exception as e:
            logger.error(f"❌ [TryOn] 發生錯誤: {str(e)}")
            return JsonResponse({"code": 500, "message": str(e)}, status=500)