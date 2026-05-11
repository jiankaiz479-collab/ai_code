"""工廠：依環境變數決定回傳哪個 RemoveBgPipeline 實作。

切換方式（.env）：
    REMOVE_BG_VERSION=legacy   # 預設，跑舊邏輯（與升級前完全一致）
    REMOVE_BG_VERSION=robust   # 跑 v1 升級版（待 Phase 4 實作）

未來新增 v2 / commercial 時，只需在這裡多 if/elif，view 層完全不需動。
"""

import logging
import os

from ..interfaces import RemoveBgPipeline
from .legacy import LegacyRemoveBg

logger = logging.getLogger(__name__)


def get_remove_bg_pipeline(processor) -> RemoveBgPipeline:
    """根據 .env 設定回傳對應的去背流程實作。

    Args:
        processor: AIProcessor 實例（給 legacy 用，未來 robust 也會用）。
    """
    version = os.getenv("REMOVE_BG_VERSION", "legacy").lower().strip()

    if version == "robust":
        # Phase 4 完成後啟用
        try:
            from .robust import RobustRemoveBg
            logger.info("🔧 [preprocessor] 使用 robust v1 去背流程")
            return RobustRemoveBg(processor)
        except ImportError:
            logger.warning("⚠️ [preprocessor] robust 尚未實作，降級回 legacy")
            return LegacyRemoveBg(processor)

    if version != "legacy":
        logger.warning(f"⚠️ [preprocessor] 未知 REMOVE_BG_VERSION='{version}'，降級回 legacy")

    return LegacyRemoveBg(processor)
