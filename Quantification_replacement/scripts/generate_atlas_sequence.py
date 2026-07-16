"""Pre-generate the FULL atlas/coronal image sequence for the atlas slider.

Walks every coronal depth from 0 to the max MRI depth (inclusive) and writes
the matching pair into AtlasImgs/:

    AtlasImgs/coronal_slice_depth_{d}.png
    AtlasImgs/atlas_slice_depth_{d}.png

Pairs that already exist are skipped (idempotent — safe to re-run). This is the
same generation path the app uses (get_coronal_slice + save_slices_as_images),
so the cached images are exactly what the UI would produce on demand — they just
exist ahead of time so sliding the atlas slider never blocks on generation.

Run:
    .venv/Scripts/python.exe scripts/generate_atlas_sequence.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import nibabel as nib

import atlas_position_getter as ap


def main():
    try:
        min_d, max_d = ap.get_depth_range()
    except Exception as e:
        print(f"[atlas-seq] ERROR reading depth range: {e}")
        sys.exit(1)
    print(f"[atlas-seq] depth range: {min_d} .. {max_d} ({max_d - min_d + 1} slices)")

    out_dir = os.path.join(ROOT, "AtlasImgs")
    os.makedirs(out_dir, exist_ok=True)

    # Load the NIfTI volumes ONCE (get_or_create_slice_images reloads per call,
    # which would be ~1000x slower). We replicate its exact slice math here.
    img = nib.load(ap.IRM_PATH)
    atlas_img = nib.load(ap.ATLAS_PATH)
    data = img.get_fdata()
    labels = atlas_img.get_fdata().astype(int)
    if data.shape != labels.shape:
        print(f"[atlas-seq] ERROR: MRI {data.shape} vs atlas {labels.shape}")
        sys.exit(1)

    total = max_d - min_d + 1
    done = 0
    skipped = 0
    created = 0
    t0 = time.time()

    for d in range(min_d, max_d + 1):
        coronal_path, atlas_path = ap.get_slice_image_paths(d, out_dir)
        if os.path.exists(coronal_path) and os.path.exists(atlas_path):
            skipped += 1
            done += 1
            continue
        coronal_slice = data[:, d, :]
        atlas_slice = labels[:, d, :]
        ap.save_slices_as_images(coronal_slice, atlas_slice, d, out_dir)
        created += 1
        done += 1
        if done % 100 == 0:
            el = time.time() - t0
            print(f"[atlas-seq] {done}/{total}  (created={created}, skipped={skipped})  {el:.0f}s")

    el = time.time() - t0
    print(f"[atlas-seq] DONE in {el:.0f}s  total={total} created={created} skipped={skipped}")
    print(f"[atlas-seq] images in {out_dir}")


if __name__ == "__main__":
    main()
