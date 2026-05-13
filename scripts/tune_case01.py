"""掃 case_01_sunlit_floor 在不同曝光修復策略下的去背結果。

Usage:
    cd /home/mitlab/try-on/ai_code
    python scripts/tune_case01.py

輸出:
    scripts/tune_outputs/case01_v{N}_{strategy}.png  ← 各種變體的去背結果
    scripts/tune_outputs/case01_v{N}_input.png       ← 修復後丟給 rembg 的中介圖
"""

import logging
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from rembg import new_session, remove

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT = Path(__file__).parent.parent
INPUT_PATH = PROJECT / "portfolio/08_demos/known_limitations/case_01_sunlit_floor_input.png"
OUTPUT_DIR = PROJECT / "scripts/tune_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def report_stats(img: Image.Image, label: str) -> None:
    arr = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    logger.info(
        f"  [{label}] brightness={gray.mean():.1f} "
        f">230_ratio={(gray>230).sum()/gray.size:.1%} "
        f">240_ratio={(gray>240).sum()/gray.size:.1%}"
    )


def run_rembg(img: Image.Image, session) -> Image.Image:
    """跑 rembg + 後處理(LCC + erosion 1px),與 v1 / v2 邏輯一致。"""
    rgba = remove(
        img.convert("RGBA"),
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=10,
    )
    arr = np.array(rgba)
    alpha = arr[:, :, 3]
    binary = (alpha > 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=4)
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        largest = int(np.argmax(areas)) + 1
        kept = (labels == largest).astype(np.uint8)
        kernel = np.ones((3, 3), np.uint8)
        kept = cv2.erode(kept, kernel, iterations=1)
        arr[:, :, 3] = alpha * kept
    return Image.fromarray(arr, mode="RGBA")


# ============================================================
# 修復策略
# ============================================================

def strategy_none(img):
    return img

def strategy_autocontrast_2(img):
    return ImageOps.autocontrast(img.convert("RGB"), cutoff=2)

def strategy_autocontrast_5(img):
    return ImageOps.autocontrast(img.convert("RGB"), cutoff=5)

def strategy_gamma_15(img):
    """gamma=1.5 變暗亮部(壓抑強光區)."""
    arr = np.array(img.convert("RGB"))
    table = np.array([((i / 255.0) ** 1.5) * 255 for i in range(256)]).astype(np.uint8)
    return Image.fromarray(cv2.LUT(arr, table), mode="RGB")

def strategy_gamma_20(img):
    """更激進的變暗."""
    arr = np.array(img.convert("RGB"))
    table = np.array([((i / 255.0) ** 2.0) * 255 for i in range(256)]).astype(np.uint8)
    return Image.fromarray(cv2.LUT(arr, table), mode="RGB")

def strategy_clahe(img):
    """CLAHE 自適應直方圖均衡(局部對比抑制)."""
    arr = np.array(img.convert("RGB"))
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    merged = cv2.merge((l, a, b))
    rgb = cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
    return Image.fromarray(rgb, mode="RGB")

def strategy_dehighlight(img):
    """像素 > 235 的部分壓到 235(壓掉強光高光)."""
    arr = np.array(img.convert("RGB")).astype(np.int32)
    mask = arr > 235
    arr[mask] = 235
    return Image.fromarray(arr.astype(np.uint8), mode="RGB")


STRATEGIES = [
    ("01_none", strategy_none),
    ("02_autocontrast_cutoff2", strategy_autocontrast_2),
    ("03_autocontrast_cutoff5", strategy_autocontrast_5),
    ("04_gamma_1.5", strategy_gamma_15),
    ("05_gamma_2.0", strategy_gamma_20),
    ("06_clahe", strategy_clahe),
    ("07_dehighlight_clip235", strategy_dehighlight),
]


def main():
    if not INPUT_PATH.exists():
        logger.error(f"找不到輸入圖: {INPUT_PATH}")
        sys.exit(1)

    logger.info(f"📥 載入: {INPUT_PATH.name}")
    img = Image.open(INPUT_PATH)
    # 模仿 v2: EXIF 旋轉 + resize 到 2048
    img = ImageOps.exif_transpose(img)
    long_side = max(img.size)
    if long_side > 2048:
        scale = 2048 / long_side
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    logger.info(f"📐 size after normalize: {img.size}")
    report_stats(img, "ORIGINAL")

    logger.info("🚀 初始化 rembg (isnet-general-use)…")
    session = new_session(model_name="isnet-general-use")

    results = []
    for tag, fn in STRATEGIES:
        logger.info(f"🔧 跑變體: {tag}")
        fixed = fn(img)
        report_stats(fixed, tag)
        # 存中介圖
        fixed.save(OUTPUT_DIR / f"case01_{tag}_INPUT.png")

        out = run_rembg(fixed, session)
        out_arr = np.array(out)
        alpha = out_arr[:, :, 3]
        coverage = (alpha > 0).sum() / alpha.size
        logger.info(f"  → mask_coverage={coverage:.1%}")

        # crop bbox 讓圖好看
        bbox = out.getbbox()
        if bbox:
            out = out.crop(bbox)
        out.save(OUTPUT_DIR / f"case01_{tag}_OUTPUT.png")
        results.append((tag, coverage, out.size))

    logger.info("=" * 60)
    logger.info("✅ 完成,變體與覆蓋率:")
    for tag, cov, sz in results:
        logger.info(f"  {tag:35s}  coverage={cov:6.1%}  output_bbox={sz}")
    logger.info(f"📁 輸出目錄: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
