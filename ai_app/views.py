import json
import logging
import time

from PIL import Image
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

# Service Layer：view 只負責 HTTP I/O，業務邏輯全在 services/
from .services.processing import AIProcessor  # ReconstructView 仍直接呼叫 (尚未抽 service)
from .services.remove_bg_service import RemoveBgService
from .services.try_on_service import TryOnService
from .services.reconstruct_3d_service import Reconstruct3DService, Reconstruct3DOptions
from .services.try_on_3d_service import TryOn3DService
from .models import HistoryRecord
from django.conf import settings

# ==========================================
# 1. 去背功能 (Remove Background)
# 此 View 負責處理單張衣服圖片的去背、磨皮及風格分析
# ==========================================

# 取得日誌記錄器
logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class RemoveBgView(View):
    """1. 去背功能 (Remove Background)

    View 層只負責：
      ① 解析 multipart 取出 clothes_image
      ② 基本驗證（檔案是否存在、是否為圖片）
      ③ 呼叫 RemoveBgService 跑業務邏輯
      ④ 把 service 回傳結果包成 HTTP response

    所有「去背 + 風格分析」業務邏輯都在 services/remove_bg_service.py。
    """
    _CODE_MAP = {
        "1200": (200, ""),
        "1400": (400, "缺少衣服圖片，請重新上傳"),
        "1410": (422, "請在光線明亮/對焦清楚處重拍"),
        "1415": (415, "僅支援 JPG/PNG/WEBP 格式"),
        "1420": (422, "請上傳比例正常的圖片"),
        "1422": (422, "圖片品質過低，請上傳清晰照片"),
        "1423": (422, "背景與主體對比不足，請重拍"),
        "1500": (500, "去背失敗，請稍後再試"),
        "1501": (500, "風格分析失敗，請稍後再試"),
        "1510": (422, "AI 偵測不到完整衣物，請確認照片內容"),
    }

    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G2] 接收到去背請求 ---")

        # ① 解析輸入
        clothes_image = request.FILES.get('clothes_image')
        if not clothes_image:
            return JsonResponse({"code": 400, "message": "1400"}, status=400)
        if not clothes_image.content_type.startswith('image/'):
            return JsonResponse({"code": 415, "message": "1415"}, status=415)

        # ② 開圖
        try:
            input_pil = Image.open(clothes_image).convert("RGBA")
        except Exception as e:
            logger.exception(f"💥 [G2] 圖片開啟失敗: {e}")
            return JsonResponse({
                "code": 500, "message": "1500",
                "debug_info": {"error_detail": f"無法開啟圖片: {e}"},
            }, status=500)

        # ③ 呼叫 Service
        result = RemoveBgService().process(input_pil)

        # ④ 結果 → HTTP response
        if not result.ok:
            return self._fail_response(result.code, result.error_detail, result.diagnosis)

        return self._success_response(result, start_time)

    # ---- helpers ----
    def _fail_response(self, code, detail, diagnosis):
        http_status, default_detail = self._CODE_MAP.get(code, (500, "未知錯誤"))
        detail = detail or default_detail
        logger.warning(f"❌ [G2] 失敗 message={code} http={http_status} detail={detail}")
        ui_behavior = (diagnosis or {}).get("ui_behavior") or detail
        payload = {
            "code": http_status,
            "message": int(code),
            "debug_info": {"ui_behavior": ui_behavior},
        }
        return JsonResponse(payload, status=http_status)

    def _success_response(self, result, start_time):
        analysis_data = {
            "code": 200,
            "message": "1200",
            "data": {
                "file_name": result.file_name,
                "style_analysis": result.style_analysis,
            }
        }
        
        # 如果有多部位拆解，將檔名加入 JSON 讓前端知道
        if result.extracted_items_data:
            analysis_data["data"]["extracted_items"] = {k: v["file_name"] for k, v in result.extracted_items_data.items()}
            
        json_pretty = json.dumps(analysis_data, indent=4, ensure_ascii=False)
        boundary = 'bg_removal_boundary'
        body = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="analysis"\r\nContent-Type: application/json\r\n\r\n{json_pretty}\r\n'.encode('utf-8'),
            f'--{boundary}\r\nContent-Disposition: form-data; name="processed_image"; filename="{result.file_name}"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8'),
        ]
        with open(result.file_path, 'rb') as f:
            body.append(f.read())
        body.append(b'\r\n')
        
        # 附加多部位的實體圖片檔案 (例如 name="upper" / name="lower")
        if result.extracted_items_data:
            for part_name, part_data in result.extracted_items_data.items():
                body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{part_name}"; filename="{part_data["file_name"]}"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8'))
                with open(part_data["file_path"], 'rb') as f:
                    body.append(f.read())
                body.append(b'\r\n')
                
        body.append(f'--{boundary}--\r\n'.encode('utf-8'))

        logger.info(f"🎉 [G2] 去背完成！總耗時: {time.time()-start_time:.2f}s")
        return HttpResponse(b''.join(body), content_type=f'multipart/form-data; boundary={boundary}')
# ==========================================
# 2. 虛擬試穿 (Virtual Try-On)
# 此 View 負責接收模特兒與衣服，執行合成任務
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryCombineView(View):
    """2. 虛擬試穿 (Virtual Try-On)

    View 層只負責 HTTP I/O；業務邏輯在 services/try_on_service.py。
    """
    _CODE_MAP = {
        "2200": (200, ""),
        "2400": (400, "參數不完整或格式錯誤，請檢查輸入"),
        "2422": (422, "未偵測到人物，請上傳清楚的人像照"),
        "2500": (500, "AI 分析服務異常，請稍後再試"),
        "2501": (500, "AI 合成或系統錯誤，請稍後再試"),
    }

    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G3] 接收到虛擬試穿請求 ---")

        # ① 解析 + 驗證輸入
        model_image = request.FILES.get('model_image')
        garment_images = request.FILES.getlist('garment_images')
        try:
            data = json.loads(request.POST.get('data', '{}'))
        except json.JSONDecodeError as e:
            return self._fail("2400", f"JSON 格式錯誤: {e}")

        if not model_image or not garment_images:
            return self._fail("2400", "缺少必要圖片檔案 (model_image 或 garment_images)")

        garments_info = data.get('garments', [])
        if len(garment_images) != len(garments_info):
            return self._fail("2400",
                              f"圖片數量({len(garment_images)})與資訊數量({len(garments_info)})不匹配")

        # 重置 file pointer（service 可能不會做）
        model_image.seek(0)
        for g in garment_images:
            g.seek(0)

        # ② 呼叫 Service
        result = TryOnService().synthesize(model_image, garment_images, data)

        # ③ 結果 → HTTP response
        if not result.ok:
            return self._fail(result.code, result.error_detail)

        return self._success_response(result, start_time)

    # ---- helpers ----
    def _fail(self, code, detail):
        http_status, default_detail = self._CODE_MAP.get(code, (500, "未知錯誤"))
        detail = detail or default_detail
        logger.warning(f"❌ [G3] 失敗 message={code} http={http_status} detail={detail}")
        return JsonResponse({
            "code": http_status,
            "message": code,
            "debug_info": {"error_detail": detail},
        }, status=http_status)

    def _success_response(self, result, start_time):
        analysis_data = {
            "code": 200,
            "message": "2200",
            "data": {
                "file_name": result.file_name,
                "style_name": result.style_name,
                "file_format": "PNG",
            }
        }
        boundary = 'frame_boundary'
        body = []
        body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
        body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
        body.append(b'\r\n')
        body.append(f'--{boundary}\r\nContent-Type: image/png\r\n'.encode('utf-8'))
        body.append(f'Content-Disposition: attachment; filename="{result.file_name}"\r\n\r\n'.encode('utf-8'))
        with open(result.file_path, 'rb') as f:
            body.append(f.read())
        body.append(b'\r\n')
        body.append(f'--{boundary}--\r\n'.encode('utf-8'))

        logger.info(f"🎉 [G3] 虛擬試穿完成！總耗時: {time.time()-start_time:.2f}s")
        return HttpResponse(b''.join(body), content_type=f'multipart/mixed; boundary={boundary}')
        


@method_decorator(csrf_exempt, name='dispatch')
class ReconstructView(View):
    def post(self, request, *args, **kwargs):
        # ========== 步驟 1: 接收圖片 (對應 3400) ==========
        model_image = request.FILES.get('model_image')
        
        if not model_image:
            return JsonResponse({
                "code": 400,
                "message": "3400",
                "debug_info": {"error_detail": "缺少模特圖片，請重新上傳"}
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
                        "error_detail": err_msg or "請改上傳全身、四肢清楚的正面照"
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
                "debug_info": {"error_detail": f"系統忙碌中，請稍後再試: {str(e)}"}
            }, status=500)


# ==========================================
# 4. 3D 物理試穿 (Tripo image-to-3D)
# 輸入: model_image (單張圖)
# 輸出: multipart/mixed (JSON + .glb)
# 錯誤碼: 4200 / 4400 / 4422 / 4500
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class Reconstruct_3D(View):
    """4. 3D 物理試穿 (Tripo image-to-3D)

    View 層只負責 HTTP I/O；業務邏輯在 services/reconstruct_3d_service.py。
    """
    _CODE_MAP = {
        "4200": (200, ""),
        "4400": (400, "缺少必要參數，請重新上傳"),
        "4410": (402, "積分不足，請前往儲值頁面"),
        "4415": (415, "圖片格式不支援，請改用 JPG/PNG"),
        "4422": (422, "請改上傳全身、四肢清楚的正面照"),
        "4429": (429, "服務忙碌中，請稍後再試"),
        "4500": (500, "系統忙碌中，請稍後再試"),
    }

    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G4] 接收到 3D 重建請求 ---")

        # ① 解析 + 驗證
        model_image = request.FILES.get('model_image')
        if not model_image:
            return self._fail("4400", "缺少模特圖片，請重新上傳")

        # data JSON 選填
        data_str = request.POST.get('data', '').strip()
        if data_str:
            try:
                json.loads(data_str)  # 純驗證格式，內容暫未使用
            except json.JSONDecodeError as e:
                return self._fail("4400", f"Invalid JSON in 'data' field: {e}")

        # 開圖
        try:
            model_image.seek(0)
            pil_img = Image.open(model_image).convert("RGBA")
        except Exception as e:
            return self._fail("4500", f"無法開啟圖片: {e}")

        # 解析 3D 選項
        options = self._parse_options(request)

        # ② 呼叫 Service
        result = Reconstruct3DService().reconstruct(pil_img, options)

        # ③ 結果 → HTTP response
        if not result.ok:
            return self._fail(result.code, result.error_detail)

        if result.is_mock:
            logger.info("🎉 [G4] 3D 重建完成 (MOCK)")
        else:
            logger.info(f"🎉 [G4] 3D 重建完成！總耗時: {time.time()-start_time:.2f}s")
        return self._success_response(result)

    # ---- helpers ----
    @staticmethod
    def _parse_options(request) -> Reconstruct3DOptions:
        pbr_raw = request.POST.get('pbr')
        req_refine = request.POST.get('refine')
        return Reconstruct3DOptions(
            prompt=(request.POST.get('prompt') or '').strip() or None,
            negative_prompt=(request.POST.get('negative_prompt') or '').strip() or None,
            texture_quality=(request.POST.get('texture_quality') or '').strip() or None,
            face_limit=int(request.POST.get('face_limit')) if request.POST.get('face_limit') else None,
            pbr=None if pbr_raw is None else pbr_raw.lower() in ('1', 'true', 'yes'),
            style=(request.POST.get('style') or '').strip() or None,
            enable_refine=None if req_refine is None else req_refine.lower() in ('1', 'true', 'yes'),
            refine_face_limit=int(request.POST.get('refine_face_limit'))
                              if request.POST.get('refine_face_limit') else None,
        )

    def _fail(self, code, detail):
        http_status, default_detail = self._CODE_MAP.get(code, (500, "未知錯誤"))
        detail = detail or default_detail
        logger.error(f"❌ [G4] 失敗 message={code} http={http_status} detail={detail}")
        return JsonResponse({
            "code": http_status,
            "message": code,
            "debug_info": {"error_detail": detail},
        }, status=http_status)

    def _success_response(self, result):
        analysis_data = {
            "code": 200,
            "message": "4200",
            "data": {
                "file_name": result.file_name,
                "file_format": "GLB",
            }
        }
        boundary = 'frame_boundary'
        body = []
        body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
        body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
        body.append(b'\r\n')
        body.append(f'--{boundary}\r\nContent-Type: model/gltf-binary\r\n'.encode('utf-8'))
        body.append(f'Content-Disposition: attachment; filename="{result.file_name}"\r\n\r\n'.encode('utf-8'))
        body.append(result.glb_bytes)
        body.append(b'\r\n')
        body.append(f'--{boundary}--\r\n'.encode('utf-8'))
        return HttpResponse(b''.join(body), content_type=f'multipart/mixed; boundary={boundary}')


# ==========================================
# 6. 歷史紀錄查詢介面 (Frontend View)
# ==========================================
class HistoryPageView(View):
    """
    回傳歷史紀錄檢視的前端網頁 (目前為 Mock 資料展示)
    """
    def get(self, request, *args, **kwargs):
        return render(request, 'ai_app/history.html')


# ==========================================
# 7. 歷史紀錄 API (Fetch History)
# ==========================================
class HistoryApiView(View):
    def get(self, request, *args, **kwargs):
        operation = request.GET.get('operation', 'all')
        qs = HistoryRecord.objects.all()
        if operation != 'all':
            qs = qs.filter(operation=operation)
            
        data = []
        for record in qs[:50]:  # 暫時回傳最新 50 筆
            data.append({
                "id": record.id,
                "filter": record.operation,
                "type": {"remove_bg": "去背", "tryon_2d": "2D", "reconstruct_3d": "3D"}.get(record.operation, record.operation),
                "status": record.status,
                "title": f"任務 #{record.id}",
                "subtitle": "執行完成" if record.status == 'success' else "執行失敗",
                "createdAt": record.created_at.strftime('%Y-%m-%d %H:%M'),
                "start": record.start_ts.strftime('%Y-%m-%d %H:%M:%S') if record.start_ts else "",
                "end": record.end_ts.strftime('%Y-%m-%d %H:%M:%S') if record.end_ts else "",
                "duration": f"{record.exec_time_ms / 1000.0:.1f} s",
                "result": "輸出成功" if record.status == 'success' else "發生錯誤",
                "image": self._get_url(record.bucket, record.thumb_key),
                "largeImage": self._get_url(record.bucket, record.object_key),
                "response": record.response_json,
            })
        return JsonResponse({"results": data})
        
    def _get_url(self, bucket, key):
        if not key:
            return "https://via.placeholder.com/400?text=No+Image"
        endpoint = getattr(settings, 'MINIO_EXTERNAL_ENDPOINT', 'localhost:9002')
        if not endpoint.startswith('http'):
            endpoint = f"http://{endpoint}"
        return f"{endpoint}/{bucket}/{key}"


# ==========================================
# 5. 2D 試穿 + 3D 重建 一條龍 (TryOn3D Outfit)
# 輸入: model_image + garment_images[] + data (同 /fitting/generate)
# 輸出: multipart/mixed (JSON + .glb) (同 /fitting/tryon_3d_physics)
# 業務碼: 2xxx (2D 階段失敗) / 4xxx (3D 階段失敗) / 4200 (成功)
# ==========================================
@method_decorator(csrf_exempt, name='dispatch')
class TryOn3DOutfitView(View):
    """2D 試穿 + 3D 重建 一條龍 endpoint。

    View 層只負責 HTTP I/O；業務邏輯在 services/try_on_3d_service.py
    （內部組合 TryOnService 與 Reconstruct3DService，零複製代碼）。
    """
    _CODE_MAP = {
        "2200": (200, ""),
        "2400": (400, "參數不完整或格式錯誤，請檢查輸入"),
        "2422": (422, "未偵測到人物，請上傳清楚的人像照"),
        "2500": (500, "AI 分析服務異常，請稍後再試"),
        "2501": (500, "AI 合成或系統錯誤，請稍後再試"),
        "4200": (200, ""),
        "4400": (400, "缺少必要參數，請重新上傳"),
        "4410": (402, "積分不足，請前往儲值頁面"),
        "4415": (415, "圖片格式不支援，請改用 JPG/PNG"),
        "4422": (422, "請改上傳全身、四肢清楚的正面照"),
        "4429": (429, "服務忙碌中，請稍後再試"),
        "4500": (500, "系統忙碌中，請稍後再試"),
    }

    def post(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info("--- [G5] 接收到 2D+3D 一條龍請求 ---")

        # ① 解析 + 驗證輸入（同 TryCombineView）
        model_image = request.FILES.get('model_image')
        garment_images = request.FILES.getlist('garment_images')
        try:
            data = json.loads(request.POST.get('data', '{}'))
        except json.JSONDecodeError as e:
            return self._fail("2400", f"JSON 格式錯誤: {e}")

        if not model_image or not garment_images:
            return self._fail("2400", "缺少必要圖片檔案 (model_image 或 garment_images)")

        garments_info = data.get('garments', [])
        if len(garment_images) != len(garments_info):
            return self._fail("2400",
                              f"圖片數量({len(garment_images)})與資訊數量({len(garments_info)})不匹配")

        model_image.seek(0)
        for g in garment_images:
            g.seek(0)

        # 解析 3D 選項（沿用 Reconstruct_3D 同名方法）
        options = Reconstruct_3D._parse_options(request)

        # ② 呼叫一條龍 Service
        result = TryOn3DService().execute(model_image, garment_images, data, options)

        # ③ 結果 → HTTP response
        if not result.ok:
            return self._fail(result.code, result.error_detail)

        if result.is_mock:
            logger.info(f"🎉 [G5] 一條龍完成 (3D MOCK)！總耗時: {time.time()-start_time:.2f}s "
                        f"(2d={result.timings.get('2d', 0):.2f}s, 3d={result.timings.get('3d', 0):.2f}s)")
        else:
            logger.info(f"🎉 [G5] 一條龍完成！總耗時: {time.time()-start_time:.2f}s "
                        f"(2d={result.timings.get('2d', 0):.2f}s, 3d={result.timings.get('3d', 0):.2f}s)")
        return self._success_response(result)

    # ---- helpers ----
    def _fail(self, code, detail):
        http_status, default_detail = self._CODE_MAP.get(code, (500, "未知錯誤"))
        detail = detail or default_detail
        logger.warning(f"❌ [G5] 失敗 message={code} http={http_status} detail={detail}")
        return JsonResponse({
            "code": http_status,
            "message": code,
            "debug_info": {"error_detail": detail},
        }, status=http_status)

    def _success_response(self, result):
        analysis_data = {
            "code": 200,
            "message": "4200",
            "data": {
                "file_name": result.file_name,
                "file_format": "GLB",
                "style_name": result.style_name,  # 從 2D 階段帶上來
            }
        }
        boundary = 'frame_boundary'
        body = []
        body.append(f'--{boundary}\r\nContent-Type: application/json\r\n\r\n'.encode('utf-8'))
        body.append(json.dumps(analysis_data, indent=2, ensure_ascii=False).encode('utf-8'))
        body.append(b'\r\n')
        body.append(f'--{boundary}\r\nContent-Type: model/gltf-binary\r\n'.encode('utf-8'))
        body.append(f'Content-Disposition: attachment; filename="{result.file_name}"\r\n\r\n'.encode('utf-8'))
        body.append(result.glb_bytes)
        body.append(b'\r\n')
        body.append(f'--{boundary}--\r\n'.encode('utf-8'))
        return HttpResponse(b''.join(body), content_type=f'multipart/mixed; boundary={boundary}')