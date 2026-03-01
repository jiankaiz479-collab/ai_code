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
        
        self.consultant_model = os.getenv("GEMINI_CONSULTANT_MODEL", "gemini-1.5-flash")
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-exp")

    def get_unique_filename(self, prefix="img", ext="png"):
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    # [工具] OpenCV BGR 矩陣提取 (原始方法)
    # ==========================================
    def _extract_bgr_matrix(self, image_path):
        try:
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4: return [200, 200, 200]

            b, g, r, a = cv2.split(img)
            kernel = np.ones((5,5), np.uint8)
            inner_mask = cv2.erode(a, kernel, iterations=2) 
            
            bgr_tmp = cv2.merge([b, g, r])
            hsv = cv2.cvtColor(bgr_tmp, cv2.COLOR_BGR2HSV)
            v_channel = hsv[:, :, 2]

            physical_mask = (inner_mask > 0) & (v_channel > 50) & (v_channel < 220)
            
            if not np.any(physical_mask): return [255, 192, 203]

            mean_b = np.median(b[physical_mask])
            mean_g = np.median(g[physical_mask])
            mean_r = np.median(r[physical_mask])
            
            return [int(mean_r), int(mean_g), int(mean_b)]
        except Exception as e:
            logger.error(f"色彩矩陣提取失敗: {e}")
            return [255, 255, 255]

    # ==========================================
    # [後期開發] 語意遮罩生成 (原始方法)
    # ==========================================
    def _get_semantic_ruffle_mask(self, pil_img, gray_cv_img):
        h, w = gray_cv_img.shape
        prompt = """
        Identify precise bounding boxes for "deep_shadows" and "specular_highlights".
        Return JSON: [{"label": string, "box_2d": [ymin, xmin, ymax, xmax]}].
        Normalized to 1000.
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
    # [核心] OpenCV 磨皮引擎 (原始方法)
    # ==========================================
    def _opencv_smooth_fabric(self, pil_img):
        try:
            USE_SEMANTIC_LOGIC = False 
            
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
                smooth_power = 160

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
            logger.error(f"OpenCV 磨皮失敗: {e}")
            return pil_img

    # ==========================================
    # [工具方法] Rembg 去背 (可切換)
    # ==========================================
    def remove_bg_with_rembg(self, input_img):
        """
        去背工具方法
        返回: (output_img, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        try:
            output_img = remove(input_img, session=self.rembg_session)
            bbox = output_img.getbbox()
            if bbox: 
                output_img = output_img.crop(bbox)
            return output_img, True, None
        except Exception as e:
            logger.error(f"Rembg 去背失敗: {e}")
            return None, False, str(e)

    # ==========================================
    # [工具方法] 圖片清晰度檢測 (可切換)
    # ==========================================
    def check_image_blur(self, pil_img, threshold=50.0):
        """
        清晰度檢測工具方法
        返回: (is_clear: bool, score: float, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        try:
            gray = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            is_clear = laplacian_var >= threshold
            return is_clear, laplacian_var, None
        except Exception as e:
            logger.warning(f"清晰度檢測失敗: {e}")
            return True, 0, str(e)

    # ==========================================
    # [工具方法] OpenCV 磨皮處理 (可切換)
    # ==========================================
    def smooth_fabric_with_opencv(self, rgb_img):
        """
        磨皮工具方法
        返回: (smoothed_rgb, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        try:
            smoothed_rgb = self._opencv_smooth_fabric(rgb_img)
            return smoothed_rgb, True, None
        except Exception as e:
            logger.error(f"OpenCV 磨皮失敗: {e}")
            return None, False, str(e)

    # ==========================================
    # [工具方法] 顏色矩陣提取 (可切換)
    # ==========================================
    def extract_color_matrix(self, image_path):
        """
        顏色提取工具方法
        返回: (rgb_matrix: list, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        try:
            rgb_matrix = self._extract_bgr_matrix(image_path)
            return rgb_matrix, True, None
        except Exception as e:
            logger.error(f"顏色提取失敗: {e}")
            return None, False, str(e)

    # ==========================================
    # [工具方法] 衣服風格分析 (可切換)
    # ==========================================
    def analyze_clothing_style(self, image_path):
        """
        風格分析工具方法
        返回: (style_analysis: dict, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        # ========== 測試硬編碼版本（暫時） ==========
        style_analysis = {
            "ategory": "T-Shirt",
            "style": "Casual",
            "season": "Universal",
            "color": "red",  
            "analysis": "This is a casual t-shirt with a relaxed fit, suitable for everyday wear."
        }
        return style_analysis, True, None
        
        # ========== 真實 Gemini API 版本（待啟用） ==========
        # if self.client:
        #     try:
        #         pil_img = Image.open(image_path)
        #         prompt = """
        #         Analyze the clothing item in this image. Return JSON format:
        #         {
        #             "clothing_type": "type of garment",
        #             "style": "style category (Casual, Formal, Vintage, etc.)",
        #             "season": "suitable season",
        #             "analysis": "brief description"
        #         }
        #         """
        #         response = self.client.models.generate_content(
        #             model=self.consultant_model,
        #             contents=[pil_img, prompt],
        #             config=types.GenerateContentConfig(response_mime_type="application/json")
        #         )
        #         return json.loads(response.text), True, None
        #     except Exception as e:
        #         logger.warning(f"Gemini 風格分析失敗: {e}")
        #         return {
        #             "clothing_type": "Unknown",
        #             "style": "Unknown",
        #             "season": "Universal",
        #             "analysis": "Analysis failed"
        #         }, False, str(e)
        # else:
        #     return {
        #         "clothing_type": "Unknown",
        #         "style": "Unknown",
        #         "season": "Universal",
        #         "analysis": "Client not initialized"
        #     }, False, "Client not initialized"

    # ==========================================
    # [功能 1] 去背並提取顏色矩陣
    # ==========================================
    def remove_background(self, clothes_image):
        """
        返回結構：
        {
            'success': True/False,
            'code': 200/400/415/422/500,
            'message': str,
            'tools_status': [{...}],
            'file_name': str,
            'rgb_matrix': [r, g, b],
            'style_analysis': {...},
            'debug_info': {...}  # 仅失败时
        }
        """
        tools_status = {
            "rembg_engine": "not_started",
            "opencv_masking": "not_started",
            "gemini_consultant": "not_started",
            "color_extraction": "not_started"
        }
        
        try:
            if hasattr(clothes_image, 'seek'): 
                clothes_image.seek(0)
            
            input_img = Image.open(clothes_image).convert("RGBA")
            
            # ========== Step 1: 調用 Rembg 去背工具 ==========
            output_img, success, error = self.remove_bg_with_rembg(input_img)
            if not success:
                tools_status["rembg_engine"] = "fail"
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 圖片過於模糊",
                    'tools_status': [tools_status],
                    'debug_info': {
                        'error_type': 'RembgError',
                        'error': error
                    }
                }
            tools_status["rembg_engine"] = "success"
            
            # ========== Step 2: 調用清晰度檢測工具 ==========
            is_clear, score, _ = self.check_image_blur(output_img, threshold=50.0)
            if not is_clear:
                logger.warning(f"⚠️ 圖片清晰度不足: {score:.2f}")
                return {
                    'success': False,
                    'code': 422,
                    'message': f"Unprocessable Entity: 圖片過於模糊 (score: {score:.1f})",
                    'tools_status': [tools_status],
                    'debug_info': {
                        'error_type': 'ImageBlurryError',
                        'score': round(score, 1),
                        'threshold': 50.0,
                        'suggest': "Please retake the photo in a brighter environment or stabilize your camera."
                    }
                }
            
            # ========== Step 3: 調用 OpenCV 磨皮工具 ==========
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            smoothed_rgb, success, error = self.smooth_fabric_with_opencv(rgb_img)
            if not success:
                tools_status["opencv_masking"] = "fail"
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 圖片過於模糊",
                    'tools_status': [tools_status],
                    'debug_info': {
                        'error_type': 'OpenCVProcessingError',
                        'error': error
                    }
                }
            tools_status["opencv_masking"] = "success"

            # ========== Step 4: 保存圖片 ==========
            final_output = Image.merge('RGBA', (*smoothed_rgb.split(), a))
            final_output = ImageEnhance.Contrast(final_output).enhance(0.85)
            
            filename, save_path = self.get_unique_filename(prefix="processed", ext="png")
            final_output.save(save_path, "PNG")

            # ========== Step 5: 調用顏色提取工具 ==========
            rgb_matrix, success, error = self.extract_color_matrix(save_path)
            if not success:
                tools_status["color_extraction"] = "fail"
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 圖片過於模糊",
                    'tools_status': [tools_status],
                    'debug_info': {
                        'error_type': 'ColorExtractionError',
                        'error': error
                    }
                }
            tools_status["color_extraction"] = "success"

            # ========== Step 6: 調用風格分析工具 ==========
            style_analysis, success, error = self.analyze_clothing_style(save_path)
            tools_status["gemini_consultant"] = "success" if success else "fail"

            # ========== 成功回傳 ==========
            logger.info(f"✅ 去背成功: {filename}, RGB: {rgb_matrix}")
            return {
                'success': True,
                'code': 200,
                'message': "OK: 去背成功",
                'tools_status': [tools_status],
                'file_name': filename,
                'rgb_matrix': rgb_matrix,
                'style_analysis': style_analysis
            }

        except Exception as e:
            logger.error(f"❌ 去背發生未知錯誤: {str(e)}")
            return {
                'success': False,
                'code': 500,
                'message': "Internal Server Error: AI 模型運算失敗",
                'tools_status': [tools_status],
                'debug_info': {
                    'error_type': type(e).__name__,
                    'error': str(e)
                }
            }

    # ==========================================
    # [功能 2] 合成衣服 (接收外部去背圖與顏色矩陣)
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path, rgb_matrix):
        """
        此函式現在直接接收由 remove_background 產出的 path 與 matrix
        返回結構：
        {
            'success': True/False,
            'code': 200/422/500,
            'message': str,
            'tools_status': {...},
            'model_image_filename': str,
            'tryon_result_filename': str,
            'debug_info': {...}  # 仅失败时
        }
        ⚠️ TODO: 正式上線時，刪除硬編碼部分，換成真實 Gemini API 調用
        """
        tools_status = {
            "ai_model": "not_started",
            "frame_boundary": "not_started",
            "human_detection": "not_started"
        }
        
        try:
            # ========== 測試硬編碼版本（暫時） ==========
            pil_model = Image.open(model_image)
            pil_cloth = Image.open(clean_clothes_path)
            
            # 保存 model_image
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")
            tools_status["ai_model"] = "success"
            tools_status["frame_boundary"] = "success"
            tools_status["human_detection"] = "success"
            
            # 保存試穿結果（暫時用衣服圖作為試穿結果的代替）
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="tryon", ext="png")
            pil_cloth.save(tryon_save_path, "PNG")
            
            logger.info(f"✅ 虛擬試穿成功（測試模式）: model={model_filename}, result={tryon_filename}")
            return {
                'success': True,
                'code': 200,
                'message': "OK: 虛擬試穿成功",
                'tools_status': tools_status,
                'model_image_filename': model_filename,
                'tryon_result_filename': tryon_filename
            }
            
            # ========== 真實 Gemini API 版本（待啟用） ==========
            # if not self.client:
            #     return {
            #         'success': False,
            #         'code': 500,
            #         'message': "Internal Server Error: AI 模型運算失敗",
            #         'tools_status': tools_status,
            #         'debug_info': {
            #             'error_type': 'ClientNotInitialized',
            #             'error': "Gemini Client 未初始化"
            #         }
            #     }
            # 
            # try:
            #     pil_model = Image.open(model_image)
            #     pil_cloth = Image.open(clean_clothes_path)
            #     
            #     vfx_prompt = f"""
            #     ACT AS: Professional VFX Technical Director.
            #     
            #     TASK: High-fidelity clothing re-rendering using [Image 1] as the absolute texture and color reference for the model in [Image 2].
            #     
            #     STRICT COLOR CONSTRAINTS:
            #     - COLOR ANCHOR: The garment in [Image 1] has a measured Albedo (flat color) of RGB{rgb_matrix}.
            #     - NO COLOR DRIFT: Do not allow the environment lighting or background of [Image 2] to tint or shift this color. If [Image 2] has a warm/cool cast, the garment must remain physically accurate to RGB{rgb_matrix} while only accepting the luminosity (light/dark) values.
            #     - DELTA-E MINIMIZATION: Minimize perceived color difference between the processed [Image 1] and the final result.
            #     
            #     LIGHTING & SHADOW LOGIC:
            #     - SHADOW NEUTRALITY: Render new shadows using desaturated, neutral tones. Do not use dark-saturated colors for folds, as this creates a "dirty" appearance.
            #     - AMBIENT OCCLUSION: Only apply subtle contact shadows where fabric touches skin, ensuring the color at the contact point remains consistent with the garment's base.
            #     
            #     OUTPUT REQUIREMENT:
            #     The final product must pass a color-matching test against the provided RGB{rgb_matrix}. Ensure the fabric looks "new" and the color "vibrant" without artificial dullness.
            #     """
            #     
            #     response = self.client.models.generate_content(
            #         model=self.model_name, 
            #         contents=[pil_cloth, pil_model, vfx_prompt]
            #     )
            #     tools_status["ai_model"] = "success"
            #     
            #     final_save_path = None
            #     if response.parts:
            #         for part in response.parts:
            #             if part.inline_data:
            #                 image = part.as_image()
            #                 tryon_filename, final_save_path = self.get_unique_filename(prefix="tryon", ext="png")
            #                 image.save(final_save_path)
            #                 tools_status["frame_boundary"] = "success"
            #                 tools_status["human_detection"] = "success"
            #     
            #     if not final_save_path:
            #         return {
            #             'success': False,
            #             'code': 422,
            #             'message': "Unprocessable Entity: 合成結果為空",
            #             'tools_status': tools_status,
            #             'debug_info': {
            #                 'error_type': 'NoOutputError',
            #                 'error': "AI response 沒有圖像數據"
            #             }
            #         }
            #     
            #     # 保存 model_image
            #     model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            #     pil_model.save(model_save_path, "PNG")
            #     
            #     logger.info(f"✅ 虛擬試穿成功: model={model_filename}, result={os.path.basename(final_save_path)}")
            #     return {
            #         'success': True,
            #         'code': 200,
            #         'message': "OK: 虛擬試穿成功",
            #         'tools_status': tools_status,
            #         'model_image_filename': model_filename,
            #         'tryon_result_filename': os.path.basename(final_save_path)
            #     }
            # except Exception as e:
            #     logger.error(f"AI 合成失敗: {e}")
            #     tools_status["ai_model"] = "fail"
            #     return {
            #         'success': False,
            #         'code': 422,
            #         'message': "Unprocessable Entity: AI 模型運算失敗",
            #         'tools_status': tools_status,
            #         'debug_info': {
            #             'error_type': 'AIModelError',
            #             'error': str(e)
            #         }
            #     }
        
        except Exception as e:
            logger.error(f"❌ 虛擬試穿發生未知錯誤: {str(e)}")
            return {
                'success': False,
                'code': 500,
                'message': "Internal Server Error: AI 模型運算失敗",
                'tools_status': tools_status,
                'debug_info': {
                    'error_type': type(e).__name__,
                    'error': str(e)
                }
            }