import os
from django.conf import settings
from django.http import JsonResponse, FileResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# ==========================================
#  1. 去背功能 (Remove Background)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        # --- [檢查 1] 是否有上傳檔案 (400) ---
        clothes_image = request.FILES.get('clothes_image')
        
        if not clothes_image:
            return JsonResponse({
                "code": 400,
                "message": "未上傳圖片 (Missing parameter: clothes_image)"
            }, status=400)

        # --- [檢查 2] 檔案格式是否支援 (415) ---
        # 確保上傳的是 image 類型 (如 image/jpeg, image/png)
        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({
                "code": 415,
                "message": "不支援的檔案格式 (Unsupported Media Type)"
            }, status=415)

        try:
            processor = AIProcessor()
            result_url = processor.remove_background(clothes_image)
            
            # 準備回傳檔案
            filename = os.path.basename(result_url)
            local_file_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            # --- [檢查 3] 結果檔案是否存在 (500) ---
            if not os.path.exists(local_file_path):
                return JsonResponse({
                    "code": 500, 
                    "message": "檔案處理失敗，找不到結果檔"
                }, status=500)

            # --- [成功] 回傳 200 OK 與檔案 ---
            response = FileResponse(open(local_file_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['X-Message'] = 'Success'
            return response

        except OSError:
            # --- [錯誤] 圖片損壞或無法讀取 (422) ---
            return JsonResponse({
                "code": 422,
                "message": "圖片過於模糊或損壞 (Unprocessable Entity)"
            }, status=422)

        except Exception as e:
            # --- [錯誤] 伺服器內部錯誤 (500) ---
            print(f"Error: {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": f"AI 模型運算失敗 (Internal Server Error): {str(e)}"
            }, status=500)

# ==========================================
#  2. 虛擬試穿 (Virtual Try-On)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # --- [檢查 1] 參數是否齊全 (400) ---
        model_image = request.FILES.get('model_image')
        clothes_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not clothes_image:
            return JsonResponse({
                "code": 400, 
                "message": "缺少參數 (Missing: model_image or garment_image)"
            }, status=400)

        # --- [檢查 2] 檔案格式 (415) ---
        if not model_image.content_type.startswith('image/') or not clothes_image.content_type.startswith('image/'):
             return JsonResponse({
                "code": 415,
                "message": "不支援的檔案格式 (Unsupported Media Type)"
            }, status=415)

        try:
            processor = AIProcessor()
            result_url = processor.virtual_try_on(model_image, clothes_image)
            
            filename = os.path.basename(result_url)
            local_file_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            if not os.path.exists(local_file_path):
                 return JsonResponse({"code": 500, "message": "試穿處理失敗 (File missing)"}, status=500)

            response = FileResponse(open(local_file_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="tryon_result.png"'
            return response

        except OSError:
             return JsonResponse({
                "code": 422,
                "message": "圖片過於模糊或損壞 (Unprocessable Entity)"
            }, status=422)

        except Exception as e:
            return JsonResponse({"code": 500, "message": str(e)}, status=500)

# ==========================================
#  3. Debug 頁面
# ==========================================
class DebugPageView(View):
    def get(self, request):
        return JsonResponse({
            "status": "running",
            "message": "AI Core Server is Online",
            "api_endpoints": [
                "/api/remove_bg",
                "/api/try_combine"
            ]
        })