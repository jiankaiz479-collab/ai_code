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
           # ========== 步驟 6: 存檔 ==========
            final_image = result.get('result_image') 
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
            final_image.save(file_path, "PNG")

            # ========== 步驟 7: 合成後的風格分析 ==========
            # 呼叫函式，這時如果出錯 code 會是 "2504"
            # 1. 執行分析
            style_result, outfit_success, outfit_code, outfit_err = processor.analyze_clothing_style(final_image, mode="outfit")

            # 2. 處理 style_name (確保拿到的是整個列表)
            if outfit_success:
                # 這裡的 style_result 內容現在只有 {"style_name": [...]}
                main_style = style_result.get("style_name", ["Casual"])
            else:
                # 如果分析失敗 (2504)，給一個標示失敗的列表
                main_style = ["Unknown"]

            # 3. 放入 JSON 回傳
            analysis_data = {
                "code": 200,
                "message": "2200", 
                "data": {
                    "file_name": file_name,
                    "style_name": main_style,
                    "file_format": "PNG"
                }
            }
            # 如果分析失敗 (outfit_success 為 False)，將 2504 放入 debug_info
            if not outfit_success:
                analysis_data["debug_info"] = {
                    "suggest": f"穿搭風格分析失敗 (錯誤碼: {outfit_code})",
                    "error_detail": outfit_err
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
        model_image = request.FILES.get('model_image')
        raw_data = request.POST.get('data') 

        if not model_image or not raw_data:
            return JsonResponse({"message": 3400, "detail": "缺少參數"}, status=400)

        try:
            processing=AIProcessor()
            parsed_json = json.loads(raw_data)
            model_info = parsed_json.get('model_info')
            print(f">>> [測試] 進入 View，準備呼叫重建...", flush=True)

            with tempfile.NamedTemporaryFile(delete=True, suffix='.jpg') as temp_img:
                for chunk in model_image.chunks():
                    temp_img.write(chunk)
                temp_img.flush()

                # --- 1. 呼叫重建函式拿資料 (影格列表) ---
                frames, success, message = processing.reconstruct_3d(temp_img.name, model_info)

                if success and frames:
                    # --- 2. 在 View 執行存檔功能 ---
                    # 使用你寫好的 get_unique_filename
                    file_name, final_save_path = processing.get_unique_filename(prefix="3d_reconstruct", ext="gif")
                    
                    # 執行存檔
                    imageio.mimsave(final_save_path, frames, fps=10, loop=0)
                    print(f"✅ [View 存檔成功] 檔案位置: {final_save_path}", flush=True)

                    # --- 3. 構建 Multipart 回傳 ---
                    boundary = "frame_boundary"
                    json_payload = {
                        "code": 200,
                        "message": 3200,
                        "data": {
                            "file_name": file_name,
                            "file_format": "GIF"
                        }
                    }

                    with open(final_save_path, "rb") as f:
                        img_binary = f.read()

                    # 人類方便看的格式 (Indent=2)
                    pretty_json = json.dumps(json_payload, indent=2, ensure_ascii=False)
                    
                    body = (
                        f"--{boundary}\r\n"
                        f"Content-Type: application/json\r\n\r\n"
                        f"{pretty_json}\r\n\r\n"
                        f"--{boundary}\r\n"
                        f"Content-Type: image/gif\r\n"
                        f"Content-Disposition: attachment; filename=\"{file_name}\"\r\n\r\n"
                    ).encode('utf-8') + img_binary + f"\r\n--{boundary}--\r\n".encode('utf-8')

                    return HttpResponse(body, content_type=f"multipart/mixed; boundary={boundary}")
                else:
                    return JsonResponse({"code": 422, "message": 3422, "detail": message}, status=422)

        except Exception as e:
            import traceback
            print(traceback.format_exc(), flush=True)
            return JsonResponse({"code": 500, "message": 3500, "detail": str(e)}, status=500)
        

@method_decorator(csrf_exempt, name='dispatch')
class SMPLReconstructView(View):
    def post(self, request, *args, **kwargs):
        # ========== 步驟 1: 接收圖片 (對應 3400) ==========
        model_image = request.FILES.get('model_image')
        
        if not model_image:
            return JsonResponse({
                "code": 400,
                "message": "3400",
                "debug_info": {"error_detail": "Missing model_image file."}
            }, status=400)

        try:
            processor = AIProcessor()
            model_image.seek(0)
            pil_img = Image.open(model_image).convert("RGB")

            # ========== 步驟 2: 執行 2D 去背 ==========
            # result 字典應包含 'processed_image' (去背後的 PIL Image)
            # status 用於判斷人物是否完整
            result, status = processor.remove_background_2d(pil_img)

            # ========== 步驟 3: 肢體不全錯誤處理 (對應 3422) ==========
            if status == "incomplete_body":
                return JsonResponse({
                    "code": 422,
                    "message": "3422",
                    "debug_info": {
                        "error_detail": "Please ensure your full body and all limbs are visible in the photo."
                    }
                }, status=422)

            # ========== 步驟 4: 存檔與回傳 (對應 3200) ==========
            final_image = result.get('processed_image')
            # 檔名改為 modules 前綴
            file_name, file_path = processor.get_unique_filename(prefix="modules", ext="png")
            final_image.save(file_path, "PNG")

            analysis_data = {
                "code": 200,
                "message": "3200",
                "data": {
                    "file_name": file_name,
                    "file_format": "PNG"
                }
            }

            # 構建 Multipart/mixed 響應
            boundary = 'frame_boundary'
            response_body = []
            response_body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
            response_body.append(json.dumps(analysis_data, indent=2).encode('utf-8'))
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}\r\nContent-Type: image/png\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: attachment; filename="{file_name}"\r\n\r\n'.encode('utf-8'))
            
            with open(file_path, 'rb') as f:
                response_body.append(f.read())
            
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))

            return HttpResponse(b''.join(response_body), content_type=f'multipart/mixed; boundary={boundary}')

        except Exception as e:
            # 系統崩潰處理 (對應 3500)
            return JsonResponse({
                "code": 500,
                "message": "3500",
                "debug_info": {"error_detail": f"Server crash or heavy load: {str(e)}"}
            }, status=500)        