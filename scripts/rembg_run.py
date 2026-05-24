"""Run rembg on an input image with configurable alpha-matting parameters.

Usage:
  python3 scripts/rembg_run.py input.png output.png

This script writes output PNG with alpha channel. It also saves a copy
with suffix _rembg.png in the same folder.
"""
import sys
import os
from pathlib import Path

def ensure_module(name):
    try:
        __import__(name)
    except Exception as e:
        print(f"Missing module: {name}. Install it in your venv before running.")
        raise

def run(input_path, output_path,
        erode=10, fg_thresh=240, bg_thresh=10):
    ensure_module('rembg')
    from rembg import remove
    with open(input_path, 'rb') as f:
        data = f.read()
    result = remove(data, alpha_matting=True,
                    alpha_matting_erode_size=erode,
                    alpha_matting_foreground_threshold=fg_thresh,
                    alpha_matting_background_threshold=bg_thresh)
    with open(output_path, 'wb') as o:
        o.write(result)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python3 scripts/rembg_run.py input.png output.png')
        sys.exit(2)
    inp = sys.argv[1]
    out = sys.argv[2]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    try:
        run(inp, out)
        print('Saved', out)
    except Exception as e:
        print('Error running rembg:', e)
