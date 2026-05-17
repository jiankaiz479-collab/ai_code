"""2D 虛擬試穿完整流程 service。

責任：協調衣物分析 → 模特圖預處理 → Gemini 合成 → 穿搭風格分析。

設計：
  - 不依賴 HTTP，輸入 PIL Image + dict
  - 回傳 TryOnResult dataclass
  - 可被 view、3D 一條龍 endpoint、CLI、unit test 共用
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Union
from django.utils import timezone
from django.conf import settings
from ai_app.models import HistoryRecord
from .storage_service import StorageService

from PIL import Image

from .processing import AIProcessor

logger = logging.getLogger(__name__)


@dataclass
class TryOnResult:
    """TryOnService 的標準回傳格式。

    成功時：image / style_name / file_name / file_path / ok=True / code="2200"
    失敗時：ok=False / code="2xxx" / error_detail
    """
    image: Optional[Image.Image] = None
    style_name: list = field(default_factory=list)
    file_name: str = ""
    file_path: str = ""
    ok: bool = False
    code: str = ""
    error_detail: Optional[str] = None
    timings: dict = field(default_factory=dict)


class TryOnService:
    """2D 虛擬試穿完整流程。"""

    def __init__(self, processor: Optional[AIProcessor] = None):
        self.processor = processor or AIProcessor()

    def synthesize(self,
                   model_image: Union[Image.Image, object],
                   garment_images: List,
                   data: dict) -> TryOnResult:
        """執行完整 2D 試穿流程。

        Args:
            model_image: PIL Image 或 file-like（會自動 convert("RGB")）
            garment_images: 衣物圖片列表（file-like 或 PIL Image）
            data: dict，含 model_info / garments

        Returns:
            TryOnResult: 統一格式結果
        """
        result = TryOnResult()
        t_start = time.time()
        start_ts = timezone.now()

        try:
            # 並行：衣物分析 + 模特圖預處理
            t1 = time.time()
            with ThreadPoolExecutor(max_workers=2) as executor:
                fut_garment = executor.submit(
                    self.processor.tool_garment_analysis, garment_images, data
                )
                fut_image = executor.submit(self._prepare_model_image, model_image)

                garments_ctx, consult_status = fut_garment.result()
                pil_raw = fut_image.result()
            result.timings["parallel"] = time.time() - t1

            if consult_status == "fail":
                result.code = "2500"
                result.error_detail = (garments_ctx or {}).get("suggest", "AI 分析服務異常")
                return result

            # 核心合成
            t2 = time.time()
            vto_result, status = self.processor.virtual_try_on(
                model_image=pil_raw,
                garments_ctx=garments_ctx,
                user_data=data,
            )
            result.timings["vto_core"] = time.time() - t2

            if status == "fail":
                error_code = vto_result.get("error_code", 2501)
                result.code = str(error_code)
                result.error_detail = vto_result.get("suggest", "AI 合成引擎異常")
                return result

            final_image = vto_result.get("result_image")
            if final_image is None:
                result.code = "2501"
                result.error_detail = "virtual_try_on 回傳結果缺少 result_image"
                return result

            # 落地
            file_name, file_path = self.processor.get_unique_filename(prefix="processed", ext="png")
            final_image.save(file_path, "PNG")

            # 穿搭風格分析（合成後）
            t3 = time.time()
            style_result, outfit_success, outfit_code, outfit_err = \
                self.processor.analyze_clothing_style(final_image, mode="outfit")
            result.timings["outfit_analysis"] = time.time() - t3

            if outfit_success:
                result.style_name = style_result.get("style_name", ["Casual"])
            else:
                logger.warning(f"穿搭分析失敗 code={outfit_code} err={outfit_err}")
                result.style_name = ["Unknown"]

            result.image = final_image
            result.file_name = file_name
            result.file_path = file_path
            result.ok = True
            result.code = "2200"

        except Exception as e:
            logger.exception(f"💥 TryOnService 失敗: {str(e)}")
            result.code = "2501"
            result.error_detail = f"系統發生非預期錯誤: {str(e)}"

        result.timings["total"] = time.time() - t_start
        end_ts = timezone.now()
        exec_time_ms = int((time.time() - t_start) * 1000)

        # 寫入歷史紀錄
        try:
            storage = StorageService()
            obj_key, thumb_key = storage.upload_image(result.image, prefix="tryon_2d") if result.image else (None, None)
            HistoryRecord.objects.create(
                operation="tryon_2d",
                status="success" if result.ok else "failed",
                bucket=getattr(settings, 'MINIO_BUCKET_HISTORY', 'history-images'),
                object_key=obj_key,
                thumb_key=thumb_key,
                response_json={"code": result.code, "data": {"style_name": result.style_name}} if result.ok else {"code": result.code, "error": result.error_detail},
                start_ts=start_ts,
                end_ts=end_ts,
                exec_time_ms=exec_time_ms
            )
        except Exception as e:
            logger.error(f"寫入 HistoryRecord 失敗: {e}")

        return result

    @staticmethod
    def _prepare_model_image(model_image) -> Image.Image:
        """確保拿到 RGB PIL Image。"""
        if isinstance(model_image, Image.Image):
            return model_image.convert("RGB")
        # file-like 物件：先重置指標再 open
        if hasattr(model_image, "seek"):
            model_image.seek(0)
        return Image.open(model_image).convert("RGB")
