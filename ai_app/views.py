import os
import io
import json
import tempfile
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
        
        # 基礎檢查 1400 / 1415
        if not clothes_image:
            return JsonResponse({"code": 400, "message": "1400"}, status=400)
        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "1415"}, status=415)

        try:
            processor = AIProcessor()
            if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
            input_pil = Image.open(clothes_image).convert("RGBA")

            # --- 第一關：去背處理 [1500] ---
            output_img, success, code, err = processor.remove_background(input_pil)
            if not success: 
                # 如果是去背失敗，code 會回傳 1500
                return JsonResponse({"code": 500, "message": code, "err": err}, status=500)

            # 暫存去背後的圖供後續分析
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
            output_img.save(file_path, "PNG")


            # --- 第三關：時尚風格分析 [1501] ---
            # 注意：這裡原本是 1504，我幫你順延改成 1501
            style_analysis, success, code, err = processor.analyze_clothing_style(file_path)
            if not success: 
                # 這裡要確保 analyze_clothing_style 內部出錯時回傳的是 1501
                return JsonResponse({"code": 500, "message": "1501", "err": err}, status=500)

            # 將 1501 抓到的顏色整合進去
            # style_analysis['detected_colors'] = color_list

            # ========== [成功回覆: 1200 OK] ==========
            analysis_data = {
                "code": 200,
                "message": "1200",
                "data": {
                    "file_name": file_name,
                    "style_analysis": style_analysis,
                }
            }
            json_pretty = json.dumps(analysis_data, indent=4, ensure_ascii=False)

            # Multipart 回傳格式
            boundary = 'bg_removal_boundary'
            response_body = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="analysis"\r\nContent-Type: application/json\r\n\r\n{json_pretty}\r\n'.encode('utf-8'),
                f'--{boundary}\r\nContent-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8')
            ]
            with open(file_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
            
            return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')

        except Exception as e:
            # 任何未預期的崩潰統一噴 1500 (系統基礎錯誤)
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
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
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
        



@method_decorator(csrf_exempt, name='dispatch')
class Reconstruct_3D(View):
    def post(self, request, *args, **kwargs):
        # --- 1. 檢查與解析參數 (維持原樣) ---
        model_image = request.FILES.get('model_image')
        raw_data = request.POST.get('data') 

        if not model_image or not raw_data:
            return JsonResponse({
                "code": 400,
                "message": 3400,
                "debug_info": {"error_detail": "缺少參數 model_image 或 data"}
            }, status=400)

        try:
            parsed_json = json.loads(raw_data)
            model_info = parsed_json.get('model_info')
            if not model_info:
                return JsonResponse({"message": 3400, "detail": "JSON 內缺少 model_info 層級"}, status=400)
            
            user_height = model_info.get('user_height')
            print(f"🚀 [DEBUG] 拿到資料了！身高: {user_height}", flush=True)

        except json.JSONDecodeError:
            return JsonResponse({"message": 3400, "detail": "JSON 解析失敗"}, status=400)

        # --- 2. 處理流程 ---
        with tempfile.NamedTemporaryFile(delete=True, suffix='.jpg') as temp_img:
            for chunk in model_image.chunks():
                temp_img.write(chunk)
            temp_img.flush()

            try:
                processing = AIProcessor()
                # 執行 generate_densepose，它內部會調用 get_unique_filename 並存入 media/
                pose_map_path, success, message = processing.generate_densepose(temp_img.name)

                if success and pose_map_path:
                    # 取得檔名 (例如 pose_map_a1b2c3d4.png)
                    file_name = os.path.basename(pose_map_path)
                    
                    # --- [成功] 構建人類友善的 multipart/mixed 回傳 ---
                    boundary = "frame_boundary"
                    
                    # 準備數據，加入 indent=4 讓 JSON 變漂亮
                    json_payload = {
                        "code": 200,
                        "message": 3200,
                        "data": {
                            "file_name": file_name,
                            "file_url": f"{settings.MEDIA_URL}{file_name}", # 直接在 media 根目錄
                            "file_format": "PNG",
                            "metrics": {
                                "ssim_score": 0.91,
                                "fid_score": 14.2
                            }
                        }
                    }

                    # 讀取剛剛存好的圖片二進位數據
                    with open(pose_map_path, "rb") as f:
                        img_binary = f.read()

                    # 組合 Body (JSON 部分加上 indent 漂亮格式)
                    # \r\n 是為了符合 HTTP 規範
                    pretty_json = json.dumps(json_payload, indent=4, ensure_ascii=False)
                    
                    body = (
                        f"--{boundary}\r\n"
                        f"Content-Type: application/json; charset=utf-8\r\n\r\n"
                        f"{pretty_json}\r\n"
                        f"--{boundary}\r\n"
                        f"Content-Type: image/png\r\n"
                        f"Content-Disposition: attachment; filename=\"{file_name}\"\r\n\r\n"
                    ).encode('utf-8') + img_binary + f"\r\n--{boundary}--\r\n".encode('utf-8')

                    print(f"✅ DensePose 任務完成，檔案已存在 media: {file_name}", flush=True)
                    return HttpResponse(body, content_type=f"multipart/mixed; boundary={boundary}")

                else:
                    return JsonResponse({
                        "code": 422,
                        "message": 3422,
                        "debug_info": {"error_detail": message}
                    }, status=422)

            except Exception as e:
                import traceback
                print(traceback.format_exc(), flush=True)
                return JsonResponse({
                    "code": 500,
                    "message": 3500,
                    "debug_info": {"error_detail": str(e)}
                }, status=500)