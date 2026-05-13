"""Round 2 tuning: 圍繞 CLAHE 找最佳組合。

目標:case_01 拿到「完整的衣服去背」(hood + 上衣 + 褲子)。
"""
import logging
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps
from rembg import new_session, remove

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT = Path(__file__).parent.parent
INPUT_PATH = PROJECT / "portfolio/08_demos/known_limitations/case_01_sunlit_floor_input.png"
OUTPUT_DIR = PROJECT / "scripts/tune_outputs_r2"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def clahe(img, clipLimit, tile):
    arr = np.array(img.convert("RGB"))
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    c = cv2.createCLAHE(clipLimit=clipLimit, tileGridSize=(tile, tile))
    l = c.apply(l)
    return Image.fromarray(cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB), mode="RGB")


def gamma(img, g):
    arr = np.array(img.convert("RGB"))
    table = np.array([((i / 255.0) ** g) * 255 for i in range(256)]).astype(np.uint8)
    return Image.fromarray(cv2.LUT(arr, table), mode="RGB")


def run_rembg(img, session, alpha_matting=True):
    rgba = remove(
        img.convert("RGBA"),
        session=session,
        alpha_matting=alpha_matting,
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


# Round 2: 圍繞 CLAHE 的參數掃描 + 組合策略
STRATEGIES = [
    ("01_clahe_2.0_t8",  lambda i: clahe(i, 2.0, 8)),   # baseline
    ("02_clahe_3.0_t8",  lambda i: clahe(i, 3.0, 8)),
    ("03_clahe_4.0_t8",  lambda i: clahe(i, 4.0, 8)),
    ("04_clahe_2.0_t4",  lambda i: clahe(i, 2.0, 4)),   # 更細的 grid
    ("05_clahe_2.0_t16", lambda i: clahe(i, 2.0, 16)),
    ("06_clahe_3.0_t4",  lambda i: clahe(i, 3.0, 4)),
    ("07_clahe2.0+gamma1.3", lambda i: gamma(clahe(i, 2.0, 8), 1.3)),
    ("08_clahe2.0+gamma1.5", lambda i: gamma(clahe(i, 2.0, 8), 1.5)),
    ("09_clahe3.0+gamma1.3", lambda i: gamma(clahe(i, 3.0, 8), 1.3)),
    ("10_clahe4.0+gamma1.5", lambda i: gamma(clahe(i, 4.0, 8), 1.5)),
]


def main():
    img = Image.open(INPUT_PATH)
    img = ImageOps.exif_transpose(img)
    long_side = max(img.size)
    if long_side > 2048:
        scale = 2048 / long_side
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.LANCZOS)
    img = img.convert("RGB")

    session = new_session(model_name="isnet-general-use")

    results = []
    for tag, fn in STRATEGIES:
        fixed = fn(img)
        fixed.save(OUTPUT_DIR / f"{tag}_INPUT.png")
        out = run_rembg(fixed, session)
        alpha = np.array(out)[:, :, 3]
        coverage = (alpha > 0).sum() / alpha.size
        bbox = out.getbbox()
        if bbox:
            out = out.crop(bbox)
        out.save(OUTPUT_DIR / f"{tag}_OUTPUT.png")
        logger.info(f"{tag}: coverage={coverage:.1%}  bbox={out.size}")
        results.append((tag, coverage, out.size))

    print("\n=== 排序(覆蓋率高低)===")
    for tag, cov, sz in sorted(results, key=lambda x: -x[1]):
        print(f"  {tag:32s}  {cov:6.1%}  {sz}")


if __name__ == "__main__":
    main()
