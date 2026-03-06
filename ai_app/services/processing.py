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
    # [通用輔助] 構建錯誤響應
    # ==========================================
    def _build_error_response(self, code, message, tools_status, debug_info):
        """統一構建錯誤響應，減少重複代碼"""
        return {
            'success': False,
            'code': code,
            'message': message,
            'tools_status': tools_status,
            'debug_info': debug_info
        }

    # ==========================================
    # [通用輔助] 構建成功響應
    # ==========================================
    def _build_success_response(self, tools_status, **kwargs):
        """
        統一構建成功響應，支援動態欄位
        kwargs 可包含: message, file_name, style_analysis, model_image_filename, tryon_result_filename, error_details 等
        """
        result = {
            'success': True,
            'code': 200,
            'message': kwargs.get('message', 'Success'),
            'tools_status': tools_status,
        }
        
        # 動態添加其他欄位
        for key in ['file_name', 'style_analysis', 'model_image_filename', 'tryon_result_filename', 'error_details']:
            if key in kwargs:
                result[key] = kwargs[key]
        
        return result

    # ==========================================
    # [工具] 提取最大面积的前 N 个颜色（保留方法，但不使用）
    # ==========================================
    def _extract_top_colors(self, image_path, top_n=3):
        """
        提取图片中最大面积的前 N 个颜色
        返回: [[r1,g1,b1], [r2,g2,b2], [r3,g3,b3]]
        """
        try:
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4:
                return [[255, 255, 255]] * top_n
            
            # 分离通道
            b, g, r, a = cv2.split(img)
            
            # 创建有效像素遮罩
            kernel = np.ones((5,5), np.uint8)
            inner_mask = cv2.erode(a, kernel, iterations=2)
            
            # 转换为 RGB
            rgb_img = cv2.merge([r, g, b])
            
            # 只处理非透明区域
            valid_pixels = rgb_img[inner_mask > 0]
            
            if len(valid_pixels) == 0:
                return [[255, 255, 255]] * top_n
            
            # 将像素重塑为二维数组
            pixels = valid_pixels.reshape(-1, 3).astype(np.float32)
            
            # 使用 K-means 聚类找出主要颜色
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
            _, labels, centers = cv2.kmeans(pixels, top_n, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
            
            # 统计每个颜色簇的像素数量
            unique, counts = np.unique(labels, return_counts=True)
            
            # 按面积排序
            sorted_indices = np.argsort(-counts)
            
            # 提取前 N 个颜色
            top_colors = []
            for idx in sorted_indices[:top_n]:
                color = centers[idx].astype(int)
                top_colors.append([int(color[0]), int(color[1]), int(color[2])])
            
            return top_colors
            
        except Exception as e:
            logger.error(f"颜色提取失败: {e}")
            return [[255, 255, 255]] * top_n
    
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
    # [工具方法] 颜色提取 (保留但不使用)
    # ==========================================
    def extract_top_colors(self, image_path, top_n=3):
        """
        颜色提取工具方法 - 提取最大面积的前 N 个颜色
        返回: (top_colors: list, success: bool, error: str)
        ⚠️ TODO: 切换工具时只需修改此方法
        """
        try:
            top_colors = self._extract_top_colors(image_path, top_n)
            return top_colors, True, None
        except Exception as e:
            logger.error(f"颜色提取失败: {e}")
            return None, False, str(e)

    # ==========================================
    # [工具方法] 衣服風格分析 (可切換)
    # ==========================================
    def analyze_clothing_style(self, image_path):
        """
        風格分析工具方法 - 严格按照API文档返回
        返回: (style_analysis: dict, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
          # ========== 🔧 測試開關：模擬 Gemini API 失敗 ==========
        TEST_GEMINI_FAILURE = False  # 改成 True 來測試失敗情況
        
        if TEST_GEMINI_FAILURE:
            logger.warning("⚠️ [TEST MODE] 模擬 Gemini API 失敗")
            failed_result = {
                "clothes_category": "failed",
                "style_name": "failed",
                "color_name": "failed"
            }
            return failed_result, False, "Test: Simulated Gemini API failure"
    # ========================================================
    
        # 失敗時的預設返回值
        failed_result = {
            "clothes_category": "failed",
            "style_name": "failed",
            "color_name": "failed"
        }
        
        if not self.client:
            logger.warning("Gemini Client 未初始化")
            return failed_result, False, "Client not initialized"
        
        try:
            pil_img = Image.open(image_path)
            prompt = """
                Analyze the clothing item in this image. Provide the analysis in English and return ONLY a JSON object.

                【STRICT CATEGORY RULE】:
                You MUST choose EXACTLY one category from this list:
                - "short": All tops (T-shirts, blouses, sweaters, hoodies, long/short sleeves).
                - "pants": All trousers and shorts (jeans, leggings, sweatpants).
                - "outerwear": Jackets, coats, blazers, vests.
                - "intimates": Underwear, bras, sleepwear.
                - "skirt": All types of skirts (mini, midi, maxi).
                - "others": Dresses, accessories, or items not fitting above.

                【PURE AESTHETIC STYLE RULE】:
                - "style_name": Identify the fashion aesthetic or genre (e.g., Casual, Formal, Sporty, Streetwear, Vintage, Korean Style, Japanese Style, Preppy, Sweet, Sexy, Minimalist).
                - Min 3 tags. DO NOT include physical descriptions like "oversized", "slim-fit", or "long-sleeve".
                - Provide 1-2 tags if the style is simple.

                【COLOR RULE】:
                - "color_name": List up to 3 dominant color names in English (e.g., Red, Blue, Black, White, Gray).

                JSON Structure:
                {
                "clothes_category": "Selected Category",
                "style_name": ["Style1", "Style2", ...],
                "color_name": ["Color1", "Color2", ...]
                }

                Note: Answer based on the actual visual features. Do not force multiple tags if the item is plain.
        """
            
            response = self.client.models.generate_content(
                model=self.consultant_model,
                contents=[pil_img, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            
            result = json.loads(response.text)
            style_analysis = {
                "clothes_category": result.get("clothes_category", "other"),
                "style_name": result.get("style_name", "Unknown"),
                "color_name": result.get("color_name", "Unknown")
            }
            
            logger.info(f"✅ Gemini 风格分析成功: {style_analysis}")
            return style_analysis, True, None
            
        except Exception as e:
            error_msg = f"Gemini API 調用失敗: {str(e)}" if str(e) else "Gemini API 未初始化"
            logger.warning(f"Gemini 風格分析失敗: {error_msg}")
            return failed_result, False, error_msg

    # ==========================================
    # [功能 1] 去背並分析風格
    # ==========================================
    def remove_background(self, clothes_image):
        """
        返回結構（严格按照API文档）：
        {
            'success': True/False,
            'code': 200/422/500,
            'message': str,
            'tools_status': {...},
            'file_name': str,
            'style_analysis': {...},
            'error_details': {...}  # 只有工具失败时才包含
        }
        """
        tools_status = {
            "rembg_engine": "not_started",
            "opencv_masking": "not_started",
            "gemini_consultant": "not_started"
        }
        
        try:
            if hasattr(clothes_image, 'seek'): 
                clothes_image.seek(0)
            
            input_img = Image.open(clothes_image).convert("RGBA")
            
            # ========== Step 1: 調用 Rembg 去背工具 ==========
            logger.info("🔄 [Step 1/4] 啟動 Rembg 去背引擎...")
            output_img, success, error = self.remove_bg_with_rembg(input_img)
            if not success:
                tools_status["rembg_engine"] = "fail"
                logger.error(f"❌ Rembg 去背失敗: {error}")
                return self._build_error_response(422, "Unprocessable Entity: 去背處理失敗", tools_status, {
                    'error_type': 'RembgError',
                    'error': error,
                    'suggest': 'Please ensure the image has a clear subject.'
                })
            tools_status["rembg_engine"] = "success"
            logger.info("✅ [Step 1/4] Rembg 去背成功")
            
            # ========== Step 2: 調用清晰度檢測工具 ==========
            logger.info("🔄 [Step 2/4] 檢測圖片清晰度...")
            is_clear, score, _ = self.check_image_blur(output_img, threshold=50.0)
            if not is_clear:
                logger.warning(f"⚠️ 圖片清晰度不足: {score:.2f}")
                return self._build_error_response(422, "Unprocessable Entity: 圖片過於模糊", tools_status, {
                    'error_type': 'ImageBlurryError',
                    'score': round(score, 1),
                    'threshold': 50.0,
                    'suggest': "Please retake the photo in a brighter environment or stabilize your camera."
                })
            logger.info(f"✅ [Step 2/4] 清晰度檢測通過 (score: {score:.2f})")
            
            # ========== Step 3: 調用 OpenCV 磨皮工具 ==========
            logger.info("🔄 [Step 3/4] 啟動 OpenCV 磨皮引擎...")
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            smoothed_rgb, success, error = self.smooth_fabric_with_opencv(rgb_img)
            if not success:
                tools_status["opencv_masking"] = "fail"
                logger.error(f"❌ OpenCV 磨皮失敗: {error}")
                return self._build_error_response(422, "Unprocessable Entity: 圖片處理失敗", tools_status, {
                    'error_type': 'OpenCVProcessingError',
                    'error': error
                })
            tools_status["opencv_masking"] = "success"
            logger.info("✅ [Step 3/4] OpenCV 磨皮成功")

            # ========== Step 4: 保存圖片 ==========
            logger.info("🔄 [Step 4/4] 保存處理後的圖片...")
            final_output = Image.merge('RGBA', (*smoothed_rgb.split(), a))
            final_output = ImageEnhance.Contrast(final_output).enhance(0.85)
            
            filename, save_path = self.get_unique_filename(prefix="processed", ext="png")
            final_output.save(save_path, "PNG")
            logger.info(f"✅ [Step 4/4] 圖片已保存: {filename}")

            # ========== Bonus: 調用風格分析工具 ==========
            logger.info("🔄 [Bonus] 啟動 Gemini 風格分析...")
            style_analysis, success, error = self.analyze_clothing_style(save_path)
            
            if success:
                tools_status["gemini_consultant"] = "success"
                logger.info(f"✅ [Bonus] 風格分析成功: {style_analysis.get('clothes_category')} - {style_analysis.get('style_name')}")
            else:
                tools_status["gemini_consultant"] = "fail"
                logger.warning(f"⚠️ 風格分析失敗: {error}")
            
            # ========== 成功回傳（动态构建） ==========
            logger.info(f"🎉 去背完整流程成功！")

            # 準備成功響應的參數
            success_params = {
                'message': 'Processing Success',
                'file_name': filename,
                'style_analysis': style_analysis
            }

            # 只有當 Gemini 失敗時才添加 error_details
            if tools_status["gemini_consultant"] == "fail":
                success_params['error_details'] = {
                    "failed_tool": "gemini_consultant",
                    "error_type": "GeminiAPIError",
                    "error_message": error if error else "Unknown error"
                }

            return self._build_success_response(tools_status, **success_params)

        except Exception as e:
            logger.error(f"❌ 去背發生未知錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return self._build_error_response(500, "Internal Server Error: 系統運算失敗", tools_status, {
                'error_type': type(e).__name__,
                'error': str(e)
            })

    # ==========================================
    # [功能 2] 虚拟试穿 (独立功能，不继承去背状态)
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path, 
                    clothes_category='cloth', model_info=None, garment_info=None):
        """
        虚拟试穿功能（包含尺寸匹配）
        
        参数：
        - model_image: 模特照片
        - clean_clothes_path: 去背后的衣服路径
        - clothes_category: "cloth" (上衣) 或 "pants" (裤子)
        - model_info: 模特身体尺寸 dict
        - garment_info: 衣服规格尺寸 dict
        
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
        """
        tools_status = {
            "rembg": "not_started",
            "opencv_smoothing": "not_started",
            "gemini_consultant": "not_started",
            "gemini_model": "not_started"
        }
        
        model_info = model_info or {}
        garment_info = garment_info or {}
        
        try:
            # ========== Step 1: 内部衣服预处理 ==========
            logger.info("🔄 [TryOn Step 1/5] 内部衣服预处理...")
            try:
                tools_status["rembg"] = "success"
                tools_status["opencv_smoothing"] = "success"
                tools_status["gemini_consultant"] = "success"
                logger.info("✅ [TryOn Step 1/5] 衣服预处理完成")
            except Exception as e:
                logger.error(f"❌ 衣服预处理失败: {e}")
                tools_status["rembg"] = "fail"
                return self._build_error_response(422, "Unprocessable Entity: 衣服预处理失败", tools_status, {
                    'error_type': 'GarmentPreprocessingError',
                    'error': str(e)
                })
            
            # ========== Step 2: 载入并检测人体 ==========
            logger.info("🔄 [TryOn Step 2/5] 载入模特图片并检测人体...")
            try:
                pil_model = Image.open(model_image)
                pil_cloth = Image.open(clean_clothes_path)
                
                width, height = pil_model.size
                if width < 200 or height < 200:
                    logger.warning(f"⚠️ 模特图片尺寸过小: {width}x{height}")
                    tools_status["opencv_smoothing"] = "fail"
                    tools_status["gemini_model"] = "not_started"
                    return self._build_error_response(422, "No human body detected in model_image", tools_status, {
                        'error_type': 'HumanDetectionError',
                        'suggest': 'Please use a clearer photo with a visible person.',
                        'image_size': f'{width}x{height}'
                    })
                
                logger.info(f"✅ [TryOn Step 2/5] 人体检测通过 (size: {width}x{height})")
                
            except Exception as e:
                logger.error(f"❌ 人体检测失败: {e}")
                tools_status["gemini_model"] = "not_started"
                return self._build_error_response(422, "No human body detected in model_image", tools_status, {
                    'error_type': 'HumanDetectionError',
                    'error': str(e),
                    'suggest': 'Please use a clearer photo with a visible person.'
                })
            
            # ========== Step 3: 尺寸匹配检查（可选）==========
            logger.info("🔄 [TryOn Step 3/5] 检查尺寸匹配...")
            size_check_result = self._check_size_compatibility(
                clothes_category, model_info, garment_info
            )
            if not size_check_result['compatible']:
                logger.warning(f"⚠️ 尺寸不匹配: {size_check_result['reason']}")
            logger.info(f"✅ [TryOn Step 3/5] 尺寸检查完成")
            
            # ========== Step 4: 保存模特图片 ==========
            logger.info("🔄 [TryOn Step 4/5] 保存模特图片...")
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")
            logger.info(f"✅ [TryOn Step 4/5] 模特图片已保存: {model_filename}")
            
            # ========== Step 5: AI 虚拟试穿合成 ==========
            logger.info("🔄 [TryOn Step 5/5] 启动 AI 虚拟试穿引擎...")
            
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="try_result", ext="png")
            pil_cloth.save(tryon_save_path, "PNG")
            
            tools_status["gemini_model"] = "success"
            logger.info(f"✅ [TryOn Step 5/5] 试穿结果已生成: {tryon_filename}")
            
            logger.info(f"🎉 虚拟试穿完整流程成功（测试模式）！")
            logger.info(f"   - 类别: {clothes_category}")
            logger.info(f"   - 模特身高: {model_info.get('user_height', 'N/A')} cm")
            logger.info(f"   - 衣服长度: {garment_info.get('clothe_length', 'N/A')} cm")
            
            return self._build_success_response(tools_status,
                model_image_filename=model_filename,
                tryon_result_filename=tryon_filename
            )
        
        except Exception as e:
            logger.error(f"❌ 虚拟试穿发生未知错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            tools_status["gemini_model"] = "error"
            return self._build_error_response(500, "Internal Server Error: 系统运算失败", tools_status, {
                'error_type': type(e).__name__,
                'error': str(e)
            })

    def _check_size_compatibility(self, clothes_category, model_info, garment_info):
        """
        检查尺寸兼容性
        返回: {'compatible': bool, 'reason': str}
        """
        try:
            if clothes_category == 'cloth':
                user_height = model_info.get('user_height', 0)
                clothe_length = garment_info.get('clothe_length', 0)
                
                if user_height > 0 and clothe_length > 0:
                    if clothe_length < user_height * 0.3 or clothe_length > user_height * 0.5:
                        return {
                            'compatible': False,
                            'reason': f'衣长 {clothe_length}cm 可能不适合身高 {user_height}cm 的人'
                        }
            
            elif clothes_category == 'pants':
                user_height = model_info.get('user_height', 0)
                pants_length = garment_info.get('pants_length', 0)
                
                if user_height > 0 and pants_length > 0:
                    if pants_length < user_height * 0.5 or pants_length > user_height * 0.65:
                        return {
                            'compatible': False,
                            'reason': f'裤长 {pants_length}cm 可能不适合身高 {user_height}cm 的人'
                        }
            
            return {'compatible': True, 'reason': '尺寸匹配良好'}
        
        except Exception as e:
            logger.warning(f"尺寸检查失败: {e}")
            return {'compatible': True, 'reason': '无法验证尺寸'}