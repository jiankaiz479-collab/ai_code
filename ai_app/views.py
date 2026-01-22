import os
from django.conf import settings
from django.http import JsonResponse, FileResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# 根據 API 文件，這是一個 API 接口，所以我們要豁免 CSRF 檢查
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        # 1. 檢查請求格式是否為 multipart/form-data
        # 雖然 Django 會自動處理，但確認是否有檔案上傳是必要的
        
        # 2. 獲取上傳的圖片
        # 根據 API 文件 Page 3 (Source 9)，欄位名稱 (Key) 是 "clothes_image"
        clothes_image = request.FILES.get('clothes_image')
        
        if not clothes_image:
            # 根據 API 文件 Page 6，缺少參數回傳 400 Bad Request
            return JsonResponse({
                "code": 400,
                "message": "Missing parameter: clothes_image"
            }, status=400)

        try:
            # 3. 呼叫您的 AI 處理器
            processor = AIProcessor()
            
            # processor.remove_background 會回傳圖片的 "URL 字串" (例如 /media/removed_bg_xxx.png)
            result_url = processor.remove_background(clothes_image)
            
            # 4. 準備回傳「二進位檔案」 (Binary)
            # 因為 API 文件 Page 4 (Source 20-24) 要求回傳 Content-Type: image/png 的檔案流
            # 我們需要從硬碟讀取剛剛存好的檔案
            
            # 從 URL 解析出實際檔案名稱
            filename = os.path.basename(result_url)
            local_file_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            if not os.path.exists(local_file_path):
                return JsonResponse({"code": 500, "message": "File processing failed"}, status=500)

            # 開啟檔案並建立 FileResponse
            # 這會自動設定 Content-Type: image/png 和 Content-Disposition
            response = FileResponse(open(local_file_path, 'rb'), content_type='image/png')
            
            # 設定檔名 header
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            # 根據文件加入自定義 header (可選)
            response['X-Message'] = 'Success'
            
            return response

        except Exception as e:
            # 捕捉任何運算錯誤，回傳 500
            print(f"Error: {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": f"AI 模型運算失敗: {str(e)}"
            }, status=500)
# ==========================================
#  補上缺失的 TryCombineView (虛擬試穿)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # 1. 根據 API 文件 Page 3 (Source 12) 接收兩張圖片
        # 注意：API 文件寫 'garment_image'，但您的處理器可能習慣用 'clothes_image'，這裡做個相容
        model_image = request.FILES.get('model_image')
        clothes_image = request.FILES.get('garment_image') or request.FILES.get('clothes_image')

        if not model_image or not clothes_image:
            return JsonResponse({
                "code": 400, 
                "message": "Missing parameters: model_image or garment_image"
            }, status=400)

        try:
            # 2. 呼叫 AIProcessor 的試穿功能
            processor = AIProcessor()
            # 根據您之前的程式碼，這裡會回傳 URL
            result_url = processor.virtual_try_on(model_image, clothes_image)
            
            # 3. 轉成檔案回傳
            filename = os.path.basename(result_url)
            local_file_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            if not os.path.exists(local_file_path):
                 return JsonResponse({"code": 500, "message": "Try-on processing failed"}, status=500)

            response = FileResponse(open(local_file_path, 'rb'), content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="tryon_result.png"'
            return response

        except Exception as e:
            return JsonResponse({"code": 500, "message": str(e)}, status=500)


# ==========================================
#  補上缺失的 DebugPageView (防止報錯)
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