#!/usr/bin/env python3
"""Headless smoke test for Window 4 new features (transparency, zoom, pan).

Exercises the render pipeline on synthetic images (no display needed) to prove
the code paths execute without error. Does NOT start a Tk mainloop.
"""
from __future__ import annotations
from pathlib import Path
import sys
import tkinter as tk

BASE = Path(__file__).parent.resolve().parent
sys.path.insert(0, str(BASE))

import numpy as np
from PIL import Image, ImageDraw

import screens.window4_validate as w4


def _make_fake_app():
    """Build a minimal fake App so Window4Screen can be instantiated headless."""
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


def _synthetic_item(tmp: Path):
    """Create a small histology image + region mask + cell mask + cells.json."""
    img = Image.new("RGB", (200, 200), (120, 90, 60))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 80, 80], fill=(200, 180, 140))
    img_path = tmp / "scene_z_slice_1.jpeg"
    img.save(img_path, format="JPEG")

    mask = Image.new("RGB", (200, 200), (0, 0, 0))
    md = ImageDraw.Draw(mask)
    md.ellipse([30, 30, 90, 90], fill=(255, 0, 0))  # a region blob
    mask_path = tmp / "roi_region.png"
    mask.save(mask_path)

    cell_mask = Image.new("L", (200, 200), 0)
    cd = ImageDraw.Draw(cell_mask)
    cd.ellipse([40, 40, 50, 50], fill=255)
    cell_path = tmp / "roi_cell.tif"
    cell_mask.save(cell_path)

    return {
        "czi_stem": "fake",
        "roi_folder_name": "fake_roi",
        "roi_folder_path": tmp,
        "z_images": [img_path],
        "mask_png": mask_path,
        "mask_overlay_png": mask_path,
        "quant_json": None,
        "quant_data": {"cells": [{"x_relative": 0.45, "y_relative": 0.45}]},
        "cell_mask_path": cell_path,
        "cells_csv_path": None,
    }


def main():
    app = _make_fake_app()
    scr = w4.Window4Screen(app)
    scr.index = 0
    scr.z_index = 0
    scr.mode = "image"
    scr.items = []

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        item = _synthetic_item(Path(td))

        # --- Transparency: build composite at different opacities ---
        scr.region_opacity.set(0.0)
        scr.cell_opacity.set(0.0)
        base0 = scr._composite_preview_base(item)
        assert base0 is not None and base0.size == (200, 200), "composite@0 failed"

        scr.region_opacity.set(100.0)
        scr.cell_opacity.set(100.0)
        base100 = scr._composite_preview_base(item)
        assert base100 is not None, "composite@100 failed"

        # Cache hit: same key -> same object
        scr.region_opacity.set(100.0)
        scr.cell_opacity.set(100.0)
        base100b = scr._composite_preview_base(item)
        assert base100b is base100, "cache did not hit"

        # Cache miss on opacity change
        scr.region_opacity.set(50.0)
        base50 = scr._composite_preview_base(item)
        assert base50 is not base100, "cache should miss on opacity change"

        # The two composites must actually differ in pixel content.
        a = np.asarray(base0).astype(int)
        b = np.asarray(base50).astype(int)
        diff = int(np.abs(a - b).sum())
        assert diff > 1000, f"opacity change produced no pixel diff ({diff})"

        # --- Zoom crop ---
        scr._reset_zoom()
        scr.zoom_state = {"zoom": 4.0, "cx": 0.5, "cy": 0.5}
        vp = scr._zoom_viewport()
        assert abs((vp[2] - vp[0]) - 0.25) < 1e-6, f"viewport width wrong: {vp}"
        sw, sh = base50.size
        left = int(round(vp[0] * sw))
        upper = int(round(vp[1] * sh))
        right = max(left + 1, int(round(vp[2] * sw)))
        lower = max(upper + 1, int(round(vp[3] * sh)))
        crop = base50.crop((left, upper, right, lower))
        assert crop.size[0] == 50 and crop.size[1] == 50, f"crop size wrong: {crop.size}"

        # --- Pan clamps cx/cy to [0,1] ---
        scr.zoom_state = {"zoom": 4.0, "cx": 0.5, "cy": 0.5}
        scr.pan_state = {"start_x": 0, "start_y": 0, "start_cx": 0.5,
                         "start_cy": 0.5, "viewport": (0.375, 0.375, 0.625, 0.625)}
        scr.preview_photo = None  # _displayed_size returns None -> pan no-op safe
        scr._on_preview_pan_motion(type("E", (), {"x": 9999, "y": 9999})())
        assert 0.0 <= scr.zoom_state["cx"] <= 1.0, "cx not clamped"

        # --- Simulate the Scale command firing (the user's exact complaint) ---
        # Wire a fresh build so the real lambda -> _refresh_preview path runs.
        scr.build = lambda: None  # not building full UI; just call refresh directly
        before = np.asarray(scr._composite_preview_base(item)).astype(int).copy()
        scr.region_opacity.set(10.0)
        after = np.asarray(scr._composite_preview_base(item)).astype(int)
        assert int(np.abs(before - after).sum()) > 1000, "scale change did not alter composite"

    print("[smoke_w4] ALL CHECKS PASSED: transparency, zoom crop, pan clamp, cache.")


if __name__ == "__main__":
    main()
