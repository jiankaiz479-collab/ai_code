import os
import uuid
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove 
from PIL import Image 
from google import genai
from google.genai import types

# [修正 3] 移除不必要的 quote import (那是 views.py 用的)

class AIProcessor(ImageProcessingInterface):
    
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        
        # 1. 分析專用：輕量、快速
        self.analysis_model = "gemini-3-flash-preview"
        
        # 2. 合成專用：強力繪圖模型
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")

        print(f"🤖 AI 模型載入完成:")
        print(f"   - 分析師: {self.analysis_model}")
        print(f"   - 畫師:   {self.model_name}")

    def _get_unique_filename(self, prefix="img", ext="png"):
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    #  功能 A: 純去背
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
    #  功能 B: 衣服特徵分析 (Internal Function)
    # ==========================================
    def analyze_garment(self, pil_cloth_img) -> str:
        """
        讓 AI 先看懂這件衣服，產生詳細描述
        """
        print(f"🧐 [AI 分析] 正在解析衣服細節...")
        try:
            # 讓 AI 擔任高階時尚分析師，不限字數，細節全開
            # 修改分析指令：強調「客觀描述」與「禁止美化」
            # 終極版 Prompt：加入「比例測繪」與「座標定位」
            analysis_prompt = """
            Role: Expert Technical Fashion Analyst & Forensic Observer.
            Task: Analyze this clothing image and provide an extremely detailed, OBJECTIVE visual description with SPATIAL PRECISION.

            [Requirements]
            1. **NO WORD LIMIT**: Describe every visible detail thoroughly.
            
            2. **Fabric & Texture**: Is it knitted? Woven? Denim? Silk? Describe the surface texture (e.g., ribbed, fuzzy, smooth, shiny), fabric weight, and how light reflects off it.
            
            3. **Patterns & Graphics (Visual Content)**: 
               - Describe stripes, prints, or graphics EXACTLY as they appear. 
               - If the pattern is a repeated print, describe the repeat size and density.
               - If there is text/logo, describe the font style (serif/sans-serif), capitalization, and distress level.

            4. **Construction Details**: Describe the neckline, sleeve style, hemline, and fit exactly as seen. Note any visible stitching (color, thickness).

            5. **Hardware**: Mention buttons, zippers, or drawstrings. Describe their material (metal/plastic), finish (matte/glossy), and color.

            6. **Color Accuracy**: Use specific color names (e.g., "navy blue", "off-white", "heather grey").

            7. **Graphic Scale & Spatial Mapping (CRITICAL)**: 
               - **Relative Scale**: Estimate the size of graphics relative to the garment (e.g., "The central logo occupies approx. 40% of the chest width").
               - **Positioning**: Describe exact placement (e.g., "The print starts 3 inches below the neckline and stops above the hem").
               - **Alignment**: Is the graphic centered, or offset to the left/right?
               - **Aspect Ratio**: Is the graphic tall and narrow, or wide and short?

            [CRITICAL: DO NOT MODIFY OR "FIX" THE DESIGN]
            - **OBSERVE ONLY**: Do not guess obscured details. Do not "improve" the style.
            - **FIDELITY**: If a graphic looks faded, cracked, or off-center, describe it EXACTLY that way. Do not correct it.
            - **Negative Constraints**: Do not use generic terms like "standard fit" without defining what that looks like.

            [Output Goal]
            Produce a technical specification that allows a 1:1 reconstruction of the garment, ensuring precise size and placement of all visual elements.
            """
            
            response = self.client.models.generate_content(
                model=self.analysis_model,
                contents=[pil_cloth_img, analysis_prompt]
            )
            
            description = response.text if response.text else "A stylish garment"
            print(f"📝 分析結果: {description}")
            return description

        except Exception as e:
            print(f"⚠️ 分析失敗 (使用預設值): {e}")
            return "A clothing item"

    # ==========================================
    #  功能 C: 最終合成 (整合了分析與繪圖)
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path):
        print(f"👗 [AI] 執行合成: 模特兒 + 去背衣服")

        if not self.api_key:
            raise ValueError("No API Key found")

        # 1. 讀取圖片 (Load Signals)
        if hasattr(model_image, 'seek'): model_image.seek(0)
        pil_model = Image.open(model_image)
        pil_cloth = Image.open(clean_clothes_path) # 讀取剛剛去背好的圖

        # --- [修正 1] 關鍵步驟：先分析衣服，拿到特徵 ---
        garment_description = self.analyze_garment(pil_cloth)

        # 2. 設定 Prompt (將分析結果注入 Prompt)
        # 這樣做，生成模型就不會「瞎畫」，它會知道這是一件 "Blue Denim Jacket"
        prompt = f"""
        Task: Virtual Try-On.
        Garment Details: {garment_description}
        Action: Generate a photorealistic image of the person (Input 2) wearing the garment (Input 1).
        Constraint: The output must be the person WEARING the clothes. Ensure the texture matches the description.
        """

        # 3. 呼叫 Gemini (Synthesis)
        # 注意：這裡不設 response_mime_type，因為我們可能想看它的思考文字
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[pil_cloth, pil_model, prompt]
        )

        # 預設回傳值
        # 這裡我們回傳剛剛分析出來的 garment_description，這樣你在 Postman 就看得到「衣服分析」
        final_analysis_text = f"[衣服分析]: {garment_description}"
        final_save_path = None

        if response.parts:
            for part in response.parts:
                # 📷 抓取圖片
                if part.inline_data:
                    image = part.as_image()
                    filename, final_save_path = self._get_unique_filename(prefix="tryon_final", ext="png")
                    image.save(final_save_path)
                
                # 📝 抓取生成模型的額外說明 (如果有)
                if part.text:
                    final_analysis_text += f" | [生成備註]: {part.text}"
                    print(f"📝 [生成備註]: {part.text}")

        if final_save_path:
            # 回傳：圖片路徑, 分析文字
            return final_save_path, final_analysis_text
        
        raise ValueError("Gemini 未回傳圖片")