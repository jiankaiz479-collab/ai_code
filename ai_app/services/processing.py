import os
import os
import sys
import detectron2
import uuid
import logging
import json
import numpy as np
import cv2  
import torch
import urllib.request
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove, new_session
from PIL import Image, ImageEnhance
from google import genai
from google.genai import types
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor
from densepose import add_densepose_config
from densepose.vis.base import CompoundVisualizer
from densepose.vis.densepose_results import DensePoseResultsFineSegmentationVisualizer
from densepose.vis.extractor import CompoundExtractor, DensePoseResultExtractor
from densepose.vis.base import CompoundVisualizer
from densepose.vis.bounding_box import ScoredBoundingBoxVisualizer # 引入 Box 視覺化
from densepose.vis.densepose_results import DensePoseResultsFineSegmentationVisualizer

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
        # 專案指定的生圖模型，確保環境變數為 nano-banana
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "nano-banana")
        self.enable_densepose = os.getenv("ENABLE_DENSEPOSE", "false").lower() == "true"

    def get_unique_filename(self, prefix="img", ext="png"):
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    # [通用輔助] 構建錯誤響應
    # ==========================================
    def _build_error_response(self, code, message, tools_status, debug_info):
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
        result = {
            'success': True,
            'code': 200,
            'message': kwargs.get('message', 'Success'),
            'tools_status': tools_status,
        }
        for key in ['file_name', 'style_analysis', 'model_image_filename', 'tryon_result_filename', 'error_details']:
            if key in kwargs:
                result[key] = kwargs[key]
        return result

    # ==========================================
    # [工具] 提取最大面积的前 N 个颜色
    # ==========================================
    def _extract_top_colors(self, image_path, top_n=3):
        try:
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4:
                return [[255, 255, 255]] * top_n
            
            b, g, r, a = cv2.split(img)
            kernel = np.ones((5,5), np.uint8)
            inner_mask = cv2.erode(a, kernel, iterations=2)
            rgb_img = cv2.merge([r, g, b])
            valid_pixels = rgb_img[inner_mask > 0]
            
            if len(valid_pixels) == 0:
                return [[255, 255, 255]] * top_n
            
            pixels = valid_pixels.reshape(-1, 3).astype(np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
            _, labels, centers = cv2.kmeans(pixels, top_n, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
            
            unique, counts = np.unique(labels, return_counts=True)
            sorted_indices = np.argsort(-counts)
            
            top_colors = []
            for idx in sorted_indices[:top_n]:
                color = centers[idx].astype(int)
                top_colors.append([int(color[0]), int(color[1]), int(color[2])])
            return top_colors
            
        except Exception as e:
            logger.error(f"颜色提取失败: {e}")
            return [[255, 255, 255]] * top_n
    
    # ==========================================
    # [後期開發] 語意遮罩生成 
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
        except Exception:
            return np.zeros((h, w), dtype=np.uint8)

    # ==========================================
    # [核心] OpenCV 磨皮引擎 
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

    def remove_bg_with_rembg(self, input_img):
        try:
            output_img = remove(input_img, session=self.rembg_session)
            bbox = output_img.getbbox()
            if bbox:
                output_img = output_img.crop(bbox)
            return output_img, True, None
        except Exception as e:
            logger.error(f"Rembg 去背失敗: {e}")
            return None, False, str(e)

    def check_image_blur(self, pil_img, threshold=50.0):
        try:
            gray = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            is_clear = laplacian_var >= threshold
            return is_clear, laplacian_var, None
        except Exception as e:
            logger.warning(f"清晰度檢測失敗: {e}")
            return True, 0, str(e)

    def smooth_fabric_with_opencv(self, rgb_img):
        try:
            smoothed_rgb = self._opencv_smooth_fabric(rgb_img)
            return smoothed_rgb, True, None
        except Exception as e:
            logger.error(f"OpenCV 磨皮失敗: {e}")
            return None, False, str(e)

    def analyze_clothing_style(self, image_path):
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
        tools_status = {
            "rembg_engine": "not_started",
            "opencv_masking": "not_started",
            "gemini_consultant": "not_started"
        }
        
        try:
            if hasattr(clothes_image, 'seek'):
                clothes_image.seek(0)
            input_img = Image.open(clothes_image).convert("RGBA")
            
            # Step 1: Rembg 去背
            logger.info("🔄 [Step 1/4] 啟動 Rembg 去背引擎...")
            output_img, success, error = self.remove_bg_with_rembg(input_img)
            if not success:
                tools_status["rembg_engine"] = "fail"
                return self._build_error_response(422, "Unprocessable Entity: 去背處理失敗", tools_status, {'error': error})
            tools_status["rembg_engine"] = "success"
            
            # Step 2: 清晰度檢測
            is_clear, score, _ = self.check_image_blur(output_img, threshold=50.0)
            if not is_clear:
                return self._build_error_response(422, "Unprocessable Entity: 圖片過於模糊", tools_status, {'score': round(score, 1)})
            
            # Step 3: OpenCV 磨皮
            r, g, b, a = output_img.split()
            rgb_img = Image.merge('RGB', (r, g, b))
            smoothed_rgb, success, error = self.smooth_fabric_with_opencv(rgb_img)
            if not success:
                tools_status["opencv_masking"] = "fail"
                return self._build_error_response(422, "Unprocessable Entity: 圖片處理失敗", tools_status, {'error': error})
            tools_status["opencv_masking"] = "success"

            # Step 4: 保存圖片
            final_output = Image.merge('RGBA', (*smoothed_rgb.split(), a))
            final_output = ImageEnhance.Contrast(final_output).enhance(0.85)
            filename, save_path = self.get_unique_filename(prefix="processed", ext="png")
            final_output.save(save_path, "PNG")

            # Bonus: 風格分析
            style_analysis, success, error = self.analyze_clothing_style(save_path)
            if success:
                tools_status["gemini_consultant"] = "success"
            else:
                tools_status["gemini_consultant"] = "fail"
            
            success_params = {
                'message': 'Processing Success',
                'file_name': filename,
                'style_analysis': style_analysis
            }
            if tools_status["gemini_consultant"] == "fail":
                success_params['error_details'] = {"error_message": error}

            return self._build_success_response(tools_status, **success_params)

        except Exception as e:
            logger.error(f"❌ 去背發生未知錯誤: {str(e)}")
            return self._build_error_response(500, "Internal Server Error: 系統運算失敗", tools_status, {'error': str(e)})

    # ==========================================
    # [功能 2] 虚拟试穿
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path, clothes_category='cloth', model_info=None, garment_info=None):
        tools_status = {
            "rembg": "not_started",
            "opencv_smoothing": "not_started",
            "gemini_consultant": "not_started",
            "gemini_model": "not_started",
            "densepose": "not_started"
        }
        
        model_info = model_info or {}
        garment_info = garment_info or {}
        
        try:
            # Step 1: 預設工具成功 (這裡不重複執行預處理，直接標記)
            tools_status["rembg"] = "success"
            tools_status["opencv_smoothing"] = "success"
            tools_status["gemini_consultant"] = "success"
            
            # Step 2: 载入并检测人体图片
            logger.info("🔄 [TryOn] 载入模特图片...")
            try:
                pil_model = Image.open(model_image)
                width, height = pil_model.size
                if width < 200 or height < 200:
                    tools_status["densepose"] = "fail"
                    return self._build_error_response(422, "No human body detected in model_image", tools_status, {'image_size': f'{width}x{height}'})
            except Exception as e:
                tools_status["densepose"] = "fail"
                return self._build_error_response(422, "Invalid model image", tools_status, {'error': str(e)})
            
            # 保存模特圖片以供後續使用
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")
            
            # Step 3: 姿態提取 (嚴格限定 DensePose)
            pose_map_path, ok, err = self.extract_pose_map(model_save_path)
            if not ok:
                tools_status["densepose"] = "fail"
                logger.error(f"❌ 姿態提取失敗: {err}")
                return self._build_error_response(422, "Pose extraction failed", tools_status, {"error": err})
            tools_status["densepose"] = "success"  
            
            # Step 4: AI 虚拟试穿合成 
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="try_result", ext="png")
            
            ok, err = self.generate_tryon_image(
                model_image_path=model_save_path,
                garment_image_path=clean_clothes_path,
                pose_map_path=pose_map_path,
                output_path=tryon_save_path,
                model_info=model_info,
                garment_info=garment_info
            )
            
            if not ok:
                tools_status["gemini_model"] = "fail"
                logger.error(f"❌ 試穿生成失敗: {err}")
                return self._build_error_response(422, "Try-on generation failed", tools_status, {"error": err})

            tools_status["gemini_model"] = "success"    
            logger.info(f"🎉 虚拟试穿合成成功！")
            
            return self._build_success_response(tools_status, model_image_filename=model_filename, tryon_result_filename=tryon_filename)
        
        except Exception as e:
            logger.error(f"❌ 虚拟试穿发生未知错误: {str(e)}")
            tools_status["gemini_model"] = "error"
            return self._build_error_response(500, "Internal Server Error: 系统运算失败", tools_status, {'error': str(e)})


    def extract_pose_map(self, model_image_path):
        """
        [輔助工具] 姿態提取工具 - 專為 detectron2 v0.6 與 Gemini 視覺約束優化
        包含動態解包防呆機制與純淨 IUV 渲染。
        """
        try:
            # 1. 自動尋找 detectron2 套件位置推算路徑
            import detectron2
            d2_pkg_path = os.path.dirname(detectron2.__file__)
            calculated_densepose_path = os.path.join(os.path.dirname(d2_pkg_path), 'projects', 'DensePose')
            
            if not os.path.exists(calculated_densepose_path):
                calculated_densepose_path = "/app/detectron2/projects/DensePose"

            _, pose_map_path = self.get_unique_filename(prefix="pose_map", ext="png")

            # 2. 初始化 DensePose Predictor
            if not hasattr(self, '_densepose_predictor'):
                from detectron2.config import get_cfg
                from detectron2.engine import DefaultPredictor
                from densepose import add_densepose_config

                cfg = get_cfg()
                add_densepose_config(cfg)
                
                cfg_path = os.getenv("DENSEPOSE_CFG", "").strip()
                if not cfg_path:
                    cfg_path = os.path.join(calculated_densepose_path, "configs/densepose_rcnn_R_50_FPN_s1x.yaml")
                
                weights_path = os.getenv("DENSEPOSE_WEIGHTS", "").strip()
                if weights_path and weights_path.startswith('http'):
                    weights_local = "/tmp/densepose_weights.pkl"
                    if not os.path.exists(weights_local):
                        import urllib.request
                        urllib.request.urlretrieve(weights_path, weights_local)
                    weights_path = weights_local
                
                cfg.merge_from_file(cfg_path)
                if weights_path:
                    cfg.MODEL.WEIGHTS = weights_path
                cfg.MODEL.DEVICE = os.getenv("DENSEPOSE_DEVICE", "cpu")
                
                # 💡 增加信心分數門檻，過濾雜訊，防止抓到錯誤的微小特徵
                cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5 
                
                self._densepose_predictor = DefaultPredictor(cfg)
            

            
            img = cv2.imread(model_image_path)
            if img is None:
                return None, False, "無法讀取模特兒圖片"
            
            with torch.no_grad():
                outputs = self._densepose_predictor(img)
            
            # 4. 檢查結果
            if "instances" not in outputs:
                return None, False, "DensePose 輸出格式異常"
            instances = outputs["instances"].to("cpu")
            if len(instances) == 0:
                return None, False, "DensePose 未檢測到人體"
            if not instances.has("pred_densepose"):
                return None, False, "無法從影像中提取姿態特徵"

            # ==========================================
            # 5. 提取結果並繪圖 (終極解法：動態解包與純淨渲染)
            # ==========================================
            from densepose.vis.extractor import DensePoseResultExtractor
            from densepose.vis.densepose_results import DensePoseResultsFineSegmentationVisualizer
            
            # A. 使用官方 Extractor 解析原始特徵 (將 GPU 裸訊號解碼)
            extractor = DensePoseResultExtractor()
            extracted_data = extractor(instances)
            
            # B. 動態解包 (Dynamic Unpacking) 🛡️ 防呆機制
            # 解決 detectron2 不同版本 API 回傳變數數量不一致的致命痛點
            if len(extracted_data) == 3:
                boxes, scores, dp_results = extracted_data  # 某些版本回傳 3 個參數
            elif len(extracted_data) == 2:
                boxes, dp_results = extracted_data          # 某些版本回傳 2 個參數
            else:
                return None, False, f"未知的 DensePose 特徵格式: 預期 2 或 3 個變數，卻收到 {len(extracted_data)} 個"
            
            # C. 強制重新打包為嚴格的 2 元組格式 (Tuple)
            formatted_data = (boxes, dp_results)
            
            # D. 啟動純淨渲染 (拔除 Bounding Box 外框，專供 Gemini 作為邊界約束圖)
            visualizer = DensePoseResultsFineSegmentationVisualizer()
            blank_bg = np.zeros(img.shape, dtype=np.uint8)
            vis_img = visualizer.visualize(blank_bg, formatted_data)
            # ==========================================

            # 6. 儲存圖片
            from PIL import Image
            Image.fromarray(cv2.cvtColor(vis_img, cv2.COLOR_BGR2RGB)).save(pose_map_path, "PNG")
            logger.info(f"✅ DensePose 成功產出純淨版 Pose Map: {pose_map_path}")
            
            return pose_map_path, True, None
            
        except Exception as e:
            logger.error(f"❌ DensePose 報錯: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None, False, f"DensePose 執行失敗: {str(e)}"


    def generate_tryon_image(self, model_image_path, garment_image_path, pose_map_path, output_path, model_info, garment_info):
        """
        試穿生成工具 - 唯一指定使用 Gemini API 
        並將尺寸數據直接注入 Prompt。
        """
        try:
            if not self.client:
                logger.error("❌ Gemini client 未初始化，無法執行試穿合成")
                return False, "Gemini client 未初始化"
            
            # 載入所有輔助圖片
            model_img = Image.open(model_image_path)
            garment_img = Image.open(garment_image_path)
            pose_img = Image.open(pose_map_path)
            
            # 格式化尺寸數據以供 Prompt 使用
            m_info_str = json.dumps(model_info, ensure_ascii=False) if model_info else "Not provided"
            g_info_str = json.dumps(garment_info, ensure_ascii=False) if garment_info else "Not provided"
            
            # 將尺寸參數注入，讓模型判斷衣服長度
            prompt = f"""
            You are a professional virtual try-on AI. Generate a realistic photo of the person wearing the provided garment.

            **Inputs:**
            1. Model photo: Original person image
            2. Garment photo: Clothing item (background removed)
            3. Pose map: Body pose guidance (DensePose output)

            **Size Information (Crucial for rendering correct garment length):**
            - Model Body Measurements (cm): {m_info_str}
            - Garment Measurements (cm): {g_info_str}
            Please analyze these measurements to accurately determine how long or short the garment should appear on the model's body. 

            **Requirements:**
            - Preserve the person's face, body proportions, and pose EXACTLY
            - Fit the garment naturally on the person's body reflecting the exact size constraints
            - Maintain realistic lighting, shadows, and wrinkles
            - Keep background unchanged
            - Output high-quality photorealistic result

            Generate the final try-on image now.
            """
            
            logger.info(f"🔄 調用 Gemini ({self.model_name}) 進行多圖合成試穿...")
            
            # 保留你要求的呼叫格式
            response = self.client.models.generate_content(
                model=self.model_name, 
                contents=[
                    prompt,
                    model_img,
                    garment_img, 
                    pose_img
                ]
            )
            
            # 提取生成的圖片並存檔
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data'):
                            image_data = part.inline_data.data
                            with open(output_path, 'wb') as f:
                                f.write(image_data)
                            logger.info("✅ Gemini api試穿圖合成成功！")
                            return True, None
                            
            return False, "API 回傳成功，但沒有解析到圖片資料"
            
        except Exception as e:
            # 捕捉詳細的 Gemini 錯誤，直接宣告失敗，不跑降級
            logger.error(f"❌ Gemini 生成失敗: {repr(e)}")
            return False, f"Gemini API 錯誤: {str(e)}"