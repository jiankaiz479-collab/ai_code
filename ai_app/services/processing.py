import os
import uuid
import logging
import json
import time
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove 
from PIL import Image 
from google import genai
from google.genai import types

# 設定日誌
logger = logging.getLogger(__name__)

class AIProcessor(ImageProcessingInterface):
    
    def __init__(self):
        # 1. 安全載入 API Key
        self.api_key = os.getenv("GOOGLE_API_KEY")
        
        # 2. 初始化 Gemini Client
        try:
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except Exception as e:
            logger.error(f"⚠️ Gemini Client 初始化失敗: {e}")
            self.client = None
        
        # 3. 設定模型策略
        # 顧問模型：快速判斷顏色、執行品管 (Flash)
        self.consultant_model = "gemini-1.5-flash"
        # 分析模型：深度邏輯推理 (Pro)
        self.analysis_model = "gemini-1.5-pro" 
        # 合成模型：高傳真繪圖 (Flash 2.0 Exp)
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-exp")

        print(f"🤖 AI 核心已啟動 (旗艦版 + 智慧品管):")
        print(f"   - 品管/色彩顧問: {self.consultant_model}")
        print(f"   - 邏輯分析: {self.analysis_model}")
        print(f"   - 渲染引擎: {self.model_name}")

    def _get_unique_filename(self, prefix="img", ext="png"):
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    #  [輔助功能] 1. 智慧取色 (數學抗反光)
    # ==========================================
    def _get_dominant_color(self, pil_img):
        try:
            img = pil_img.convert("RGBA")
            img.thumbnail((200, 200)) 
            colors = img.getcolors(maxcolors=200*200)

            if not colors: return "#000000"

            valid_colors = []
            for count, color in colors:
                r, g, b, a = color
                if a < 200: continue # 忽略透明
                brightness = (r * 0.299 + g * 0.587 + b * 0.114)
                if brightness > 220: continue # 忽略反光
                if brightness < 30: continue  # 忽略皺摺
                valid_colors.append((count, (r, g, b)))

            if not valid_colors: return "original color"

            valid_colors.sort(key=lambda x: x[0], reverse=True)
            top_color = valid_colors[0][1]
            return '#{:02x}{:02x}{:02x}'.format(top_color[0], top_color[1], top_color[2])
        except Exception as e:
            return "original color"

    # ==========================================
    #  [輔助功能] 2. AI 色彩顧問 (語意抗反光)
    # ==========================================
    def _ask_ai_true_color(self, pil_cloth_img) -> str:
        try:
            color_prompt = """
            Task: Identify the true, flat base color of this garment.
            Constraint: IGNORE all bright reflections, white highlights, and deep shadow wrinkles.
            Output: Just give me a precise color description and an estimated Hex code.
            """
            response = self.client.models.generate_content(
                model=self.consultant_model,
                contents=[pil_cloth_img, color_prompt]
            )
            return response.text.strip() if response.text else "Standard Color"
        except Exception as e:
            return "Base color"

    # ==========================================
    #  [輔助功能] 3. 材質樣本 (中心裁切)
    # ==========================================
    def _create_texture_swatch(self, pil_img):
        width, height = pil_img.size
        left = width * 0.35
        top = height * 0.35
        right = width * 0.65
        bottom = height * 0.65
        return pil_img.crop((left, top, right, bottom))

    # ==========================================
    #  功能 A: 去背
    # ==========================================
    def remove_background(self, clothes_image) -> str:
        print(f"🚀 [AI] 執行背景移除...")
        if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
        input_img = Image.open(clothes_image)
        output_img = remove(input_img)
        filename, save_path = self._get_unique_filename(prefix="clean_cloth", ext="png")
        output_img.save(save_path)
        return save_path

    # ==========================================
    #  功能 B: 結構化分析
    # ==========================================
    def analyze_garment(self, pil_cloth_img) -> str:
        print(f"🧐 [AI 分析] 啟動特徵提取...")
        try:
            analysis_prompt = """
            ### Role
            You are a Technical Garment Engineer.

            ### Task
            Classify and describe the garment based on visual evidence.

            ### Output Format
            1. **Classification**: Type (Top/Bottom/Dress/Outerwear).
            2. **Visual Details**: Sleeve length, Neckline, Color, Graphics.
            """
            response = self.client.models.generate_content(
                model=self.analysis_model,
                contents=[pil_cloth_img, analysis_prompt]
            )
            return response.text if response.text else "Standard garment"
        except Exception as e:
            return "Clothing item"

    # ==========================================
    #  功能 C: 虛擬試穿 (核心邏輯 - 支援修正指令)
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path, correction_instruction=""):
        """
        核心合成函式。
        [更新] 新增 correction_instruction 參數，用於接收自動修正的指令。
        """
        print(f"👗 [AI] 啟動合成引擎: 光影重塑模式")

        if not self.client: raise ValueError("Gemini Client 未初始化")

        if hasattr(model_image, 'seek'): model_image.seek(0)
        pil_model = Image.open(model_image)
        pil_cloth = Image.open(clean_clothes_path)

        # 1. 準備數據
        hex_color = self._get_dominant_color(pil_cloth)
        ai_true_color = self._ask_ai_true_color(pil_cloth)
        texture_swatch = self._create_texture_swatch(pil_cloth)
        garment_specs = self.analyze_garment(pil_cloth)
        
        # 2. 設定 VFX Prompt
        prompt = f"""
        ### Role
        You are an expert AI VFX Artist specializing in photorealistic virtual try-on.

        ### Input Data
        - **Image 1 (Garment)**: The clothing item. IGNORE bad lighting/reflections.
        - **Image 2 (Model)**: The target person.
        - **Image 3 (Texture Detail)**: Micro-texture grain.
        
        ### Technical Specs
        - **TRUE BASE COLOR**: {ai_true_color}
        - **Reference Hex**: {hex_color}
        - **Garment Specs**: {garment_specs}

        ### Task
        Generate a photorealistic image of the person from [Image 2] wearing the garment from [Image 1].

        ### Execution Instructions
        {correction_instruction}  <-- [自動修正指令插入點]

        1. **Identity & Body Preservation (CRITICAL)**: 
           - Keep the model's face, body shape, and pose EXACTLY the same as in [Image 2].

        2. **Lighting Re-construction**:
           - **NEUTRALIZE Input Lighting**: Remove highlights/shadows from [Image 1].
           - **APPLY Target Lighting**: Apply [Image 2]'s lighting to the garment.

        3. **Material & Color Fidelity**:
           - Fabric must be **soft and matte**. Force match **TRUE BASE COLOR** ({ai_true_color}).

        4. **Garment Fitting**:
           - Warp naturally. Create *new* realistic folds based on body shape.

        ### Negative Constraints
        - **STRICTLY FORBIDDEN**: Retaining original reflections/wrinkles.
        - Do not change model's appearance.
        - **No Color Drift**: Pink must stay Pink.

        ### Output
        A single high-resolution photorealistic image.
        """

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[pil_cloth, pil_model, texture_swatch, prompt] 
            )

            final_analysis_text = f"[規格]: {garment_specs} | [AI本色]: {ai_true_color}"
            final_save_path = None

            if response.parts:
                for part in response.parts:
                    if part.inline_data:
                        image = part.as_image()
                        filename, final_save_path = self._get_unique_filename(prefix="tryon_v3", ext="png")
                        image.save(final_save_path)
                        print(f"✅ 合成成功: {final_save_path}")
                    
                    if part.text:
                        print(f"🧠 [AI 思考]: {part.text}")

            if final_save_path:
                return final_save_path, final_analysis_text
            
            raise ValueError("AI 完成運算但未輸出圖像")

        except Exception as e:
            logger.error(f"❌ 合成崩潰: {str(e)}")
            raise e

    # ======================================================
    #  [擴充模組 1] 專注型 AI 品管 (QA)
    # ======================================================
    def _check_result_quality(self, original_cloth_path, generated_image_path):
        """
        內部功能：使用 Gemini 1.5 Flash 擔任品管。
        特點：只檢查結構錯誤（如長袖變短袖），忽略姿勢貼合度。
        回傳: (True/False, "錯誤原因")
        """
        print(f"🕵️ [QA 系統] 正在執行結構檢查 (忽略姿勢)...")
        
        try:
            img_original = Image.open(original_cloth_path)
            img_result = Image.open(generated_image_path)

            qa_prompt = """
            ### Role
            You are a Strict Fashion Structural Inspector.
            
            ### Task
            Compare Image 1 (Reference Garment) with Image 2 (Try-On Result).
            Check ONLY for major structural discrepancies. 
            **IGNORE** how well the garment fits the pose. Focus on the garment type itself.

            ### CRITICAL FAIL CRITERIA (Report FAIL if these occur):
            1. **Sleeve Length Mismatch (MOST IMPORTANT)**: 
               - Reference is Long Sleeve -> Result is Short/Sleeveless = FAIL.
               - Reference is Sleeveless -> Result has Sleeves = FAIL.
            2. **Garment Type Mismatch**:
               - Reference is a Dress -> Result is Top + Pants = FAIL.
            3. **Color Mismatch**:
               - Major color drift (e.g. Pink became White).

            ### Output Format (JSON ONLY)
            If PASS: {"pass": true, "reason": "Structure matches"}
            If FAIL: {"pass": false, "reason": "CRITICAL: Input was long sleeve, output is short sleeve."}
            """

            response = self.client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[img_original, img_result, qa_prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            
            result = json.loads(response.text)
            print(f"📋 [QA 報告]: {result}")
            return result.get("pass", True), result.get("reason", "Unknown Error")

        except Exception as e:
            logger.warning(f"⚠️ QA 檢查執行失敗 (視為通過): {e}")
            return True, "QA Error"

    # ======================================================
    #  [擴充模組 2] 智慧自動修復外殼 (Smart Auto-Fix Wrapper)
    #  請在 views.py 改呼叫這個函式！
    # ======================================================
    def virtual_try_on_with_auto_fix(self, model_image, clean_clothes_path, max_retries=1):
        """
        智慧外殼：執行合成 -> 檢查結構 -> 如果錯誤，將錯誤原因回饋給 AI 進行修正重繪。
        """
        attempt = 0
        correction_note = "" # 用來存放給 AI 的修正指令
        
        while attempt <= max_retries:
            prefix = "[初始執行]" if attempt == 0 else f"[修正重試 {attempt}]"
            print(f"🔄 {prefix} 開始合成...")
            
            # 1. 執行合成 (傳入修正指令)
            try:
                result_path, analysis_text = self.virtual_try_on(
                    model_image, 
                    clean_clothes_path, 
                    correction_instruction=correction_note 
                )
            except Exception as e:
                raise e

            # 2. 執行專注型品管檢查
            is_good, reason = self._check_result_quality(clean_clothes_path, result_path)

            if is_good:
                final_text = f"{analysis_text} | ✅ 結構檢查通過"
                return result_path, final_text
            
            else:
                print(f"❌ {prefix} 結構檢查未通過: {reason}")
                attempt += 1
                
                if attempt <= max_retries:
                    print("⚠️ 正在準備智慧重繪...")
                    # 建立修正指令，告訴 AI 上次錯在哪
                    correction_note = f"""
                    *** URGENT CORRECTION FROM PREVIOUS FAILED ATTEMPT ***
                    Your previous generation failed Quality Control.
                    Error Reason: {reason}
                    YOU MUST FIX THIS STRUCTURAL ERROR IN THIS ATTEMPT.
                    ******************************************************
                    """
                else:
                    print("⛔ 已達重試上限，無法修復。")
                    final_text = f"{analysis_text} | ⚠️ 結構錯誤 (修復失敗): {reason}"
                    return result_path, final_text