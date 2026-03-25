import os
import uuid
import logging
import json
import numpy as np
import cv2  
from rembg import new_session
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove, new_session
from PIL import Image, ImageEnhance
from google import genai
from google.genai import types

# 設定日誌記錄器
logger = logging.getLogger(__name__)

class AIProcessor(ImageProcessingInterface):
    """
    AI 影像處理核心類別：負責去背、磨皮、顏色提取及 Gemini 試穿合成。
    """
    
    def __init__(self):
        # 從環境變數獲取 Google API Key
        self.api_key = os.getenv("GOOGLE_API_KEY")
        
        # 第一次嘗試初始化預設的 rembg session
        try:
            self.rembg_session = new_session()
        except Exception as e:
            logger.warning(f"rembg session 初始化失敗: {e}")
            self.rembg_session = None

        # 初始化 Google GenAI Client
        try:
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except Exception as e:
            logger.error(f"⚠️ Gemini Client 初始化失敗: {e}")
            self.client = None
        
        # 設定模型名稱 (預設使用 flash 進行諮詢，nano-banana 進行試穿)
        self.consultant_model = os.getenv("GEMINI_CONSULTANT_MODEL", "gemini-1.5-flash")
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "nano-banana")
        self.enable_densepose = os.getenv("ENABLE_DENSEPOSE", "false").lower() == "true"

        # 💡 進階初始化：嘗試加載針對人像分割優化的模型 (u2net_human_seg)
        try:
            self.rembg_session = new_session(model_name='u2net_human_seg') 
            logger.info("✅ 已初始化針對人像 SEG 優化的 rembg session")
        except Exception as e:
            logger.warning(f"⚠️ rembg session 初始化失敗，使用預設模型: {e}")
            self.rembg_session = new_session() 

    def get_unique_filename(self, prefix="img", ext="png"):
        """
        生成唯一檔名並確保儲存目錄存在。
        """
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    # [通用輔助] 構建錯誤響應 (標準化 API 回傳格式)
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
    # [通用輔助] 構建成功響應 (標準化 API 回傳格式)
    # ==========================================
    def _build_success_response(self, tools_status, **kwargs):
        result = {
            'success': True,
            'code': 200,
            'message': kwargs.get('message', 'Success'),
            'tools_status': tools_status,
        }
        # 動態加入回傳欄位
        for key in ['file_name', 'style_analysis', 'model_image_filename', 'tryon_result_filename', 'error_details']:
            if key in kwargs:
                result[key] = kwargs[key]
        return result

    # ==========================================
    # [工具] 提取最大面積的前 N 個顏色 (使用 K-Means 演算法)
    # ==========================================
    def _extract_top_colors(self, image_path, top_n=3):
        try:
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4:
                return [[255, 255, 255]] * top_n
            
            # 分離通道，利用 Alpha 通道進行腐蝕處理，避免邊緣雜色干擾
            b, g, r, a = cv2.split(img)
            kernel = np.ones((5,5), np.uint8)
            inner_mask = cv2.erode(a, kernel, iterations=2)
            rgb_img = cv2.merge([r, g, b])
            valid_pixels = rgb_img[inner_mask > 0] # 只提取非透明區域
            
            if len(valid_pixels) == 0:
                return [[255, 255, 255]] * top_n
            
            # K-Means 聚類分析主要顏色
            pixels = valid_pixels.reshape(-1, 3).astype(np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
            _, labels, centers = cv2.kmeans(pixels, top_n, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
            
            # 根據出現頻率排序
            unique, counts = np.unique(labels, return_counts=True)
            sorted_indices = np.argsort(-counts)
            
            top_colors = []
            for idx in sorted_indices[:top_n]:
                color = centers[idx].astype(int)
                top_colors.append([int(color[0]), int(color[1]), int(color[2])])
            return top_colors
            
        except Exception as e:
            logger.error(f"顏色提取失敗: {e}")
            return [[255, 255, 255]] * top_n
    
    # ==========================================
    # [後期開發] 語意遮罩生成 (利用 Gemini 識別陰影與高光區域)
    # ==========================================
    def _get_semantic_ruffle_mask(self, pil_img, gray_cv_img):
        h, w = gray_cv_img.shape
        prompt = """
        Identify precise bounding boxes for "deep_shadows" and "specular_highlights".
        Return JSON: [{"label": string, "box_2d": [ymin, xmin, ymax, xmax]}].
        Normalized to 1000.
        """
        try:
            # 調用多模態模型獲取視覺座標
            response = self.client.models.generate_content(
                model=self.consultant_model,
                contents=[pil_img, prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(response.text)
            mask = np.zeros((h, w), dtype=np.uint8)
            for item in data:
                ymin, xmin, ymax, xmax = item['box_2d']
                # 將正規化座標轉換回影像尺寸
                cv_ymin, cv_xmin = int(ymin * h / 1000), int(xmin * w / 1000)
                cv_ymax, cv_xmax = int(ymax * h / 1000), int(xmax * w / 1000)
                cv2.rectangle(mask, (cv_xmin, cv_ymin), (cv_xmax, cv_ymax), 255, -1)
            # 使用大尺寸高斯模糊平衡遮罩邊緣
            return cv2.GaussianBlur(mask, (61, 61), 0)
        except Exception:
            return np.zeros((h, w), dtype=np.uint8)

    # ==========================================
    # [核心] OpenCV 磨皮引擎 (雙邊濾波 + 動態遮罩)
    # ==========================================
    def _opencv_smooth_fabric(self, pil_img):
        try:
            USE_SEMANTIC_LOGIC = False # 開關：是否使用 AI 輔助遮罩
            open_cv_image = np.array(pil_img.convert('RGB'))
            img = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # 使用大津演算法 (Otsu) 提取亮度細節
            _, brightness_detail = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            
            if USE_SEMANTIC_LOGIC:
                semantic_area = self._get_semantic_ruffle_mask(pil_img, gray)
                combined_mask = cv2.addWeighted(brightness_detail, 0.4, semantic_area, 0.6, 0)
                smooth_power = 200 
            else:
                # 傳統邏輯：提取高光區域與亮度細節
                max_val = np.max(gray)
                _, highlight_mask = cv2.threshold(gray, max_val * 0.9, 255, cv2.THRESH_BINARY)
                combined_mask = cv2.bitwise_or(brightness_detail, highlight_mask)
                smooth_power = 160

            # 建立保護遮罩，避免在褶皺處過度模糊
            blur_size = int(max(img.shape[:2]) / 40)
            if blur_size % 2 == 0: blur_size += 1
            combined_mask = cv2.GaussianBlur(combined_mask, (blur_size, blur_size), 0)
            mask_3d = cv2.cvtColor(combined_mask, cv2.COLOR_GRAY2BGR).astype(float) / 255.0

            # 雙邊濾波：在保留邊緣的同時平滑表面細節
            full_smoothed = cv2.bilateralFilter(img, d=15, sigmaColor=smooth_power, sigmaSpace=75)
            result = (img.astype(float) * (1.0 - mask_3d) + full_smoothed.astype(float) * mask_3d)
            result = result.clip(0, 255).astype(np.uint8)

            # 動態 Gamma 校正：根據影像平均亮度調整暗部細節
            avg_brightness = np.mean(gray)
            dynamic_gamma = 1.4 if avg_brightness < 127 else 1.1
            invGamma = 1.0 / dynamic_gamma
            table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
            final_cv_img = cv2.LUT(result, table)

            return Image.fromarray(cv2.cvtColor(final_cv_img, cv2.COLOR_BGR2RGB))
        except Exception as e:
            logger.error(f"OpenCV 磨皮失敗: {e}")
            return pil_img

    def remove_background(self, input_img):
        """
        封裝 Rembg 去背功能並自動裁剪透明邊框。
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

    def check_image_blur(self, pil_img, threshold=50.0):
        """
        使用拉普拉斯變異數檢測影像是否過於模糊。
        """
        try:
            gray = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            is_clear = laplacian_var >= threshold
            return is_clear, laplacian_var, None
        except Exception as e:
            logger.warning(f"清晰度檢測失敗: {e}")
            return True, 0, str(e)

    def smooth_fabric_with_opencv(self, rgb_img):
        """
        公開接口：對布料進行磨皮處理。
        """
        try:
            smoothed_rgb = self._opencv_smooth_fabric(rgb_img)
            return smoothed_rgb, True, None
        except Exception as e:
            logger.error(f"OpenCV 磨皮失敗: {e}")
            return None, False, str(e)

    def analyze_clothing_style(self, image_path):
        """
        利用 Gemini 分析服裝類別、風格標籤與主色。
        """
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
            # 強制要求 JSON 回傳格式
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
    # [VFX 工具] 視覺特徵提取
    # ==========================================
    def _get_dominant_color(self, pil_img):
        """
        計算圖片主色調，過濾透明背景、極白與極黑雜訊。
        """
        try:
            img = pil_img.convert("RGBA")
            img.thumbnail((200, 200)) # 縮小尺寸提高運算速度
            colors = img.getcolors(maxcolors=200*200)
            if not colors:
                return "#000000"
            valid_colors = []
            for count, color in colors:
                r, g, b, a = color
                if a < 128: continue # 過濾透明像素
                if r > 250 and g > 250 and b > 250: continue # 過濾接近白色
                if r < 5 and g < 5 and b < 5: continue # 過濾接近黑色
                valid_colors.append((count, (r, g, b)))
            if not valid_colors:
                return "original color"
            valid_colors.sort(key=lambda x: x[0], reverse=True)
            top_color = valid_colors[0][1]
            return '#{:02x}{:02x}{:02x}'.format(top_color[0], top_color[1], top_color[2])
        except Exception as e:
            logger.warning(f"⚠️ 取色失敗: {e}")
            return "original color"

    def _create_texture_swatch(self, pil_img):
        """
        裁切圖片中心區域，生成材質採樣塊 (Swatch)。
        """
        width, height = pil_img.size
        left = width * 0.25
        top = height * 0.25
        right = width * 0.75
        bottom = height * 0.75
        return pil_img.crop((left, top, right, bottom))

    def analyze_garment(self, pil_cloth_img):
        """
        技術分析：生成服裝的「數位孿生」描述。
        這段程式碼維持你原本最精確的幾何掃描邏輯，作為後續合成的「圖紙」。
        """
        print(f"🧐 [AI 分析] 正在執行幾何掃描與物理特性解析...")
        try:
            # 這是你原本那份非常專業的 Prompt，建議維持，因為它抓細節很準
            analysis_prompt = """
            ### Role
            You are a Senior Computer Vision Engineer. Your task is to perform a "Geometric Scan" of the uploaded garment image to facilitate a seamless Virtual Try-On (VTO) synthesis.

            ### Task
            Perform a pixel-level spatial analysis. Identify the EXACT boundaries and material behavior of the garment. 

            ### Output Format (Strict Structural Specification)
            1. **Spatial Coordinates & Boundaries (Crucial)**:
               - **Neckline Geometry**: (e.g., Narrow Crew-neck, Off-shoulder, Deep V-plunge. Specify how high or low it sits relative to a standard collarbone.)
               - **Hemline Termination**: (e.g., Cropped at high-waist, Standard hip-length, Extra-long tunic. Specify the exact vertical cut-off point.)
               - **Sleeve Termination**: (e.g., Full-length to wrist, Quarter-length to elbow, Cap-sleeve. Does it have a cuff or raw edge?)

            2. **Anatomical Displacement (Volume)**:
               - **Shoulder Silhouette**: (e.g., Structured padding, Natural drop-shoulder, Raglan seam. Describe the transition from neck to arm.)
               - **Fit Category**: (e.g., Compression/Skin-tight, Regular, Oversized/Boxy. How much "air gap" exists between the fabric and a standard body?)
               - **3D Shape**: (Is the garment flat or does it have built-in volume like puff sleeves or a quilted puffer texture?)

            3. **Physical Material Properties**:
               - **Stiffness & Drape**: (e.g., Rigid canvas, Fluid silk, Weighted fleece. How does the edge of the fabric behave?)
               - **Surface Micro-detail**: (e.g., 1x1 Ribbed knit, Distressed denim fraying, Matte nylon. Describe the texture grain.)
               - **Transparency/Opacity**: (100% Opaque, Semi-sheer, or Transparent. Specify if the background/skin would be visible through the weave.)

            4. **High-Contrast Keypoints**:
               - **Color Fidelity**: (Exact Hex-code or precise shade, including shadow/highlight intensity.)
               - **Graphic Anchors**: (Describe any logos, patterns, or text. Specify exact size and location using coordinates like "Upper Left Chest" or "Full Frontal Center".)
               - **Hardware**: (Detail any zippers, buttons, or drawstrings. Are they functional or decorative?)

            ### Constraints (Strict Enforcement)
            - **Boundary Focus**: Use terms that define where the garment ENDS (e.g., "ends precisely at the wrist bone").
            - **No Hallucinations**: Only describe the object. Ignore any hangers, labels, or backgrounds.
            - **VTO Optimized**: Focus on info that helps a model know WHICH skin to cover and WHICH skin to keep.
            """
            
            # 使用顧問模型進行分析 (通常用 Flash 就很快很準)
            response = self.client.models.generate_content(
                model=self.consultant_model, # 或是 self.model_name
                contents=[pil_cloth_img, analysis_prompt]
            )
            
            # 取得分析內容，如果失敗則給予預設值
            description = response.text if response and response.text else "Standard garment"
            print(f"📝 分析完成。已產出服裝數位藍圖。")
            
            return {
                "success": True,
                "description": description,
                "gemini_consultant": "success"
            }

        except Exception as e:
            print(f"⚠️ [analyze_garment] 分析出錯: {e}")
            return {
                "success": False,
                "description": "A standard clothing item",
                "gemini_consultant": "error"
            }

    # ==========================================
    # [核心合成] 虛擬試穿 (VFX 生圖引擎)
    # ==========================================

    def virtual_try_on(self, model_image, garment_image, hex_color, texture_swatch, garment_description, model_info=None, garment_info=None):
        """
        針對 Gemini 1.5 Flash 影像模型優化的試穿合成。
        已移除 DensePose 依賴，直接進行影像合成。
        """
        tools_status = {
            "rembg": "success", 
            "opencv_smoothing": "success", 
            "gemini_consultant": "success", 
            "gemini_model": "running",
            "densepose": "skipped" # 固定為跳過
        }
        
        try:
            if not self.client:
                return self._build_error_response(500, "Gemini Client 未初始化", tools_status, {})

            # 1. 讀取圖片素材 (直接處理傳入的檔案物件)
            if hasattr(model_image, 'seek'): model_image.seek(0)
            pil_model = Image.open(model_image).convert("RGB")
            
            if hasattr(garment_image, 'seek'): garment_image.seek(0)
            pil_cloth = Image.open(garment_image).convert("RGB")
            
            # --- DensePose 邏輯已註解 ---
            """
            pil_densepose = None
            if densepose_path and os.path.exists(densepose_path):
                pil_densepose = Image.open(densepose_path).convert("RGB")
            """

            # 2. 保存備份 (供後台紀錄或 debug 使用)
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")

            # 3. 構建合成指令 (移除 DensePose 相關描述)
            prompt = f"""
                ### Core Mission
                Forcefully transfer the complete garment from [Image 1] onto the model in [Image 2].

                ### Mandatory Execution Rules
                1. **Source Integrity**: Maintain 100% of the original color, pattern, texture, and details from [Image 1]. Do NOT change its color.
                2. **Body Mapping**: Wrap and fit the garment precisely to the model's anatomy in [Image 2]. The neckline, shoulders, chest, and sleeves MUST align perfectly with the model's pose, ensuring a natural 3D draped effect.
                3. **Zero-Invasive Masking**: Do NOT modify the model's head, hands, feet, skin tone, or any background elements. These must remain 100% unchanged from [Image 2].
                4. **Physical Occlusion**: If the model's hair or hands are in front of their torso in [Image 2], the transferred garment MUST be rendered behind them.

                ### Final Specification
                - Output: A realistic, high-definition image of the model wearing the exact garment from [Image 1].
                - Quality: Clean edges, seamless layering, 4K detail.
                """

            # 4. 調用生成
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="tryon_final", ext="png")
            
            # 組合內容：衣服、材質、模特兒、指令
            contents = [pil_cloth, texture_swatch, pil_model, prompt]

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents
            )

            # 5. 存檔邏輯
            image_saved = False
            if response and response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                        with open(tryon_save_path, 'wb') as f:
                            f.write(part.inline_data.data)
                        image_saved = True
                        break
                    elif hasattr(part, 'text') and not image_saved:
                        try:
                            img_obj = part.as_image()
                            img_obj.save(tryon_save_path)
                            image_saved = True
                            break
                        except: pass

            if not image_saved:
                tools_status["gemini_model"] = "fail"
                return self._build_error_response(422, "合成失敗", tools_status, {})

            tools_status["gemini_model"] = "success"
            return self._build_success_response(
                tools_status,
                model_image_filename=model_filename,
                tryon_result_filename=tryon_filename,
                style_analysis={"tech_spec": garment_description, "hex_color": hex_color}
            )

        except Exception as e:
            logger.error(f"❌ 試穿合成過程出錯: {str(e)}")
            return self._build_error_response(500, f"內部異常: {str(e)}", tools_status, {"detail": str(e)})
        
    def generate_densepose(self, input_image_path):
        """
        [實戰成功版] 姿態提取工具 - 整合動態解包防呆機制與純淨 IUV 渲染。
        """
        logger.info(f"🧠 [DensePose] 啟動成功版幾何掃描: {input_image_path}")
        try:
            import cv2
            import torch
            import numpy as np
            from PIL import Image
            import detectron2
            from detectron2.config import get_cfg
            from detectron2.engine import DefaultPredictor
            from densepose import add_densepose_config
            from densepose.vis.extractor import DensePoseResultExtractor
            from densepose.vis.densepose_results import DensePoseResultsFineSegmentationVisualizer

            # 1. 自動定位路徑
            d2_pkg_path = os.path.dirname(detectron2.__file__)
            calculated_densepose_path = os.path.join(os.path.dirname(d2_pkg_path), 'projects', 'DensePose')
            if not os.path.exists(calculated_densepose_path):
                calculated_densepose_path = "/app/detectron2/projects/DensePose"

            _, pose_map_path = self.get_unique_filename(prefix="pose_map", ext="png")

            # 2. 初始化 Predictor (使用單例模式避免重複載入權重)
            if not hasattr(self, '_densepose_predictor'):
                cfg = get_cfg()
                add_densepose_config(cfg)
                
                cfg_path = os.path.join(calculated_densepose_path, "configs/densepose_rcnn_R_50_FPN_s1x.yaml")
                weights_path = "/app/densepose_assets/model_final_162be9.pkl" # 這裡指向你 Docker 的位置
                
                cfg.merge_from_file(cfg_path)
                cfg.MODEL.WEIGHTS = weights_path
                cfg.MODEL.DEVICE = "cpu"
                cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5 
                
                self._densepose_predictor = DefaultPredictor(cfg)

            # 3. 讀取與推理
            img = cv2.imread(input_image_path)
            if img is None:
                return {"success": False, "error": "無法讀取模特兒圖片"}
            
            with torch.no_grad():
                outputs = self._densepose_predictor(img)
            
            if "instances" not in outputs:
                return {"success": False, "error": "DensePose 輸出格式異常"}
            
            instances = outputs["instances"].to("cpu")
            if len(instances) == 0:
                return {"success": False, "error": "DensePose 未檢測到人體"}
            
            if not instances.has("pred_densepose"):
                return {"success": False, "error": "無法從影像中提取姿態特徵"}

            # 4. 提取結果並繪圖 (動態解包防呆)
            extractor = DensePoseResultExtractor()
            extracted_data = extractor(instances)
            
            # 🛡️ 處理不同版本 API 回傳變數數量不一致
            if len(extracted_data) == 3:
                boxes, scores, dp_results = extracted_data
            elif len(extracted_data) == 2:
                boxes, dp_results = extracted_data
            else:
                return {"success": False, "error": f"未知特徵格式: {len(extracted_data)}"}
            
            formatted_data = (boxes, dp_results)
            
            # 5. 純淨渲染 (拔除 Bounding Box 外框，專供 Gemini 作為邊界約束圖)
            visualizer = DensePoseResultsFineSegmentationVisualizer()
            blank_bg = np.zeros(img.shape, dtype=np.uint8)
            vis_img = visualizer.visualize(blank_bg, formatted_data)

            # 6. 儲存圖片
            Image.fromarray(cv2.cvtColor(vis_img, cv2.COLOR_BGR2RGB)).save(pose_map_path, "PNG")
            logger.info(f"✅ DensePose 成功產出純淨版 Pose Map: {pose_map_path}")
            
            return {"success": True, "densepose_path": pose_map_path}

        except Exception as e:
            logger.error(f"❌ DensePose 嚴重報錯: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}



    def _visualize_iuv(self, labels, uv):
        """ 核心渲染：確保 IUV 數值轉換為可見的 RGB 範圍 """
        import numpy as np
        h, w = labels.shape
        vis = np.zeros((h, w, 3), dtype=np.uint8)
        
        # R 通道：人體部位標籤 (1-24)，放大倍數讓顏色變明顯
        vis[:, :, 0] = (labels.astype(float) / 24.0 * 255.0).astype(np.uint8)
        # G 通道：U 座標 (0-1)
        vis[:, :, 1] = (uv[0, :, :] * 255.0).astype(np.uint8)
        # B 通道：V 座標 (0-1)
        vis[:, :, 2] = (uv[1, :, :] * 255.0).astype(np.uint8)
        
        return vis