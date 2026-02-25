import os
import logging
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
        clothes_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not clothes_image:
            return JsonResponse({"code": 400, "message": "缺少參數"}, status=400)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 啟動色彩保護合成流水線...")
            
            # --- 順序 1: 先去背並提取 OpenCV 顏色矩陣 ---
            # 這是為了確保 Gemini 在重畫時有「顏色物理標準」可參考
            clean_path, color_matrix = processor.remove_background(clothes_image)
            logger.info(f"🎨 [TryOn] 提取顏色矩陣成功: {color_matrix}")

            # --- 順序 2: 將去背圖與顏色矩陣餵入合成功能 ---
            # 透過 rgb_matrix 鎖定渲染色，防止衣服失真
            result_path, ai_analysis = processor.virtual_try_on(
                model_image, 
                clean_path, 
                color_matrix
            )
            
            if not os.path.exists(result_path):
                raise FileNotFoundError("合成完成但找不到輸出檔")

            filename = os.path.basename(result_path)
            response = FileResponse(open(result_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="tryon_result.png"'
            
            # 將分析文字與使用的顏色矩陣封裝進 Header
            safe_text = quote(ai_analysis, safe='/:, =.') 
            response['X-AI-Analysis'] = safe_text
            response['X-Color-Locked'] = str(color_matrix)

            logger.info(f"✅ [TryOn] 合成成功，輸出路徑: {filename}")
            return response

        except Exception as e:
            logger.error(f"❌ [TryOn] 流水線錯誤: {str(e)}")
            return JsonResponse({"code": 500, "message": str(e)}, status=500)