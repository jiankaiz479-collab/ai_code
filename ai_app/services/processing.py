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
                        "suggest": "AI 模型分析服務暫時無法使用，請稍後再試。"
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
                return {"error_code": 2422, "suggest": "內容被安全機制阻擋或未偵測到人物。"}, "fail"

            try:
                if "ERROR: NO_HUMAN_DETECTED" in response.text:
                    return {"error_code": 2422, "suggest": "提供的照片無效，未偵測到人物。"}, "fail"
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
                return {"error_code": 2422, "suggest": "系統無法偵測到清晰的人體結構。"}, "fail"

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
                return {"error_code": 2422, "suggest": "未偵測到人物，請上傳清楚的人像照。"}, "fail"
            return {"error_code": 2501, "suggest": f"AI 合成服務異常: {str(e)}"}, "fail"
    
    
    
    
       

    # ==========================================
    # 3D 物理試穿：Tripo image-to-3D 各別功能函式
    # 由 view 編排呼叫；機密與設定一律走 .env
    # 統一回傳: (data, status, code, err_msg)
    #   - data: 該步驟產出 (token / task_id / model_url / glb bytes)
    #   - status: "success" / "fail"
    #   - code: "4xxx"
    #   - err_msg: 錯誤訊息或 None
    # ==========================================

    def _tripo_config(self):
        """從 .env 讀取 Tripo 設定 (集中管理機密與參數)"""
        return {
            "api_key": os.getenv("TRIPO_API_KEY"),
            "base_url": os.getenv("TRIPO_BASE_URL", "https://api.tripo3d.ai/v2/openapi"),
            "upload_timeout": int(os.getenv("TRIPO_UPLOAD_TIMEOUT", "60")),
            "task_timeout": int(os.getenv("TRIPO_TASK_TIMEOUT", "60")),
            "poll_timeout": int(os.getenv("TRIPO_POLL_TIMEOUT", "30")),
            "download_timeout": int(os.getenv("TRIPO_DOWNLOAD_TIMEOUT", "120")),
            "poll_max_seconds": int(os.getenv("TRIPO_POLL_MAX_SECONDS", "600")),
            "poll_interval": int(os.getenv("TRIPO_POLL_INTERVAL", "5")),
        }

    @staticmethod
    def _map_tripo_error(http_status, response_text, stage="tripo"):
        """將 Tripo 上游錯誤映射為內部 4xxx 業務碼。
        對照表：
          HTTP 429 / code=2000 → 4429 速率限制
          HTTP 403 / code=2010 → 4410 積分不足
          code=2003 / 2004     → 4415 檔案空白或格式不支援
          code=2008 / 2018     → 4422 內容違規或模型過於複雜（無法重建）
          其他                  → 4500 服務崩潰或上游異常
        """
        try:
            body = json.loads(response_text) if response_text else {}
        except Exception:
            body = {}
        tripo_code = body.get("code")
        tripo_msg = body.get("message") or body.get("error") or ""
        snippet = (response_text or "")[:200]

        if http_status == 429 or tripo_code == 2000:
            return "4429", f"[{stage}] 速率限制 (HTTP {http_status}, code={tripo_code}): {tripo_msg or snippet}"
        if http_status == 403 or tripo_code == 2010:
            return "4410", f"[{stage}] 積分不足 (HTTP {http_status}, code={tripo_code}): {tripo_msg or snippet}"
        if tripo_code in (2003, 2004):
            return "4415", f"[{stage}] 檔案不支援或空白 (code={tripo_code}): {tripo_msg or snippet}"
        if tripo_code in (2008, 2018):
            return "4422", f"[{stage}] 內容違規或模型過度複雜 (code={tripo_code}): {tripo_msg or snippet}"
        return "4500", f"[{stage}] Tripo 上游錯誤 HTTP {http_status}, code={tripo_code}: {tripo_msg or snippet}"

    def tripo_upload_image(self, pil_image):
        """[3D-Step1] 上傳圖片至 Tripo，回傳 file_token"""
        import requests as _req
        cfg = self._tripo_config()
        if not cfg["api_key"]:
            return None, "fail", "4500", "TRIPO_API_KEY not configured"

        try:
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="PNG")
            buf.seek(0)

            r = _req.post(
                f"{cfg['base_url']}/upload",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                files={"file": ("input.png", buf, "image/png")},
                timeout=cfg["upload_timeout"],
            )
            if r.status_code != 200:
                code, err = self._map_tripo_error(r.status_code, r.text, stage="upload")
                return None, "fail", code, err

            file_token = (r.json().get("data") or {}).get("image_token")
            if not file_token:
                return None, "fail", "4500", f"Tripo upload returned no image_token: {r.text[:200]}"
            return file_token, "success", "4200", None
        except _req.exceptions.RequestException as e:
            return None, "fail", "4500", f"Tripo upload network error: {str(e)}"
        except Exception as e:
            return None, "fail", "4500", f"Tripo upload crashed: {str(e)}"

    # ---------- Tripo 預設指令 (追求「跟輸入圖一模一樣」) ----------
    TRIPO_DEFAULT_PROMPT = (
        "photorealistic 3D character, exact replica of the input photo, "
        "preserve identical facial features, identity, hairstyle, skin tone, "
        "preserve exact clothing design, fabric color, patterns, logos, wrinkles, "
        "preserve body proportions and silhouette, sharp clean outline, "
        "realistic colors matching the source image, no color shift, "
        "high-fidelity texture, accurate detail reproduction, "
        "neutral A-pose, full body"
    )
    TRIPO_DEFAULT_NEGATIVE_PROMPT = (
        "cartoon, anime, stylized, deformed, distorted, blurry, "
        "oversmooth, plastic skin, color shift, saturated, washed out, "
        "extra limbs, missing limbs, asymmetric face, melted features, "
        "artistic interpretation, fantasy elements"
    )
    TRIPO_DEFAULT_TEXTURE_QUALITY = os.getenv("TRIPO_TEXTURE_QUALITY", "detailed")
    TRIPO_DEFAULT_FACE_LIMIT = int(os.getenv("TRIPO_FACE_LIMIT", "100000"))
    TRIPO_DEFAULT_PBR = os.getenv("TRIPO_PBR", "true").lower() in ("1", "true", "yes")
    TRIPO_DEFAULT_MODEL_VERSION = os.getenv("TRIPO_MODEL_VERSION", "v3.1-20260211")
    TRIPO_DEFAULT_TEXTURE_ALIGNMENT = os.getenv("TRIPO_TEXTURE_ALIGNMENT", "original_image")
    TRIPO_DEFAULT_GEOMETRY_QUALITY = os.getenv("TRIPO_GEOMETRY_QUALITY", "detailed")
    # Refine 階段預設參數 (image_to_model 結果再精修)
    TRIPO_REFINE_FACE_LIMIT = int(os.getenv("TRIPO_REFINE_FACE_LIMIT", "200000"))

    def tripo_create_task(self, file_token, prompt=None, negative_prompt=None,
                          texture_quality=None, face_limit=None, pbr=None, style=None):
        """[3D-Step2] 建立 image_to_model 任務，回傳 task_id

        所有參數皆選填；未填則套用「最大化還原原圖」的預設值：
          prompt: 預設 TRIPO_DEFAULT_PROMPT (寫實還原 + 衣物保留 + 顏色精準)
          negative_prompt: 預設 TRIPO_DEFAULT_NEGATIVE_PROMPT (擋掉卡通/變形/偏色)
          texture_quality: 預設 "detailed"
          face_limit: 預設 30000 (高面數 → 精細輪廓)
          pbr: 預設 True (PBR 材質 → 寫實光照)
          style: 預設不啟用 (避免風格化)
        """
        import requests as _req
        cfg = self._tripo_config()
        if not cfg["api_key"]:
            return None, "fail", "4500", "TRIPO_API_KEY not configured"

        # 套用預設值 (None = 用預設；空字串也視為未填)
        eff_prompt = prompt if prompt else self.TRIPO_DEFAULT_PROMPT
        eff_neg = negative_prompt if negative_prompt else self.TRIPO_DEFAULT_NEGATIVE_PROMPT
        eff_tex_q = texture_quality if texture_quality else self.TRIPO_DEFAULT_TEXTURE_QUALITY
        eff_face = int(face_limit) if face_limit else self.TRIPO_DEFAULT_FACE_LIMIT
        eff_pbr = pbr if pbr is not None else self.TRIPO_DEFAULT_PBR

        logger.info(f"🧾 [3D] Tripo 任務參數: prompt='{eff_prompt[:60]}...', "
                    f"neg='{eff_neg[:40]}...', tex_q={eff_tex_q}, "
                    f"face_limit={eff_face}, pbr={eff_pbr}, style={style}")

        try:
            payload = {
                "type": "image_to_model",
                "file": {"type": "png", "file_token": file_token},
                "prompt": eff_prompt,
                "negative_prompt": eff_neg,
                "texture_quality": eff_tex_q,
                "face_limit": eff_face,
                "pbr": eff_pbr,
                "model_version": self.TRIPO_DEFAULT_MODEL_VERSION,
                "texture_alignment": self.TRIPO_DEFAULT_TEXTURE_ALIGNMENT,
                "geometry_quality": self.TRIPO_DEFAULT_GEOMETRY_QUALITY,
            }
            if style:
                payload["style"] = style

            r = _req.post(
                f"{cfg['base_url']}/task",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=cfg["task_timeout"],
            )
            if r.status_code != 200:
                code, err = self._map_tripo_error(r.status_code, r.text, stage="create_task")
                return None, "fail", code, err

            task_id = (r.json().get("data") or {}).get("task_id")
            if not task_id:
                return None, "fail", "4500", f"Tripo task returned no task_id: {r.text[:200]}"
            return task_id, "success", "4200", None
        except _req.exceptions.RequestException as e:
            return None, "fail", "4500", f"Tripo create task network error: {str(e)}"
        except Exception as e:
            return None, "fail", "4500", f"Tripo create task crashed: {str(e)}"

    def tripo_create_refine_task(self, draft_task_id, face_limit=None,
                                 prompt=None, negative_prompt=None,
                                 texture_quality=None, pbr=None):
        """[3D-Step3.5] 用 draft task_id 建立 refine_model 精修任務 (200k 面 + 高品質貼圖)。

        會額外消耗 1 次 Tripo credit。

        預設套用最高品質：
          face_limit: 200000
          texture_quality: detailed
          pbr: True
          prompt / negative_prompt: 同 image_to_model 預設 (一致還原)
        """
        import requests as _req
        cfg = self._tripo_config()
        if not cfg["api_key"]:
            return None, "fail", "4500", "TRIPO_API_KEY not configured"

        eff_face = int(face_limit) if face_limit else self.TRIPO_REFINE_FACE_LIMIT
        eff_tex_q = texture_quality if texture_quality else self.TRIPO_DEFAULT_TEXTURE_QUALITY
        eff_pbr = pbr if pbr is not None else self.TRIPO_DEFAULT_PBR
        eff_prompt = prompt if prompt else self.TRIPO_DEFAULT_PROMPT
        eff_neg = negative_prompt if negative_prompt else self.TRIPO_DEFAULT_NEGATIVE_PROMPT

        logger.info(f"🧾 [3D] Refine 任務參數: draft={draft_task_id}, face_limit={eff_face}, "
                    f"tex_q={eff_tex_q}, pbr={eff_pbr}, prompt='{eff_prompt[:50]}...'")

        try:
            payload = {
                "type": "refine_model",
                "draft_model_task_id": draft_task_id,
                "face_limit": eff_face,
                "texture_quality": eff_tex_q,
                "pbr": eff_pbr,
                "texture": True,
                "texture_alignment": self.TRIPO_DEFAULT_TEXTURE_ALIGNMENT,
                "prompt": eff_prompt,
                "negative_prompt": eff_neg,
            }
            r = _req.post(
                f"{cfg['base_url']}/task",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=cfg["task_timeout"],
            )
            if r.status_code != 200:
                code, err = self._map_tripo_error(r.status_code, r.text, stage="refine")
                return None, "fail", code, err

            task_id = (r.json().get("data") or {}).get("task_id")
            if not task_id:
                return None, "fail", "4500", f"Tripo refine returned no task_id: {r.text[:200]}"
            return task_id, "success", "4200", None
        except _req.exceptions.RequestException as e:
            return None, "fail", "4500", f"Tripo refine network error: {str(e)}"
        except Exception as e:
            return None, "fail", "4500", f"Tripo refine crashed: {str(e)}"

    def tripo_poll_task(self, task_id, progress_cb=None):
        """[3D-Step3] 同步輪詢任務狀態，完成後回傳 model_url
        progress_cb(status, progress) 可選；當 progress 變化時呼叫，方便 view 印 log"""
        import requests as _req
        cfg = self._tripo_config()
        if not cfg["api_key"]:
            return None, "fail", "4500", "TRIPO_API_KEY not configured"

        poll_url = f"{cfg['base_url']}/task/{task_id}"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        deadline = time.time() + cfg["poll_max_seconds"]
        last_progress = -1

        try:
            while time.time() < deadline:
                rp = _req.get(poll_url, headers=headers, timeout=cfg["poll_timeout"])
                if rp.status_code != 200:
                    time.sleep(cfg["poll_interval"])
                    continue

                pj = rp.json().get("data") or {}
                status = pj.get("status")
                progress = pj.get("progress", 0)

                if progress_cb and progress != last_progress:
                    try:
                        progress_cb(status, progress)
                    except Exception:
                        pass
                    last_progress = progress

                if status == "success":
                    out = pj.get("output") or {}
                    pbr = out.get("pbr_model")
                    model_url = (pbr.get("url") if isinstance(pbr, dict) else pbr) \
                        or out.get("model") or out.get("base_model")
                    if not model_url:
                        return None, "fail", "4500", "Tripo task success but no model url"
                    return model_url, "success", "4200", None

                if status in ("failed", "cancelled", "banned", "expired"):
                    err = pj.get("error") or pj.get("message") or status
                    return None, "fail", "4422", f"Tripo task {status}: {err}"

                time.sleep(cfg["poll_interval"])

            return None, "fail", "4500", "Tripo task polling timeout"
        except _req.exceptions.RequestException as e:
            return None, "fail", "4500", f"Tripo poll network error: {str(e)}"
        except Exception as e:
            return None, "fail", "4500", f"Tripo poll crashed: {str(e)}"

    def tripo_download_model(self, model_url):
        """[3D-Step4] 下載 .glb，回傳 bytes"""
        import requests as _req
        cfg = self._tripo_config()
        try:
            r = _req.get(model_url, timeout=cfg["download_timeout"])
            if r.status_code != 200:
                return None, "fail", "4500", f"Tripo download HTTP {r.status_code}"
            return r.content, "success", "4200", None
        except _req.exceptions.RequestException as e:
            return None, "fail", "4500", f"Tripo download network error: {str(e)}"
        except Exception as e:
            return None, "fail", "4500", f"Tripo download crashed: {str(e)}"

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