import os
import uuid
from django.conf import settings
from .interfaces import ImageProcessingInterface
# ★ 新增這兩個套件
from rembg import remove 
from PIL import Image 

class AIProcessor(ImageProcessingInterface):
    
    def remove_background(self, clothes_image) -> str:
        print(f"🚀 [AI 啟動] 正在為 {clothes_image.name} 進行去背運算...")
        
        # 1. 使用 PIL 讀取上傳的圖片
        input_img = Image.open(clothes_image)
        
        # 2. ★ 呼叫真正的 AI 模型進行去背
        # (第一次執行時，程式會自動從網路下載 U2-Net 模型，大約 170MB，會卡住一下是正常的)
        output_img = remove(input_img)
        
        # 3. 準備存檔路徑 (一定要存成 .png 才能保留透明背景)
        filename = f"removed_bg_{uuid.uuid4().hex[:8]}.png"
        save_path = os.path.join(settings.MEDIA_ROOT, filename)
        
        # 確保資料夾存在
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        
        # 4. 儲存結果
        output_img.save(save_path)
        
        # 5. 回傳網址
        return os.path.join(settings.MEDIA_URL, filename)

    def virtual_try_on(self, model_image, clothes_image) -> str:
        # 試穿功能我們先保留模擬狀態，等去背成功後再來寫這個
        print(f"👗 [模擬] 試穿功能: {model_image.name} + {clothes_image.name}")
        
        # 這裡用簡單的方式存個檔做樣子
        save_path = os.path.join(settings.MEDIA_ROOT, f"tryon_{clothes_image.name}")
        with open(save_path, 'wb+') as dest:
            for chunk in clothes_image.chunks():
                dest.write(chunk)
                
        return os.path.join(settings.MEDIA_URL, f"tryon_{clothes_image.name}")