"""去背前處理器：Strategy Pattern 實作。

抽象合約定義在 ai_app/services/interfaces.py（RemoveBgPipeline / RemoveBgResult）。
這個 package 只放具體實作 + 工廠。

用法：
    from ai_app.services.preprocessors import get_remove_bg_pipeline
    pipeline = get_remove_bg_pipeline(processor)
    result = pipeline.process(pil_img)
"""

from .factory import get_remove_bg_pipeline

__all__ = ["get_remove_bg_pipeline"]
