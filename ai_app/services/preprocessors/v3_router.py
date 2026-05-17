import logging
from PIL import Image
from ..interfaces import RemoveBgPipeline, RemoveBgResult
from .robust import RobustRemoveBg
from .robust_v3 import HumanParsingRemoveBg

logger = logging.getLogger(__name__)

class V3RouterRemoveBg(RemoveBgPipeline):
    """
    [v3] 雙軌路由策略 (Composite Strategy)
    這不是一個真實的去背模型，而是一個「交通警察」。
    它會先呼叫 LLM 預檢，再根據情境把任務派發給對應的底層 Strategy。
    """
    @property
    def name(self) -> str:
        return "robust_v3"

    def __init__(self, processor):
        self.processor = processor

    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        logger.info("🔍 [v3_router] 開始執行 LLM 預檢...")
        
        # 1. 呼叫 Gemini 預檢
        validation_result = self.processor.validate_input_image(pil_image)
        
        # 2. 擋掉非服裝爛圖
        if not validation_result.get("is_valid_garment", True):
            logger.warning(f"❌ [v3_router] 預檢失敗: {validation_result.get('reasoning')}")
            return RemoveBgResult(
                image=None,
                ok=False,
                code="1423",
                error_detail=validation_result.get("reasoning", "未偵測到服裝主體")
            )
            
        # 3. 根據情境分流
        mode = validation_result.get("presentation_mode", "flat_lay")
        logger.info(f"🔀 [v3_router] LLM 判定情境為: {mode}")
        
        if mode == "worn_on_body":
            logger.info("👉 [v3_router] 走新路徑：真人穿搭 (HumanParsing)")
            strategy = HumanParsingRemoveBg(self.processor)
        else:
            logger.info("👉 [v3_router] 走舊路徑：平鋪或衣架 (Robust)")
            strategy = RobustRemoveBg(self.processor)
            
        # 4. 將工作交接給被選中的策略
        return strategy.process(pil_image)