"""影像輸入正規化工具：處理手機現場拍照常見問題。

責任：
  1. 格式正規化（HEIC/HEIF/JPG/PNG/WebP → 統一 RGB/RGBA PIL）
  2. EXIF 旋轉校正（iPhone/Android 都帶 EXIF orientation）
  3. 解析度上限（避免 12MP 巨圖拖慢 rembg）
  4. 色彩 profile 統一（廣色域 P3 → sRGB）

設計原則：每個函式都是 PIL Image → PIL Image，可串聯使用。
"""

import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# HEIC/HEIF 支援（pillow-heif）。未安裝時不會 crash，但會 log 警告。
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False
    logger.warning("pillow-heif not installed; HEIC/HEIF images will not be decodable. "
                   "Install with: pip install pillow-heif")


def is_heif_available() -> bool:
    """供外部檢查 HEIC 支援是否就緒。"""
    return _HEIF_AVAILABLE


def open_any_format(file_obj_or_path) -> Image.Image:
    """開啟任何格式（含 HEIC），統一回傳 PIL Image。

    Args:
        file_obj_or_path: 檔案物件、bytes、或路徑字串。

    Raises:
        OSError: 格式不支援或檔案損毀。
    """
    if isinstance(file_obj_or_path, bytes):
        return Image.open(io.BytesIO(file_obj_or_path))
    return Image.open(file_obj_or_path)


def apply_exif_rotation(img: Image.Image) -> Image.Image:
    """套用 EXIF orientation 旗標並清除（避免下游再次套用）。

    iPhone/Android 拍直立照通常實際儲存的是橫向像素 + EXIF 標「請旋轉 90 度」。
    PIL 預設不主動套用，下游處理會拿到方向錯誤的圖。
    """
    return ImageOps.exif_transpose(img)


def resize_if_huge(img: Image.Image, max_side: int = 2048) -> Image.Image:
    """超過 max_side 時等比縮小。

    動機：
      - 12MP（4032×3024）直接餵 rembg 邊緣會抖、處理時間 3 倍
      - 2048 對去背任務已足夠（rembg 內部多半就 downsample 處理）
    """
    w, h = img.size
    long_side = max(w, h)
    if long_side <= max_side:
        return img
    scale = max_side / long_side
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def convert_to_srgb(img: Image.Image) -> Image.Image:
    """強制轉 sRGB（iPhone P3 廣色域 → 標準 sRGB）。

    避免下游 Gemini 顏色判斷因色彩空間不同而偏移。
    若圖片沒有 ICC profile 就當作已是 sRGB，不做處理。
    """
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        from PIL import ImageCms
        srgb_profile = ImageCms.createProfile("sRGB")
        src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(
            img, src_profile, srgb_profile, outputMode=img.mode
        )
        return converted
    except Exception as e:
        logger.warning(f"sRGB conversion failed, returning original: {e}")
        return img


def normalize_input(file_obj_or_path,
                    max_side: int = 2048,
                    to_srgb: bool = True,
                    target_mode: str = "RGBA") -> Image.Image:
    """一鍵正規化：開檔 → EXIF 旋轉 → resize → sRGB → 統一 mode。

    這是 view 層最常呼叫的入口；單一函式涵蓋多個正規化步驟。
    """
    img = open_any_format(file_obj_or_path)
    img = apply_exif_rotation(img)
    if to_srgb:
        img = convert_to_srgb(img)
    img = resize_if_huge(img, max_side=max_side)
    if img.mode != target_mode:
        img = img.convert(target_mode)
    return img
