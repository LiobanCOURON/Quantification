"""Headless proof that the Window-2 atlas overlay stays pixel-aligned with the
MRI under zoom + middle-mouse pan.

We replicate the EXACT transform chain used by Window2Screen for the `tl` pane
with a NON-SQUARE source (real coronal/atlas slices are not square, which is
what makes the old bug visible) and a grid of markers so a subset stays inside
the zoomed viewport at every pan position.

  MRI :  crop(src, viewport) -> uniform-fit to (tw,th) -> base_pil (size WxH)
  OVL :  crop(atlas, SAME viewport) -> ROTATE_270+FLIP_LEFT_RIGHT
         -> uniform-fit to (W,H) with same get_img_dims rule -> center on WxH canvas

For every marker that remains visible in BOTH the MRI and the overlay, the
normalized source coordinate recovered from each must agree. The OLD path
(independent fit to tw,th + non-uniform post-rotation stretch) fails this once
the source is non-square and zoomed/panned; the NEW path passes.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.image_utils import get_img_dims  # same fit rule as the app

SRC_W, SRC_H = 800, 600  # NON-square, like real slices (triggers the bug)


def zoom_viewport(zoom_state):
    st = zoom_state
    zoom = max(1.0, st["zoom"])
    half = 0.5 / zoom
    cx, cy = st["cx"], st["cy"]
    nx0 = max(0.0, cx - half); ny0 = max(0.0, cy - half)
    nx1 = min(1.0, cx + half); ny1 = min(1.0, cy + half)
    if (nx1 - nx0) < 2 * half and (nx1 - nx0) < 1.0:
        cx = (nx0 + nx1) / 2
        nx0 = max(0.0, cx - half); nx1 = min(1.0, cx + half)
    if (ny1 - ny0) < 2 * half and (ny1 - ny0) < 1.0:
        cy = (ny0 + ny1) / 2
        ny0 = max(0.0, cy - half); ny1 = min(1.0, cy + half)
    return (nx0, ny0, nx1, ny1)


def make_source(fill, marker):
    img = Image.new("RGB", (SRC_W, SRC_H), fill)
    d = ImageDraw.Draw(img)
    for gx in range(1, 5):
        for gy in range(1, 5):
            x = int(gx * SRC_W / 5.0); y = int(gy * SRC_H / 5.0)
            r = 12
            d.ellipse([x - r, y - r, x + r, y + r], fill=marker)
    return img


def crop_viewport(src, viewport):
    sw, sh = src.size
    nx0, ny0, nx1, ny1 = viewport
    left = int(round(nx0 * sw)); upper = int(round(ny0 * sh))
    right = max(left + 1, int(round(nx1 * sw))); lower = max(upper + 1, int(round(ny1 * sh)))
    return src.crop((left, upper, right, lower))


def mri_pipeline(mri, viewport, tw, th):
    cropped = crop_viewport(mri, viewport)
    fw, fh = get_img_dims(cropped.width, cropped.height, tw, th)
    return cropped.resize((fw, fh), Image.Resampling.LANCZOS).convert("RGBA")


def overlay_pipeline_NEW(atlas, viewport, base_size):
    cropped = crop_viewport(atlas, viewport)
    cropped = cropped.transpose(Image.Transpose.ROTATE_270).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    cropped = cropped.convert("RGBA")
    fw, fh = get_img_dims(cropped.width, cropped.height, base_size[0], base_size[1])
    resized = cropped.resize((fw, fh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", base_size, (0, 0, 0, 0))
    canvas.paste(resized, ((base_size[0] - fw) // 2, (base_size[1] - fh) // 2))
    return canvas


def overlay_pipeline_OLD(atlas, viewport, tw, th, base_size):
    cropped = crop_viewport(atlas, viewport)
    fw, fh = get_img_dims(cropped.width, cropped.height, tw, th)
    fitted = cropped.resize((fw, fh), Image.Resampling.LANCZOS).convert("RGBA")
    fitted = fitted.transpose(Image.Transpose.ROTATE_270).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if fitted.size != base_size:
        fitted = fitted.resize(base_size, Image.Resampling.LANCZOS)
    return fitted


def marker_centroids(rgba, kind):
    arr = np.asarray(rgba)
    if kind == "mri":
        mask = (arr[..., 0] > 150) & (arr[..., 1] > 150) & (arr[..., 2] < 120)
    else:
        mask = (arr[..., 1] > 120) & (arr[..., 0] < 120)
    ys, xs = np.where(mask)
    H, W = arr.shape[0], arr.shape[1]
    pts = {}
    for x, y in zip(xs, ys):
        # cluster into nearest grid cell (the 4x4 marker lattice)
        gx = int(round(x / W * 4)) * W / 4.0
        gy = int(round(y / H * 4)) * H / 4.0
        pts[(round(gx), round(gy))] = (x, y)
    return pts


def main():
    mri = make_source((60, 60, 60), (230, 230, 30))
    atlas = make_source((0, 0, 0), (20, 200, 20))
    tw, th = 400, 365
    cases = [
        ({"zoom": 1.0, "cx": 0.5, "cy": 0.5}, "zoom=1 (fit)"),
        ({"zoom": 4.0, "cx": 0.3, "cy": 0.7}, "zoom=4 pan (0.3,0.7)"),
        ({"zoom": 8.0, "cx": 0.85, "cy": 0.15}, "zoom=8 pan corner"),
        ({"zoom": 3.0, "cx": 0.5, "cy": 0.5}, "zoom=3 center"),
        ({"zoom": 6.0, "cx": 0.2, "cy": 0.8}, "zoom=6 pan (0.2,0.8)"),
    ]
    all_ok = True
    for zs, lbl in cases:
        vp = zoom_viewport(zs)
        base = mri_pipeline(mri, vp, tw, th)
        W, H = base.size
        ovl_new = overlay_pipeline_NEW(atlas, vp, (W, H))
        ovl_old = overlay_pipeline_OLD(atlas, vp, tw, th, (W, H))
        mri_pts = marker_centroids(base, "mri")
        new_pts = marker_centroids(ovl_new, "atlas")
        old_pts = marker_centroids(ovl_old, "atlas")
        # compare only markers visible in BOTH mri and overlay
        max_new = 0.0; max_old = 0.0; n = 0
        for key in mri_pts:
            for ovl_pts, store in ((new_pts, "new"), (old_pts, "old")):
                if key not in ovl_pts:
                    continue
                mx, my = mri_pts[key]
                ox, oy = ovl_pts[key]
                n_mri = (mx / W, my / H)
                n_ovl = (ox / W, oy / H)
                diff = abs(n_mri[0] - n_ovl[0]) + abs(n_mri[1] - n_ovl[1])
                if store == "new":
                    max_new = max(max_new, diff)
                else:
                    max_old = max(max_old, diff)
                n += 1
        good_new = max_new < 0.02
        good_old = max_old < 0.02
        if not good_new:
            all_ok = False
        tag_new = "LOCKED" if good_new else "DRIFT!"
        tag_old = "LOCKED" if good_old else "DRIFT!"
        print(f"[{lbl}] visible_markers={n}  NEW max_diff={max_new:.4f}[{tag_new}]  "
              f"OLD max_diff={max_old:.4f}[{tag_old}]")
    print("\nASSERT NEW overlay locked to MRI at all zoom/pan states:",
          "PASS" if all_ok else "FAIL")
    assert all_ok, "NEW overlay drifted"
    print("DONE.")


if __name__ == "__main__":
    main()
