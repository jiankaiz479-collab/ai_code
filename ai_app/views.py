import os
import io
import json
import tempfile
import numpy as np  # 如果你有用到 np.array 也要補這行
import logging
import requests  # 加上這一行
from PIL import Image, ImageEnhance
from django.conf import settings
from concurrent.futures import ThreadPoolExecutor
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import uuid
# 從自定義的服務層導入 AI 處理核心
from .services.processing import AIProcessor
import time  # 👈 就是漏掉這一行！

# ==========================================
# 1. 去背功能 (Remove Background)
# 此 View 負責處理單張衣服圖片的去背、磨皮及風格分析
# ==========================================

# 取得日誌記錄器
logger = logging.getLogger(__name__)


def _timed(fn, *args, **kwargs):
    t = time.time()
    return fn(*args, **kwargs), time.time() - t
@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    """
    1. 去背功能 (Remove Background)
    加速邏輯：採用 ThreadPoolExecutor 讓去背與 Gemini 分析並行執行。
    """
    def _run_parallel(self, processor, input_pil):
        """並行執行核心：去背與分析同時啟動"""
        t_total = time.time()
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 同時發送去背與分析請求
            fut_bg = executor.submit(_timed, processor.remove_background, input_pil)
            fut_style = executor.submit(_timed, processor.analyze_clothing_style, input_pil)
            
            # 獲取結果
            (output_img, ok_bg, code_bg, err_bg), bg_dt = fut_bg.result()
            (style, ok_style, code_style, err_style), style_dt = fut_style.result()
            
        total_dt = time.time() - t_total
        
        if not ok_bg:
            raise RuntimeError(f"1500|{code_bg}|{err_bg}")
        if not ok_style:
            raise RuntimeError(f"1501|1501|{err_style}")

        # 處理完成後才進行 IO 存檔
        file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
        output_img.save(file_path, "PNG")

        return output_img, style, file_name, file_path, {
            "bg": bg_dt, "style": style_dt, "total": total_dt
        }

    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G2] 接收到去背請求 (並行加速模式) ---")

        clothes_image = request.FILES.get('clothes_image')
        if not clothes_image:
            return JsonResponse({"code": 400, "message": "1400"}, status=400)
        
        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "1415"}, status=415)

        try:
            processor = AIProcessor()
            input_pil = Image.open(clothes_image).convert("RGBA")

            # 執行並行加速處理
            output_img, style_analysis, file_name, file_path, timings = self._run_parallel(processor, input_pil)

            # 封裝 JSON
            analysis_data = {
                "code": 200,
                "message": "1200",
                "data": {
                    "file_name": file_name,
                    "style_analysis": style_analysis,
                }
            }
            json_pretty = json.dumps(analysis_data, indent=4, ensure_ascii=False)

            # 封裝 Multipart 響應
            boundary = 'bg_removal_boundary'
            response_body = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="analysis"\r\nContent-Type: application/json\r\n\r\n{json_pretty}\r\n'.encode('utf-8'),
                f'--{boundary}\r\nContent-Disposition: form-data; name="processed_image"; filename="{file_name}"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8')
            ]
            with open(file_path, 'rb') as f:
                response_body.append(f.read())
            response_body.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))

            logger.info(f"🎉 去背完成！總耗時: {time.time()-start_time:.2f}s (並行節省了約 {min(timings['bg'], timings['style']):.2f}s)")
            return HttpResponse(b''.join(response_body), content_type=f'multipart/form-data; boundary={boundary}')

        except Exception as e:
            logger.exception(f"💥 處理崩潰: {str(e)}")
            return JsonResponse({"code": 500, "message": "1500", "debug": str(e)}, status=500)
# ==========================================
# 2. 虛擬試穿 (Virtual Try-On)
# 此 View 負責接收模特兒與衣服，執行合成任務
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    def post(self, request, *args, **kwargs):
        # 紀錄整體流程開始時間
        start_time = time.time()
        logger.info("--- [G3] 開始執行虛擬試穿合成流程 (TryCombineView POST) ---")

        # ========== 步驟 1: 數據與多檔案接收 ==========
        model_image = request.FILES.get('model_image')
        garment_images = request.FILES.getlist('garment_images') 

        try:
            data_str = request.POST.get('data', '{}')
            data = json.loads(data_str)
            garments_info = data.get('garments', [])
            logger.info(f"✅ [G3] JSON 解析成功: 收到 {len(garments_info)} 件衣物資訊")
        except Exception as e:
            # 2400: 缺少輸入參數或格式錯誤
            logger.error(f"❌ [G3] JSON 解析失敗: {str(e)}")
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": "JSON 格式錯誤，請檢查傳入的 data 欄位。"}
            }, status=400)

        # ========== 步驟 2: 指標重置與數量驗證 ==========
        if not model_image or not garment_images:
            logger.warning("❌ [G3] 基礎檢查失敗: 缺少 model_image 或 garment_images")
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": "缺少必要圖片檔案 (model_image 或 garment_images)。"}
            }, status=400)
        
        if len(garment_images) != len(garments_info):
            logger.warning(f"❌ [G3] 數量不匹配: 圖片({len(garment_images)}) vs 資訊({len(garments_info)})")
            return JsonResponse({
                "code": 400,
                "message": "2400",
                "debug_info": {"suggest": f"圖片數量({len(garment_images)})與資訊數量({len(garments_info)})不匹配。"}
            }, status=400)

        try:
            logger.info("⏳ [G3] 初始化 AIProcessor 並進行圖片指標重置...")
            processor = AIProcessor()
            model_image.seek(0)
            for g in garment_images: 
                g.seek(0)
            logger.info(f"✅ [G3] 指標重置完成，Model 圖片名稱: {model_image.name}")

            # ========== 步驟 3: 款式分析 ==========
            # 這是第一階段 AI 分析
            logger.info("⏳ [G3] 啟動 tool_garment_analysis (衣物風格分析)...")
            analysis_start = time.time()
            try:
                garments_ctx, consult_status = processor.tool_garment_analysis(garment_images, data)
                
                # 關鍵修正：檢查 tool_garment_analysis 是否回傳失敗狀態
                if consult_status == "fail":
                    logger.error("❌ [G3] tool_garment_analysis 回傳失敗狀態")
                    return JsonResponse({
                        "code": 500,
                        "message": "2500",
                        "debug_info": {"suggest": garments_ctx.get('suggest', "AI 分析服務異常")}
                    }, status=500)
                
                logger.info(f"✅ [G3] Style 分析完成，耗時: {round(time.time() - analysis_start, 2)}s")
            except Exception as e:
                logger.exception(f"💥 [G3] Style 分析階段發生未預期錯誤: {str(e)}")
                return JsonResponse({
                    "code": 500,
                    "message": "2500",
                    "debug_info": {"suggest": f"AI 分析服務異常: {str(e)}"}
                }, status=500)

            # ========== 步驟 4: 影像優化 ==========
            logger.info("⏳ [G3] 正在轉換 Model 圖片為 RGB 格式...")
            pil_raw = Image.open(model_image).convert("RGB")

            # ========== 步驟 5: 核心合成 ==========
            logger.info("🚀 [G3] 開始核心合成流程 (virtual_try_on)...")
            vto_start = time.time()
            
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
                
                logger.warning(f"⚠️ [G3] 核心合成回傳失敗: 代碼 {error_code}, 建議: {suggest}")
                
                return JsonResponse({
                    "code": 422 if error_code == 2422 else 500,
                    "message": str(error_code),
                    "debug_info": {"suggest": suggest}
                }, status=422 if error_code == 2422 else 500)

            # ========== 步驟 6: 存檔 ==========
            logger.info("⏳ [G3] 合成成功，正在處理最終圖片存檔...")
            final_image = result.get('result_image') 
            file_name, file_path = processor.get_unique_filename(prefix="processed", ext="png")
            final_image.save(file_path, "PNG")
            logger.info(f"✅ [G3] 存檔完成: {file_name}")

            # ========== 步驟 7: 合成後的風格分析 ==========
            # 呼叫分析函式，若失敗 code 為 "2504"
            logger.info("⏳ [G3] 啟動合成後穿搭風格分析 (mode=outfit)...")
            style_result, outfit_success, outfit_code, outfit_err = processor.analyze_clothing_style(final_image, mode="outfit")

            # 2. 處理 style_name (確保拿到的是整個列表)
            if outfit_success:
                logger.info(f"✅ [G3] 穿搭分析成功: {style_result.get('style_name')}")
                # 這裡的 style_result 內容現在只有 {"style_name": [...]}
                main_style = style_result.get("style_name", ["Casual"])
            else:
                # 如果分析失敗 (2504)，給一個標示失敗的列表，並記錄 Error
                logger.error(f"❌ [G3] 穿搭分析失敗: 代碼 {outfit_code}, 原因: {outfit_err}")
                main_style = ["Unknown"]

            # 3. 放入 JSON 回傳結構
            analysis_data = {
                "code": 200,
                "message": "2200", 
                "data": {
                    "file_name": file_name,
                    "style_name": main_style,
                    "file_format": "PNG"
                }
            }
            
            # 如果分析失敗 (outfit_success 為 False)，將 2504 資訊放入 debug_info 供前端參考
            if not outfit_success:
                analysis_data["debug_info"] = {
                    "suggest": f"穿搭風格分析失敗 (錯誤碼: {outfit_code})",
                    "error_detail": outfit_err
                }

            # ========== 步驟 8: 構建 Multipart/mixed 響應 ==========
            logger.info(f"⏳ [G3] 正在封裝最終響應 (Boundary: frame_boundary)...")
            boundary = 'frame_boundary'
            response_body = []
            
            # JSON 部分
            response_body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            
            # 圖片部分
            response_body.append(f'--{boundary}\r\nContent-Type: image/png\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: attachment; filename="{file_name}"\r\n\r\n'.encode('utf-8'))
            
            with open(file_path, 'rb') as f: 
                response_body.append(f.read())
            
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))
            
            # 計算整體流程時間
            duration = round(time.time() - start_time, 2)
            logger.info(f"🎉 [G3] 虛擬試穿流程圓滿結束！總耗時: {duration}s")
            
            return HttpResponse(b''.join(response_body), content_type=f'multipart/mixed; boundary={boundary}')

        except Exception as e:
            # 通用型系統錯誤 (對應 2501)，記錄完整 Traceback
            logger.exception(f"💥 [G3] 流程發生非預期崩潰 (2501): {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": "2501",
                "debug_info": {"suggest": f"系統發生非預期錯誤: {str(e)}"}
            }, status=500)
        


@method_decorator(csrf_exempt, name='dispatch')
class ReconstructView(View):
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
            # 讀取圖片並確保是 RGBA
            pil_img = Image.open(model_image).convert("RGBA")

            # ========== 步驟 2: 執行 2D 去背與標準化 (修正邏輯) ==========
            # 1. 執行去背
            result, status, code, err_msg = processor.remove_background(pil_img)
            
            # 2. 獲取去背後的 Image 對象 (假設 result 裡面存的是 PIL Image)
            # 這裡要確保傳給下一個函數的是去背後的圖片
            no_bg_img = result.get('processed_image') if isinstance(result, dict) else result

            # 3. 執行正方形標準化 (5% 留白, 1024x1024)
            # 注意：這裡我們把產出的結果接給 final_image
            final_image = processor.compose_square_portrait(no_bg_img, top_bottom_ratio=0.05, output_size=1024)

            # ========== 步驟 3: 錯誤處理 (對應 3422) ==========
            # 檢查 final_image 是否生成成功，以及 status 是否為完整人體
            if final_image is None or status == "incomplete_body":
                return JsonResponse({
                    "code": 422,
                    "message": "3422",
                    "debug_info": {
                        "error_detail": err_msg or "Please ensure your full body and all limbs are visible in the photo."
                    }
                }, status=422)

            # ========== 步驟 4: 存檔與構建 Multipart 回傳 (對應 3200) ==========
            # 這裡使用的是經過 compose_square_portrait 處理後的 final_image
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

            # 構建 Multipart/mixed 響應 (維持原有的高品質回傳格式)
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
            return JsonResponse({
                "code": 500,
                "message": "3500",
                "debug_info": {"error_detail": f"Server crash or heavy load: {str(e)}"}
            }, status=500)