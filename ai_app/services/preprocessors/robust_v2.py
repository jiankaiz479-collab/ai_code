"""Robust v2 去背流程：在 v1 之上加四件事。

繼承 RobustRemoveBg，只 override / 包裝差異的部分，v1 邏輯完全不動。

v2 新增：
  ① 過曝檢查（_quality_check override）                                 ✅
  ② 失敗樣本歸檔（process 包裝,失敗時存原圖到 tests/fail_samples/）       ✅
  ③ Gemini 預檢「這是不是衣服 + 形狀可辨識」                              ✅
  ④ 自動修復曝光(過暗 gamma 拉亮 / 過曝 autocontrast 拉伸)                ✅
"""

import hashlib
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..interfaces import RemoveBgResult
from .robust import RobustRemoveBg

logger = logging.getLogger(__name__)


class RobustV2RemoveBg(RobustRemoveBg):
    """v2：在 v1 之上補過曝檢查 + 失敗歸檔 + Gemini 預檢。"""

    # ---- v2 新增 env ----
    DEFAULT_OVEREXPOSURE_RATIO_MAX = float(
        os.getenv("REMOVE_BG_OVEREXPOSURE_RATIO_MAX", "0.60")
    )  # 亮度 > 240 的像素佔比上限
    DEFAULT_OVEREXPOSURE_THRESHOLD = int(
        os.getenv("REMOVE_BG_OVEREXPOSURE_THRESHOLD", "240")
    )  # 多亮算「過曝像素」

    # 失敗歸檔目錄(可由 env 覆寫)
    FAIL_SAMPLES_DIR = Path(
        os.getenv("REMOVE_BG_FAIL_SAMPLES_DIR", "tests/fail_samples")
    )

    # Gemini 預檢:可關閉(預設啟用),Gemini API 異常時降級為直接放行
    ENABLE_GEMINI_PRECHECK = (
        os.getenv("REMOVE_BG_GEMINI_PRECHECK", "true").lower() in ("1", "true", "yes")
    )

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

    # ============================================================
    # Override：v1 的 process 外加四件事
    #   1. 自動修復曝光(過暗/過曝先試圖救)
    #   2. CV 品質檢查(走 _quality_check override,含過曝)
    #   3. Gemini 預檢「這是不是衣服 + 形狀可辨識」
    #   4. 失敗歸檔
    # ============================================================
    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        # 自己做一次 normalize,後面 super().process() 會再做一次(idempotent)
        try:
            pre_img = self._normalize(pil_image)
        except Exception:
            # normalize 炸了讓 super().process() 那邊處理
            result = super().process(pil_image)
            if not result.ok:
                self._archive_failure(pil_image, result)
            return result

        # Step 1: 自動修復曝光
        if self.ENABLE_AUTO_FIX_EXPOSURE:
            pre_img = self._try_fix_exposure(pre_img)

        # Step 2: CV 品質檢查(過暗/過糊/過曝),修復後若仍不過則直接 fail
        quality = self._quality_check(pre_img)
        if not quality["ok"]:
            result = self._fail(quality["code"], quality["detail"], quality["diagnosis"])
            self._archive_failure(pil_image, result)
            return result

        # Step 3: Gemini 預檢
        if self.ENABLE_GEMINI_PRECHECK:
            gemini = self._gemini_is_clothing(pre_img)
            if not gemini["ok"]:
                result = self._fail(gemini["code"], gemini["detail"], gemini["diagnosis"])
                self._archive_failure(pil_image, result)
                return result

        # Step 4: 走 v1 完整流程(rembg + 後處理 + mask 檢查)
        # 注意:傳入「已修復」的 pre_img,super 內部會再 normalize 一次(idempotent)
        result = super().process(pre_img)
        if not result.ok:
            self._archive_failure(pil_image, result)
        return result

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
    # Gemini 預檢
    # ============================================================
    def _gemini_is_clothing(self, img: Image.Image) -> dict:
        """問 Gemini「這張圖是不是衣服」,回傳統一格式。

        失敗時的處理:API 異常 → 放行(避免 Gemini 服務掛掉導致全部去背都失敗)
        """
        client = getattr(self.processor, "client", None)
        consultant_model = getattr(
            self.processor, "consultant_model", "gemini-2.5-flash"
        )
        if client is None:
            logger.warning("⚠️ [v2] Gemini client 未初始化,跳過預檢")
            return {"ok": True, "skipped": True}

        prompt = (
            "Look at the image (with original background, before any background "
            "removal) and answer TWO questions about the main subject:\n\n"
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
            "Respond ONLY in JSON:\n"
            '{"is_clothing": true|false, "shape_recognizable": true|false|null, '
            '"fail_reason": "<short, only if either field is false>"}'
        )

        try:
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
            is_clothing = bool(data.get("is_clothing"))
            shape_recognizable = data.get("shape_recognizable")  # true/false/None
            fail_reason = data.get("fail_reason", "")
            logger.info(
                f"🤖 [v2] Gemini 預檢: is_clothing={is_clothing} "
                f"shape_recognizable={shape_recognizable} reason='{fail_reason}'"
            )
        except Exception as e:
            logger.warning(f"⚠️ [v2] Gemini 預檢異常,降級放行: {e}")
            return {"ok": True, "skipped": True, "error": str(e)}

        if not is_clothing:
            return {
                "ok": False,
                "code": "1500",
                "detail": f"[gemini] Subject is not clothing: {fail_reason}",
                "diagnosis": {
                    "failure_type": "not_clothing",
                    "gemini_reason": fail_reason,
                    "suggestion": "AI 偵測這張不是衣服,請上傳衣服照片",
                },
            }

        if shape_recognizable is False:
            return {
                "ok": False,
                "code": "1500",
                "detail": f"[gemini] Garment shape not recognizable: {fail_reason}",
                "diagnosis": {
                    "failure_type": "shape_not_recognizable",
                    "gemini_reason": fail_reason,
                    "suggestion": "衣服形狀看不清楚(揉皺/捲起),請攤平後重拍",
                },
            }

        return {
            "ok": True,
            "is_clothing": True,
            "shape_recognizable": shape_recognizable,
        }

    def _archive_failure(self, pil_image: Image.Image, result: RemoveBgResult) -> None:
        """失敗時把原圖寫到 tests/fail_samples/{日期}/{code}_{hash}.jpg。

        失敗歸檔本身不能讓主流程崩潰,任何 I/O 例外都吞掉只 log。
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            target_dir = self.FAIL_SAMPLES_DIR / today
            target_dir.mkdir(parents=True, exist_ok=True)

            # 用圖片 bytes 算 hash,避免同一張圖重複存
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
            short_hash = hashlib.md5(img_bytes).hexdigest()[:8]

            filename = f"{result.code}_{short_hash}.jpg"
            target_path = target_dir / filename

            if target_path.exists():
                logger.info(f"📁 [v2] 失敗樣本已存在(跳過): {target_path}")
                return

            target_path.write_bytes(img_bytes)
            logger.info(f"📁 [v2] 失敗樣本已歸檔: {target_path}")
        except Exception as e:
            logger.warning(f"⚠️ [v2] 失敗歸檔本身炸了(主流程不受影響): {e}")

    # ============================================================
    # Override：在 v1 的 quality check 後追加「過曝」檢查
    # ============================================================
    def _quality_check(self, img: Image.Image) -> dict:
        # 先跑 v1 原本的「過暗 / 過糊」檢查
        v1_result = super()._quality_check(img)
        if not v1_result["ok"]:
            return v1_result

        # v2 新增：過曝檢查
        cv_rgb = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(cv_rgb, cv2.COLOR_RGB2GRAY)
        overexposed_ratio = float(
            (gray > self.DEFAULT_OVEREXPOSURE_THRESHOLD).sum() / gray.size
        )
        if overexposed_ratio > self.DEFAULT_OVEREXPOSURE_RATIO_MAX:
            return {
                "ok": False,
                "code": "1422",
                "detail": (
                    f"[quality] Image overexposed "
                    f"(ratio={overexposed_ratio:.1%} > {self.DEFAULT_OVEREXPOSURE_RATIO_MAX:.0%}, "
                    f"threshold>{self.DEFAULT_OVEREXPOSURE_THRESHOLD})"
                ),
                "diagnosis": {
                    "failure_type": "overexposed",
                    "overexposed_ratio": overexposed_ratio,
                    "suggestion": "光線太強，請避開直射光重拍",
                },
            }

        return {
            "ok": True,
            **{k: v for k, v in v1_result.items() if k != "ok"},
            "overexposed_ratio": overexposed_ratio,
        }
