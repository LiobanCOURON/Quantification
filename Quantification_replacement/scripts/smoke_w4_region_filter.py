#!/usr/bin/env python3
"""Headless smoke test for the W4 "delete out-of-region cells" behaviour.

Proves:
  * Cells whose centroid is in the region-mask "none" area (black) are NOT
    drawn in the image preview (visually deleted).
  * They are still present in the quantification JSON of relative coordinates
    (saved, not destroyed).
  * Calculations (diagram count / filtered-cells list) exclude them.

No display / no Tk mainloop required.
"""
from __future__ import annotations
from pathlib import Path
import sys
import tkinter as tk
from PIL import Image, ImageDraw

BASE = Path(__file__).parent.resolve().parent
sys.path.insert(0, str(BASE))

import numpy as np
import screens.window4_validate as w4
from mask_replacer import filter_cells_by_region


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
    """Synthetic scene + region mask + cells (3 in-region, 2 out-of-region)."""
    w = h = 200
    img = Image.new("RGB", (w, h), (120, 90, 60))
    ImageDraw.Draw(img).ellipse([20, 20, 180, 180], fill=(200, 180, 140))
    img_path = tmp / "scene_z_slice_1.jpeg"
    img.save(img_path, format="JPEG")

    # Region mask: red blob in the LEFT half only -> right half is "none".
    mask = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(mask).rectangle([0, 0, 99, 199], fill=(255, 0, 0))
    mask_path = tmp / "roi_region.png"
    mask.save(mask_path)

    cells = [
        {"x_relative": 0.25, "y_relative": 0.25},  # in-region  (left)
        {"x_relative": 0.10, "y_relative": 0.80},  # in-region  (left)
        {"x_relative": 0.45, "y_relative": 0.50},  # in-region  (left)
        {"x_relative": 0.75, "y_relative": 0.25},  # OUT of region (right/none)
        {"x_relative": 0.90, "y_relative": 0.90},  # OUT of region (right/none)
    ]
    return img_path, mask_path, cells


def _count_cyan_markers(preview_rgb, tmp):
    """Count opaque-ish cyan-ish pixels (the drawn in-region cells)."""
    arr = np.asarray(preview_rgb.convert("RGB")).astype(int)
    cyan = (arr[:, :, 0] < 80) & (arr[:, :, 1] > 120) & (arr[:, :, 2] > 150)
    return int(cyan.sum())


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img_path, mask_path, cells = _build_tmp(tmp)

        # 1) filter_cells_by_region (the calculation-side filter) keeps 3.
        kept = filter_cells_by_region(cells, str(mask_path))
        assert len(kept) == 3, f"expected 3 in-region cells, got {len(kept)}"
        # All kept cells are on the left half (in-region).
        for c in kept:
            assert c["x_relative"] < 0.5, f"kept an out-of-region cell: {c}"
        print(f"[ok] filter_cells_by_region keeps {len(kept)}/{len(cells)} cells")

        # 2) Build a Window4Screen and force _make_image_preview to draw from
        #    the filtered cells. We proxy _current_item()/_filtered_cells.
        app = _make_fake_app()
        screen = w4.Window4Screen(app)
        item = {
            "roi_folder_name": "scene",
            "z_images": [img_path],
            "mask_png": mask_path,
            "cell_mask_path": None,
            "quant_data": {"cells": cells},
            "quant_json": None,
            "cells_csv_path": None,
        }
        screen.items = [item]
        screen.index = 0
        screen.z_index = 0
        preview = screen._make_image_preview(item)
        drawn = _count_cyan_markers(preview, tmp)
        # 3 in-region markers must be drawn; the 2 out-of-region ones must NOT.
        assert drawn > 0, "no in-region cell markers were drawn"
        # Each marker is a small disc; ensure out-of-region right half has none.
        arr = np.asarray(preview.convert("RGB")).astype(int)
        right_half = (arr[:, 100:, 0] < 80) & (arr[:, 100:, 1] > 120) & (arr[:, 100:, 2] > 150)
        assert int(right_half.sum()) == 0, "out-of-region (none area) cells were drawn!"
        print(f"[ok] image preview drew {drawn} cyan pixels, 0 in the 'none' area")

        # 3) The JSON (cells list) is untouched -> still holds all 5.
        assert len(item["quant_data"]["cells"]) == 5, "cells JSON mutated!"
        print("[ok] quantification JSON still holds all 5 cells (saved)")

        # 4) _filtered_cells used by diagram/counts == 3.
        assert len(screen._filtered_cells(item)) == 3
        print("[ok] diagram/counts use the 3 in-region cells only")

    print("\nALL CHECKS PASSED — out-of-region cells are visually + computationally removed, still saved in JSON.")


if __name__ == "__main__":
    main()
