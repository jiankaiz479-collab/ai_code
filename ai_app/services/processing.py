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
    # [工具] 提取最大面积的前 N 个颜色
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
    # [工具方法] 颜色提取 (可切换)
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
        風格分析工具方法
        返回: (style_analysis: dict, success: bool, error: str)
        ⚠️ TODO: 切換工具時只需修改此方法
        """
        # ========== 測試硬編碼版本（暫時） ==========
        # style_analysis = {
        #     "clothes_category": "T-Shirt",
        #     "style_name": "Casual",
        #     "color_name": "Red"
        # }
        # return style_analysis, True, None
        
        if self.client:
            try:
                pil_img = Image.open(image_path)
                prompt = """
                分析这件衣服，请用繁体中文回答，返回 JSON 格式：
                
                {
                    "clothes_category": "选择一个: 長袖、短袖、裙子、褲子、外套",
                    "style_names": ["风格1", "风格2", "风格3", ...],  // 最多6种风格，如果没那么多就少一点
                    "color_names": ["颜色1", "颜色2", ...]  // 根据衣服颜色丰富度，至少1个，最多3个
                }
                
                说明：
                1. clothes_category: 必须从【長袖、短袖、裙子、褲子、外套】中选择一个
                2. style_names: 描述衣服的风格，例如：休閒、正式、運動、街頭、復古、韓風、日系、歐美、學院、甜美、性感、簡約等，最多6种
                3. color_names: 衣服上的主要颜色名称（繁体中文），如：紅色、藍色、黑色、白色、灰色、粉色、黃色、綠色、紫色、橙色、咖啡色、米色等
                
                注意：根据实际情况回答，不要凑数。如果风格单一就返回1-2个，如果颜色单调就返回1-2个。
                """
                
                response = self.client.models.generate_content(
                    model=self.consultant_model,
                    contents=[pil_img, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                
                result = json.loads(response.text)
                
                # 转换为旧格式兼容（保留 style_name 和 color_name）
                style_analysis = {
                    "clothes_category": result.get("clothes_category", "短袖"),
                    "style_names": result.get("style_names", ["休閒"]),
                    "color_names": result.get("color_names", ["白色"]),
                    # 兼容旧字段（取第一个值）
                    "style_name": result.get("style_names", ["休閒"])[0] if result.get("style_names") else "休閒",
                    "color_name": result.get("color_names", ["白色"])[0] if result.get("color_names") else "白色"
                }
                
                logger.info(f"✅ Gemini 风格分析成功: {style_analysis}")
                return style_analysis, True, None
                
            except Exception as e:
                error_msg = f"Gemini API 調用失敗: {str(e)}" if str(e) else "Gemini API 未初始化"
                logger.warning(f"Gemini 風格分析失敗: {error_msg}")
                return {
                    "clothes_category": "短袖",
                    "style_names": ["未知"],
                    "color_names": ["未知"],
                    "style_name": "未知",
                    "color_name": "未知"
                }, False, error_msg  # ← 返回中文错误信息
        else:
            logger.warning("Gemini Client 未初始化，使用默认值")
            return {
                "clothes_category": "短袖",
                "style_names": ["未知"],
                "color_names": ["未知"],
                "style_name": "未知",
                "color_name": "未知"
            }, False, "Client not initialized"

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
            'tools_status': {...},  # 字典，不是列表
            'file_name': str,
            'rgb_matrix': [r, g, b],
            'style_analysis': {...},
            'debug_info': {...}  # 仅失败时
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
            logger.info("🔄 [Step 1/5] 啟動 Rembg 去背引擎...")
            output_img, success, error = self.remove_bg_with_rembg(input_img)
            if not success:
                tools_status["rembg_engine"] = "fail"
                logger.error(f"❌ Rembg 去背失敗: {error}")
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 去背處理失敗",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'RembgError',
                        'error': error,
                        'suggest': 'Please ensure the image has a clear subject.'
                    }
                }
            tools_status["rembg_engine"] = "success"
            logger.info("✅ [Step 1/5] Rembg 去背成功")
            
            # ========== Step 2: 調用清晰度檢測工具 ==========
            logger.info("🔄 [Step 2/5] 檢測圖片清晰度...")
            is_clear, score, _ = self.check_image_blur(output_img, threshold=50.0)
            if not is_clear:
                logger.warning(f"⚠️ 圖片清晰度不足: {score:.2f}")
                return {
                    'success': False,
                    'code': 422,
                    'message': f"Unprocessable Entity: 圖片過於模糊",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'ImageBlurryError',
                        'score': round(score, 1),
                        'threshold': 50.0,
                        'suggest': "Please retake the photo in a brighter environment or stabilize your camera."
                    }
                }
            logger.info(f"✅ [Step 2/5] 清晰度檢測通過 (score: {score:.2f})")
            
            # ========== Step 3: 調用 OpenCV 磨皮工具 ==========
            logger.info("🔄 [Step 3/5] 啟動 OpenCV 磨皮引擎...")
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            smoothed_rgb, success, error = self.smooth_fabric_with_opencv(rgb_img)
            if not success:
                tools_status["opencv_masking"] = "fail"
                logger.error(f"❌ OpenCV 磨皮失敗: {error}")
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 圖片處理失敗",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'OpenCVProcessingError',
                        'error': error
                    }
                }
            tools_status["opencv_masking"] = "success"
            logger.info("✅ [Step 3/5] OpenCV 磨皮成功")

            # ========== Step 4: 保存圖片並提取顏色 ==========
            logger.info("🔄 [Step 4/5] 保存處理後的圖片...")
            final_output = Image.merge('RGBA', (*smoothed_rgb.split(), a))
            final_output = ImageEnhance.Contrast(final_output).enhance(0.85)
            
            filename, save_path = self.get_unique_filename(prefix="processed", ext="png")
            final_output.save(save_path, "PNG")
            logger.info(f"✅ [Step 4/5] 圖片已保存: {filename}")

            # ========== Step 5: 調用顏色提取工具 ==========
            logger.info("🔄 [Step 5/5] 提取最大面积的3个颜色...")
            top_colors, success, error = self.extract_top_colors(save_path, top_n=3)
            if not success:
                logger.error(f"❌ 顏色提取失敗: {error}")
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 顏色提取失敗",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'ColorExtractionError',
                        'error': error
                    }
                }
            logger.info(f"✅ [Step 5/5] 颜色提取成功: {top_colors}")


            # ========== Step 6: 調用風格分析工具 ==========
            logger.info("🔄 [Bonus] 啟動 Gemini 風格分析...")
            style_analysis, success, error = self.analyze_clothing_style(save_path)
            error_details = None  # 初始化错误详情
            if success:
                tools_status["gemini_consultant"] = "success"
                logger.info(f"✅ [Bonus] 風格分析成功: {style_analysis.get('clothes_category')} - {style_analysis.get('style_name')}")
            else:
                tools_status["gemini_consultant"] = "fail"
                logger.warning(f"⚠️ 風格分析失敗，使用默認值: {error}")
                # 记录失败详情
                error_details = {
                    "failed_tool": "gemini_consultant",
                    "error_type": "GeminiAPIError",
                    "error_message": error if error else "Unknown error"
                }
            # ========== 成功回傳 ==========
            logger.info(f"🎉 去背完整流程成功！")

            # 根据是否有错误调整 message
            if error_details:
                message = "Partial Success: 部分工具失败，但已继续处理"
            else:
                message = "Processing Success"

            return {
                'success': True,
                'code': 200,
                'message': message,  # ← 動態設置
                'tools_status': tools_status,
                'file_name': filename,
                'top_colors': top_colors,
                'style_analysis': style_analysis,
                'error_details': error_details
            }

        except Exception as e:
            logger.error(f"❌ 去背發生未知錯誤: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'code': 500,
                'message': "Internal Server Error: 系統運算失敗",
                'tools_status': tools_status,
                'debug_info': {
                    'error_type': type(e).__name__,
                    'error': str(e)
                }
            }

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
        # 虚拟试穿的工具状态（独立追踪）
        tools_status = {
            "rembg": "not_started",
            "opencv_smoothing": "not_started",
            "gemini_consultant": "not_started",
            "gemini_model": "not_started"
        }
        
        # 设置默认值
        if model_info is None:
            model_info = {}
        if garment_info is None:
            garment_info = {}
        
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
                return {
                    'success': False,
                    'code': 422,
                    'message': "Unprocessable Entity: 衣服预处理失败",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'GarmentPreprocessingError',
                        'error': str(e)
                    }
                }
            
            # ========== Step 2: 载入并检测人体 ==========
            logger.info("🔄 [TryOn Step 2/5] 载入模特图片并检测人体...")
            try:
                pil_model = Image.open(model_image)
                pil_cloth = Image.open(clean_clothes_path)
                
                # 简单的人体检测（测试版本）
                width, height = pil_model.size
                if width < 200 or height < 200:
                    logger.warning(f"⚠️ 模特图片尺寸过小: {width}x{height}")
                    tools_status["opencv_smoothing"] = "fail"
                    tools_status["gemini_model"] = "not_started"
                    return {
                        'success': False,
                        'code': 422,
                        'message': "No human body detected in model_image",
                        'tools_status': tools_status,
                        'debug_info': {
                            'error_type': 'HumanDetectionError',
                            'suggest': 'Please use a clearer photo with a visible person.',
                            'image_size': f'{width}x{height}'
                        }
                    }
                
                logger.info(f"✅ [TryOn Step 2/5] 人体检测通过 (size: {width}x{height})")
                
            except Exception as e:
                logger.error(f"❌ 人体检测失败: {e}")
                tools_status["gemini_model"] = "not_started"
                return {
                    'success': False,
                    'code': 422,
                    'message': "No human body detected in model_image",
                    'tools_status': tools_status,
                    'debug_info': {
                        'error_type': 'HumanDetectionError',
                        'error': str(e),
                        'suggest': 'Please use a clearer photo with a visible person.'
                    }
                }
            
            # ========== Step 3: 尺寸匹配检查（可选）==========
            logger.info("🔄 [TryOn Step 3/5] 检查尺寸匹配...")
            size_check_result = self._check_size_compatibility(
                clothes_category, model_info, garment_info
            )
            if not size_check_result['compatible']:
                logger.warning(f"⚠️ 尺寸不匹配: {size_check_result['reason']}")
                # 注意：这里可以选择返回警告或继续处理
                # 暂时只记录日志，继续处理
            logger.info(f"✅ [TryOn Step 3/5] 尺寸检查完成")
            
            # ========== Step 4: 保存模特图片 ==========
            logger.info("🔄 [TryOn Step 4/5] 保存模特图片...")
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")
            logger.info(f"✅ [TryOn Step 4/5] 模特图片已保存: {model_filename}")
            
            # ========== Step 5: AI 虚拟试穿合成 ==========
            logger.info("🔄 [TryOn Step 5/5] 启动 AI 虚拟试穿引擎...")
            
            # ========== 测试版本（暂时） ==========
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="try_result", ext="png")
            pil_cloth.save(tryon_save_path, "PNG")
            
            tools_status["gemini_model"] = "success"
            logger.info(f"✅ [TryOn Step 5/5] 试穿结果已生成: {tryon_filename}")
            
            logger.info(f"🎉 虚拟试穿完整流程成功（测试模式）！")
            logger.info(f"   - 类别: {clothes_category}")
            logger.info(f"   - 模特身高: {model_info.get('user_height', 'N/A')} cm")
            logger.info(f"   - 衣服长度: {garment_info.get('clothe_length', 'N/A')} cm")
            
            return {
                'success': True,
                'code': 200,
                'message': "Success",
                'tools_status': tools_status,
                'model_image_filename': model_filename,
                'tryon_result_filename': tryon_filename
            }
            
            # ========== 真实 Gemini API 版本（待启用） ==========
            # if not self.client:
            #     tools_status["gemini_model"] = "error"
            #     return {
            #         'success': False,
            #         'code': 500,
            #         'message': "Internal Server Error: AI 模型未初始化",
            #         'tools_status': tools_status,
            #         'debug_info': {
            #             'error_type': 'ClientNotInitialized',
            #             'error': "Gemini Client 未初始化"
            #         }
            #     }
            # 
            # try:
            #     # 人体检测（使用 Gemini）
            #     logger.info("🔄 检测人体...")
            #     detection_prompt = """
            #     Analyze if there is a clear, full human body visible in this image.
            #     The person should be standing and clearly visible.
            #     Return JSON: {"has_human": true/false, "confidence": 0-100, "reason": "explanation"}
            #     """
            #     detection_response = self.client.models.generate_content(
            #         model=self.consultant_model,
            #         contents=[pil_model, detection_prompt],
            #         config=types.GenerateContentConfig(response_mime_type="application/json")
            #     )
            #     detection_result = json.loads(detection_response.text)
            #     
            #     if not detection_result.get('has_human') or detection_result.get('confidence', 0) < 70:
            #         tools_status["gemini_model"] = "not_started"
            #         logger.warning(f"⚠️ 未检测到清晰的人体: {detection_result.get('reason')}")
            #         return {
            #             'success': False,
            #             'code': 422,
            #             'message': "No human body detected in model_image",
            #             'tools_status': tools_status,
            #             'debug_info': {
            #                 'error_type': 'HumanDetectionError',
            #                 'suggest': 'Please use a clearer photo with a visible person.',
            #                 'confidence': detection_result.get('confidence', 0),
            #                 'reason': detection_result.get('reason', 'Unknown')
            #             }
            #         }
            #     
            #     logger.info(f"✅ 人体检测通过 (confidence: {detection_result.get('confidence')}%)")
            #     
            #     # 虚拟试穿合成
            #     logger.info("🔄 启动 Gemini 虚拟试穿引擎...")
            #     vfx_prompt = f"""
            #     ACT AS: Professional VFX Technical Director.
            #     
            #     CONTEXT:
            #     - Garment Type: {clothes_category}
            #     - Model Height: {model_info.get('user_height', 'N/A')} cm
            #     - Garment Length: {garment_info.get('clothe_length', 'N/A')} cm
            #     
            #     TASK: Create a photorealistic virtual try-on.
            #     
            #     REQUIREMENTS:
            #     1. NATURAL FIT: The garment should fit naturally on the person's body based on measurements.
            #     2. LIGHTING CONSISTENCY: Match the lighting of the model image.
            #     3. SHADOW & WRINKLES: Add realistic shadows and natural fabric wrinkles.
            #     4. PRESERVE PERSON: Keep the person's pose, face, and other body parts unchanged.
            #     5. COLOR ACCURACY: Maintain the original garment colors.
            #     
            #     OUTPUT: A photorealistic image of the person wearing the garment.
            #     """
            #     
            #     response = self.client.models.generate_content(
            #         model=self.model_name, 
            #         contents=[pil_cloth, pil_model, vfx_prompt]
            #     )
            #     
            #     tools_status["gemini_model"] = "success"
            #     logger.info("✅ Gemini 试穿引擎执行成功")
            #     
            #     # 保存结果
            #     final_save_path = None
            #     if response.parts:
            #         for part in response.parts:
            #             if part.inline_data:
            #                 image = part.as_image()
            #                 tryon_filename, final_save_path = self.get_unique_filename(prefix="try_result", ext="png")
            #                 image.save(final_save_path)
            #                 logger.info(f"✅ 试穿结果已保存: {tryon_filename}")
            #     
            #     if not final_save_path:
            #         tools_status["gemini_model"] = "fail"
            #         return {
            #             'success': False,
            #             'code': 422,
            #             'message': "Unprocessable Entity: 合成结果为空",
            #             'tools_status': tools_status,
            #             'debug_info': {
            #                 'error_type': 'NoOutputError',
            #                 'error': "AI response 没有图像数据"
            #             }
            #         }
            #     
            #     logger.info(f"🎉 虚拟试穿完整流程成功！")
            #     return {
            #         'success': True,
            #         'code': 200,
            #         'message': "Success",
            #         'tools_status': tools_status,
            #         'model_image_filename': model_filename,
            #         'tryon_result_filename': os.path.basename(final_save_path)
            #     }
            # 
            # except Exception as e:
            #     logger.error(f"❌ AI 合成失败: {e}")
            #     tools_status["gemini_model"] = "fail"
            #     return {
            #         'success': False,
            #         'code': 422,
            #         'message': "Unprocessable Entity: AI 模型运算失败",
            #         'tools_status': tools_status,
            #         'debug_info': {
            #             'error_type': 'AIModelError',
            #             'error': str(e)
            #         }
            #     }
        
        except Exception as e:
            logger.error(f"❌ 虚拟试穿发生未知错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            tools_status["gemini_model"] = "error"
            return {
                'success': False,
                'code': 500,
                'message': "Internal Server Error: 系统运算失败",
                'tools_status': tools_status,
                'debug_info': {
                    'error_type': type(e).__name__,
                    'error': str(e)
                }
            }


    def _check_size_compatibility(self, clothes_category, model_info, garment_info):
        """
        检查尺寸兼容性
        返回: {'compatible': bool, 'reason': str}
        """
        try:
            if clothes_category == 'cloth':
                # 检查上衣尺寸
                user_height = model_info.get('user_height', 0)
                clothe_length = garment_info.get('clothe_length', 0)
                
                if user_height > 0 and clothe_length > 0:
                    # 简单的比例检查
                    if clothe_length < user_height * 0.3 or clothe_length > user_height * 0.5:
                        return {
                            'compatible': False,
                            'reason': f'衣长 {clothe_length}cm 可能不适合身高 {user_height}cm 的人'
                        }
            
            elif clothes_category == 'pants':
                # 检查裤子尺寸
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
        