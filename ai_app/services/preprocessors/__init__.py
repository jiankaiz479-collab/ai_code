import os

def get_remove_bg_pipeline(processor):
    """
    去背策略工廠：讀取 .env 的 REMOVE_BG_VERSION 動態決定去背實作。
    """
    version = os.getenv("REMOVE_BG_VERSION", "legacy")
    if version == "robust":
        from .robust import RobustRemoveBg
        return RobustRemoveBg(processor)
    elif version == "robust_v3":
        from .v3_router import V3RouterRemoveBg
        return V3RouterRemoveBg(processor)
    else:
        from .legacy import LegacyRemoveBg
        return LegacyRemoveBg(processor)