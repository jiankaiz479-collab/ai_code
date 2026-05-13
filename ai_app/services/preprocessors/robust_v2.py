"""Robust v2 去背流程。

繼承 RobustRemoveBg，只 override / 包裝差異的部分，v1 邏輯完全不動。

v2 重點（依據 roadmap）：
  ① 獨立的輕量 InputValidation 層（Heuristics + LLM Gate）
  ② 自動修復曝光(過暗/過曝)
  ③ 移除暫緩的失敗樣本歸檔
  ④ 實作「多指標綜合決策」取代單一閾值的 mask_quality_check
"""

import io
import json
import logging
import os

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..interfaces import RemoveBgResult
from .robust import RobustRemoveBg
from .validators import InputValidator

logger = logging.getLogger(__name__)


class RobustV2RemoveBg(RobustRemoveBg):
    """v2：Validation Gate + 多指標決策。"""

    DEFAULT_OVEREXPOSURE_THRESHOLD = int(
        os.getenv("REMOVE_BG_OVEREXPOSURE_THRESHOLD", "240")
    )  # 多亮算「過曝像素」

    # 自動修復曝光(過暗 / 過曝):先試圖救回,救不回再走原本的失敗流程
    ENABLE_AUTO_FIX_EXPOSURE = (
        os.getenv("REMOVE_BG_AUTO_FIX_EXPOSURE", "true").lower() in ("1", "true", "yes")
    )
    # 過暗自動拉亮:當亮度 < 此值時觸發 gamma correction(同 v1 BRIGHTNESS_MIN)
    AUTO_FIX_BRIGHTNESS_TRIGGER = float(
        os.getenv("REMOVE_BG_AUTO_FIX_DARK_TRIGGER", "30")
    )
    AUTO_FIX_DARK_GAMMA = float(
        os.getenv("REMOVE_BG_AUTO_FIX_DARK_GAMMA", "0.5")
    )  # gamma < 1 = 變亮;0.5 = 中度提亮

    # 過曝自動修復觸發門檻(比品質檢查的拒絕門檻更早觸發,試圖救回)
    AUTO_FIX_OVEREXPOSED_RATIO_TRIGGER = float(
        os.getenv("REMOVE_BG_AUTO_FIX_OVEREXPOSED_TRIGGER", "0.30")
    )

    @property
    def name(self) -> str:
        return "robust_v2"

    def __init__(self, processor):
        super().__init__(processor)
        self.validator = InputValidator(processor)

    # ============================================================
    # Override：主流程
    # ============================================================
    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        try:
            pre_img = self._normalize(pil_image)
        except Exception:
            return super().process(pil_image)

        # Step 1: 自動修復曝光
        if self.ENABLE_AUTO_FIX_EXPOSURE:
            pre_img = self._try_fix_exposure(pre_img)

        # Step 2: Validation Gate (Heuristics + LLM)
        val_res = self.validator.validate(pre_img)
        if not val_res.ok:
            return self._fail(val_res.code, val_res.detail, val_res.diagnosis)

        # Step 3: 走 v1 完整流程
        return super().process(pre_img)

    # ============================================================
    # 自動修復曝光
    # ============================================================
    def _try_fix_exposure(self, img: Image.Image) -> Image.Image:
        """過暗 → gamma 拉亮;過曝 → autocontrast 拉伸。輕量、可重複呼叫。"""
        rgb = img.convert("RGB")
        arr = np.array(rgb)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        brightness = float(gray.mean())
        overexposed_ratio = float(
            (gray > self.DEFAULT_OVEREXPOSURE_THRESHOLD).sum() / gray.size
        )

        fixed = img

        # 過暗 → gamma 拉亮
        if brightness < self.AUTO_FIX_BRIGHTNESS_TRIGGER:
            gamma = self.AUTO_FIX_DARK_GAMMA
            inv = 1.0 / gamma
            table = np.array([((i / 255.0) ** inv) * 255 for i in range(256)]).astype(np.uint8)
            # 套用到 RGB,保留 alpha
            rgb_arr = np.array(fixed.convert("RGB"))
            corrected = cv2.LUT(rgb_arr, table)
            if fixed.mode == "RGBA":
                alpha = np.array(fixed)[:, :, 3]
                fixed_arr = np.dstack([corrected, alpha])
                fixed = Image.fromarray(fixed_arr, mode="RGBA")
            else:
                fixed = Image.fromarray(corrected, mode="RGB")
            new_brightness = float(cv2.cvtColor(corrected, cv2.COLOR_RGB2GRAY).mean())
            logger.info(
                f"🔧 [v2] auto-fix dark: brightness {brightness:.1f} → {new_brightness:.1f} (gamma={gamma})"
            )

        # 過曝 → CLAHE 局部均衡(只壓抑強光區,不動正常區)
        # 與 autocontrast 相反:autocontrast 拉伸對比反而讓強光更白、陰影更暗,
        # 在 case_01_sunlit_floor 實測下 mask 從 20.3% 掉到 9.6%。
        # CLAHE 對局部強光最有效。
        elif overexposed_ratio > self.AUTO_FIX_OVEREXPOSED_RATIO_TRIGGER:
            rgb_arr = np.array(fixed.convert("RGB"))
            lab = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            # clipLimit=3.0 經 case_01 實測最佳(21.0% 完整 vs 2.0 的 20.3%)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            l = clahe.apply(l)
            merged = cv2.merge((l, a, b))
            corrected = cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
            if fixed.mode == "RGBA":
                alpha = np.array(fixed)[:, :, 3]
                fixed_arr = np.dstack([corrected, alpha])
                fixed = Image.fromarray(fixed_arr, mode="RGBA")
            else:
                fixed = Image.fromarray(corrected, mode="RGB")
            new_gray = cv2.cvtColor(corrected, cv2.COLOR_RGB2GRAY)
            new_ratio = float((new_gray > self.DEFAULT_OVEREXPOSURE_THRESHOLD).sum() / new_gray.size)
            logger.info(
                f"🔧 [v2] auto-fix overexposed: ratio {overexposed_ratio:.1%} → {new_ratio:.1%} (CLAHE clipLimit=2.0)"
            )

        return fixed

    # ============================================================
    # Override：Quality Checks (Validation 層已處理，故這邊覆寫)
    # ============================================================
    def _quality_check(self, img: Image.Image) -> dict:
        # 視覺驗證已在 InputValidator 做過，這裡直接 pass
        return {"ok": True}

    def _mask_quality_check(self, rgba: Image.Image, post_diag: dict) -> dict:
        """多指標綜合決策，而非只靠單一閾值。"""
        arr = np.array(rgba)
        alpha = arr[:, :, 3]
        total_px = alpha.size
        coverage = float((alpha > 0).sum() / total_px)

        # 新增: Bounding Box 比例
        x, y, w, h = rgba.getbbox() or (0, 0, 0, 0)
        bbox_area = w * h
        fill_ratio = (alpha > 0).sum() / bbox_area if bbox_area > 0 else 0

        # 如果面積太小，無論如何都是失敗
        if coverage < self.DEFAULT_MASK_COVERAGE_MIN:
            return {
                "ok": False,
                "code": "1423",
                "detail": f"[mask] No subject detected (coverage={coverage:.1%})",
                "diagnosis": {"failure_type": "no_subject", "ui_behavior": "未偵測到主體"}
            }

        # 綜合判斷: 面積超大 (>90%) 且幾乎填滿 bbox (fill_ratio > 0.95)，通常是背景沒切掉
        if coverage > 0.90 and fill_ratio > 0.95:
            return {
                "ok": False,
                "code": "1423",
                "detail": f"[mask] Background not removed (coverage={coverage:.1%}, fill={fill_ratio:.1%})",
                "diagnosis": {"failure_type": "background_not_removed", "ui_behavior": "背景與主體對比不足"}
            }

        return {"ok": True, "mask_coverage": coverage, "fill_ratio": fill_ratio, **post_diag}
