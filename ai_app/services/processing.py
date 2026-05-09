import os
import io
import sys
import logging
import uuid
import json
import time
from concurrent.futures import ThreadPoolExecutor
import torch
import cv2
import numpy as np  # 2D 影像處理與 RemBG 仍需使用
from PIL import Image, ImageEnhance
from django.conf import settings
from rembg import remove, new_session
from google import genai
from google.genai import types

from .interfaces import ImageProcessingInterface

# 設定日誌記錄器
logger = logging.getLogger(__name__)





class AIProcessor(ImageProcessingInterface):
    """
    AI 影像處理核心類別：負責去背、顏色提取及 Gemini 試穿合成。
    """
    
    def __init__(self):
        # 從環境變數獲取 Google API Key
        self.api_key = os.getenv("GOOGLE_API_KEY")
        
        # 初始化 Google GenAI Client
        try:
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except Exception as e:
            logger.error(f"⚠️ Gemini Client 初始化失敗: {e}")
            self.client = None
        
        # 設定模型名稱 (預設使用 flash 進行諮詢，nano-banana 進行試穿)
        self.consultant_model = os.getenv("GEMINI_CONSULTANT_MODEL", "gemini-1.5-flash")
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "nano-banana")

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
    # [後期開發] 語意遮罩生成 (利用 Gemini 識別陰影與高光區域)
    # ==========================================
    def remove_background(self, input_img):
        """
        [核心] Rembg 去背功能 (狀態碼: 1500)
        任務：移除背景並自動裁剪透明邊框。
        """
        try:
            # 1. 影像預檢查
            if input_img is None:
                return None, False, "1500", "傳入影像為空 (NoneType)"

            # 2. 執行去背運算
            # 這裡使用的是 __init__ 裡面的 self.rembg_session
            output_img = remove(input_img, session=self.rembg_session)
            
            # 3. 自動裁剪透明邊框 (為了後續 1501 顏色提取更準)
            bbox = output_img.getbbox()
            if bbox:
                output_img = output_img.crop(bbox)
            else:
                # 如果 getbbox 拿不到東西，代表整張圖被去背去光了，或是圖本來就是空的
                return None, False, "1500", "去背結果異常：影像被完全移除或偵測不到主體"

            # 成功回傳 1200
            return output_img, True, "1200", None

        except Exception as e:
            # 只要 Rembg 模型運算崩潰，就噴 1500
            logger.error(f"❌ [1500] Rembg 去背失敗: {str(e)}")
            return None, False, "1500", f"Rembg 運算核心異常: {str(e)}"

    def compose_square_portrait(self, pil_img, top_bottom_ratio=0.05, output_size=None):
        """
        將去背後的人像放到固定尺寸的正方形透明畫布中，預留上下留白。
        預設上下各保留 5% 空間，輸出尺寸固定，避免每次圖片大小不一致。
        """
        if pil_img is None:
            return None

        if output_size is None:
            output_size = int(os.getenv("VTO_OUTPUT_SIZE", "1024"))

        rgba = pil_img.convert("RGBA")
        bbox = rgba.getbbox()
        if not bbox:
            return rgba

        person_img = rgba.crop(bbox)
        p_w, p_h = person_img.size

        square_side = max(1, int(output_size))

        margin_y = int(round(square_side * top_bottom_ratio))
        margin_y = max(0, min(margin_y, square_side // 2 - 1))
        target_inner_h = max(1, square_side - (2 * margin_y))
        target_inner_w = target_inner_h

        # 固定輸出尺寸下，將人物等比縮放到可容納的最大尺寸。
        scale = min(target_inner_w / max(1, p_w), target_inner_h / max(1, p_h))
        new_w = max(1, int(round(p_w * scale)))
        new_h = max(1, int(round(p_h * scale)))
        if new_w != p_w or new_h != p_h:
            person_img = person_img.resize((new_w, new_h), Image.LANCZOS)
            p_w, p_h = person_img.size

        paste_x = (square_side - p_w) // 2
        paste_y = margin_y + max(0, (target_inner_h - p_h) // 2)

        canvas = Image.new("RGBA", (square_side, square_side), (0, 0, 0, 0))
        canvas.paste(person_img, (paste_x, paste_y), person_img)
        return canvas

    def check_image_blur(self, pil_img, threshold=50.0):
        """
        [工具] 清晰度檢測 (狀態碼: 1422)
        任務：利用拉普拉斯算子計算變異數，判斷圖片是否太模糊。
        """
        try:
            # 1. 影像預檢查
            if pil_img is None:
                return False, 0, "1422", "傳入影像為空"

            # 2. 轉灰階並運算
            # 將 PIL 轉成 numpy array 給 OpenCV 用
            cv_img = np.array(pil_img.convert('RGB'))
            gray = cv2.cvtColor(cv_img, cv2.COLOR_RGB2GRAY)
            
            # 計算拉普拉斯變異數 (數值越高代表越清晰)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            # 3. 門檻判斷
            if laplacian_var < threshold:
                # 模糊程度超過門檻，判定為失敗 (1422)
                return False, laplacian_var, "1422", f"圖片清晰度不足 (Score: {round(laplacian_var, 2)} < {threshold})"
            
            # 成功通過檢查
            return True, laplacian_var, "1200", None

        except Exception as e:
            # 運算過程崩潰 (例如圖片格式毀損)
            logger.error(f"❌ [1422] 清晰度檢測異常: {str(e)}")
            return False, 0, "1422", f"清晰度運算崩潰: {str(e)}"
        

    def analyze_clothing_style(self, image_input, mode="item"):
        """
        利用 Gemini 分析服裝類別、風格標籤與主色。
        單品分析失敗狀態碼: 1501
        合成穿搭分析失敗狀態碼: 2504
        """
        if not self.client:
            # 根據模式決定 Client 未初始化的錯誤碼
            err_code = "2504" if mode == "outfit" else "1501"
            logger.warning(f"Gemini Client 未初始化 (模式: {mode})")
            return None, False, err_code, "Gemini API Client not initialized"
        
        try:
            # 讓函式相容路徑字串或 PIL Image 物件
            if isinstance(image_input, str):
                pil_img = Image.open(image_input)
            else:
                pil_img = image_input

            # 使用 if/else 根據模式切換 Prompt
            if mode == "outfit":
                prompt = """
                Analyze the OVERALL COORDINATION in this image. 
                Identify the fashion aesthetic/genre. 
                Return ONLY a JSON object.

                【AESTHETIC GUIDELINES】:
                    -  "style_name": Identify the fashion aesthetic or genre. 
                    * Output MUST be in Traditional Chinese (繁體中文).
                    * Provide 0-3 tags. DO NOT include physical descriptions.
                    * Do not limit to a fixed list; use any modern fashion subcultures that best fit.
                【STRICT RULES】:
                1. DO NOT use gendered words (e.g., Feminine, Masculine, Girly).
                2. DO NOT use physical descriptions (e.g., Long-sleeve, Cotton, Slim).
                3. Return UP TO 3 tags (最多3種) in Traditional Chinese (繁體中文) that best describe the vibe. 
                   Only provide relevant tags; do not force three if fewer are appropriate.

                JSON Structure:
                {
                "style_name": ["風格標籤1", "風格標籤2"]
                }
            """
            else:
                prompt = """
                    Analyze the clothing item in this image. Provide the analysis in English (except for style_name) and return ONLY a JSON object.

                    【STRICT CATEGORY RULE】:
                    You MUST choose EXACTLY one category from this list in English:
                    - "clothing", "pants", "outerwear", "intimates", "skirt", "others".

                    【PURE AESTHETIC STYLE RULE】:
                    - "style_name": Identify the fashion aesthetic or genre. 
                    * Output MUST be in Traditional Chinese (繁體中文).
                    * Provide 0-3 tags. DO NOT include physical descriptions.
                    * Do not limit to a fixed list; use any modern fashion subcultures that best fit.

                    【STYLING ANALYSIS RULE】:
                    - "style_analysis": Provide a brief description (2-3 sentences) in English covering:
                    * Typical occasions or scenarios where this outfit would be appropriate.
                    * A general description of the outfit's visual vibe and coordination style.

                    【COLOR RULE】:
                    - "color_name": List up to 3 dominant color names in English.
                    * Use basic color names only. DO NOT distinguish between brightness or shades (e.g., use "Pink" instead of "Light Pink" or "Dark Pink"). 

                    JSON Structure:
                    {
                    "clothes_category": "Selected Category",
                    "style_name": ["風格1", "風格2", "風格3"],
                    "style_analysis": "Occasions and visual vibe description in English...",
                    "color_name": ["Color1", "Color2", "Color3"]
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
            
            # 解析 JSON 結果
            result = json.loads(response.text)
            
            # 根據模式決定回傳的字典內容
            if mode == "outfit":
                # 合成模式：只回傳風格名稱列表
                style_analysis = {
                    "style_name": result.get("style_name")
                }
            else:
                # 單品模式：維持原本三個欄位都回傳
                style_analysis = {
                    "clothes_category": result.get("clothes_category"),
                    "style_name": result.get("style_name"),
                    "style_analysis": result.get("style_analysis"),
                    "color_name": result.get("color_name")
                }
                
            logger.info(f"✅ Gemini 風格分析成功 ({mode})")
            return style_analysis, True, "1200", None
                
            logger.info(f"✅ Gemini 風格分析成功 ({mode})")
            # 成功狀態碼統一使用 1200
            return style_analysis, True, "1200", None
            
        except Exception as e:
            error_msg = str(e) if str(e) else "Unknown Gemini API error"
            # 關鍵修改：根據模式回傳不同的失敗狀態碼
            fail_code = "2504" if mode == "outfit" else "1501"
            
            logger.warning(f"❌ [{fail_code}] Gemini 風格分析失敗 ({mode}): {error_msg}")
            return None, False, fail_code, error_msg
    #--------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------
    #虛擬試穿
    #--------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------
    def tool_garment_analysis(self, garment_files, user_data):
        """
        [Step 3] 幾何掃描：除了邊界，更要求 Gemini 分析材質垂墜度與透明度。
        """
        has_bottom = False
        info_list = user_data.get('garments', [])

        # 虛擬繪畫工程師專業掃描 Prompt
        analysis_prompt = """
        ### Role: Senior Computer Vision & VFX Engineer. 
        ### Task: Perform a "Geometric Scan" of the uploaded garment image.
        ### Output Format: (STRICT JSON)
        {
          "neckline_geometry": "e.g., Narrow Crew-neck, Deep V-plunge. Sit relative to collarbone.",
          "hemline_termination": "e.g., Cropped at high-waist, Standard hip-length. Exact vertical cut-off.",
          "sleeve_termination": "e.g., Full-length to wrist, Quarter-length to elbow. Cuff or raw edge?",
          
          "material_properties": "e.g., Rigid canvas, Fluid silk, Weighted fleece. Define stiffness and drape behavior.",
          "transparency_level": "e.g., 100% Opaque, Semi-sheer, Transparent. Identify which background/skin would be visible through the weave.",
          
          "fit_category": "e.g., Compression/Skin-tight, Oversized/Boxy. Volume between fabric and standard body.",
          "color_hex": "Primary hex code.",
          "graphic_anchors": "Logo or pattern coordinate."
        }
        ### Constraints:
        - VTO Optimized: Focus on info that helps a model know WHICH skin to cover and WHICH skin to keep.
        """


        def _scan_one(idx_file):
            idx, f = idx_file
            t_start = time.time()
            logger.info(f"  [Step3] item#{idx} start @ {t_start:.3f}")
            try:
                pil_img = Image.open(f).convert("RGB")
                category = info_list[idx].get('clothes_category', 'others')
                response = self.client.models.generate_content(
                    model=self.consultant_model,
                    contents=[pil_img, analysis_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                logger.info(f"  [Step3] item#{idx} done in {time.time()-t_start:.2f}s")
                return idx, {
                    "img": pil_img,
                    "cat": category,
                    "rule": info_list[idx].get('garment_info', {}),
                    "scan_report": response.text,
                }, None
            except Exception as e:
                logger.info(f"  [Step3] item#{idx} FAIL in {time.time()-t_start:.2f}s")
                return idx, None, e

        n = len(garment_files)
        items_by_idx = [None] * n
        max_workers = max(1, min(n, 4))
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for idx, item, err in ex.map(_scan_one, enumerate(garment_files)):
                if err is not None:
                    logger.error(f"❌ Step 3 分析失敗 (item {idx}): {err}")
                    return {
                        "error_code": 2500,
                        "suggest": "AI Model (Analysis) service is currently unavailable. Please try again later."
                    }, "fail"
                items_by_idx[idx] = item
                if item["cat"] in ('pants', 'skirt'):
                    has_bottom = True

        logger.info(f"✅ Step 3 完成: {n} 件衣物分析 (耗時 {time.time() - t0:.2f}s)")
        return {"items": items_by_idx, "has_bottom": has_bottom}, "success"


    # ==========================================
    # [Step 5] 核心合成邏輯 (純邏輯，不存檔)
    # ==========================================
    def virtual_try_on(self, model_image, garments_ctx, user_data):
        """
        [Step 5] 核心合成邏輯：AI 穿衣 -> 二次去背 -> 手動置中並預留底邊。
        """
        try:
            m_info = user_data.get('model_info', {})
            u_h = m_info.get('user_height', 170.0)
            u_w = m_info.get('user_waistline', 80.0)

            # --- [1. 構建 Final Prompt: 強調邊緣對比與單一背景，利於後續去背] ---
            final_prompt = (
                # 规则 1：保持不变，人本偵測
                f"### CRITICAL RULE 1: HUMAN PRESENCE CHECK. "
                f"IF no legible HUMAN BODY is detected, output: 'ERROR: NO_HUMAN_DETECTED' and STOP. "

                # 修改 ROLE：升級為资深 VFX 工程师與幾何專家，強調「挂载」與「物理幾何」
                f"### ROLE: Senior Virtual Fashion VFX Engineer & Geometric Realism Specialist. "

                # 保持不变，標準化輸入目標
                f"### TARGET: Generate a standardized input image for high-resolution 3D Human Reconstruction (PIFuHD). "

                # 保持不变，硬性圖像規格
                f"### IMAGE SPECIFICATIONS: "
                f"1. CANVAS: 1024x1024 pixels, 1:1 square ratio. "
                f"2. SUBJECT SCALE: Human figure must occupy 90% of vertical height. "
                f"3. PADDING: 5% clear buffer at top (above head) and 5% at bottom (below feet). "
                f"4. ALIGNMENT: Perfectly centered horizontally. "

                f"### EXECUTION RULES: "
                # 修改 1：強調照片真實，但要是「幾何感知」的照片
                f"1. VISUAL STYLE: A photorealistic, high-resolution full-body PHOTOGRAPH. Ensure the composite garments look naturally and seamlessly integrated with the human subject. "
                # 保持不变，純白底
                f"2. BACKGROUND: SOLID PURE WHITE (#FFFFFF). No shadows on the floor, no gradient, no textures. "
                
                # ⚠️ 修改 3：針對 3D 生成最重要的修改！ ⚠️
                # PIFuHD 是靠皺褶陰影來判斷深度。原本的 'Flat lighting' 會消滅皺褶，導致 3D 變扁。
                # 修改為「幾何陰影燈光」，利用微妙的定向光製造皱褶，提供深度資訊。
                f"3. GEOMETRIC-AWARE LIGHTING: Use bright studio lighting that mimics a soft yet distinct directional key light (e.g., from top-left) to reveal all garment textures and, Crucially, cast subtle, localized micro-shadows within garment folds and wrinkles. This is ESSENTIAL for providing 3D depth and geometry information for the reconstruction algorithm. NO DARK OR HARSH SHADOWS, ONLY MICRO-SHADOWS WITHIN FOLDS. "
                
                # 修改 4：人機對齊指令
                f"4. ANATOMICAL & GARMENT FIDELITY: Maintain original user's face and body proportions Perfectly UNCHANGED. The garments must wrap around the body naturally, adhering to the body geometry. There must be ZERO GAP between the garment and the skin or body outline. The synthesis must be pixel-aligned to the subject's anatomy. "
                
                # 保持不变，不產生 Artifacts
                f"5. NO ARTIFACTS: Do not generate normal maps, depth maps, or wireframes. Output ONLY the RGB colored photo. "
                
                # 修改 6：自動補全 (修正原本的邏輯，確保生成腳部)
                f"6. AUTO-COMPLETION: Paint plain MATTE BLACK shoes to complete the full-body look. "

                # --- 以下為新增的「對後續 3D 製作有幫助」強化區塊 ---
                f"### OPTIMIZATION FOR 3D MESH GENERATION (PIFuHD): "
                # 強調「邊緣銳利」，方便演算法偵測人體邊界
                f"7. BOUNDARY PRECISION: Ensure absolute razor-sharp edges and clear separation between the garment outlines, body silhouette, and the solid white background. No anti-aliasing or motion blur. "
                # 修改原本的「紋理渲染」，更強調幾何細節（釦子、縫線、釦眼）
                f"8. GEOMETRIC DETAIL ENHANCEMENT: Intensify visible geometric details of the garment: buttons, distinct seam lines, lapel edges, and zipper tracks must be sharply discernible and three-dimensional, not flattened texture. "
                # 拉高「皺褶對比」，提供明確的深度梯度
                f"9. WRINKLE DEPTH OPTIMIZATION: Slightly increase the contrast within garment folds (wrinkles) to make clothing depth more decipherable for the 3D depth extraction algorithm. "
                # ---------------------------------------------------

                # 修改 Output 描述，強調「一模一樣」和「3D 幾何」
                f"### OUTPUT: One high-resolution RGB photo, 1024x1024, centered, full-body (including shoes), perfectly composite and hanging naturally on the anatomy, with ultra-sharp edges, perfectly optimized for Normal Map derivation and 3D mesh reconstruction."
            )

            source_images = [item['img'] for item in garments_ctx['items']]
            
            # --- [2. 調用 AI 進行合成] ---
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[model_image, *source_images, final_prompt]
            )
            
            # --- [3. 狀態攔截：2422 偵測不到人體 / 安全過濾] ---
            if not response or not response.candidates:
                return {"error_code": 2422, "suggest": "Content blocked by safety filters or no human detected."}, "fail"

            try:
                if "ERROR: NO_HUMAN_DETECTED" in response.text:
                    return {"error_code": 2422, "suggest": "The provided model_image is not valid. No human detected."}, "fail"
            except:
                pass

            # --- [4. 解析結果圖片] ---
            try:
                parts = response.candidates[0].content.parts
                generated_part = next((p for p in parts if hasattr(p, 'inline_data') or getattr(p, 'blob', None)), None)
                
                if not generated_part:
                    raise ValueError("No image part found")

                image_bytes = generated_part.inline_data.data if hasattr(generated_part, 'inline_data') else generated_part.blob.data
                result_pil = Image.open(io.BytesIO(image_bytes))

            except Exception as e:
                return {"error_code": 2422, "suggest": "Our engine couldn't detect a clear human body structure."}, "fail"

            # --- [5. 後處理：二次去背與重新置中佈局] ---
            try:
                # ==========================================
                # (原本代碼) 去背處理
                # ==========================================
                processed_png, success, _, _ = self.remove_background(result_pil)
                if not success:
                    # 如果失敗，強制轉為 RGBA 模式
                    processed_png = result_pil.convert("RGBA")

                # 轉為正方形構圖，頭頂與腳底各保留 5% 空間。
                final_output = self.compose_square_portrait(processed_png, top_bottom_ratio=0.05, output_size=1024)
                if final_output is None:
                    final_output = result_pil

            except Exception as e:
                logger.error(f"❌ 後處理構圖失敗: {e}")
                final_output = result_pil

            return {"result_image": final_output, "status": "success"}, "success"

        except Exception as e:
            err_msg = str(e).lower()
            logger.error(f"❌ Step 5 合成嚴重失敗: {e}")
            if any(word in err_msg for word in ["person", "human", "safety", "block"]):
                return {"error_code": 2422, "suggest": "Please use a clearer photo with a visible person."}, "fail"
            return {"error_code": 2501, "suggest": f"AI Synthesis service abnormal: {str(e)}"}, "fail"
    
    
    
    
       

    def remove_background_2d(self, pil_image):
        """
        純 2D 去背，並檢查人體完整度
        """
        try:
            # 呼叫去背模型 (例如 rembg)
            processed_pil_img = remove(pil_image)
            
            
            # 假設去背成功
            return {"processed_image": processed_pil_img}, "success"
            pass
        except Exception:
            return None, "fail"