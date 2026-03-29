import os
import io
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
        [Step 5] 核心合成邏輯：加入嚴格空值檢查，防止 NoneType 報錯。
        """
        try:
            m_info = user_data.get('model_info', {})
            u_h = m_info.get('user_height', 170.0)
            u_w = m_info.get('user_waistline', 80.0)

            garment_details = ""
            for i, item in enumerate(garments_ctx['items']):
                rule = item['rule']
                garment_details += f"- Item {i+1} ({item['cat']}): Sleeve {rule.get('clothes_arm_length', 0)}cm, Shoulder {rule.get('clothes_shoulder_width', 0)}cm. "

            # --- [2422 防禦開關] ---
            final_prompt = (
                f"### CRITICAL RULE: BEFORE ANY SYNTHESIS, PERFORM A HUMAN PRESENCE CHECK ON THE model_image. "
                f"IF the model_image contains ONLY A FLAT-LAID GARMENT, AN EMPTY HANGER, or NO VISIBLE HUMAN BODY, you MUST STOP and OUTPUT THE EXACT TEXT: 'ERROR: NO_HUMAN_DETECTED'. DO NOT PROCESS THE IMAGE. "
                f"### ROLE: You are a Senior Virtual Fashion VFX Engineer. Your mission is precision garment synthesis and pixel-level painting. "
                f"### GEOMETRIC SCAN PROTOCOL: Perform a spatial analysis of the garment. Identify EXACT boundaries: Neckline Geometry, Sleeve Termination, and Hemline Termination. "
                f"Analyze Anatomical Displacement (Shoulder Silhouette/Fit Category) and Physical Material Properties (Stiffness/Drape/Surface Grain). "
                f"### MODEL DATA: Height {u_h}cm, Waistline Level {u_w}cm. "
                f"### EXECUTION RULES: "
                f"1. SELECTIVE PAINTING: ONLY modify pixels where garments are applied. KEEP the model's head, hands, feet, and background 100% UNCHANGED. Do NOT paint areas that do not require clothing. "
                f"2. BOUNDARY LOCK: For tops/outerwear, the hemline MUST end precisely at the {u_w}cm waistline. DO NOT extend fabric; IT IS NOT A DRESS. "
                f"3. AUTO-COMPLETION: {'NONE.' if garments_ctx['has_bottom'] else 'CRITICAL: No lower-body garment detected; paint plain MATTE BLACK trousers to complete the look.'} "
                f"4. FIDELITY: Maintain 100% color, texture grain, and graphic anchor (logos/hardware) from the source images. "
                f"### OUTPUT: A high-resolution, seamless, and photorealistic composite image."
            )

            source_images = [item['img'] for item in garments_ctx['items']]
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[model_image, *source_images, final_prompt]
            )
            
            # --- [關鍵修正：檢查 Response 是否有效] ---
            if not response or not response.candidates:
                logger.error("❌ Gemini 回傳空回應 (可能觸發安全過濾器)")
                return {
                    "error_code": 2422,
                    "suggest": "Please use a clearer photo with a visible person. Content blocked by safety filters."
                }, "fail"

            # --- [AI 狀態攔截點] ---
            # 增加安全檢查，防止 .text 在沒有內容時噴錯
            try:
                response_text = response.text
                if "ERROR: NO_HUMAN_DETECTED" in response_text:
                    logger.error("❌ Gemini 主動終止：model_image 偵測不到人體")
                    return {
                        "error_code": 2422,
                        "suggest": "Please use a clearer photo with a visible person. The provided model_image is not valid."
                    }, "fail"
            except Exception:
                # 如果連 .text 都拿不到，代表回應中可能只有圖片或根本沒東西
                pass

            # 取得結果圖片物件
            try:
                # 確保 candidates[0].content.parts 存在
                parts = response.candidates[0].content.parts
                # 尋找含有圖片數據的 part
                generated_part = next((part for part in parts if hasattr(part, 'inline_data') and part.inline_data or getattr(part, 'blob', None)), None)
                
                if not generated_part:
                    raise ValueError("No image part found in response")

                if hasattr(generated_part, 'inline_data') and generated_part.inline_data:
                    image_bytes = generated_part.inline_data.data
                else:
                    image_bytes = generated_part.blob.data
                
                result_pil = Image.open(io.BytesIO(image_bytes))

            except (StopIteration, Exception) as e:
                logger.error(f"❌ 解析圖片數據失敗: {e}")
                return {
                    "error_code": 2422,
                    "suggest": "Please use a clearer photo with a visible person. Our engine couldn't detect a human body to dress."
                }, "fail"

            return {
                "success": True, 
                "result_image": result_pil, 
                "status": "success"
            }, "success"

        except Exception as e:
            err_msg = str(e).lower()
            logger.error(f"❌ Step 5 合成嚴重失敗: {e}")
            
            if any(word in err_msg for word in ["person", "human", "safety", "block", "finish_reason"]):
                return {
                    "error_code": 2422,
                    "suggest": "Please use a clearer photo with a visible person."
                }, "fail"
            
            return {
                "error_code": 2501,
                "suggest": f"AI Synthesis service abnormal: {str(e)}"
            }, "fail"
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
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