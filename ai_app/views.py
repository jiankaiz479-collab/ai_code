import json
import base64
import os
from django.conf import settings # 需要用到 settings 來找檔案路徑
from django.shortcuts import render
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# --- 輔助函式 1：把前端傳來的 Base64 轉成圖片檔案 (Input) ---
def decode_base64_image(base64_string, file_name):
    if ';base64,' in base64_string:
        format, imgstr = base64_string.split(';base64,')
        ext = format.split('/')[-1]
    else:
        imgstr = base64_string
        ext = 'png'
        
    return ContentFile(base64.b64decode(imgstr), name=f"{file_name}.{ext}")

# --- [新增] 輔助函式 2：把處理好的圖片轉回 Base64 (Output) ---
def image_to_base64(image_path):
    """
    讀取硬碟上的圖片並轉為 Base64 字串 (不帶檔頭)
    image_path: 圖片在系統中的絕對路徑
    """
    try:
        with open(image_path, "rb") as img_file:
            # 讀取二進制資料並轉為 Base64
            encoded_string = base64.b64encode(img_file.read()).decode('utf-8')
            return encoded_string
    except FileNotFoundError:
        return None

# --- 1. 測試頁面 View (保持不變) ---
class DebugPageView(View):
    def get(self, request):
        return render(request, 'ai_app/debug_page.html')

    def post(self, request):
        if 'image' not in request.FILES:
            return render(request, 'ai_app/debug_page.html', {'error': '請選擇圖片'})
            
        processor = AIProcessor()
        result_url = processor.remove_background(request.FILES['image'])
        
        return render(request, 'ai_app/debug_page.html', {
            'result_url': result_url
        })

# --- 2. 正式 API: 去背 (已修改為回傳 Base64) ---
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    """
    API: /api/remove_bg
    Method: POST
    Response: 符合 Excel 定義，回傳 JSON + Base64
    """

    def post(self, request):
        try:
            # 1. 解析 JSON
            data = json.loads(request.body)
            
            # 2. 檢查欄位
            if 'clothes_image_base64' not in data:
                return JsonResponse({'status': 'error', 'message': 'Missing clothes_image_base64'}, status=400)

            # 3. 轉檔 (Base64 -> File)
            image_file = decode_base64_image(data['clothes_image_base64'], "temp_clothes")

            # 4. 呼叫服務 (這裡假設它回傳的是相對路徑 URL，例如 '/media/results/xx.png')
            processor = AIProcessor()
            result_url = processor.remove_background(image_file)

            # 5. [關鍵修改] 取得檔案的真實絕對路徑
            # 注意：result_url 通常是 '/media/...'，我們需要把它轉成 '/app/media/...'
            if result_url.startswith('/media/'):
                # 去掉開頭的 /media/，然後跟 MEDIA_ROOT 接起來
                real_file_path = os.path.join(settings.MEDIA_ROOT, result_url.replace('/media/', '', 1))
            else:
                # 如果回傳的已經是檔名，直接接
                real_file_path = os.path.join(settings.MEDIA_ROOT, result_url)

            # 6. [關鍵修改] 將圖片轉回 Base64
            processed_base64 = image_to_base64(real_file_path)

            if not processed_base64:
                 return JsonResponse({'status': 'error', '2message': 'Result file not found'}, status=500)

            # 7. 回傳結果 (完全依照 Excel 截圖的格式)
            return JsonResponse({
                "code": 200, 
                "message": "success",
                "data": {
                    # 這裡就是 Excel 要求的欄位名稱
                    "clothes_image_processed_base64": processed_base64
                }
            }, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message222': str(e)}, status=500)

# --- 3. 正式 API: 試穿 (同樣修改為回傳 Base64) ---
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request):
        try:
            data = json.loads(request.body)

            if 'model_image_base64' not in data or 'clothes_image_base64' not in data:
                return JsonResponse({'status': 'error', 'message': 'Missing base64 images'}, status=400)

            model_file = decode_base64_image(data['model_image_base64'], "temp_model")
            clothes_file = decode_base64_image(data['clothes_image_base64'], "temp_clothes")

            processor = AIProcessor()
            result_url = processor.virtual_try_on(model_file, clothes_file)

            # 取得真實路徑並轉碼
            if result_url.startswith('/media/'):
                real_file_path = os.path.join(settings.MEDIA_ROOT, result_url.replace('/media/', '', 1))
            else:
                real_file_path = os.path.join(settings.MEDIA_ROOT, result_url)
            
            result_base64 = image_to_base64(real_file_path)

            if not result_base64:
                 return JsonResponse({'status': 'error', 'messagessssss': 'Result file not found'}, status=500)

            return JsonResponse({
                "code": 201, # Excel 上是寫 200，這裡建議統一
                "message": "success",
                "data": {
                    "result_image_base64": result_base64
                }
            }, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)