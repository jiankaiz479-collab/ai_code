"""Robust v1 去背流程：解 7 個現場拍照常見問題。

流程：
  ① normalize_input：HEIC/EXIF/resize/sRGB
  ② quality check：過暗 / 過模糊 → fail with reason
  ③ rembg(isnet-general-use, alpha_matting=True)
  ④ mask post-process：最大連通區域過濾 + erosion 收邊
  ⑤ mask quality check：偵測蒙版面積異常 / 多主體殘留 → fail with reason

設計原則：
  - Fail loud：每個 AI 步驟後做 sanity check，失敗回 diagnosis 結構
  - 共通工具用 utils/image_io.py，不重複寫
  - rembg session 在這個 class 內 lazy-init，不污染 AIProcessor
"""

import io
import logging
import os
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

from ..interfaces import RemoveBgPipeline, RemoveBgResult
from ..utils import image_io

logger = logging.getLogger(__name__)


class RobustRemoveBg(RemoveBgPipeline):
    """v1 升級流程：包 7 個失敗模式的偵測與處理。"""

    # ---- 環境變數覆蓋預設值 ----
    DEFAULT_MODEL = os.getenv("REMOVE_BG_REMBG_MODEL", "isnet-general-use")
    DEFAULT_MAX_SIDE = int(os.getenv("REMOVE_BG_MAX_RESOLUTION", "2048"))
    DEFAULT_ALPHA_MATTING = os.getenv("REMOVE_BG_ALPHA_MATTING", "true").lower() in ("1", "true", "yes")
    DEFAULT_BRIGHTNESS_MIN = float(os.getenv("REMOVE_BG_BRIGHTNESS_MIN", "30"))   # 0-255
    DEFAULT_BLUR_MIN = float(os.getenv("REMOVE_BG_BLUR_MIN", "50"))               # Laplacian variance
    DEFAULT_ERODE_PX = int(os.getenv("REMOVE_BG_ERODE_PX", "1"))
    DEFAULT_MASK_COVERAGE_MIN = float(os.getenv("REMOVE_BG_MASK_COVERAGE_MIN", "0.03"))  # 3%
    DEFAULT_MASK_COVERAGE_MAX = float(os.getenv("REMOVE_BG_MASK_COVERAGE_MAX", "0.95"))  # 95%

    def __init__(self, processor):
        self.processor = processor  # 保留以備未來呼叫 analyze_clothing_style 之類
        self._session = None  # lazy init，避免 import 時就載入模型

    @property
    def name(self) -> str:
        return "robust_v1"

    def _get_session(self):
        """Lazy init rembg session，第一次使用時才載入模型。"""
        if self._session is None:
            try:
                self._session = new_session(model_name=self.DEFAULT_MODEL)
                logger.info(f"✅ [robust] 載入 rembg 模型: {self.DEFAULT_MODEL}")
            except Exception as e:
                logger.warning(f"⚠️ [robust] 模型 {self.DEFAULT_MODEL} 載入失敗，降級用 u2net: {e}")
                self._session = new_session()
        return self._session

    # ============================================================
    # 主流程
    # ============================================================
    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        # Step 1: 正規化（外部已開檔，這裡只做 EXIF/resize/sRGB）
        try:
            img = self._normalize(pil_image)
        except Exception as e:
            return self._fail("1500", f"[normalize] {e}", {"stage": "normalize"})

        # Step 2: 品質預檢查
        check = self._quality_check(img)
        if not check["ok"]:
            return self._fail(check["code"], check["detail"], check["diagnosis"])

        # Step 3: rembg 去背
        try:
            rgba = self._run_rembg(img)
        except Exception as e:
            return self._fail("1500", f"[rembg] {e}", {"stage": "rembg"})

        # Step 4: 蒙版後處理（最大連通區域 + erosion）
        try:
            rgba, post_diag = self._postprocess_mask(rgba)
        except Exception as e:
            return self._fail("1500", f"[postprocess] {e}", {"stage": "postprocess"})

        # Step 5: 蒙版品質檢查
        mask_check = self._mask_quality_check(rgba, post_diag)
        if not mask_check["ok"]:
            return self._fail(mask_check["code"], mask_check["detail"], mask_check["diagnosis"])

        # 6: 裁掉透明邊框（保留舊行為，下游 Gemini 顏色判斷較準）
        bbox = rgba.getbbox()
        if bbox:
            rgba = rgba.crop(bbox)

        logger.info(f"✅ [robust] 去背成功 (post_diag={post_diag})")
        return RemoveBgResult(image=rgba, ok=True, code="1200")

    # ============================================================
    # 各 Step 實作
    # ============================================================
    def _normalize(self, img: Image.Image) -> Image.Image:
        """套用 EXIF + resize + sRGB（已 open 完，不重開檔）。"""
        img = image_io.apply_exif_rotation(img)
        img = image_io.convert_to_srgb(img)
        img = image_io.resize_if_huge(img, max_side=self.DEFAULT_MAX_SIDE)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        return img

    def _quality_check(self, img: Image.Image) -> dict:
        """過暗 / 過模糊偵測（純 OpenCV，毫秒級）。"""
        cv_rgb = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(cv_rgb, cv2.COLOR_RGB2GRAY)

        # 亮度（平均灰階值，0-255）
        brightness = float(gray.mean())
        if brightness < self.DEFAULT_BRIGHTNESS_MIN:
            return {
                "ok": False,
                "code": "1417",
                "detail": f"[quality] Image too dark (brightness={brightness:.1f} < {self.DEFAULT_BRIGHTNESS_MIN})",
                "diagnosis": {
                    "failure_type": "too_dark",
                    "brightness": brightness,
                    "suggestion": "請在光線充足處重拍",
                },
            }

        # 模糊度（Laplacian variance，越高越清晰）
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur_score < self.DEFAULT_BLUR_MIN:
            return {
                "ok": False,
                "code": "1418",
                "detail": f"[quality] Image too blurry (Laplacian var={blur_score:.1f} < {self.DEFAULT_BLUR_MIN})",
                "diagnosis": {
                    "failure_type": "too_blurry",
                    "blur_score": blur_score,
                    "suggestion": "請對焦清楚後重拍",
                },
            }

        return {"ok": True, "brightness": brightness, "blur_score": blur_score}

    def _run_rembg(self, img: Image.Image) -> Image.Image:
        """rembg + alpha matting（細邊緣）。"""
        session = self._get_session()
        return remove(
            img,
            session=session,
            alpha_matting=self.DEFAULT_ALPHA_MATTING,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )

    def _postprocess_mask(self, rgba: Image.Image) -> tuple:
        """後處理：最大連通區域過濾 + erosion 收邊。

        Returns:
            (processed_rgba, diagnosis_dict)
        """
        arr = np.array(rgba)
        alpha = arr[:, :, 3]
        # 二值化（>0 視為前景）
        binary = (alpha > 0).astype(np.uint8)

        # 連通區域分析（4-connectivity 速度快、人物連續夠用）
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=4)
        # stats[0] 是背景，跳過
        component_areas = stats[1:, cv2.CC_STAT_AREA] if num_labels > 1 else np.array([])
        n_components = len(component_areas)

        if n_components == 0:
            # 沒有任何前景
            return rgba, {"components_before": 0, "components_after": 0, "discarded_pixels": 0}

        # 取最大連通區域索引（注意 labels 從 1 開始）
        largest_idx = int(np.argmax(component_areas)) + 1
        largest_area = int(component_areas[largest_idx - 1])
        total_other_area = int(component_areas.sum()) - largest_area

        # 只保留最大區域
        kept_mask = (labels == largest_idx).astype(np.uint8)

        # erosion 收邊（解白邊 fringe）
        if self.DEFAULT_ERODE_PX > 0:
            kernel = np.ones((self.DEFAULT_ERODE_PX * 2 + 1, self.DEFAULT_ERODE_PX * 2 + 1), np.uint8)
            kept_mask = cv2.erode(kept_mask, kernel, iterations=1)

        # 套回 alpha：原 alpha × kept_mask
        new_alpha = alpha * kept_mask
        arr[:, :, 3] = new_alpha
        processed = Image.fromarray(arr, mode="RGBA")

        return processed, {
            "components_before": n_components,
            "components_after": 1,
            "largest_area_px": largest_area,
            "discarded_pixels": total_other_area,
        }

    def _mask_quality_check(self, rgba: Image.Image, post_diag: dict) -> dict:
        """蒙版品質檢查：覆蓋率、是否找到主體。"""
        arr = np.array(rgba)
        alpha = arr[:, :, 3]
        total_px = alpha.shape[0] * alpha.shape[1]
        coverage = float((alpha > 0).sum() / total_px)

        if coverage < self.DEFAULT_MASK_COVERAGE_MIN:
            return {
                "ok": False,
                "code": "1424",
                "detail": f"[mask] No subject detected (mask coverage={coverage:.1%} < {self.DEFAULT_MASK_COVERAGE_MIN:.0%})",
                "diagnosis": {
                    "failure_type": "no_subject",
                    "mask_coverage": coverage,
                    "suggestion": "未偵測到主體，請確認衣服在畫面中",
                },
            }

        if coverage > self.DEFAULT_MASK_COVERAGE_MAX:
            return {
                "ok": False,
                "code": "1423",
                "detail": f"[mask] Background not removed (mask coverage={coverage:.1%} > {self.DEFAULT_MASK_COVERAGE_MAX:.0%})",
                "diagnosis": {
                    "failure_type": "background_not_removed",
                    "mask_coverage": coverage,
                    "suggestion": "背景與主體對比不足，請換單色背景重拍",
                },
            }

        return {"ok": True, "mask_coverage": coverage, **post_diag}

    # ============================================================
    # 工具
    # ============================================================
    @staticmethod
    def _fail(code: str, detail: str, diagnosis: Optional[dict] = None) -> RemoveBgResult:
        logger.warning(f"❌ [robust] {code} {detail}")
        return RemoveBgResult(
            image=None, ok=False, code=code,
            error_detail=detail, diagnosis=diagnosis or {},
        )
