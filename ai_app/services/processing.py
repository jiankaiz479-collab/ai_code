import os
import uuid
from django.conf import settings
from .interfaces import ImageProcessingInterface
from rembg import remove 
from PIL import Image 
from google import genai
from google.genai import types

class AIProcessor(ImageProcessingInterface):
    
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        
        # 如果是 Docker 環境，記得確認已安裝 google-genai
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        
        # ★ 修改重點：優先讀取環境變數，如果沒設定才用預設值
        # 這樣您的 .env 設定 (GEMINI_MODEL_NAME=gemini-2.0-flash-exp 或其他) 就會生效
        self.model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")
        
        print(f"🤖 目前使用的 AI 模型: {self.model_name}")

    def _get_unique_filename(self, prefix="img", ext="png"):
        """
        核心命名邏輯：透過 prefix 區分不同用途的檔案
        """
        # 例如產生: clean_cloth_a1b2c3d4.png
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        return filename, save_path

    # ==========================================
    #  功能 A: 純去背 (產出 clean_cloth)
    # ==========================================
    def remove_background(self, clothes_image) -> str:
        print(f"🚀 [AI] 執行去背: {clothes_image.name}")
        
        if hasattr(clothes_image, 'seek'): clothes_image.seek(0)
        input_img = Image.open(clothes_image)
        output_img = remove(input_img)
        
        # ★ 關鍵：這裡命名為 'clean_cloth'，代表乾淨的衣服
        filename, save_path = self._get_unique_filename(prefix="clean_cloth", ext="png")
        output_img.save(save_path)
        
        print(f"✅ 去背存檔: {filename}")
        # 回傳完整路徑 (方便 View 直接讀取)
        return save_path

    # ==========================================
    #  功能 B: 純合成 (產出 tryon_final)
    # ==========================================
    def virtual_try_on(self, model_image, clean_clothes_path) -> str:
        print(f"👗 [AI] 執行合成: 模特兒 + 去背衣服")

        if not self.api_key:
            raise ValueError("No API Key found")

        # 1. 讀取圖片
        if hasattr(model_image, 'seek'): model_image.seek(0)
        pil_model = Image.open(model_image)
        
        # 直接讀取剛剛去背好的檔案路徑
        pil_cloth = Image.open(clean_clothes_path)

        # 2. 設定 Prompt (保持簡潔)
        prompt = """
        Task: Virtual Try-On.
        Action: Generate a photorealistic image of the person (Input 2) wearing the garment (Input 1).
        Constraint: The output must be the person WEARING the clothes.
        """

        # 3. 呼叫 Gemini
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[pil_cloth, pil_model, prompt],
        )

        # 4. 存檔
        if response.parts:
            for part in response.parts:
                if part.inline_data:
                    image = part.as_image()
                    
                    # ★ 關鍵：這裡命名為 'tryon_final'，代表最終合成圖
                    filename, save_path = self._get_unique_filename(prefix="tryon_final", ext="png")
                    image.save(save_path)
                    
                    print(f"✅ 合成存檔: {filename}")
                    return save_path
        
        raise ValueError("Gemini 未回傳圖片")