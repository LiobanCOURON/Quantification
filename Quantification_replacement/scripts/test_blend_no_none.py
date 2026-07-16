"""Headless test: verify the atlas 'none' (black) region is NOT painted over
the MRI, while colored labeled regions ARE blended according to mask_opacity.

Reproduces the exact numpy math from Window2Screen._blend_mask_overlay.
"""
import numpy as np
from PIL import Image


def blend_mask_overlay(base_pil, mask_pil, mask_opacity):
    """Mirror of window2_mask.Window2Screen._blend_mask_overlay core logic."""
    if mask_pil is None:
        return base_pil
    if mask_pil.size != base_pil.size:
        mask_pil = mask_pil.resize(base_pil.size, Image.Resampling.LANCZOS)
    if mask_pil.mode != "RGBA":
        mask_pil = mask_pil.convert("RGBA")
    alpha = int(round(255 * mask_opacity / 100.0))
    arr = np.asarray(mask_pil, dtype=np.uint16)
    rgb = arr[..., :3]
    is_none = (rgb <= 8).all(axis=2)
    new_alpha = (arr[..., 3].astype(np.uint16) * alpha) // 255
    new_alpha[is_none] = 0
    arr[..., 3] = new_alpha.astype(np.uint8)
    mask = Image.fromarray(arr.astype(np.uint8), "RGBA")
    return Image.alpha_composite(base_pil, mask)


def make_atlas(size=64):
    """Synthetic atlas label: black 'none' background + a red labeled region."""
    img = np.zeros((size, size, 4), dtype=np.uint8)
    # label region (e.g. a colored structure) in the top-left quadrant
    img[4:size // 2, 4:size // 2, 0] = 200   # red channel -> colored label
    img[4:size // 2, 4:size // 2, 1] = 30
    img[4:size // 2, 4:size // 2, 2] = 30
    img[:, :, 3] = 255  # fully opaque label source (alpha scaled later)
    return Image.fromarray(img, "RGBA")


def main():
    size = 64
    mri = Image.new("RGBA", (size, size), (120, 120, 120, 255))  # grey MRI
    atlas = make_atlas(size)
    opacity = 50  # 50%

    out = blend_mask_overlay(mri.convert("RGBA"), atlas, opacity)
    out_arr = np.asarray(out)

    # --- 1. 'none' (black) region must be the original MRI grey, untouched ---
    none_region = out_arr[size // 2 + 2, size // 2 + 2]  # bottom-right = none
    assert np.array_equal(none_region[:3], (120, 120, 120)), \
        f"none region was painted! got {none_region[:3]}"
    print(f"[OK] 'none' (black) region untouched: {tuple(none_region[:3])}")

    # --- 2. colored label region must be blended toward red at 50% ---
    label_region = out_arr[size // 4, size // 4]
    # expected ~ blend of grey(120) and red(200) at 50% -> (160,...)
    assert label_region[0] > 140 and label_region[0] < 180, \
        f"label region blend wrong: {label_region[:3]}"
    print(f"[OK] colored label region blended (50%): {tuple(label_region[:3])}")

    # --- 3. opacity 0 -> overlay fully dropped ---
    out0 = blend_mask_overlay(mri.convert("RGBA"), atlas, 0)
    assert np.array_equal(np.asarray(out0), np.asarray(mri.convert("RGBA"))), \
        "opacity 0 should return the MRI unchanged"
    print("[OK] opacity 0 -> MRI unchanged")

    print("\nALL ASSERTIONS PASSED: black 'none' region is NOT rendered.")


if __name__ == "__main__":
    main()
