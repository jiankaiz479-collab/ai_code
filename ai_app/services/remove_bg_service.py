"""衣服去背 + 風格分析完整流程 service。

責任：協調「去背 pipeline (rembg/legacy/robust)」與「Gemini 風格分析」並行執行，
      組裝統一回傳格式給 view。

設計：
  - 不依賴 Django request / response
  - 輸入純 Python 物件（PIL Image）
  - 失敗時回傳 RemoveBgServiceResult 含 code / error_detail / diagnosis
  - 可被 view、Celery task、unit test 共用
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

from .preprocessors import get_remove_bg_pipeline
from .processing import AIProcessor

logger = logging.getLogger(__name__)


@dataclass
class RemoveBgServiceResult:
    """RemoveBgService 的標準回傳格式。

    成功時：image / style_analysis / file_name / file_path / ok=True / code="1200"
    失敗時：ok=False / code="1xxx" / error_detail / diagnosis
    """
    image: Optional[Image.Image] = None
    extracted_items_data: dict = field(default_factory=dict)
    style_analysis: Optional[dict] = None
    file_name: str = ""
    file_path: str = ""
    ok: bool = False
    code: str = ""
    error_detail: Optional[str] = None
    diagnosis: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)


class RemoveBgService:
    """衣服去背 + 風格分析完整流程（並行）。"""

    def __init__(self, processor: Optional[AIProcessor] = None):
        self.processor = processor or AIProcessor()

    def process(self, pil_image: Image.Image) -> RemoveBgServiceResult:
        result = RemoveBgServiceResult()
        t_start = time.time()

        try:
            pipeline = get_remove_bg_pipeline(self.processor)

            with ThreadPoolExecutor(max_workers=2) as executor:
                fut_bg = executor.submit(self._timed, pipeline.process, pil_image)
                fut_style = executor.submit(self._timed, self.processor.analyze_clothing_style, pil_image)

                bg_result, bg_dt = fut_bg.result()
                (style, ok_style, _code_style, err_style), style_dt = fut_style.result()

            result.timings = {"bg": bg_dt, "style": style_dt}

            if not bg_result.ok:
                result.code = bg_result.code
                result.error_detail = bg_result.error_detail
                result.diagnosis = bg_result.diagnosis or {}
                return result

            if not ok_style:
                result.code = "1501"
                result.error_detail = err_style
                return result

            # 落地
            # 檢查是否有切出多個部位 (robust_v3)
            if hasattr(bg_result, "extracted_items") and bg_result.extracted_items:
                for part_name, part_img in bg_result.extracted_items.items():
                    p_name, p_path = self.processor.get_unique_filename(prefix=part_name, ext="png")
                    part_img.save(p_path, "PNG")
                    result.extracted_items_data[part_name] = {"file_name": p_name, "file_path": p_path}

            # 處理主圖 (可能是單件，也可能是從 extracted_items fall_back 出來的上衣)
            output_img = bg_result.image
            file_name, file_path = self.processor.get_unique_filename(prefix="processed", ext="png")
            if output_img:
                output_img.save(file_path, "PNG")

            result.image = output_img
            result.style_analysis = style
            result.file_name = file_name
            result.file_path = file_path
            result.ok = True
            result.code = "1200"

        except Exception as e:
            logger.exception(f"💥 RemoveBgService 失敗: {str(e)}")
            result.code = "1500"
            result.error_detail = f"系統發生非預期錯誤: {str(e)}"

        result.timings["total"] = time.time() - t_start
        return result

    @staticmethod
    def _timed(fn, *args, **kwargs):
        t = time.time()
        return fn(*args, **kwargs), time.time() - t
