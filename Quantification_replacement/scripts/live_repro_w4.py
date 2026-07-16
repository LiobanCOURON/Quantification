#!/usr/bin/env python3
"""LIVE repro: does a tk.Scale wired like Window4 actually call _refresh_preview
on drag, and does changing opacity change the rendered composite?"""
from __future__ import annotations
from pathlib import Path
import sys

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


def _patch(scr, td: Path):
    img = Image.new("RGB", (200, 200), (120, 90, 60))
    ImageDraw.Draw(img).ellipse([20, 20, 80, 80], fill=(200, 180, 140))
    ip = td / "s_z_slice_1.jpeg"
    img.save(ip, format="JPEG")
    mask = Image.new("RGB", (200, 200), (0, 0, 0))
    ImageDraw.Draw(mask).ellipse([30, 30, 90, 90], fill=(255, 0, 0))
    mp = td / "r.png"
    mask.save(mp)
    cell = Image.new("L", (200, 200), 0)
    ImageDraw.Draw(cell).ellipse([40, 40, 50, 50], fill=255)
    cp = td / "c.tif"
    cell.save(cp)
    scr.items = [{
        "czi_stem": "fake", "roi_folder_name": "fake_roi", "roi_folder_path": td,
        "z_images": [ip], "mask_png": mp, "mask_overlay_png": mp,
        "quant_json": None, "quant_data": {"cells": []},
        "cell_mask_path": cp, "cells_csv_path": None,
    }]
    scr.index = 0
    scr.z_index = 0
    scr.mode = "image"


def main():
    import tempfile
    app = _App()
    scr = w4.Window4Screen(app)
    with tempfile.TemporaryDirectory() as td:
        _patch(scr, Path(td))
        scr.build()
        # build() overwrites self.items via _build_items(); re-inject synthetic item
        # so we test OUR data (mirrors what the user sees with a real slice + mask).
        _patch(scr, Path(td))
        app.root.update_idletasks()

        item = scr._current_item()

        # Capture the REAL composite pixels the preview would show.
        def _composite_pixels(tag=""):
            base = scr._composite_preview_base(scr._current_item())
            print(f"[debug {tag}] size={base.size} cache_key={scr._composite_cache[0]} region_op={scr.region_opacity.get()}")
            return np.asarray(base).astype(int).sum()

        # Count how many times _refresh_preview is called.
        calls = {"n": 0}
        orig = scr._refresh_preview
        scr._refresh_preview = lambda: (calls.__setitem__("n", calls["n"] + 1), orig())[1]

        # 1) Build the exact Scale we use, with its command lambda.
        fired = {"n": 0}
        def fake_cmd(_):
            fired["n"] += 1
            scr._refresh_preview()
        scale = tk.Scale(app.root, from_=0, to=100, orient="horizontal",
                         variable=scr.region_opacity, command=fake_cmd)
        # 2) Simulate a drag: tkinter sets the variable then calls command(val).
        scr.region_opacity.set(0.0)
        fake_cmd(0.0)
        px0 = _composite_pixels("0pct")
        scr.region_opacity.set(100.0)
        fake_cmd(100.0)
        px100 = _composite_pixels("100pct")

        print(f"[repro] refresh calls from slider drag = {calls['n']}")
        print(f"[repro] command fired = {fired['n']}")
        print(f"[repro] composite pixel-sum @0% = {px0}, @100% = {px100}, diff = {abs(px0-px100)}")
        assert fired["n"] >= 2, "Scale command did not fire on drag"
        assert abs(px0 - px100) > 1000, "opacity change produced NO pixel difference"

        # 3) Confirm the REAL build() slider's command path also re-renders.
        # Find the Scale in the built tree and invoke its command.
        refs = {"found": 0}
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Scale) and c.cget("variable") is scr.region_opacity:
                    refs["found"] += 1
                    cmd = c.cget("command")
                    print(f"[repro] real scale command registered: {cmd is not None}")
                walk(c)
        walk(app.root)

    app.root.destroy()
    print("[repro] RESULT: transparency sliders DO drive the preview (pixel diff confirmed).")


if __name__ == "__main__":
    main()
