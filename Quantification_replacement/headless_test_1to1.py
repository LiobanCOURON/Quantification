#!/usr/bin/env python3
"""Test headless : czi dans input -> jpeg 4x -> crop 1:1 -> quantification QuPath -> CSV."""
from __future__ import annotations
from pathlib import Path
import shutil

import sys
sys.path.insert(0, str(Path(__file__).parent))

import convert_czi_to_jpeg as czi_conv
from PIL import Image
import quantification_wrapper as qw

BASE = Path(__file__).parent.resolve()
INPUT = BASE / "input"
OUT = BASE / "output"
TEST_DIR = OUT / "headless_test_1to1"
if TEST_DIR.exists():
    shutil.rmtree(TEST_DIR)
TEST_DIR.mkdir(parents=True, exist_ok=True)

# 1) convertir le czi de input en jpeg downsample 4 (quantification)
print("[1] Conversion czi -> jpeg 4x")
czi_files = list(czi_conv.iter_czi_files(INPUT, recursive=True))
print("    czi trouves :", [p.name for p in czi_files])
all_jpeg = []
for czi in czi_files:
    paths = czi_conv.convert_one_file(
        czi_path=czi, input_dir=INPUT, output_dir=OUT,
        downsample=4, quality=95, recursive=True, fast_mosaic=True,
    )
    all_jpeg.extend(paths)
print("    jpeg crees :", len(all_jpeg))
for p in all_jpeg:
    print("      -", p)

# 2) version ratio 1:1 (crop centre carre) pour masque 1:1
print("[2] Crop 1:1 (carre) des jpeg")
square_dir = TEST_DIR / "square_jpeg"
square_dir.mkdir(parents=True, exist_ok=True)
square_imgs = []
for jp in all_jpeg:
    im = Image.open(jp).convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = im.crop((left, top, left + side, top + side))
    out = square_dir / jp.name
    cropped.save(out, format="JPEG", quality=95)
    square_imgs.append(out)
    print(f"      {jp.name}: {w}x{h} -> {cropped.size}")

# 3) quantification headless (sans UI)
print("[3] run_quantification headless")
result = qw.run_quantification(
    image_paths=square_imgs,
    output_dir=TEST_DIR / "quant_out",
    progress_cb=lambda e: print(f"    [{e.get('type')}] {e.get('message','')}") if e.get("type") in ("file_done", "file_error", "done") else None,
)
print("[4] Resultat :")
print("    status           :", result.status)
print("    total_images     :", result.total_images)
print("    successful_images:", result.successful_images)
print("    total_cells      :", result.total_cells)
print("    summary_csv_path :", result.summary_csv_path)
for r in result.results:
    print(f"    image={r.image} cells={r.num_cells} status={r.status} cells_csv={r.cells_csv_path}")
