"""3D 重建完整流程 service (Tripo image_to_model)。

責任：協調 Tripo 上傳 → 建任務 → 輪詢 → (refine) → 下載 → 存檔。
     處理 Mock 模式（省積分）。

設計：
  - 不依賴 HTTP，輸入 PIL Image + Reconstruct3DOptions dataclass
  - 失敗時回傳結構化錯誤碼 (4xxx)，由 view 映射 HTTP status
  - 可被既有 3D view、未來「一條龍」endpoint、Celery task 共用
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings
from PIL import Image

from .processing import AIProcessor

logger = logging.getLogger(__name__)


@dataclass
class Reconstruct3DOptions:
    """3D 重建參數。view 從 request.POST 解析後傳進來。"""
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    texture_quality: Optional[str] = None
    face_limit: Optional[int] = None
    pbr: Optional[bool] = None
    style: Optional[str] = None
    enable_refine: Optional[bool] = None      # None = 依 .env TRIPO_ENABLE_REFINE
    refine_face_limit: Optional[int] = None


@dataclass
class Reconstruct3DResult:
    glb_bytes: Optional[bytes] = None
    file_name: str = ""
    file_path: str = ""
    ok: bool = False
    code: str = ""
    error_detail: Optional[str] = None
    is_mock: bool = False
    timings: dict = field(default_factory=dict)


class Reconstruct3DService:
    """3D 重建完整流程。"""

    def __init__(self, processor: Optional[AIProcessor] = None):
        self.processor = processor or AIProcessor()

    def reconstruct(self,
                    pil_image: Image.Image,
                    options: Optional[Reconstruct3DOptions] = None) -> Reconstruct3DResult:
        """執行完整 3D 重建流程。

        Args:
            pil_image: 模特照（PIL Image，RGBA / RGB 皆可）
            options: 3D 重建參數（None 時用全預設）
        """
        options = options or Reconstruct3DOptions()
        result = Reconstruct3DResult()
        t_start = time.time()

        try:
            # Mock 模式（直接讀預設 GLB）
            mock_data = self._try_mock()
            if mock_data is not None:
                glb_bytes, file_name, file_path = mock_data
                result.glb_bytes = glb_bytes
                result.file_name = file_name
                result.file_path = file_path
                result.ok = True
                result.code = "4200"
                result.is_mock = True
                result.timings["total"] = time.time() - t_start
                return result

            # Step 1: 上傳
            t = time.time()
            file_token, st, code, err = self.processor.tripo_upload_image(pil_image)
            result.timings["upload"] = time.time() - t
            if st != "success":
                result.code = code
                result.error_detail = err
                return result

            # Step 2: 建任務
            t = time.time()
            task_id, st, code, err = self.processor.tripo_create_task(
                file_token,
                prompt=options.prompt,
                negative_prompt=options.negative_prompt,
                texture_quality=options.texture_quality,
                face_limit=options.face_limit,
                pbr=options.pbr,
                style=options.style,
            )
            result.timings["create_task"] = time.time() - t
            if st != "success":
                result.code = code
                result.error_detail = err
                return result

            # Step 3: 輪詢
            t = time.time()
            model_url, st, code, err = self.processor.tripo_poll_task(task_id)
            result.timings["poll"] = time.time() - t
            if st != "success":
                result.code = code
                result.error_detail = err
                return result

            # Step 3.5: Refine（可選）
            env_refine = os.getenv("TRIPO_ENABLE_REFINE", "true").lower() in ("1", "true", "yes")
            enable_refine = options.enable_refine if options.enable_refine is not None else env_refine

            if enable_refine:
                t = time.time()
                refine_task_id, st, code, err = self.processor.tripo_create_refine_task(
                    draft_task_id=task_id,
                    face_limit=options.refine_face_limit,
                    prompt=options.prompt,
                    negative_prompt=options.negative_prompt,
                    texture_quality=options.texture_quality,
                    pbr=options.pbr,
                )
                if st != "success":
                    result.code = code
                    result.error_detail = err
                    return result
                refined_url, st, code, err = self.processor.tripo_poll_task(refine_task_id)
                if st != "success":
                    result.code = code
                    result.error_detail = err
                    return result
                model_url = refined_url
                result.timings["refine"] = time.time() - t

            # Step 4: 下載
            t = time.time()
            glb_bytes, st, code, err = self.processor.tripo_download_model(model_url)
            result.timings["download"] = time.time() - t
            if st != "success":
                result.code = code
                result.error_detail = err
                return result

            # Step 5: 落地
            glb_dir = os.path.join(settings.MEDIA_ROOT, "tripo")
            os.makedirs(glb_dir, exist_ok=True)
            file_name = f"model3d_{uuid.uuid4().hex[:8]}.glb"
            file_path = os.path.join(glb_dir, file_name)
            with open(file_path, "wb") as f:
                f.write(glb_bytes)

            result.glb_bytes = glb_bytes
            result.file_name = file_name
            result.file_path = file_path
            result.ok = True
            result.code = "4200"

        except Exception as e:
            logger.exception(f"💥 Reconstruct3DService 失敗: {str(e)}")
            result.code = "4500"
            result.error_detail = f"Server crash or heavy load: {str(e)}"

        result.timings["total"] = time.time() - t_start
        return result

    def _try_mock(self):
        """Mock 模式：true 時直接讀預設 GLB，回傳 (bytes, name, path)；否則 None。"""
        debug_mock = os.getenv("TRIPO_DEBUG_MOCK", "false").lower() in ("1", "true", "yes")
        if not debug_mock:
            return None
        mock_name = os.getenv("TRIPO_MOCK_GLB_NAME", "model3d_2ce2ec84.glb")
        mock_path = os.path.join(settings.MEDIA_ROOT, "tripo", mock_name)
        if not os.path.exists(mock_path):
            logger.warning(f"⚠️ Mock GLB not found at {mock_path}; falling back to real Tripo")
            return None
        with open(mock_path, "rb") as f:
            return f.read(), mock_name, mock_path
