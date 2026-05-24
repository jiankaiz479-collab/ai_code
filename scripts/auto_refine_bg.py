#!/usr/bin/env python3
"""
Auto-refine background removal for uploaded photos.

Usage:
  python scripts/auto_refine_bg.py --input path/to/image.jpg --output outputs/clean.png

This script:
- detects a foreground bounding box automatically (largest contour from edges)
- runs OpenCV GrabCut initialized with that rect
- post-processes mask with morphology and alpha feathering
- writes a PNG with transparent background and a side-by-side preview
"""
import argparse
import os
import cv2
import numpy as np
from PIL import Image


def ensure_dir(p):
    d = os.path.dirname(p)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def largest_contour_bbox(img_gray):
    # Edge detection and morphology to find large object contours
    blurred = cv2.GaussianBlur(img_gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    closed = cv2.dilate(edges, kernel, iterations=2)
    closed = cv2.morphologyEx(closed, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    # Pick largest contour by area
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    h, w = img_gray.shape
    if area < (w * h) * 0.01:  # if too small, ignore
        return None
    x, y, ww, hh = cv2.boundingRect(c)
    return x, y, ww, hh


def expand_rect(rect, img_shape, expand_ratio=0.15):
    x, y, w, h = rect
    H, W = img_shape[:2]
    ex = int(w * expand_ratio)
    ey = int(h * expand_ratio)
    x1 = max(0, x - ex)
    y1 = max(0, y - ey)
    x2 = min(W, x + w + ex)
    y2 = min(H, y + h + ey)
    return (x1, y1, x2 - x1, y2 - y1)


def grabcut_refine(img, rect, iter_count=5):
    mask = np.zeros(img.shape[:2], np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    x, y, w, h = rect
    # If rect is invalid, fallback to full image
    if w <= 0 or h <= 0:
        rect = (1, 1, img.shape[1]-2, img.shape[0]-2)
    cv2.grabCut(img, mask, rect, bgd, fgd, iter_count, cv2.GC_INIT_WITH_RECT)
    # mask: 0,2 are background, 1,3 are foreground
    m = np.where((mask == 2) | (mask == 0), 0, 1).astype('uint8')
    return m


def postprocess_mask(mask, kernel_size=5, erode_frac=0.03):
    k = max(3, kernel_size)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    # Close small holes then open to remove noise
    m = cv2.morphologyEx(mask.astype('uint8'), cv2.MORPH_CLOSE, kernel, iterations=2)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel, iterations=1)
    # Erode slightly to remove fringe
    h, w = m.shape
    e = max(1, int(min(h, w) * erode_frac))
    if e > 0:
        ker2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (e, e))
        m = cv2.erode(m, ker2, iterations=1)
    return m


def create_alpha(mask, blur_radius=15):
    # Create feathered alpha from binary mask using distance transform
    mask_f = (mask * 255).astype('uint8')
    dist = cv2.distanceTransform(mask_f, cv2.DIST_L2, 5)
    if dist.max() > 0:
        dist = dist / dist.max()
    alpha = (dist * 255).astype('uint8')
    # Smooth alpha
    alpha = cv2.GaussianBlur(alpha, (blur_radius|1, blur_radius|1), 0)
    alpha = np.clip(alpha, 0, 255)
    return alpha


def compose_rgba(img, alpha):
    b, g, r = cv2.split(img)
    rgba = cv2.merge([b, g, r, alpha])
    return rgba


def make_preview(original, result_rgba, out_path):
    # Create side-by-side preview (original | result) and save as JPEG
    orig_rgb = cv2.cvtColor(original, cv2.COLOR_BGR2RGB)
    res_rgb = cv2.cvtColor(result_rgba[:, :, :3], cv2.COLOR_BGR2RGB)
    h = max(orig_rgb.shape[0], res_rgb.shape[0])
    # pad both to same height
    def pad(img, H):
        h0 = img.shape[0]
        if h0 == H:
            return img
        pad_bottom = H - h0
        return np.vstack([img, np.ones((pad_bottom, img.shape[1], 3), dtype=img.dtype)*255])
    o = pad(orig_rgb, h)
    r = pad(res_rgb, h)
    side = np.hstack([o, r])
    Image.fromarray(side).save(out_path, quality=90)


def run_auto(input_path, output_png, preview_path=None, max_size=1400, mode='strong'):
    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {input_path}")
    H0, W0 = img.shape[:2]
    scale = 1.0
    if max(H0, W0) > max_size:
        scale = max_size / max(H0, W0)
        img = cv2.resize(img, (int(W0*scale), int(H0*scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    rect = largest_contour_bbox(gray)
    if rect is None:
        # fallback to center rect
        h, w = gray.shape
        rw = int(w * 0.6)
        rh = int(h * 0.6)
        rx = int((w - rw) / 2)
        ry = int((h - rh) / 2)
        rect = (rx, ry, rw, rh)
    rect = expand_rect(rect, img.shape, expand_ratio=0.18)

    # mode presets
    if mode == 'strong':
        iter_count = 6
        kernel_size = 11
        erode_frac = 0.03
        blur_radius = 9
    elif mode == 'preserve_shadow':
        iter_count = 5
        kernel_size = 7
        erode_frac = 0.01
        blur_radius = 21
    elif mode == 'preserve_detail':
        iter_count = 4
        kernel_size = 5
        erode_frac = 0.01
        blur_radius = 25
    else:
        iter_count = 6
        kernel_size = 9
        erode_frac = 0.02
        blur_radius = 21

    mask = grabcut_refine(img, rect, iter_count=iter_count)
    mask = postprocess_mask(mask, kernel_size=kernel_size, erode_frac=erode_frac)
    alpha = create_alpha(mask, blur_radius=blur_radius)
    rgba = compose_rgba(img, alpha)

    ensure_dir(output_png)
    # Save RGBA PNG
    Image.fromarray(cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA)).save(output_png)

    if preview_path:
        ensure_dir(preview_path)
        make_preview(img, rgba, preview_path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', '-i', required=True)
    p.add_argument('--output', '-o', required=True)
    p.add_argument('--preview', '-p', required=False)
    p.add_argument('--mode', choices=['strong', 'preserve_shadow', 'preserve_detail'], default='strong',
                   help='Processing mode: "strong"=強化去殘留 (預設), "preserve_shadow"=保留陰影, "preserve_detail"=保留細節')
    args = p.parse_args()
    run_auto(args.input, args.output, args.preview, mode=args.mode)


if __name__ == '__main__':
    main()
