import os
import uuid
import logging
import json
import numpy as np
import cv2  
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove, new_session
from PIL import Image, ImageEnhance
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
#---------------------------------------------------------------------------------------------------------------------------
    def _get_dominant_color(self, pil_img):
        """
        計算圖片主色調，但強制忽略透明背景 (Alpha = 0)
        """
        try:
            # 1. 確保圖片是 RGBA 模式 (包含透明通道)
            img = pil_img.convert("RGBA")
            img.thumbnail((200, 200)) # 縮小加速

            # 2. 取得所有像素的顏色與計數
            # getcolors 回傳格式: [(count, (r, g, b, a)), ...]
            colors = img.getcolors(maxcolors=200*200)

            if not colors:
                return "#000000"

            # 3. 過濾掉「透明」或「太接近白色/黑色」的雜訊
            valid_colors = []
            for count, color in colors:
                r, g, b, a = color
                
                # 忽略透明像素 (Alpha < 128)
                if a < 128: 
                    continue
                
                # 忽略極度接近純白的像素 (通常是反光或去背邊緣)
                if r > 250 and g > 250 and b > 250:
                    continue
                    
                # 忽略極度接近純黑的像素 (通常是陰影)
                if r < 5 and g < 5 and b < 5:
                    continue

                valid_colors.append((count, (r, g, b)))

            # 4. 如果過濾完沒東西了 (例如整張全白)，就回傳原本的
            if not valid_colors:
                return "original color"

            # 5. 找出出現次數最多的顏色
            valid_colors.sort(key=lambda x: x[0], reverse=True)
            top_color = valid_colors[0][1] # (r, g, b)

            # 6. 轉成 Hex Code
            return '#{:02x}{:02x}{:02x}'.format(top_color[0], top_color[1], top_color[2])

        except Exception as e:
            logger.warning(f"⚠️ 取色失敗: {e}")
            return "original color"
        



    def _create_texture_swatch(self, pil_img):
        """
        裁切圖片中心 50% 的區域，當作材質特寫餵給 AI
        """
        width, height = pil_img.size
        left = width * 0.25
        top = height * 0.25
        right = width * 0.75
        bottom = height * 0.75
        return pil_img.crop((left, top, right, bottom))

    def analyze_garment(self, pil_cloth_img) -> str:
        """
        讓 AI 擔任驗布師，產生極度詳細的規格書
        """
        print(f"🧐 [AI 分析] 正在解析衣服細節...")
        try:
            analysis_prompt = """
            ### Role
            You are a Senior Technical Fashion Analyst. Your job is to extract a precise "Digital Twin" specification from a clothing image.

            ### Task
            Analyze the provided garment image and generate a structured technical description. Focus on physical reality.

            ### Output Format (Strictly follow this structure)
            1. **Category**: (e.g., Hoodie, Maxi Dress, Denim Jacket)
            2. **Material Physics**:
               - Texture: (e.g., Ribbed, Satin-finish, Distressed denim)
               - Weight: (e.g., Heavyweight, Sheer, Stiff)
               - Drape: (e.g., Flows loosely, Structured/Rigid)
            3. **Visual Details**:
               - Color: (Specific shade description)
               - Pattern: (Describe exact print, logo text, or graphics and their location)
            4. **Construction**:
               - Fit: (Oversized, Slim, Boxy)
               - Neckline/Sleeves: (Crew neck, Drop shoulder, Raglan)
               - Details: (Visible stitching, buttons, zippers, pockets)

            ### Constraint
            Describe EXACTLY what you see. Do not hallucinate accessories not present in the image.
            """
            
            response = self.client.models.generate_content(
                model=self.analysis_model,
                contents=[pil_cloth_img, analysis_prompt]
            )
            
            description = response.text if response.text else "Standard garment"
            print(f"📝 分析結果: {description}")
            return description

        except Exception as e:
            print(f"⚠️ 分析失敗 (使用預設值): {e}")
            return "A clothing item"

    
    
    #  功能 2: 虛擬試穿 (Virtual Try-On) - 最終強大版
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path, model_info=None, garment_info=None):
        """
        虛擬試穿功能：融合取色、材質特寫與技術分析的高保真版本。
        修復格式衝突，回傳標準 Dictionary 以供 View 使用。
        """
        tools_status = {
            "rembg": "success", 
            "opencv_smoothing": "success", 
            "gemini_consultant": "running", 
            "gemini_model": "not_started",
            "densepose": "skipped"
        }
        
        try:
            if not self.client:
                return self._build_error_response(500, "Gemini Client 未初始化", tools_status, {})

            # 1. 讀取圖片素材
            if hasattr(model_image, 'seek'): model_image.seek(0)
            pil_model = Image.open(model_image)
            pil_cloth = Image.open(clean_clothes_path)

            # 2. [VFX 邏輯] 取得色碼與材質特寫圖
            hex_color = self._get_dominant_color(pil_cloth)
            texture_swatch = self._create_texture_swatch(pil_cloth)
            
            # 3. [技術分析] 讓分析模型提取衣服細節
            garment_description = self.analyze_garment(pil_cloth)
            tools_status["gemini_consultant"] = "success"

            # 4. 保存模特圖檔名備份 (對應 View 的需求)
            model_filename, model_save_path = self.get_unique_filename(prefix="model", ext="png")
            pil_model.save(model_save_path, "PNG")

            # 5. 構建合成 Prompt
            m_info_str = json.dumps(model_info, ensure_ascii=False) if model_info else "Standard"
            prompt = f"""
            ### Role: Expert AI VFX Artist specializing in photorealistic virtual try-on.
            ### Inputs: Image 1(Garment), Image 2(Model), Image 3(Texture Swatch)
            
            ### Specs:
            - Target Color: {hex_color} (STRICT)
            - Technical Analysis: {garment_description}
            - Model Context: {m_info_str}

            ### Instructions:
            1. IDENTITY: KEEP face, expression, and body shape of Image 2 EXACTLY.
            2. TEXTURE: Map texture from Image 3 onto the garment in Image 1.
            3. REALISM: Photorealistic high-resolution output. No filters.
            """

            # 6. 調用 Gemini 進行合成
            # 注意：這裡使用了 get_unique_filename 取得檔名與路徑
            tryon_filename, tryon_save_path = self.get_unique_filename(prefix="tryon_final", ext="png")
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[pil_cloth, pil_model, texture_swatch, prompt]
            )

            # 7. 提取產出的圖片並儲存
            image_saved = False
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data'):
                        with open(tryon_save_path, 'wb') as f:
                            f.write(part.inline_data.data)
                        image_saved = True
                        break
                    elif hasattr(part, 'text') and not image_saved:
                        try:
                            # 某些版本 SDK 的處理方式
                            img_obj = part.as_image()
                            img_obj.save(tryon_save_path)
                            image_saved = True
                        except: pass

            if not image_saved:
                tools_status["gemini_model"] = "fail"
                return self._build_error_response(422, "合成失敗：未獲取到影像數據", tools_status, {})

            # 8. 【關鍵修正】回傳標準 Dictionary 格式，避免 tuple .get 報錯
            tools_status["gemini_model"] = "success"
            return self._build_success_response(
                tools_status,
                model_image_filename=model_filename,     # 對應 View 的 model_image_filename
                tryon_result_filename=tryon_filename,    # 對應 View 的 tryon_result_filename
                style_analysis={
                    "tech_spec": garment_description, 
                    "hex_color": hex_color
                }
            )

        except Exception as e:
            logger.error(f"❌ 試穿合成過程出錯: {str(e)}")
            # 同樣回傳字典格式
            return self._build_error_response(500, f"內部合成引擎異常: {str(e)}", tools_status, {})