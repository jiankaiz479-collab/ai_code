"""輕量輸入驗證層 (InputValidation Layer)。

負責在真正進入去背流程前，進行快速視覺 heuristics (長寬比/模糊/曝光) 
與可選的 LLM 語意預檢 (是否為衣服/形狀是否可辨識)。
依循 Strategy Pattern 模式設計，實作 ImageValidator 介面。
"""

import json
import logging
import os

import cv2
import numpy as np
from PIL import Image

from ..interfaces import ImageValidator, ValidationResult

logger = logging.getLogger(__name__)


class InputValidator(ImageValidator):
    """v2 的輸入預檢閘門：Heuristics (14xx) + LLM Gate (15xx)。"""

    def __init__(self, processor=None):
        self.processor = processor
        self.MAX_ASPECT_RATIO = float(os.getenv("REMOVE_BG_MAX_ASPECT_RATIO", "3.0"))
        self.MIN_BRIGHTNESS = float(os.getenv("REMOVE_BG_BRIGHTNESS_MIN", "30"))
        self.MIN_BLUR = float(os.getenv("REMOVE_BG_BLUR_MIN", "50"))
        self.OVEREXPOSURE_THRESHOLD = int(os.getenv("REMOVE_BG_OVEREXPOSURE_THRESHOLD", "240"))
        self.MAX_OVEREXPOSURE_RATIO = float(os.getenv("REMOVE_BG_OVEREXPOSURE_RATIO_MAX", "0.60"))
        
        self.ENABLE_GEMINI_PRECHECK = os.getenv("REMOVE_BG_GEMINI_PRECHECK", "true").lower() in ("1", "true", "yes")

    def validate(self, img: Image.Image) -> ValidationResult:
        # 1. 快速視覺 Heuristics (14xx)
        w, h = img.size
        if max(w / h, h / w) > self.MAX_ASPECT_RATIO:
            return self._fail("1420", "extreme_aspect_ratio", "圖片比例極端，請上傳比例正常的圖片")

        cv_rgb = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(cv_rgb, cv2.COLOR_RGB2GRAY)

        brightness = float(gray.mean())
        if brightness < self.MIN_BRIGHTNESS:
            return self._fail("1410", "too_dark", "圖片過暗，請在光線充足處重拍")

        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur_score < self.MIN_BLUR:
            return self._fail("1410", "too_blurry", "圖片過於模糊，請對焦清楚後重拍")

        overexposed_ratio = float((gray > self.OVEREXPOSURE_THRESHOLD).sum() / gray.size)
        if overexposed_ratio > self.MAX_OVEREXPOSURE_RATIO:
            return self._fail("1410", "overexposed", "光線太強過曝，請避開直射光重拍")

        # 2. LLM Gate 語意預檢 (15xx)
        if self.ENABLE_GEMINI_PRECHECK and self.processor and getattr(self.processor, "client", None):
            llm_res = self._llm_semantic_gate(img)
            if not llm_res.ok:
                return llm_res

        return ValidationResult(ok=True)

    def _llm_semantic_gate(self, img: Image.Image) -> ValidationResult:
        """呼叫 Gemini 進行語意預檢。失敗時預設 fail-open (放行) 避免阻斷主流程。"""
        try:
            client = getattr(self.processor, "client", None)
            consultant_model = getattr(self.processor, "consultant_model", "gemini-2.5-flash")
            if client is None:
                logger.warning("⚠️ [validator] Gemini client 未初始化，跳過預檢")
                return ValidationResult(ok=True)

            prompt = (
                "Look at the image (with original background, before any background "
                "removal) and answer THREE questions about the main subject:\n\n"
                "1. is_clothing: Is the main subject a piece of clothing/apparel?\n"
                "   - true: shirt, pants, dress, jacket, hoodie, shorts, skirt, etc.\n"
                "   - false: food, pet, landscape, screenshot, electronic device, "
                "room interior, person without clear clothing focus.\n\n"
                "2. shape_recognizable: If is_clothing=true, can you clearly "
                "recognize the garment's overall shape?\n"
                "   - true: lying flat / hung naturally / silhouette is clear.\n"
                "   - false: crumpled into an unrecognizable ball, heavily folded "
                "so the shape is hidden, or rolled up.\n"
                "   - null: set to null if is_clothing=false.\n\n"
                "3. has_hanger: Is there a visible clothing hanger in the image?\n"
                "   - true: A hanger is visible (e.g., inside or hanging the clothes).\n"
                "   - false: No hanger is visible.\n\n"
                "Respond ONLY in JSON:\n"
                '{"is_clothing": true|false, "shape_recognizable": true|false|null, '
                '"has_hanger": true|false, "fail_reason": "<short, only if a field is false>"}'
            )

            from google.genai import types
            response = client.models.generate_content(
                model=consultant_model,
                contents=[img, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            
            logger.info(f"🤖 [validator] Gemini 預檢結果: {data}")
            
            if not data.get("is_clothing", True):
                return self._fail("1510", "not_clothing", "AI 偵測這張不是衣服，請上傳衣服照片")
            if data.get("shape_recognizable") is False:
                return self._fail("1510", "shape_not_recognizable", "衣服形狀看不清楚(揉皺/捲起)，請攤平後重拍")
            if data.get("has_hanger", False):
                # 先給個警告，目前先不阻斷，可以觀察 log，未來若要阻斷可直接回傳 _fail()
                logger.warning("⚠️ [validator] 偵測到衣架，為避免誤殺暫時放行。")

            return ValidationResult(ok=True)
        except Exception as e:
            logger.warning(f"⚠️ [validator] LLM 預檢異常，採取 fail-open 放行: {e}")
            return ValidationResult(ok=True)

    def _fail(self, code: str, failure_type: str, ui_behavior: str) -> ValidationResult:
        logger.warning(f"❌ [validator] 預檢失敗 code={code} type={failure_type}")
        return ValidationResult(
            ok=False,
            code=code,
            detail=f"[validation] Failed on {failure_type}",
            diagnosis={"failure_type": failure_type, "ui_behavior": ui_behavior}
        )