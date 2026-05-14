"""Robust v3 真人部位拆解流程 (Human Parsing)。

流程：
  ① normalize_input：EXIF/resize/sRGB
  ② lazy init ONNX (u2net_cloth_seg)
  ③ ONNX 推論：一次掃描全身上下，分離上衣、下著、連身裙
  ④ mask post-process：平滑邊緣 (GaussianBlur + Threshold)
  ⑤ 裁切與置中：呼叫 processor.compose_square_portrait

設計原則：
  - One Inference, Multiple Outputs: 一次回傳多個部位的圖片。
  - Lazy Initialization: 使用時才動態載入模型，不佔用系統啟動時間與記憶體。
"""

import os
import logging
import cv2
import numpy as np
from PIL import Image
from django.conf import settings

from ..interfaces import RemoveBgPipeline, RemoveBgResult
from ..utils import image_io

logger = logging.getLogger(__name__)


class HumanParsingRemoveBg(RemoveBgPipeline):
    @property
    def name(self) -> str:
        return "robust_v3_human_parsing"

    def __init__(self, processor):
        self.processor = processor
        self._session = None

    def _get_session(self):
        """延遲載入 (Lazy Initialization) ONNX 模型"""
        if self._session is None:
            import onnxruntime as ort
            model_path = os.path.join(settings.BASE_DIR, "models", "u2net_cloth_seg.onnx")
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"找不到模型檔案: {model_path}")
            self._session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            logger.info("✅ [robust_v3] 已成功載入 u2net_cloth_seg ONNX 模型")
        return self._session

    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        try:
            # Step 1: 正規化 (借用 v1 基礎建設，確保方向正確)
            img_rgb = self._normalize(pil_image)
            original_size = img_rgb.size
        except Exception as e:
            return self._fail("1500", f"圖片前處理失敗: {e}")

        try:
            session = self._get_session()
        except Exception as e:
            return self._fail("1500", f"模型載入失敗: {e}")

        try:
            # Step 2: 準備 ONNX 輸入 Tensor (768x768)
            img_resized = img_rgb.resize((768, 768), Image.BILINEAR)
            img_np = np.array(img_resized).astype(np.float32) / 255.0
            
            # 正規化 (ImageNet mean & std)
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            img_np = (img_np - mean) / std
            
            # HWC -> CHW 並加上 Batch 維度
            img_tensor = np.transpose(img_np, (2, 0, 1))
            img_tensor = np.expand_dims(img_tensor, axis=0)
            
            # Step 3: 執行推論
            input_name = session.get_inputs()[0].name
            ort_outs = session.run(None, {input_name: img_tensor})
            
            # Channel 0: 背景, 1: 上半身, 2: 下半身, 3: 全身(連身裙)
            preds = ort_outs[0][0]
            class_map = np.argmax(preds, axis=0).astype(np.uint8)
            
            extracted_items = {}
            
            # Step 4: 輔助函數 - 提取部位
            def _extract_part(target_class, label_name):
                mask_768 = np.where(class_map == target_class, 255, 0).astype(np.uint8)
                if cv2.countNonZero(mask_768) < 100: # 防呆：如果面積太小，代表沒有這個部位
                    return
                # 平滑邊緣
                mask_blurred = cv2.GaussianBlur(mask_768, (5, 5), 0)
                _, mask_smooth = cv2.threshold(mask_blurred, 127, 255, cv2.THRESH_BINARY)
                
                # 縮放回原圖並套用 Alpha
                mask_original = cv2.resize(mask_smooth, original_size, interpolation=cv2.INTER_NEAREST)
                rgba_img = img_rgb.convert("RGBA")
                rgba_img.putalpha(Image.fromarray(mask_original, mode="L"))
                
                # 置中與標準化 (借助 Processor 共用方法)
                final_img = self.processor.compose_square_portrait(rgba_img)
                if final_img:
                    extracted_items[label_name] = final_img

            # Step 5: 分別提取上衣、下著、連身裙
            _extract_part(1, "upper")
            _extract_part(2, "lower")
            _extract_part(3, "dress")
            
            if not extracted_items:
                return self._fail("1423", "未偵測到任何明顯的服裝區塊")
                
            # 為了向下相容現有的 API，把上衣或洋裝設為主要的 image
            fallback_img = extracted_items.get("upper") or extracted_items.get("dress") or extracted_items.get("lower")
            
            # 將多個物件放進 result 屬性中回傳
            result = RemoveBgResult(image=fallback_img, ok=True, code="1200")
            result.extracted_items = extracted_items
            logger.info(f"✅ [robust_v3] 部位拆解成功: 包含 {list(extracted_items.keys())}")
            return result

        except Exception as e:
            logger.exception(f"❌ [robust_v3] ONNX 推論異常: {e}")
            return self._fail("1500", f"AI 部位拆解失敗: {str(e)}")

    def _normalize(self, img: Image.Image) -> Image.Image:
        """套用 EXIF + sRGB + Resize 限制"""
        img = image_io.apply_exif_rotation(img)
        img = image_io.convert_to_srgb(img)
        # 由於 u2net_cloth_seg 需要 768x768，這裡先縮到 2048 避免超大原圖 OOM
        img = image_io.resize_if_huge(img, max_side=2048)
        return img.convert("RGB")

    def _fail(self, code: str, detail: str) -> RemoveBgResult:
        logger.warning(f"❌ [robust_v3] code={code} | {detail}")
        return RemoveBgResult(
            image=None, 
            ok=False, 
            code=code, 
            error_detail=detail
        )