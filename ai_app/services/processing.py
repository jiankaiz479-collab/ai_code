import os
import uuid
import logging
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove 
from PIL import Image 
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

class AIProcessor(ImageProcessingInterface):
    
    def __init__(self):
        # 1. 安全載入 API Key
        self.api_key = os.getenv("GOOGLE_API_KEY")
        
        # 2. 初始化 Gemini Client (加入錯誤處理，避免沒 Key 直接當機)
        try:
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except Exception as e:
            logger.error(f"⚠️ Gemini Client 初始化失敗: {e}")
            self.client = None
        
        # 3. 設定模型
        # 分析用的模型 (Pro 版本看細節較準)
        self.analysis_model = "gemini-1.5-pro" 
        # 合成用的模型 (Flash 2.0 Exp 版本)
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-exp")

        print(f"🤖 AI 模型載入完成:")
        print(f"   - 分析師: {self.analysis_model}")
        print(f"   - 畫師:   {self.model_name}")

    def _get_unique_filename(self, prefix="img", ext="png"):
        """產生唯一檔名並回傳完整路徑"""
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    #  [輔助功能] 1. 科學取色 (Hex Code)
    # ==========================================
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

    # ==========================================
    #  [輔助功能] 2. 材質裁切 (Texture Swatch)
    # ==========================================
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

    # ==========================================
    #  功能 A: 純去背 (Remove Background)
    # ==========================================
    def remove_background(self, clothes_image) -> str:
        print(f"🚀 [AI] 執行去背...")
        if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
        input_img = Image.open(clothes_image)
        output_img = remove(input_img)
        filename, save_path = self._get_unique_filename(prefix="clean_cloth", ext="png")
        output_img.save(save_path)
        return save_path

    # ==========================================
    #  功能 B: 衣服特徵分析 (Analyze Garment)
    # ==========================================
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

    # ==========================================
    #  功能 C: 虛擬試穿 (Virtual Try-On) - 最終強大版
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path):
        print(f"👗 [AI] 執行合成: 強力真實化模式 (Realism + Identity Lock)")

        if not self.client:
            raise ValueError("Gemini Client 未初始化 (API Key 可能有誤)")

        # 1. 讀取圖片
        if hasattr(model_image, 'seek'): model_image.seek(0)
        pil_model = Image.open(model_image)
        pil_cloth = Image.open(clean_clothes_path)

        # --- [步驟 A] 取得精確色碼 ---
        hex_color = self._get_dominant_color(pil_cloth)
        print(f"🎨 鎖定衣服色碼: {hex_color}")

        # --- [步驟 B] 製作材質特寫圖 ---
        texture_swatch = self._create_texture_swatch(pil_cloth)

        # 2. 分析衣服
        garment_description = self.analyze_garment(pil_cloth)

        # 3. 設定 Prompt (加入 色碼 + 材質 + 身分保護)
        prompt = f"""
        ### Role
        You are an expert AI VFX Artist specializing in photorealistic virtual try-on.

        ### Input Data
        - **Image 1 (Garment)**: The full view of the clothing.
        - **Image 2 (Model)**: The target person.
        - **Image 3 (Texture Detail)**: A MICROSCOPIC CLOSE-UP of the fabric. Use this for texture mapping.
        
        ### Technical Specs
        - **Target Color Code**: {hex_color} (You MUST strictly adhere to this Hex Color)
        - **Garment Description**: {garment_description}

        ### Task
        Generate a photorealistic image of the person from [Image 2] wearing the garment from [Image 1].

        ### Execution Instructions
        1. **Identity & Body Preservation (CRITICAL)**: 
           - **Face**: You MUST keep the model's facial features (eyes, nose, mouth, jawline), expression, and skin texture EXACTLY the same as in [Image 2]. 
           - **Body Shape**: Do NOT alter the model's physique. The height, weight, proportions, and body measurements must remain UNCHANGED. Do not make the model slimmer or more muscular.
           - **Pose**: Keep the pose identical to the original image.

        2. **Color Fidelity**: 
           - The output garment MUST match the Target Color Code {hex_color} exactly.
           - Do not let the scene lighting wash out the color.

        3. **Material Rendering**:
           - Apply the texture details visible in [Image 3] to the entire garment.
           - **Reflectance**: Observe how light hits the fabric in [Image 1] (matte vs glossy) and replicate it.

        4. **Garment Fitting**:
           - Warp and shape the garment to fit the model's body naturally.
           - The clothes should wrap around the body's actual volume, not change the body's volume.

        ### Negative Constraints (STRICTLY FORBIDDEN)
        - Do not change the model's face, body shape, gender, or ethnicity.
        - Do not generate a cartoon or illustration style. Output must be a Photo.
        - Do not "beautify" or apply filters to the model.

        ### Output
        A single high-resolution photorealistic image.
        """

        # 4. 呼叫 Gemini (傳送 3 張圖 + Prompt)
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                # 順序：衣服全圖, 模特兒, 材質特寫, Prompt
                contents=[pil_cloth, pil_model, texture_swatch, prompt] 
            )

            # 5. 處理回傳結果
            final_analysis_text = f"[衣服分析]: {garment_description} | [鎖定色碼]: {hex_color}"
            final_save_path = None

            if response.parts:
                for part in response.parts:
                    # 📷 抓取圖片
                    if part.inline_data:
                        image = part.as_image()
                        filename, final_save_path = self._get_unique_filename(prefix="tryon_final", ext="png")
                        image.save(final_save_path)
                        print(f"✅ 圖片已儲存至: {final_save_path}")
                    
                    # 📝 抓取 AI 的思考備註
                    if part.text:
                        clean_text = part.text.replace("\n", " ").strip()
                        final_analysis_text += f" | [AI 備註]: {clean_text[:100]}..." # 只擷取前100字避免 header 太長
                        print(f"📝 [AI 備註]: {part.text}")

            if final_save_path:
                # 回傳: 圖片路徑, 分析文字
                return final_save_path, final_analysis_text
            
            raise ValueError("Gemini 執行完成，但未回傳任何圖片 (可能被安全過濾)")

        except Exception as e:
            logger.error(f"❌ 合成失敗: {str(e)}")
            raise e