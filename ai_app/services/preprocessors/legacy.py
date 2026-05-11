"""舊版去背流程：直接包裝 AIProcessor.remove_background()。

承諾：完全不改動原有邏輯，只是把舊行為包進新介面。
"""

import logging

from PIL import Image

from ..interfaces import RemoveBgPipeline, RemoveBgResult

logger = logging.getLogger(__name__)


class LegacyRemoveBg(RemoveBgPipeline):
    """v0：原本就在跑的 rembg + u2net 邏輯。"""

    def __init__(self, processor):
        self.processor = processor

    @property
    def name(self) -> str:
        return "legacy"

    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        output_img, ok, code, err = self.processor.remove_background(pil_image)
        return RemoveBgResult(
            image=output_img,
            ok=ok,
            code=code if ok else (code or "1500"),
            error_detail=err,
        )
