import os
import io
import uuid
import logging
import json
import torch
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
    AI 影像處理核心類別：負責去背、顏色提取及 Gemini 試穿合成。
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
        

    def analyze_clothing_style(self, image_path):
        """
        利用 Gemini 分析服裝類別、風格標籤與主色。 (狀態碼: 1501)
        """
        if not self.client:
            logger.warning("Gemini Client 未初始化")
            return None, False, "1501", "Gemini API Client not initialized"
        
        try:
            pil_img = Image.open(image_path)
            # --- Prompt 保持原樣 ---
            prompt = """
                Analyze the clothing item in this image. Provide the analysis in English and return ONLY a JSON object.

                【STRICT CATEGORY RULE】:
                You MUST choose EXACTLY one category from this list:
                - "clothing": All tops (T-shirts, blouses, sweaters, hoodies, long/short sleeves).
                - "pants": All trousers and shorts (jeans, leggings, sweatpants).
                - "outerwear": Jackets, coats, blazers, vests.
                - "intimates": Underwear, bras, sleepwear.
                - "skirt": All types of skirts (mini, midi, maxi).
                - "others": Dresses, accessories, or items not fitting above.

                【PURE AESTHETIC STYLE RULE】:
                - "style_name": Identify the fashion aesthetic or genre (e.g., Casual, Formal, Sporty, Streetwear, Vintage, Korean Style, Japanese Style, Preppy, Sweet, Sexy, Minimalist).
                - Min 3 tags. DO NOT include physical descriptions like "oversized", "slim-fit", or "long-sleeve".

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
                "clothes_category": result.get("clothes_category", "others"),
                "style_name": result.get("style_name", []),
                "color_name": result.get("color_name", [])
            }
            
            logger.info(f"✅ Gemini 風格分析成功: {style_analysis}")
            # 成功回傳碼對齊 1200
            return style_analysis, True, "1200", None
            
        except Exception as e:
            error_msg = str(e) if str(e) else "Unknown Gemini API error"
            # 失敗碼修正為 1501
            logger.warning(f"❌ [1501] Gemini 風格分析失敗: {error_msg}")
            return None, False, "1501", error_msg

    #--------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------
    #虛擬試穿
    #--------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------
    def tool_garment_analysis(self, garment_files, user_data):
        """
        [Step 3] 幾何掃描：除了邊界，更要求 Gemini 分析材質垂墜度與透明度。
        """
        from google.genai import types  # 確保有匯入此類型
        
        items = []
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


        for i, f in enumerate(garment_files):
            try:
                pil_img = Image.open(f).convert("RGB")
                category = info_list[i].get('clothes_category', 'others')
                # --- [AI 掃描點] ---
                response = self.client.models.generate_content(
                    model=self.consultant_model,
                    contents=[pil_img, analysis_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                scan_result = response.text 

            except Exception as e:
                # ❌ 這裡對齊狀態碼 2500
                logger.error(f"❌ Step 3 分析失敗: {e}")
                return {
                    "error_code": 2500,
                    "suggest": "AI Model (Analysis) service is currently unavailable. Please try again later."
                }, "fail"

            if category in ['pants', 'skirt']:
                has_bottom = True
            
            items.append({
                "img": pil_img,
                "cat": category,
                "rule": info_list[i].get('garment_info', {}),
                "scan_report": scan_result
            })
            
        return {"items": items, "has_bottom": has_bottom}, "success"


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
                f"### CRITICAL RULE 1: HUMAN PRESENCE CHECK. "
                f"BEFORE ANY SYNTHESIS, verify that the model_image contains a legible HUMAN BODY. "
                f"IF the model_image contains ONLY a flat garment, an empty hanger, or no visible person, "
                f"STOP immediately and output the exact text: 'ERROR: NO_HUMAN_DETECTED'. DO NOT PROCESS. "

                f"### ROLE: Senior Virtual Fashion VFX Engineer. Specializing in Garment Physics and Edge Precision. "

                f"### GEOMETRIC SCAN PROTOCOL: Spatial analysis of garments. Identify: Neckline, Sleeves, Hemline. "
                f"Analyze Anatomical Displacement and Material Properties (Stiffness/Drape/Grain). "

                f"### MODEL DATA: Height {u_h}cm, Waistline Level {u_w}cm. "

                f"### EXECUTION RULES: "
                f"1. CHROMA-KEY ENVIRONMENT: Synthesize the model against a UNIFORM, SOLID WHITE background. Ensure maximum contrast between the garment edges and the background. NO complex shadows, NO props, and NO background textures. "
                f"2. FULL-BODY VISIBILITY: Ensure the entire person (head to toe) is rendered within the frame. Even if the original photo is cropped, attempt to complete the silhouette for a full-body look. "
                f"3. ANATOMICAL FIDELITY: Keep the model's face, skin texture, and body proportions 100% UNCHANGED. Only apply garments over the body. "
                f"4. BOUNDARY LOCK: For tops/outerwear, the hemline MUST end precisely at the {u_w}cm waistline. DO NOT extend fabric; IT IS NOT A DRESS. "
                f"5. AUTO-COMPLETION: {'NONE.' if garments_ctx['has_bottom'] else 'CRITICAL: No lower-body garment detected; paint plain MATTE BLACK trousers to complete the look.'} "
                f"6. TEXTURE FIDELITY: Maintain 100% color accuracy, fabric grain, and logos from the source images. "

                f"### OUTPUT: A high-resolution, photorealistic composite image with sharp, clean edges against a solid white studio background."
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

                # ==========================================
                # 【改動重點】創建純透明滿版畫布 (保留 canvas 變數名)
                # ==========================================
                # 取得原始圖片尺寸
                orig_w, orig_h = result_pil.size

                # 【關鍵修改】：
                # 原本是：Image.new("RGB", (orig_w, orig_h), (255, 255, 255)) -> 純白 RGB
                # 現在改為：模式 "RGBA"，底色 (0, 0, 0, 0) -> 純透明 RGBA
                canvas = Image.new("RGBA", (orig_w, orig_h), (0, 0, 0, 0))

                # ==========================================
                # (原本代碼) 計算置中與底部預留白邊 ( Padding參數均未更動)
                # ==========================================
                # 取得去背圖的邊框信息 (PIL能自動判別 alpha 通道)
                bbox = processed_png.getbbox()

                if bbox:
                    # 裁切出乾淨的人像部分
                    person_img = processed_png.crop(bbox)
                    p_w, p_h = person_img.size
                    
                    # 水平置中 (計算 paste_x)
                    paste_x = (orig_w - p_w) // 2
                    
                    # 底部預留 10% 的畫布高度作為白邊 (計算 bottom_padding)
                    bottom_padding = int(orig_h * 0.1)
                    
                    # 計算貼上的 Y 座標 (paste_y)
                    paste_y = orig_h - p_h - bottom_padding
                    
                    # 頂部安全檢查
                    if paste_y < 0: paste_y = 10
                    
                    # 【執行貼上】：
                    # 貼上透明畫布。第三個參數 person_img 是遮罩，確保透明區塊正確融合。
                    canvas.paste(person_img, (paste_x, paste_y), person_img)
                    
                    # final_output 現在是一個具備正確人體置中比例，且背景純透明的 RGBA 圖片。
                    final_output = canvas
                else:
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
    
    
    
    
    
    
    
    
    
    def generate_densepose(self, model_image_path):
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
    
    
