import os
import uuid
import logging
import json
import io
import numpy as np
import cv2  
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove, new_session
from PIL import Image, ImageEnhance, ImageOps
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

class AIProcessor(ImageProcessingInterface):
    
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        try:
            self.rembg_session = new_session()
        except Exception as e:
            logger.warning(f"rembg session 初始化失敗: {e}")
            self.rembg_session = None

        try:
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except Exception as e:
            logger.error(f"⚠️ Gemini Client 初始化失敗: {e}")
            self.client = None
        
        self.consultant_model = "gemini-1.5-flash"
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-exp")

    def _get_unique_filename(self, prefix="img", ext="png"):
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    # [新增] OpenCV BGR 矩陣提取
    # ==========================================
    def _extract_bgr_matrix(self, image_path):
        """
        利用 OpenCV 讀取去背圖，過濾 Alpha 通道後取得衣服純色的平均 BGR 矩陣
        """
        try:
            # 讀取包含透明通道的圖片 (IMREAD_UNCHANGED)
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4:
                return [255, 255, 255] # 預設回傳白色

            # 分離通道
            b, g, r, a = cv2.split(img)
            # 建立遮罩：只選取不透明的像素 (Alpha > 0)
            mask = a > 0
            
            # 計算該區域的平均 BGR 值
            mean_b = np.mean(b[mask])
            mean_g = np.mean(g[mask])
            mean_r = np.mean(r[mask])
            
            return [int(mean_r), int(mean_g), int(mean_b)] # 轉為 RGB 順序回傳
        except Exception as e:
            logger.error(f"BGR 提取失敗: {e}")
            return [255, 255, 255]

    # ==========================================
    # [後期開發] 語意區域定位 (Gemini 分析)
    # ==========================================
    def _get_semantic_ruffle_mask(self, pil_img, gray_cv_img):
        h, w = gray_cv_img.shape
        prompt = """
        Identify precise bounding boxes for:
        1. "deep_shadows": Dark crevices in ruffles.
        2. "specular_highlights": Bright light spots.
        Return JSON: [{"label": string, "box_2d": [ymin, xmin, ymax, xmax]}].
        Coordinates normalized to 1000.
        """
        try:
            response = self.client.models.generate_content(
                model=self.consultant_model,
                contents=[pil_img, prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(response.text)
            mask = np.zeros((h, w), dtype=np.uint8)
            for item in data:
                ymin, xmin, ymax, xmax = item['box_2d']
                cv_ymin, cv_xmin = int(ymin * h / 1000), int(xmin * w / 1000)
                cv_ymax, cv_xmax = int(ymax * h / 1000), int(xmax * w / 1000)
                cv2.rectangle(mask, (cv_xmin, cv_ymin), (cv_xmax, cv_ymax), 255, -1)
            return cv2.GaussianBlur(mask, (61, 61), 0)
        except:
            return np.zeros((h, w), dtype=np.uint8)

    # ==========================================
    # [核心處理] 磨皮引擎
    # ==========================================
    def _opencv_smooth_fabric(self, pil_img):
        try:
            USE_SEMANTIC_LOGIC = True # <--- 修改此處切換初期/後期
            
            open_cv_image = np.array(pil_img.convert('RGB'))
            img = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            _, brightness_detail = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            
            if USE_SEMANTIC_LOGIC:
                semantic_area = self._get_semantic_ruffle_mask(pil_img, gray)
                combined_mask = cv2.addWeighted(brightness_detail, 0.4, semantic_area, 0.6, 0)
                smooth_power = 200 
            else:
                max_val = np.max(gray)
                _, highlight_mask = cv2.threshold(gray, max_val * 0.9, 255, cv2.THRESH_BINARY)
                combined_mask = cv2.bitwise_or(brightness_detail, highlight_mask)
                smooth_power = 180
            
            blur_size = int(max(img.shape[:2]) / 40)
            if blur_size % 2 == 0: blur_size += 1
            combined_mask = cv2.GaussianBlur(combined_mask, (blur_size, blur_size), 0)
            mask_3d = cv2.cvtColor(combined_mask, cv2.COLOR_GRAY2BGR).astype(float) / 255.0

            full_smoothed = cv2.bilateralFilter(img, d=15, sigmaColor=smooth_power, sigmaSpace=75)
            result = (img.astype(float) * (1.0 - mask_3d) + full_smoothed.astype(float) * mask_3d)
            result = result.clip(0, 255).astype(np.uint8)

            avg_brightness = np.mean(gray)
            dynamic_gamma = 1.4 if avg_brightness < 127 else 1.1
            invGamma = 1.0 / dynamic_gamma
            table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
            final_cv_img = cv2.LUT(result, table)

            return Image.fromarray(cv2.cvtColor(final_cv_img, cv2.COLOR_BGR2RGB))
        except Exception as e:
            logger.error(f"處理失敗: {e}")
            return pil_img

    def remove_background(self, clothes_image):
        if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
        input_img = Image.open(clothes_image).convert("RGBA")
        output_img = remove(input_img, session=self.rembg_session)
        
        bbox = output_img.getbbox()
        if bbox: output_img = output_img.crop(bbox)

        r, g, b, a = output_img.split()
        rgb_img = Image.merge('RGB', (r, g, b))
        smoothed_rgb = self._opencv_smooth_fabric(rgb_img)

        output_img = Image.merge('RGBA', (*smoothed_rgb.split(), a))
        output_img = ImageEnhance.Contrast(output_img).enhance(0.85) 
        
        filename, save_path = self._get_unique_filename(prefix="processed_cloth", ext="png")
        output_img.save(save_path, "PNG")
        return save_path

    # ==========================================
    # [合成] 整合 OpenCV 顏色矩陣資訊
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path):
        if not self.client: raise ValueError("Gemini Client 未初始化")
        
        # 提取物理顏色特徵
        rgb_matrix = self._extract_bgr_matrix(clean_clothes_path)
        
        pil_model = Image.open(model_image)
        pil_cloth = Image.open(clean_clothes_path)
        
        vfx_prompt = f"""
        ACT AS: Professional VFX Compositor.
        TASK: Wrap [Image 1] onto the model in [Image 2].
        TECHNICAL DATA: 
        - The absolute base color (Albedo) of this garment is RGB{rgb_matrix}.
        RULES: 
        1. Stick to RGB{rgb_matrix} for fabric surface; avoid color drift.
        2. Eliminate original heavy shadow crevices.
        3. Create NEW folds matching [Image 2]'s studio lighting.
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name, 
                contents=[pil_cloth, pil_model, vfx_prompt]
            )
            final_save_path = None
            if response.parts:
                for part in response.parts:
                    if part.inline_data:
                        image = part.as_image()
                        _, final_save_path = self._get_unique_filename(prefix="final_tryon", ext="png")
                        image.save(final_save_path)
            return final_save_path, f"Matrix Extraction: {rgb_matrix}"
        except Exception as e:
            logger.error(f"合成失敗: {e}")
            raise e