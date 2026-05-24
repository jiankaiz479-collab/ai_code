"""Refine a binary mask into a smooth alpha matte and apply to image.

Provides a function `refine_and_save(input_image, mask_image, out_path)`
that will produce an RGBA PNG with improved edges.
"""
from PIL import Image
import numpy as np
import cv2
from pathlib import Path

def refine_alpha_from_mask(mask_np, fg_dilate=10, bg_dilate=10, feather=7):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    sure_fg = cv2.erode(mask_np, kernel, iterations=fg_dilate)
    sure_bg = cv2.erode(255-mask_np, kernel, iterations=bg_dilate)
    trimap = np.full_like(mask_np, 128)
    trimap[sure_fg==255] = 255
    trimap[sure_bg==255] = 0
    fg_dist = cv2.distanceTransform((trimap==255).astype(np.uint8), cv2.DIST_L2, 5)
    bg_dist = cv2.distanceTransform((trimap==0).astype(np.uint8), cv2.DIST_L2, 5)
    denom = fg_dist + bg_dist + 1e-8
    alpha = (fg_dist / denom)
    alpha = np.clip(alpha, 0.0, 1.0)
    alpha = cv2.GaussianBlur((alpha*255).astype(np.uint8), (feather*2+1, feather*2+1), 0)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel, iterations=1)
    return alpha

def apply_alpha_to_image(rgb_pil, alpha_np):
    rgb = np.array(rgb_pil.convert('RGB'))
    if alpha_np.shape[:2] != rgb.shape[:2]:
        alpha_np = cv2.resize(alpha_np, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
    rgba = np.dstack((rgb, alpha_np))
    return Image.fromarray(rgba)

def refine_and_save(input_image_path, mask_image_path, out_path):
    img = Image.open(input_image_path).convert('RGB')
    mask = Image.open(mask_image_path).convert('L')
    mask_np = np.array(mask)
    alpha = refine_alpha_from_mask(mask_np)
    out = apply_alpha_to_image(img, alpha)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    return out_path

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 4:
        print('Usage: python ai_app/utils/refine_alpha.py input.png mask.png out.png')
        sys.exit(2)
    refine_and_save(sys.argv[1], sys.argv[2], sys.argv[3])
