#!/usr/bin/env python3
"""Headless smoke test for the W4 "delete out-of-region cells" behaviour.

Proves the IMAGE PREVIEW keeps the precise QuPath cell blobs (not dots) but
removes every cell pixel that falls in the region-mask "none" area (black),
while:
  * the quantification JSON of relative coordinates is untouched (cells saved),
  * calculations (diagram, CSVs, counts) exclude out-of-region cells.

No display / no Tk mainloop required.
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

BASE = Path(__file__).parent.resolve().parent
sys.path.insert(0, str(BASE))

import numpy as np
import tkinter as tk
from PIL import Image, ImageDraw

import screens.window4_validate as w4
from mask_replacer import filter_cells_by_region, combine_and_filter_cell_mask


def _make_fake_app():
    root = tk.Tk()
    root.withdraw()

    class _State:
        czi_folder_path = str(BASE / "input")
        slice_depth_um = 0.0
        interslice_um = 0.0

        def base_dir(self):
            return BASE

    class _App:
        def __init__(self):
            self.root = root
            self.state = _State()

        def show(self, *a, **k):
            pass

    return _App()


def _build_tmp(tmp: Path):
    """Synthetic scene + region mask + merged QuPath cell mask (blobs) + cells."""
    w = h = 256
    img = Image.new("RGB", (w, h), (120, 90, 60))
    ImageDraw.Draw(img).ellipse([20, 20, 236, 236], fill=(200, 180, 140))
    img_path = tmp / "scene_z_slice_1.jpeg"
    img.save(img_path, format="JPEG")

    # Region mask: red blob in the LEFT half only -> right half is "none".
    mask = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(mask).rectangle([0, 0, 127, 255], fill=(255, 0, 0))
    mask_path = tmp / "roi_region.png"
    mask.save(mask_path)

    # Merged QuPath cell mask: precise white blobs, some in-region (left),
    # some in the "none" area (right).
    cell = Image.new("L", (w, h), 0)
    cd = ImageDraw.Draw(cell)
    cd.ellipse([40, 40, 70, 70], fill=255)    # in-region blob
    cd.ellipse([15, 170, 45, 200], fill=255)  # in-region blob
    cd.ellipse([100, 120, 130, 150], fill=255)  # in-region blob
    cd.ellipse([200, 40, 230, 70], fill=255)  # OUT (none) blob
    cd.ellipse([220, 200, 250, 230], fill=255)  # OUT (none) blob
    cell_path = tmp / "scene_cell_mask.tif"
    cell.save(cell_path)

    cells = [
        {"x_relative": 0.21, "y_relative": 0.21},  # in-region
        {"x_relative": 0.11, "y_relative": 0.83},  # in-region
        {"x_relative": 0.45, "y_relative": 0.52},  # in-region
        {"x_relative": 0.90, "y_relative": 0.21},  # OUT (none)
        {"x_relative": 0.92, "y_relative": 0.84},  # OUT (none)
    ]
    return img_path, mask_path, cell_path, cells


def _cyan_pixels(rgb_arr):
    return (rgb_arr[:, :, 0] < 80) & (rgb_arr[:, :, 1] > 120) & (rgb_arr[:, :, 2] > 150)


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img_path, mask_path, cell_path, cells = _build_tmp(tmp)

        # 1) calculation-side filter keeps 3 (centroid based).
        kept = filter_cells_by_region(cells, str(mask_path))
        assert len(kept) == 3, f"expected 3, got {len(kept)}"
        print(f"[ok] filter_cells_by_region keeps {len(kept)}/{len(cells)} cells")

        # 2) image preview now curates the precise QuPath mask (raster AND).
        app = _make_fake_app()
        scr = w4.Window4Screen(app)
        item = {
            "roi_folder_name": "scene",
            "z_images": [img_path],
            "mask_png": mask_path,
            "cell_mask_path": cell_path,
            "quant_data": {"cells": cells},
            "quant_json": None,
            "cells_csv_path": None,
        }
        scr.items = [item]; scr.index = 0; scr.z_index = 0

        preview = np.asarray(scr._make_image_preview(item).convert("RGB")).astype(int)
        cyan = _cyan_pixels(preview)
        # The two OUT blobs live entirely in the right half -> must be gone.
        right_cyan = int(cyan[:, 128:].sum())
        assert right_cyan == 0, f"out-of-region QuPath blobs still drawn: {right_cyan} px"
        # The three in-region blobs must remain (precise), with real area.
        left_cyan = int(cyan[:, :128].sum())
        assert left_cyan > 500, f"in-region QuPath blobs missing/too few: {left_cyan} px"
        print(f"[ok] preview keeps {left_cyan} in-region blob px, 0 in 'none' area (precise, not dots)")

        # 3) JSON untouched (saved).
        assert len(item["quant_data"]["cells"]) == 5
        print("[ok] quantification JSON still holds all 5 cells (saved)")

        # 4) diagram/counts use 3 in-region cells.
        assert len(scr._filtered_cells(item)) == 3
        print("[ok] diagram/counts use the 3 in-region cells only")

        # 5) parity: the saved combined mask (combine_and_filter_cell_mask)
        #    matches the preview's curation for the right half.
        combined = tmp / "combined.png"
        combine_and_filter_cell_mask(str(cell_path), str(mask_path), str(combined))
        comb = np.asarray(Image.open(combined).convert("L"))
        assert int((comb[:, 128:] > 0).sum()) == 0, "exported combined mask has out-of-region cells"
        print("[ok] exported combined_cell_mask.png also deletes 'none' area (parity)")

    print("\nALL CHECKS PASSED — precise QuPath blobs kept, out-of-region cells visually + computationally removed, JSON preserved.")


if __name__ == "__main__":
    main()
