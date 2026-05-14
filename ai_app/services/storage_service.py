import io
import uuid
import logging
from PIL import Image
from minio import Minio
from django.conf import settings

logger = logging.getLogger(__name__)

class StorageService:
    """負責將 AI 處理完的圖片與縮圖上傳至 MinIO 雲端硬碟"""
    def __init__(self):
        endpoint = getattr(settings, 'MINIO_ENDPOINT', 'ai-minio:9002')
        # 🛡️ 終極防呆：如果在 Docker 內讀取到 localhost，強制轉為內部網址 ai-minio:9002
        if 'localhost' in endpoint or '127.0.0.1' in endpoint:
            endpoint = 'ai-minio:9002'
        endpoint = endpoint.replace('http://', '').replace('https://', '')
        access_key = getattr(settings, 'MINIO_ACCESS_KEY', 'minioadmin')
        secret_key = getattr(settings, 'MINIO_SECRET_KEY', 'minioadmin')
        secure = getattr(settings, 'MINIO_SECURE', False)
        
        try:
            self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            self.bucket = getattr(settings, 'MINIO_BUCKET_HISTORY', 'history-images')
        except Exception as e:
            logger.error(f"MinIO 客戶端初始化失敗: {e}")
            self.client = None
            
    def upload_image(self, pil_image: Image.Image, prefix="img"):
        if not pil_image or not self.client:
            return None, None
            
        try:
            object_key = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
            thumb_key = f"thumb_{object_key}"
            
            orig_buf, thumb_buf = io.BytesIO(), io.BytesIO()
            pil_image.save(orig_buf, format="PNG")
            
            thumb_img = pil_image.copy()
            thumb_img.thumbnail((400, 400))
            thumb_img.save(thumb_buf, format="PNG")
            
            self.client.put_object(self.bucket, object_key, io.BytesIO(orig_buf.getvalue()), length=len(orig_buf.getvalue()), content_type="image/png")
            self.client.put_object(self.bucket, thumb_key, io.BytesIO(thumb_buf.getvalue()), length=len(thumb_buf.getvalue()), content_type="image/png")
            
            logger.info(f"✅ [MinIO] 圖片與縮圖上傳成功: {self.bucket}/{object_key}")
            return object_key, thumb_key
        except Exception as e:
            logger.error(f"MinIO 上傳失敗: {e}")
            return None, None