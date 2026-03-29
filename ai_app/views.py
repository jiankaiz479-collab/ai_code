import os
import io
import json
import cv2
import numpy as np  # 如果你有用到 np.array 也要補這行
import logging
from PIL import Image, ImageEnhance
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import uuid
# 從自定義的服務層導入 AI 處理核心
from .services.processing import AIProcessor

# 初始化 Django 日誌記錄器
logger = logging.getLogger(__name__)

# ==========================================
# 1. 去背功能 (Remove Background)
# 此 View 負責處理單張衣服圖片的去背、磨皮及風格分析
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    def post(self, request, *args, **kwargs):
        clothes_image = request.FILES.get('clothes_image')
        
        # [1400] & [1415] 基礎檢查
        if not clothes_image:
            return JsonResponse({"code": 400, "message": "1400"}, status=400)
        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "1415"}, status=415)

        try:
            processor = AIProcessor()
            if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
            input_pil = Image.open(clothes_image).convert("RGBA")

            # 1. 去背 [1500]
            output_img, success, code, _ = processor.remove_background(input_pil)
            if not success: return JsonResponse({"code": 500, "message": code}, status=500)

            # 2. 清晰度檢測 [1422]
            is_clear, _, code, _ = processor.check_image_blur(output_img)
            if not is_clear: return JsonResponse({"code": 422, "message": code}, status=422)

            # 準備磨皮所需的圖
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            gray_cv = cv2.cvtColor(np.array(rgb_img), cv2.COLOR_RGB2GRAY)
            
            # 3. 獲取遮罩 [1502]
            mask, success, code, _ = processor._get_semantic_ruffle_mask(rgb_img, gray_cv)
            if not success: return JsonResponse({"code": 500, "message": code}, status=500)

            # 4. 執行磨皮 [1503]
            smoothed_pil, success, code, _ = processor._opencv_smooth_fabric(rgb_img, mask)
            if not success: return JsonResponse({"code": 500, "message": code}, status=500)

            # 合併 A 通道並存檔
            final_output = Image.merge('RGBA', (*smoothed_pil.split(), a))
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
            final_output.save(file_path, "PNG")

            # 5. 提取顏色 [1501] (只檢查，不拿數據)
            _, success, code, _ = processor._extract_top_colors(file_path)
            if not success: return JsonResponse({"code": 500, "message": code}, status=500)

            # 6. 時尚風格分析 [1504]
            style_analysis, success, code, _ = processor.analyze_clothing_style(file_path)
            if not success: return JsonResponse({"code": 500, "message": code}, status=500)

            # [1200 OK] 封裝回傳內容 (格式化 JSON)
            analysis_data = {
                "code": 200,
                "message": "1200",
                "data": {
                    "file_name": file_name,
                    "file_format": "PNG",
                    "style_analysis": style_analysis
                }
            }
            # 使用 indent=4 讓 JSON 漂亮換行，ensure_ascii=False 確保中文或特殊字元不亂碼
            json_pretty = json.dumps(analysis_data, indent=4, ensure_ascii=False)

            # Multipart 回傳 (JSON + Image)
            boundary = 'bg_removal_boundary'
            response_body = []
            
            # Part 1: JSON 區塊
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(b'Content-Disposition: form-data; name="analysis"\r\n')
            response_body.append(b'Content-Type: application/json\r\n\r\n')
            response_body.append(json_pretty.encode('utf-8')) # 這裡就會是你要的一行一個參數
            response_body.append(b'\r\n')
            
            # Part 2: Image 區塊
            response_body.append(f'--{boundary}\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\n'.encode('utf-8'))
            response_body.append(b'Content-Type: image/png\r\n\r\n')
            with open(file_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')

        except Exception as e:
            return JsonResponse({"code": 500, "message": "1500", "debug": str(e)}, status=500)








# ==========================================
# 2. 虛擬試穿 (Virtual Try-On)
# 此 View 負責接收模特兒與衣服，執行合成任務
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # ========== 步驟 1: 數據與多檔案接收 ==========
        model_image = request.FILES.get('model_image')
        garment_images = request.FILES.getlist('garment_images') 

        try:
            data_str = request.POST.get('data', '{}')
            data = json.loads(data_str)
            garments_info = data.get('garments', [])
        except Exception as e:
            # 2400: 缺少輸入參數或格式錯誤
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": "JSON 格式錯誤，請檢查傳入的 data 欄位。"}
            }, status=400)

        # ========== 步驟 2: 指標重置與數量驗證 ==========
        if not model_image or not garment_images:
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": "缺少必要圖片檔案 (model_image 或 garment_images)。"}
            }, status=400)
        
        if len(garment_images) != len(garments_info):
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": f"圖片數量({len(garment_images)})與資訊數量({len(garments_info)})不匹配。"}
            }, status=400)

        try:
            processor = AIProcessor()
            model_image.seek(0)
            for g in garment_images: g.seek(0)

            # ========== 步驟 3: 款式分析 ==========
            # 若分析失敗通常歸類為 2500
            try:
                garments_ctx, consult_status = processor.tool_garment_analysis(garment_images, data)
                # 關鍵修正：檢查 tool_garment_analysis 是否回傳失敗狀態
                if consult_status == "fail":
                    return JsonResponse({
                        "code": 500,
                        "message": "2500",
                        "debug_info": {"suggest": garments_ctx.get('suggest', "AI 分析服務異常")}
                    }, status=500)
            except Exception as e:
                return JsonResponse({
                    "code": 500,
                    "message": "2500",
                    "debug_info": {"suggest": f"AI 分析服務異常: {str(e)}"}
                }, status=500)

            # ========== 步驟 4: 影像優化 ==========
            from PIL import Image
            pil_raw = Image.open(model_image).convert("RGB")

            # ========== 步驟 5: 核心合成 ==========
            # 正確拆解 Tuple: (result 字典, status 字串)
            result, status = processor.virtual_try_on(
                model_image=pil_raw,
                garments_ctx=garments_ctx,
                user_data=data
            )
            
            # 處理未偵測到人體 (2422) 或其他合成錯誤 (2501)
            if status == "fail":
                error_code = result.get('error_code', 2501)
                suggest = result.get('suggest', "AI 合成引擎異常")
                
                return JsonResponse({
                    "code": 422 if error_code == 2422 else 500,
                    "message": str(error_code),
                    "debug_info": {"suggest": suggest}
                }, status=422 if error_code == 2422 else 500)

            # ========== 步驟 6: 存檔與 Multipart 封裝 ==========
            final_image = result.get('result_image')
            import uuid, os
            from django.conf import settings
            
            file_name = f"try_on_outfit_{uuid.uuid4().hex[:8]}.png" 
            file_path = os.path.join(settings.MEDIA_ROOT, file_name)
            final_image.save(file_path, "PNG")

            # 構建成功回覆的 JSON
            analysis_data = {
                "code": 200,
                "message": "2200", 
                "data": {
                    "file_name": file_name,
                    "file_format": "PNG",
                    "items_processed": len(garment_images)
                }
            }

            # 構建 Multipart/mixed 響應
            boundary = 'frame_boundary'
            response_body = []
            response_body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}\r\nContent-Type: image/png\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: attachment; filename="{file_name}"\r\n\r\n'.encode('utf-8'))
            
            with open(file_path, 'rb') as f: 
                response_body.append(f.read())
            
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            return HttpResponse(b''.join(response_body), content_type=f'multipart/mixed; boundary={boundary}')

        except Exception as e:
            # 通用型系統錯誤 (對應 2501)
            return JsonResponse({
                "code": 500,
                "message": "2501",
                "debug_info": {"suggest": f"系統發生非預期錯誤: {str(e)}"}
            }, status=500)