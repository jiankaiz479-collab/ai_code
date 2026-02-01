import os
import logging
from urllib.parse import quote  # <--- [修正 1] 補上這個工具
from django.conf import settings
from django.http import JsonResponse, FileResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# [修正 2] 初始化 System Log (UART Init)
logger = logging.getLogger(__name__)

# ==========================================
#  1. 去背功能 (Remove Background)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        # --- [檢查 1] 是否有上傳檔案 (400) ---
        clothes_image = request.FILES.get('clothes_image')
        
        if not clothes_image:
            logger.warning("⚠️ [RemoveBg] 未上傳圖片")
            return JsonResponse({"code": 400, "message": "未上傳圖片 (Missing parameter: clothes_image)"}, status=400)

        # --- [檢查 2] 檔案格式是否支援 (415) ---
        if not clothes_image.content_type.startswith('image/'):
            logger.warning(f"⚠️ [RemoveBg] 格式錯誤: {clothes_image.content_type}")
            return JsonResponse({"code": 415, "message": "不支援的檔案格式 (Unsupported Media Type)"}, status=415)

        try:
            processor = AIProcessor()
            logger.info(f"🔄 [RemoveBg] 開始去背: {clothes_image.name}")
            
            # 呼叫去背 (單一回傳值)
            result_path = processor.remove_background(clothes_image)
            
            # --- [檢查 3] 結果檔案是否存在 (500) ---
            if not os.path.exists(result_path):
                logger.error("❌ [RemoveBg] 找不到輸出檔")
                return JsonResponse({"code": 500, "message": "檔案處理失敗，找不到結果檔"}, status=500)

            # --- [成功] 回傳檔案 ---
            filename = os.path.basename(result_path)
            response = FileResponse(open(result_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['X-Message'] = 'Success'
            
            logger.info(f"✅ [RemoveBg] 成功回傳: {filename}")
            return response

        except OSError:
            logger.error("❌ [RemoveBg] 圖片損壞")
            return JsonResponse({"code": 422, "message": "圖片過於模糊或損壞"}, status=422)

        except Exception as e:
            logger.error(f"❌ [RemoveBg] 系統錯誤: {str(e)}")
            return JsonResponse({"code": 500, "message": f"AI 模型運算失敗: {str(e)}"}, status=500)

# ==========================================
#  2. 虛擬試穿 (Virtual Try-On)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # [修正 3] 補回完整的輸入檢查邏輯 (這是必要的電路，不能省略)
        model_image = request.FILES.get('model_image')
        clothes_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not clothes_image:
            logger.warning("⚠️ [TryOn] 缺少必要參數")
            return JsonResponse({"code": 400, "message": "缺少參數 (Missing: model_image or garment_image)"}, status=400)

        if not model_image.content_type.startswith('image/') or not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "不支援的檔案格式"}, status=415)

        try:
            processor = AIProcessor()
            logger.info("🔄 [TryOn] 開始 AI 試穿合成...")
            
            # 呼叫核心運算 (接收 Tuple: 路徑 + 文字)
            result_path, ai_analysis = processor.virtual_try_on(model_image, clothes_image)
            
            if not os.path.exists(result_path):
                raise FileNotFoundError("合成完成但找不到輸出檔")

            filename = os.path.basename(result_path)
            
            # 準備回傳圖片
            response = FileResponse(open(result_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="tryon_result.png"'
            
            # 將文字注入到 Header (Sideband Signal)
            # 使用 quote 將中文轉碼，防止 HTTP Header 亂碼
            safe_text = quote(ai_analysis, safe='/:, =.') 
            response['X-AI-Analysis'] = safe_text

            logger.info(f"✅ [TryOn] 成功回傳圖片與分析文字")
            return response

        except OSError:
             return JsonResponse({"code": 422, "message": "圖片過於模糊或損壞"}, status=422)

        except Exception as e:
            logger.error(f"❌ [TryOn] 系統錯誤: {str(e)}")
            return JsonResponse({"code": 500, "message": str(e)}, status=500)

# ==========================================
#  3. Debug 頁面
# ==========================================
class DebugPageView(View):
    def get(self, request):
        logger.debug("❤️ [Heartbeat] Debug Page Checked")
        return JsonResponse({
            "status": "running",
            "message": "AI Core Server is Online",
            "api_endpoints": [
                "/api/remove_bg",
                "/api/try_combine"
            ]
        })