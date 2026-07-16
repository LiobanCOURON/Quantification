"""Headless proof that the Window-2 atlas overlay stays pixel-aligned with the
MRI under zoom + middle-mouse pan, driving the REAL Window2Screen image methods
(no Tk needed for the PIL/numpy paths).

Faithful to the real data: the atlas overlay is a field of LARGE solid-colored
regions (like real atlas label blobs), not tiny markers. We paint one big shared
block at the same normalized position in BOTH the MRI (yellow) and the atlas
(green). After _blend_mask_overlay the MRI shows the yellow block with the green
block composited on top; if the overlay is locked to the MRI, the green block
centroid coincides with the yellow block centroid in displayed coordinates.

We sweep several zoom/pan states and assert the centroids match within tolerance.
"""
import sys
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _FakeState:
    def __init__(self, base_dir_arg):
        self.base = base_dir_arg
    def base_dir(self):
        return Path(self.base)


class _FakeApp:
    def __init__(self, b):
        self.root = None
        self.state = _FakeState(b)


from screens.window2_mask import Window2Screen


SRC_W, SRC_H = 800, 600  # NON-square like real coronal/atlas slices


def make_sources(mri_path, atlas_path, nx, ny):
    """Paint a big block centered at normalized (nx, ny) in both images."""
    for path, fill, blk in [
        (mri_path, (60, 60, 60), (230, 230, 30)),
        (atlas_path, (0, 0, 0), (0, 255, 0)),
    ]:
        img = Image.new("RGB", (SRC_W, SRC_H), fill)
        d = ImageDraw.Draw(img)
        cx = int(nx * SRC_W); cy = int(ny * SRC_H)
        r = 120
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=blk)
        img.save(path)


def centroid(rgba, kind):
    arr = np.asarray(rgba)
    if kind == "mri":
        mask = (arr[..., 0] > 150) & (arr[..., 1] > 150) & (arr[..., 2] < 120)
    else:
        mask = (arr[..., 1] > 120) & (arr[..., 0] < 120)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def main():
    tmp = tempfile.mkdtemp()
    mri_path = os.path.join(tmp, "mri.png")
    atlas_path = os.path.join(tmp, "atlas.png")
    app = _FakeApp(tmp)
    screen = Window2Screen(app, czi_folder_path=tmp)
    screen.current_atlas_path = atlas_path
    screen.current_coronal_path = mri_path
    screen.mask_opacity = 100  # full opacity so the overlay color is detectable

    tw, th = 400, 365
    cases = [
        ({"zoom": 1.0, "cx": 0.5, "cy": 0.5}, (0.5, 0.5), "zoom=1 (fit)"),
        ({"zoom": 4.0, "cx": 0.3, "cy": 0.7}, (0.3, 0.7), "zoom=4 pan (0.3,0.7)"),
        ({"zoom": 8.0, "cx": 0.85, "cy": 0.15}, (0.85, 0.15), "zoom=8 pan corner"),
        ({"zoom": 3.0, "cx": 0.5, "cy": 0.5}, (0.5, 0.5), "zoom=3 center"),
        ({"zoom": 6.0, "cx": 0.2, "cy": 0.8}, (0.2, 0.8), "zoom=6 pan (0.2,0.8)"),
    ]
    all_ok = True
    for zs, (nx, ny), lbl in cases:
        make_sources(mri_path, atlas_path, nx, ny)
        # fresh source cache so new PNGs are read
        screen.source_cache.clear()
        screen.zoom_state["tl"] = dict(zs)
        vp = screen._zoom_viewport("tl"); screen.viewports["tl"] = vp
        base, _ = screen._load_zoomed_pil(mri_path, tw, th, "tl")
        comp = screen._blend_mask_overlay(base, tw, th)
        W, H = base.size
        mri_c = centroid(base, "mri")
        ovl_c = centroid(comp, "atlas")
        assert mri_c is not None and ovl_c is not None, f"{lbl}: marker missing"
        n_mri = (mri_c[0] / W, mri_c[1] / H)
        n_ovl = (ovl_c[0] / W, ovl_c[1] / H)
        diff = abs(n_mri[0] - n_ovl[0]) + abs(n_mri[1] - n_ovl[1])
        locked = diff < 0.02
        if not locked:
            all_ok = False
        print(f"[{lbl}] mri_norm=({n_mri[0]:.3f},{n_mri[1]:.3f}) "
              f"ovl_norm=({n_ovl[0]:.3f},{n_ovl[1]:.3f}) "
              f"diff={diff:.4f} [{'LOCKED' if locked else 'DRIFT!'}]")
    print("\nASSERT overlay locked to MRI at all zoom/pan states:",
          "PASS" if all_ok else "FAIL")
    assert all_ok, "overlay drifted from MRI"
    print("DONE.")


if __name__ == "__main__":
    main()
