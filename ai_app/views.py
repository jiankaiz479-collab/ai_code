import json
import base64
from django.shortcuts import render
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .services.processing import AIProcessor

# --- 輔助函式：把 Base64 轉成圖片檔案 ---
def decode_base64_image(base64_string, file_name):
    # 有時候 Base64 字串會包含 'data:image/png;base64,' 前綴，需要切掉
    if ';base64,' in base64_string:
        format, imgstr = base64_string.split(';base64,')
        ext = format.split('/')[-1]
    else:
        imgstr = base64_string
        ext = 'png' # 預設格式
        
    return ContentFile(base64.b64decode(imgstr), name=f"{file_name}.{ext}")

# --- 1. 測試頁面 View (解決 ImportError 的關鍵) ---
class DebugPageView(View):
    def get(self, request):
        # 顯示測試網頁
        return render(request, 'ai_app/debug_page.html')

    def post(self, request):
        #這是給自己測試用的，還是維持原本的檔案上傳方式
        if 'image' not in request.FILES:
            return render(request, 'ai_app/debug_page.html', {'error': '請選擇圖片'})
            
        processor = AIProcessor()
        # 直接呼叫服務 (Service)，不經過 API 驗證層
        result_url = processor.remove_background(request.FILES['image'])
        
        return render(request, 'ai_app/debug_page.html', {
            'result_url': result_url
        })

# --- 2. 正式 API: 去背 (符合 Excel 定義: JSON + Base64) ---
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    """
    API: /api/remove_bg
    Method: POST
    Content-Type: application/json
    """
    def post(self, request):
        try:
            # 解析 JSON
            data = json.loads(request.body)
            
            # 檢查欄位
            if 'clothes_image_base64' not in data:
                return JsonResponse({'status': 'error', 'message': 'Missing clothes_image_base64'}, status=400)

            # 轉檔 (Base64 -> File)
            image_file = decode_base64_image(data['clothes_image_base64'], "temp_clothes")

            # 呼叫服務
            processor = AIProcessor()
            result_url = processor.remove_background(image_file)

            # 回傳結果
            return JsonResponse({
                "code": 200, 
                "status": "success",
                "image_url": result_url
            }, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

# --- 3. 正式 API: 試穿 (符合 Excel 定義: JSON + Base64) ---
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    """
    API: /api/try_combine
    Method: POST
    Content-Type: application/json
    """
    def post(self, request):
        try:
            data = json.loads(request.body)

            if 'model_image_base64' not in data or 'clothes_image_base64' not in data:
                return JsonResponse({'status': 'error', 'message': 'Missing base64 images'}, status=400)

            model_file = decode_base64_image(data['model_image_base64'], "temp_model")
            clothes_file = decode_base64_image(data['clothes_image_base64'], "temp_clothes")

            processor = AIProcessor()
            result_url = processor.virtual_try_on(model_file, clothes_file)

            return JsonResponse({
                "code": 201,
                "status": "success",
                "result_url": result_url
            }, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)