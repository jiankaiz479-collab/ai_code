"""工廠：依環境變數決定回傳哪個 RemoveBgPipeline 實作。

切換方式（.env）：
    REMOVE_BG_VERSION=legacy     # 舊版（與升級前完全一致）
    REMOVE_BG_VERSION=robust     # v1（isnet + matting + LCC + erode + 品質檢查）
    REMOVE_BG_VERSION=robust_v2  # v2（v1 + 過曝檢查 + 失敗歸檔 + Gemini 預檢）
"""

import logging
import os

from ..interfaces import RemoveBgPipeline
from .legacy import LegacyRemoveBg

logger = logging.getLogger(__name__)


def get_remove_bg_pipeline(processor) -> RemoveBgPipeline:
    """根據 .env 設定回傳對應的去背流程實作。"""
    version = os.getenv("REMOVE_BG_VERSION", "legacy").lower().strip()

    if version == "robust_v2":
        from .robust_v2 import RobustV2RemoveBg
        logger.info("🔧 [preprocessor] 使用 robust v2 去背流程")
        return RobustV2RemoveBg(processor)

    if version == "robust":
        from .robust import RobustRemoveBg
        logger.info("🔧 [preprocessor] 使用 robust v1 去背流程")
        return RobustRemoveBg(processor)

    if version != "legacy":
        logger.warning(f"⚠️ [preprocessor] 未知 REMOVE_BG_VERSION='{version}'，降級回 legacy")

    return LegacyRemoveBg(processor)
