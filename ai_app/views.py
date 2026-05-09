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
        timings = {}
        logger.info("--- [G3] 開始執行虛擬試穿合成流程 (TryCombineView POST) ---")

        # ========== 步驟 1: 數據與多檔案接收 ==========
        step1_start = time.time()
        model_image = request.FILES.get('model_image')
        garment_images = request.FILES.getlist('garment_images') 

        try:
            data_str = request.POST.get('data', '{}')
            data = json.loads(data_str)
            garments_info = data.get('garments', [])
            timings['json_parse'] = time.time() - step1_start
            logger.info(f"✅ [G3] JSON 解析成功: 收到 {len(garments_info)} 件衣物資訊 (耗時: {timings['json_parse']:.2f}s)")
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
            step2_start = time.time()
            processor = AIProcessor()
            model_image.seek(0)
            for g in garment_images: 
                g.seek(0)
            timings['init'] = time.time() - step2_start
            logger.info(f"✅ [G3] 指標重置完成，Model 圖片名稱: {model_image.name} (耗時: {timings['init']:.2f}s)")

            # ========== 步驟 3 & 4: 並行處理 (衣物分析 + 圖片轉換) ==========
            step3_start = time.time()
            logger.info("⏳ [G3] 啟動並行處理：衣物風格分析 + 模型圖片預處理...")
            
            def _analyze_garments():
                try:
                    return processor.tool_garment_analysis(garment_images, data)
                except Exception as e:
                    logger.exception(f"❌ 衣物分析異常: {str(e)}")
                    raise

            def _prepare_model_image():
                try:
                    return Image.open(model_image).convert("RGB")
                except Exception as e:
                    logger.exception(f"❌ 圖片轉換異常: {str(e)}")
                    raise

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    fut_garment = executor.submit(_analyze_garments)
                    fut_image = executor.submit(_prepare_model_image)
                    
                    # 等待兩個結果
                    garments_ctx, consult_status = fut_garment.result()
                    pil_raw = fut_image.result()
                
                # 檢查衣物分析結果
                if consult_status == "fail":
                    logger.error("❌ [G3] tool_garment_analysis 回傳失敗狀態")
                    return JsonResponse({
                        "code": 500,
                        "message": "2500",
                        "debug_info": {"suggest": garments_ctx.get('suggest', "AI 分析服務異常")}
                    }, status=500)
                
                timings['parallel_step'] = time.time() - step3_start
                logger.info(f"✅ [G3] 並行處理完成 (衣物分析 + 圖片轉換)，耗時: {timings['parallel_step']:.2f}s")
                
            except Exception as e:
                logger.exception(f"💥 [G3] 並行處理階段發生未預期錯誤: {str(e)}")
                return JsonResponse({
                    "code": 500,
                    "message": "2500",
                    "debug_info": {"suggest": f"AI 分析服務異常: {str(e)}"}
                }, status=500)

            # ========== 步驟 5: 核心合成 ==========
            step5_start = time.time()
            logger.info("🚀 [G3] 開始核心合成流程 (virtual_try_on)...")
            
            # 正確拆解 Tuple: (result 字典, status 字串)
            result, status = processor.virtual_try_on(
                model_image=pil_raw,
                garments_ctx=garments_ctx,
                user_data=data
            )
            
            timings['vto_core'] = time.time() - step5_start
            logger.info(f"⏳ [G3] 核心合成耗時: {timings['vto_core']:.2f}s")
            
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
            step7_start = time.time()
            logger.info("⏳ [G3] 啟動合成後穿搭風格分析 (mode=outfit)...")
            style_result, outfit_success, outfit_code, outfit_err = processor.analyze_clothing_style(final_image, mode="outfit")

            timings['outfit_analysis'] = time.time() - step7_start
            logger.info(f"⏳ [G3] 風格分析耗時: {timings['outfit_analysis']:.2f}s")

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
            timings['total'] = duration
            
            # 詳細時間分解日誌
            timing_details = " | ".join([f"{k}: {v:.2f}s" for k, v in timings.items()])
            logger.info(f"🎉 [G3] 虛擬試穿流程圓滿結束！")
            logger.info(f"   ⏱️ 時間分解: {timing_details}")
            logger.info(f"   📊 總耗時: {duration}s")
            
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


# ==========================================
# 4. 3D 物理試穿 (Tripo image-to-3D)
# 輸入: model_image (單張圖)
# 輸出: multipart/mixed (JSON + .glb)
# 錯誤碼: 4200 / 4400 / 4422 / 4500
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class Reconstruct_3D(View):
    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G4] 接收到 3D 重建請求 (Tripo) ---")

        # ========== 步驟 1: 接收圖片 (4400) ==========
        model_image = request.FILES.get('model_image')
        if not model_image:
            logger.warning("❌ [G4] 缺少 model_image")
            return JsonResponse({
                "code": 400,
                "message": "4400",
                "debug_info": {"error_detail": "Missing model_image file."}
            }, status=400)

        def _fail_response(code, err_msg):
            http_status = 422 if code == "4422" else 500
            logger.error(f"❌ [G4] 失敗: code={code}, err={err_msg}")
            return JsonResponse({
                "code": http_status,
                "message": code,
                "debug_info": {
                    "error_detail": err_msg or "Please ensure your full body and all limbs are visible in the photo."
                }
            }, status=http_status)

        try:
            processor = AIProcessor()
            model_image.seek(0)
            pil_img = Image.open(model_image).convert("RGBA")
            logger.info(f"✅ [G4] 圖片載入成功: name={model_image.name}, size={pil_img.size}")

            # ========== Step 1: 上傳圖片至 Tripo ==========
            t1 = time.time()
            logger.info("🚀 [G4] Step1: 上傳圖片至 Tripo...")
            file_token, st, code, err = processor.tripo_upload_image(pil_img)
            if st != "success":
                return _fail_response(code, err)
            logger.info(f"✅ [G4] Step1 完成: token={file_token[:12]}... (耗時 {time.time()-t1:.2f}s)")

            # ========== Step 2: 建立任務 (支援 prompt 等自然語言指令) ==========
            prompt = (request.POST.get('prompt') or '').strip() or None
            negative_prompt = (request.POST.get('negative_prompt') or '').strip() or None
            texture_quality = (request.POST.get('texture_quality') or '').strip() or None
            face_limit = request.POST.get('face_limit') or None
            pbr_raw = request.POST.get('pbr')
            pbr = None if pbr_raw is None else pbr_raw.lower() in ('1', 'true', 'yes')
            style = (request.POST.get('style') or '').strip() or None

            t2 = time.time()
            if prompt:
                logger.info(f"🚀 [G4] Step2: 建立 image_to_model 任務 (prompt='{prompt[:80]}')...")
            else:
                logger.info("🚀 [G4] Step2: 建立 image_to_model 任務 (無 prompt)...")
            if any([negative_prompt, texture_quality, face_limit, pbr is not None, style]):
                logger.info(f"   進階參數: neg='{negative_prompt}', tex_q={texture_quality}, "
                            f"face_limit={face_limit}, pbr={pbr}, style={style}")

            task_id, st, code, err = processor.tripo_create_task(
                file_token,
                prompt=prompt,
                negative_prompt=negative_prompt,
                texture_quality=texture_quality,
                face_limit=face_limit,
                pbr=pbr,
                style=style,
            )
            if st != "success":
                return _fail_response(code, err)
            logger.info(f"✅ [G4] Step2 完成: task_id={task_id} (耗時 {time.time()-t2:.2f}s)")

            # ========== Step 3: 輪詢任務 ==========
            t3 = time.time()
            logger.info(f"⏳ [G4] Step3: 輪詢任務 {task_id}...")
            def _on_progress(status, progress):
                logger.info(f"⏳ [G4] task={task_id} status={status} progress={progress}%")
            model_url, st, code, err = processor.tripo_poll_task(task_id, progress_cb=_on_progress)
            if st != "success":
                return _fail_response(code, err)
            logger.info(f"✅ [G4] Step3 完成: draft model_url 取得 (耗時 {time.time()-t3:.2f}s)")

            # ========== Step 3.5: Refine (200k 面精修，可關閉) ==========
            # 預設開啟；可由 .env TRIPO_ENABLE_REFINE=false 關閉，或 POST refine=false 覆蓋
            env_refine = os.getenv("TRIPO_ENABLE_REFINE", "true").lower() in ("1", "true", "yes")
            req_refine = request.POST.get("refine")
            if req_refine is not None:
                enable_refine = req_refine.lower() in ("1", "true", "yes")
            else:
                enable_refine = env_refine

            refine_face_limit = request.POST.get("refine_face_limit") or None

            if enable_refine:
                t35 = time.time()
                logger.info(f"🚀 [G4] Step3.5: 建立 Refine 任務 (face_limit={refine_face_limit or '預設200k'})...")
                refine_task_id, st, code, err = processor.tripo_create_refine_task(
                    draft_task_id=task_id,
                    face_limit=refine_face_limit,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    texture_quality=texture_quality,
                    pbr=pbr,
                )
                if st != "success":
                    return _fail_response(code, err)
                logger.info(f"   refine_task_id={refine_task_id}")

                def _on_refine_progress(status, progress):
                    logger.info(f"⏳ [G4] refine task={refine_task_id} status={status} progress={progress}%")

                refined_url, st, code, err = processor.tripo_poll_task(
                    refine_task_id, progress_cb=_on_refine_progress
                )
                if st != "success":
                    return _fail_response(code, err)
                model_url = refined_url
                logger.info(f"✅ [G4] Step3.5 完成: refined model_url 取得 (耗時 {time.time()-t35:.2f}s)")
            else:
                logger.info("⏭️ [G4] Step3.5: 已停用 Refine (使用 draft 模型)")

            # ========== Step 4: 下載 .glb ==========
            t4 = time.time()
            logger.info("🚀 [G4] Step4: 下載 .glb...")
            glb_bytes, st, code, err = processor.tripo_download_model(model_url)
            if st != "success":
                return _fail_response(code, err)
            logger.info(f"✅ [G4] Step4 完成: size={len(glb_bytes)} bytes (耗時 {time.time()-t4:.2f}s)")

            # ========== Step 5: 存檔 (.glb 落地到 media/3d/) ==========
            glb_dir = os.path.join(settings.MEDIA_ROOT, "tripo")
            os.makedirs(glb_dir, exist_ok=True)
            file_name = f"model3d_{uuid.uuid4().hex[:8]}.glb"
            file_path = os.path.join(glb_dir, file_name)
            with open(file_path, 'wb') as f:
                f.write(glb_bytes)
            file_size_kb = len(glb_bytes) / 1024
            logger.info(f"✅ [G4] Step5 存檔完成: {file_path} ({file_size_kb:.1f} KB)")

            # ========== Step 6: 構建 Multipart 響應 ==========
            analysis_data = {
                "code": 200,
                "message": "4200",
                "data": {
                    "file_name": file_name,
                    "file_format": "GLB"
                }
            }

            boundary = 'frame_boundary'
            response_body = []
            response_body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
            response_body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}\r\nContent-Type: model/gltf-binary\r\n'.encode('utf-8'))
            response_body.append(f'Content-Disposition: attachment; filename="{file_name}"\r\n\r\n'.encode('utf-8'))
            response_body.append(glb_bytes)
            response_body.append(b'\r\n')
            response_body.append(f'--{boundary}--\r\n'.encode('utf-8'))

            duration = round(time.time() - start_time, 2)
            logger.info(f"🎉 [G4] 3D 重建完成！總耗時: {duration}s")

            return HttpResponse(b''.join(response_body), content_type=f'multipart/mixed; boundary={boundary}')

        except Exception as e:
            logger.exception(f"💥 [G4] 3D 流程崩潰: {str(e)}")
            return JsonResponse({
                "code": 500,
                "message": "4500",
                "debug_info": {"error_detail": f"Server crash or heavy load: {str(e)}"}
            }, status=500)