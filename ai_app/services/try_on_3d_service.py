"""2D 試穿 + 3D 重建 一條龍 service。

把 TryOnService 跟 Reconstruct3DService 組合成一個流程，不重寫邏輯。
這就是 Service Layer 重構的回報：**新功能 = 既有 service 的組合**。

流程：
  ① TryOnService.synthesize(...) → 2D 合成圖 (PIL)
  ② Reconstruct3DService.reconstruct(2D 圖, ...) → .glb
  ③ 回傳 TryOn3DResult (含 GLB + 2D 階段的 style_name)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .reconstruct_3d_service import Reconstruct3DOptions, Reconstruct3DService
from .try_on_service import TryOnService

logger = logging.getLogger(__name__)


@dataclass
class TryOn3DResult:
    """一條龍流程的標準回傳格式。"""
    glb_bytes: Optional[bytes] = None
    file_name: str = ""
    file_path: str = ""
    style_name: list = field(default_factory=list)
    ok: bool = False
    code: str = ""
    error_detail: Optional[str] = None
    is_mock: bool = False
    timings: dict = field(default_factory=dict)


class TryOn3DService:
    """2D 試穿 → 3D 重建 一條龍。"""

    def __init__(self,
                 try_on_service: Optional[TryOnService] = None,
                 reconstruct_3d_service: Optional[Reconstruct3DService] = None):
        # 允許從外部注入 service（測試 / 替換用），預設用既有實作
        self.try_on = try_on_service or TryOnService()
        self.recon = reconstruct_3d_service or Reconstruct3DService()

    def execute(self,
                model_image,
                garment_images: List,
                data: dict,
                options: Optional[Reconstruct3DOptions] = None) -> TryOn3DResult:
        """執行 2D + 3D 完整流程。

        Args:
            model_image: 模特照（file-like 或 PIL Image）
            garment_images: 衣物圖片列表
            data: dict（model_info / garments）
            options: 3D 重建參數（None 使用預設）
        """
        t_start = time.time()

        # ===== Step 1: 2D 試穿合成 =====
        logger.info("🚀 [G5] Step1: 2D 試穿合成...")
        tryon = self.try_on.synthesize(model_image, garment_images, data)
        if not tryon.ok:
            logger.warning(f"❌ [G5] 2D 階段失敗 message={tryon.code}")
            return TryOn3DResult(
                ok=False,
                code=tryon.code,
                error_detail=tryon.error_detail,
                timings={"2d": tryon.timings.get("total"),
                         "total": time.time() - t_start},
            )
        logger.info(f"✅ [G5] Step1 完成 (style={tryon.style_name})")

        # ===== Step 2: 3D 重建（餵 2D 合成圖）=====
        logger.info("🚀 [G5] Step2: 3D 重建（用 2D 合成圖）...")
        recon = self.recon.reconstruct(tryon.image, options)
        if not recon.ok:
            logger.warning(f"❌ [G5] 3D 階段失敗 message={recon.code}")
            return TryOn3DResult(
                ok=False,
                code=recon.code,
                error_detail=recon.error_detail,
                timings={
                    "2d": tryon.timings.get("total"),
                    "3d": recon.timings.get("total"),
                    "total": time.time() - t_start,
                },
            )
        logger.info("✅ [G5] Step2 完成")

        return TryOn3DResult(
            ok=True,
            code="4200",
            glb_bytes=recon.glb_bytes,
            file_name=recon.file_name,
            file_path=recon.file_path,
            style_name=tryon.style_name,
            is_mock=recon.is_mock,
            timings={
                "2d": tryon.timings.get("total"),
                "3d": recon.timings.get("total"),
                "total": time.time() - t_start,
            },
        )
