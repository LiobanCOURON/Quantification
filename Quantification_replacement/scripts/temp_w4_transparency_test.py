#!/usr/bin/env python3
"""TEMP TEST WINDOW — transparency / zoom / pan on Window 4 preview.

Standalone tkinter window so the user can VISUALLY verify the new interactivity
on guaranteed-present synthetic masks. Uses a DENSE cell mask (not 5 sparse
dots) so the cell-opacity slider is clearly visible — matching real QuPath data.

Run:  .venv/Scripts/python.exe scripts/temp_w4_transparency_test.py
Close the window when done.
"""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile

BASE = Path(__file__).parent.resolve().parent
sys.path.insert(0, str(BASE))

import tkinter as tk
import numpy as np
from PIL import Image, ImageDraw

import screens.window4_validate as w4


class _State:
    czi_folder_path = str(BASE / "input")
    slice_depth_um = 0.0
    interslice_um = 0.0

    def base_dir(self):
        return BASE


class _App:
    def __init__(self):
        self.root = tk.Tk()
        self.state = _State()

    def show(self, *a, **k):
        pass


def _make_synthetic(td: Path):
    """Create histology image + region mask + a DENSE cell mask + cells."""
    W = H = 600
    img = Image.new("RGB", (W, H), (90, 70, 110))
    d = ImageDraw.Draw(img)
    for i in range(0, W, 40):
        d.line([(i, 0), (i, H)], fill=(80, 60, 100))
        d.line([(0, i), (W, i)], fill=(80, 60, 100))
    d.ellipse([120, 120, 360, 360], fill=(150, 120, 170))
    ip = td / "test_z_slice_1.jpeg"
    img.save(ip, format="JPEG", quality=90)

    # Region mask: anatomical regions.
    mask = Image.new("RGB", (W, H), (0, 0, 0))
    md = ImageDraw.Draw(mask)
    md.ellipse([150, 150, 330, 330], fill=(20, 180, 90))   # green region
    md.ellipse([400, 380, 520, 500], fill=(220, 60, 60))   # red region
    mp = td / "test_region.png"
    mask.save(mp)

    # DENSE cell mask: many small cells scattered (qupath-like), gated to regions.
    cell = Image.new("L", (W, H), 0)
    cd = ImageDraw.Draw(cell)
    cells = []
    rng = np.random.default_rng(0)
    pts = []
    for _ in range(120):
        cx = int(rng.integers(160, 320))
        cy = int(rng.integers(160, 320))
        pts.append((cx, cy))
        cx2 = int(rng.integers(410, 510))
        cy2 = int(rng.integers(390, 490))
        pts.append((cx2, cy2))
    for (cx, cy) in pts:
        cd.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=255)
        cells.append({"x_relative": cx / W, "y_relative": cy / H})
    cp = td / "test_cell.tif"
    cell.save(cp)

    item = {
        "czi_stem": "test", "roi_folder_name": "test_roi", "roi_folder_path": td,
        "z_images": [ip], "mask_png": mp, "mask_overlay_png": mp,
        "quant_json": None, "quant_data": {"cells": cells},
        "cell_mask_path": cp, "cells_csv_path": None,
    }
    return item


def main():
    app = _App()
    root = app.root
    root.title("TEMP TEST — Window 4 transparency / zoom / pan")

    scr = w4.Window4Screen(app)
    td = Path(tempfile.mkdtemp(prefix="w4test_"))
    item = _make_synthetic(td)
    scr.items = [item]
    scr.index = 0
    scr.z_index = 0
    scr.mode = "image"

    scr.build()
    scr.items = [item]
    scr._refresh_preview()

    info = tk.Label(
        root,
        text=("TEST WINDOW — drag 'Region mask opacity' and 'Cell mask opacity':\n"
              "both overlays should fade in/out clearly (dense cell mask like real QuPath data).\n"
              "Mouse-wheel = zoom, middle-mouse drag = pan. '?' top-right = help.\n"
              "Close this window when done testing."),
        font=("Arial", 10), bg="#fffbe6", fg="#333", wraplength=540, justify="left",
    )
    info.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=4)

    root.geometry("900x680")
    root.mainloop()


if __name__ == "__main__":
    main()
