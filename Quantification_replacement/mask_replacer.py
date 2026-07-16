"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
# Real implementation of the mask replacement script.
#
# Goal: warp the atlas/mask slice (at full native resolution) so that its
# landmarks line up with the corresponding landmarks on the histology image,
# then overlay the warped colored mask on top of the histology at ~35% opacity.
#
# Inputs (see contract in ui.py):
#   - depth:              int, current atlas depth (coronal slice index).
#   - normalized_points:  list of pairs [(tl, tr), ...] where each item is a
#                         normalized (x, y) coordinate in [0, 1], relative to
#                         the *displayed* image. tl is from the MRI/atlas image
#                         (source), tr is from the histology image (target).
#                         Pairing is by click order.
# Output:
#   - str path to the resulting image (histology with the warped mask overlaid).
#
# Method:
#   - 2 points  -> SimilarityTransform  (translation + rotation + uniform scale)
#   - >=3 points -> Thin Plate Spline (TPS) non-rigid warp, exact at landmarks
#   - The mask (integer region labels) is warped with nearest-neighbor (order=0)
#     to preserve region IDs, then colored with the atlas .label RGB table and
#     blended over the histology at alpha=0.35 for the non-background regions.

import csv
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from skimage.transform import warp, SimilarityTransform, ThinPlateSplineTransform

from atlas_position_getter import get_atlas_slice_fullres

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "ReplacedMask.temp.png")
# ITK-SNAP label description file giving the official RGB for each region.
LABEL_FILE = os.path.join(BASE_DIR, "Rat atlas", "WHS_SD_rat_atlas_v4.label")
# Region volumes (ID -> volume in mm³) shipped with the atlas.
VOLUMES_FILE = os.path.join(BASE_DIR, "Rat atlas", "atlas_volumes.csv")

# Background label in the atlas is 0; we only overlay non-background regions.
BACKGROUND_LABEL = 0
# Overlay transparency: the mask contributes 35% of the final pixel color.
ALPHA = 0.35


def _resolve_path(name):
    """Return an existing image path, falling back to the .temp.<ext> variant."""
    full = os.path.join(BASE_DIR, name)
    if os.path.exists(full):
        return full
    base, ext = os.path.splitext(name)
    temp_name = base + ".temp" + ext
    if os.path.exists(os.path.join(BASE_DIR, temp_name)):
        return os.path.join(BASE_DIR, temp_name)
    return full  # let PIL raise a clear error if missing


def _load_label_table(label_file):
    """
    Parse an ITK-SNAP .label file into a dense RGB lookup table + a name map.

    File format (one region per line):
        IDX   R   G   B   A   VIS   MSH   "LABEL"
    Comment lines start with '#'. Returns:
        lut       : (max_idx+1, 3) uint8 array with each region's RGB;
                    indices absent from the file (and label 0) default to
                    black, matching the "Clear Label" background.
        names     : {idx: human-readable label name} (idx 0 -> "Clear Label").
        rgb_to_id : {(r,g,b): idx} exact reverse lookup used to map a mask
                    pixel back to its region id.
    """
    colors = {}
    names = {}
    max_idx = 0
    with open(label_file, "r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = raw.split()
            idx = int(parts[0])
            r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
            colors[idx] = (r, g, b)
            if idx > max_idx:
                max_idx = idx
            # Name is everything between the first and last double quote.
            first = raw.find('"')
            last = raw.rfind('"')
            if first != -1 and last != -1 and last > first:
                names[idx] = raw[first + 1:last]
            else:
                names[idx] = str(idx)

    lut = np.zeros((max_idx + 1, 3), dtype=np.uint8)
    rgb_to_id = {}
    for idx, (r, g, b) in colors.items():
        lut[idx] = (r, g, b)
        rgb_to_id[(r, g, b)] = idx
    if 0 not in names:
        names[0] = "Clear Label"
    return lut, names, rgb_to_id


def _load_label_colors(label_file):
    """Backward-compatible wrapper returning only the dense RGB LUT."""
    colors = {}
    max_idx = 0
    with open(label_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            idx = int(parts[0])
            r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
            colors[idx] = (r, g, b)
            if idx > max_idx:
                max_idx = idx
    lut = np.zeros((max_idx + 1, 3), dtype=np.uint8)
    for idx, (r, g, b) in colors.items():
        lut[idx] = (r, g, b)
    return lut


# Region color table + names + reverse RGB map, loaded once from the atlas
# .label file (official RGB).
_COLOR_LUT, _LABEL_NAMES, _RGB_TO_ID = _load_label_table(LABEL_FILE)


def _label_name(label_id):
    """Return the human-readable name for a label id (falls back to the id)."""
    return _LABEL_NAMES.get(int(label_id), str(label_id))


def load_atlas_volumes(volumes_csv: str = VOLUMES_FILE) -> Dict[int, Dict[str, Any]]:
    """
    Parse the atlas volumes CSV into {region_id: {"name": str, "voxels": int,
    "volume_mm3": float}}.

    The CSV columns are:
        ID_Région, Nom_Région, Nombre_Voxels, Volume_mm3, Volume_cm3

    Names are stored surrounded by escaped double quotes (e.g. \"\"Name\"\").
    Returns an empty dict if the file is missing/unreadable.
    """
    result: Dict[int, Dict[str, Any]] = {}
    if not os.path.exists(volumes_csv):
        print(f"[volumes] Atlas volumes CSV not found: {volumes_csv}")
        return result
    try:
        with open(volumes_csv, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    region_id = int(row["ID_Région"])
                    voxels = int(row["Nombre_Voxels"])
                    volume_mm3 = float(row["Volume_mm3"])
                except (ValueError, KeyError):
                    continue
                name = (row.get("Nom_Région") or "").strip().strip('"').strip()
                result[region_id] = {
                    "name": name,
                    "voxels": voxels,
                    "volume_mm3": volume_mm3,
                }
    except Exception as exc:
        print(f"[volumes] Cannot read atlas volumes CSV {volumes_csv}: {exc}")
    return result


def compute_region_surface_areas_mm2(
    mask_png_path: str,
    pixel_size_um: float,
    downsample: int = 1,
) -> Dict[int, Dict[str, Any]]:
    """
    Compute the physical surface area (mm²) of each labeled region in a warped
    atlas region mask.

    The warped mask is an RGB PNG whose pixels are the atlas label colors
    (background label 0 is black). Each pixel covers a physical area of
    (pixel_size_um * downsample)² because the mask was produced from a JPEG
    downsampled by `downsample` from the original CZI resolution.

    Returns {label_id: {"name": str, "rgb": (r,g,b), "pixel_count": int,
    "surface_mm2": float}}. Label 0 (background) is excluded.
    Returns {} if the mask is missing/unreadable or pixel_size_um <= 0.
    """
    if not mask_png_path or not os.path.exists(mask_png_path):
        return {}
    if pixel_size_um is None or float(pixel_size_um) <= 0:
        return {}
    if int(downsample) < 1:
        downsample = 1

    try:
        mask_img = Image.open(mask_png_path).convert("RGB")
    except Exception:
        return {}
    arr = np.asarray(mask_img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return {}
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        return {}

    # Vectorized reverse mapping of every pixel to its label id.
    flat_rgb = arr.reshape(-1, 3).astype(np.int32)
    keys = list(_RGB_TO_ID.keys())
    if not keys:
        return {}
    lut_keys = np.asarray(keys, dtype=np.int32)  # (N, 3)
    lut_ids = np.asarray([_RGB_TO_ID[tuple(k)] for k in keys], dtype=np.int32)

    # Compute the index of the nearest LUT colour for each pixel. For exact
    # matches (the common case, PNG is lossless) this is O(1) per pixel via
    # broadcasting on the small LUT.
    diffs = flat_rgb[:, None, :] - lut_keys[None, :, :]
    dists = np.einsum("ijk,ijk->ij", diffs, diffs)
    nearest_idx = np.argmin(dists, axis=1)
    label_ids = lut_ids[nearest_idx]

    unique, counts = np.unique(label_ids, return_counts=True)

    effective_pixel_um = float(pixel_size_um) * float(downsample)
    pixel_area_mm2 = (effective_pixel_um * 1e-3) ** 2

    result: Dict[int, Dict[str, Any]] = {}
    for lid, cnt in zip(unique.tolist(), counts.tolist()):
        if lid == BACKGROUND_LABEL:
            continue
        rgb = tuple(int(c) for c in _COLOR_LUT[min(lid, len(_COLOR_LUT) - 1)])
        result[int(lid)] = {
            "name": _label_name(lid),
            "rgb": rgb,
            "pixel_count": int(cnt),
            "surface_mm2": float(cnt) * pixel_area_mm2,
        }
    return result


def compute_slice_area_mm2(
    mask_png_path: str,
    pixel_size_um: float,
    downsample: int = 1,
) -> float:
    """Total physical area (mm²) of the full coronal section.

    The warped atlas region mask spans the whole histology section; each of its
    (W x H) pixels covers (pixel_size_um * downsample) µm, so the total section
    area is W * H * (pixel_size_um * downsample * 1e-3)² mm².

    Returns 0.0 if the mask is missing/unreadable or pixel_size_um <= 0.
    """
    if not mask_png_path or not os.path.exists(mask_png_path):
        return 0.0
    if pixel_size_um is None or float(pixel_size_um) <= 0:
        return 0.0
    if int(downsample) < 1:
        downsample = 1
    try:
        mask_img = Image.open(mask_png_path).convert("RGB")
    except Exception:
        return 0.0
    arr = np.asarray(mask_img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return 0.0
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        return 0.0
    effective_pixel_um = float(pixel_size_um) * float(downsample)
    pixel_area_mm2 = (effective_pixel_um * 1e-3) ** 2
    return float(h * w) * pixel_area_mm2


def _rgb_to_label_id(rgb):
    """
    Map an (r,g,b) mask pixel back to its label id.

    Exact-tuple lookup first (PNG is lossless, colors come straight from the
    LUT so this is essentially always exact). Falls back to a nearest-color
    Euclidean search over the known label colors for robustness.
    """
    key = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    if key in _RGB_TO_ID:
        return _RGB_TO_ID[key]
    if not _RGB_TO_ID:
        return 0
    best_id = 0
    best_dist = None
    for (r, g, b), lid in _RGB_TO_ID.items():
        d = (r - key[0]) ** 2 + (g - key[1]) ** 2 + (b - key[2]) ** 2
        if best_dist is None or d < best_dist:
            best_dist = d
            best_id = lid
    return best_id


def count_cells_per_region(mask_png_path, cells, label_file=LABEL_FILE):
    """
    Count how many detected cells fall inside each labeled atlas region.

    `cells` is a list of dicts carrying at least `x_relative` and `y_relative`
    (both in [0, 1], measured on the same spatial extent as `mask_png_path`).
    The warped atlas mask PNG is sampled at each cell's relative position;
    the resulting RGB pixel is mapped back to a region id via the atlas
    `.label` color table.

    Returns a list of dicts (sorted by count descending, background label 0
    excluded) shaped as:
        {"label": int, "name": str, "count": int, "rgb": (r,g,b)}
    Returns [] if the mask is missing/unreadable or if `cells` is empty.
    """
    if not mask_png_path or not cells:
        return []
    try:
        mask_img = Image.open(mask_png_path).convert("RGB")
    except Exception:
        return []
    arr = np.asarray(mask_img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return []
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        return []

    counts = {}
    for cell in cells:
        try:
            nx = float(cell.get("x_relative", 0.0))
            ny = float(cell.get("y_relative", 0.0))
        except (TypeError, ValueError):
            continue
        # Clamp to [0,1] and convert to integer pixel coords.
        px = int(round(min(1.0, max(0.0, nx)) * (w - 1)))
        py = int(round(min(1.0, max(0.0, ny)) * (h - 1)))
        rgb = arr[py, px]
        lid = _rgb_to_label_id((int(rgb[0]), int(rgb[1]), int(rgb[2])))
        if lid == BACKGROUND_LABEL:
            continue
        counts[lid] = counts.get(lid, 0) + 1

    rows = []
    for lid, cnt in counts.items():
        rgb = tuple(int(c) for c in _COLOR_LUT[min(lid, len(_COLOR_LUT) - 1)])
        rows.append({"label": int(lid), "name": _label_name(lid), "count": int(cnt), "rgb": rgb})
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def _load_region_mask_array(region_mask_path):
    """
    Load the warped atlas region mask once as an (H, W, 3) uint8 array.

    Returns None if the path is missing/unreadable or the image is not RGB.
    """
    if not region_mask_path or not os.path.exists(region_mask_path):
        return None
    try:
        mask_img = Image.open(region_mask_path).convert("RGB")
    except Exception:
        return None
    arr = np.asarray(mask_img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        return None
    return arr


def _cell_in_region_array(cell, region_arr):
    """
    Test a single cell against an already-loaded region mask array.

    A pixel is "inside a region" when its RGB maps to a non-zero label id via
    the atlas `.label` color table.
    """
    if region_arr is None:
        return True
    h, w = region_arr.shape[:2]
    try:
        nx = float(cell.get("x_relative", 0.0))
        ny = float(cell.get("y_relative", 0.0))
    except (TypeError, ValueError):
        return False
    px = int(round(min(1.0, max(0.0, nx)) * (w - 1)))
    py = int(round(min(1.0, max(0.0, ny)) * (h - 1)))
    rgb = region_arr[py, px]
    lid = _rgb_to_label_id((int(rgb[0]), int(rgb[1]), int(rgb[2])))
    return lid != BACKGROUND_LABEL


def is_cell_in_region(cell, region_mask_path):
    """
    Return True if a cell's centroid lands on a non-background pixel of the
    warped atlas region mask.

    `cell` is a dict carrying at least `x_relative` and `y_relative` (in [0,1]).
    `region_mask_path` is the RGB PNG produced by save_mask_pair() (colored
    warped labels, background in black).

    NOTE: this opens the mask file each call. For bulk filtering of many cells,
    prefer `filter_cells_by_region`, which loads the mask once.
    """
    region_arr = _load_region_mask_array(region_mask_path)
    if region_arr is None:
        # Missing/unreadable mask -> treat as inside (non-destructive fallback).
        return True
    return _cell_in_region_array(cell, region_arr)


def filter_cells_by_region(cells, region_mask_path):
    """
    Keep only the cells whose centroid falls inside a labeled region of the
    warped atlas mask. Cells outside the mask (background label 0) are dropped.

    The region mask is loaded **once** (not per cell) so this stays fast even
    for thousands of detected cells — important because it runs on the Tk main
    thread during Window 4 rendering.

    Falls back to the full list when the region mask is missing/unreadable so
    callers stay non-destructive on partial inputs.
    """
    if not region_mask_path or not cells:
        return list(cells)
    region_arr = _load_region_mask_array(region_mask_path)
    if region_arr is None:
        return list(cells)
    return [c for c in cells if _cell_in_region_array(c, region_arr)]


def combine_and_filter_cell_mask(
    cell_mask_path,
    region_mask_path,
    output_path,
    target_size=None,
):
    """
    Build the final standalone cell mask: the detected-cell mask (white cells on
    black background, already merged dark+light by the quantification wrapper)
    restricted to the labeled atlas regions.

    Cell pixels that fall outside any labeled region (atlas background) are
    deleted (set to 0/black), so only in-region cells remain in the output.

    Args:
        cell_mask_path   : path to the merged detected-cell mask (e.g. *_cell_mask.tif).
        region_mask_path : path to the warped colored atlas region mask (mask_png).
        output_path      : where to save the combined+filtered cell mask (PNG/TIF).
        target_size      : optional (width, height) to resize both masks before ANDing.
                           If None, the cell-mask size is used.

    Returns output_path on success, or None if the cell mask is missing/invalid.
    """
    if not cell_mask_path or not os.path.exists(cell_mask_path):
        return None

    try:
        cell_img = Image.open(cell_mask_path).convert("L")
    except Exception:
        return None

    if target_size is not None:
        cell_img = cell_img.resize(target_size, Image.Resampling.NEAREST)
    cell_arr = np.asarray(cell_img)
    # Cells are bright (white, 255); background is dark (0).
    cell_bin = (cell_arr > 20).astype(np.uint8)

    if region_mask_path and os.path.exists(region_mask_path):
        try:
            region_img = Image.open(region_mask_path).convert("RGB")
            region_img = region_img.resize(cell_img.size, Image.Resampling.NEAREST)
            region_arr = np.asarray(region_img)
            # Region mask: any non-black pixel is a labeled region.
            region_bin = (np.any(region_arr[:, :, :3] > 8, axis=2)).astype(np.uint8)
            # Keep cell pixels only where a region exists.
            cell_bin = cell_bin * region_bin
        except Exception:
            # If the region mask cannot be read, keep the unfiltered cell mask.
            pass

    out_arr = (cell_bin * 255).astype(np.uint8)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    Image.fromarray(out_arr, mode="L").save(output_path)
    return output_path


def _labels_to_rgb(label_array):
    """Map integer region labels to RGB using the atlas .label color table."""
    safe = np.clip(label_array, 0, len(_COLOR_LUT) - 1)
    return _COLOR_LUT[safe]


def _build_backward_transform(src_pts, dst_pts):
    """
    Build the backward map (target -> source) used by skimage.warp.

    `warp(src_image, map, output_shape=target_shape)` samples, for each output
    pixel at target coords, the input pixel at `map(target_coords)`. Therefore
    `map` must go target->source, i.e. estimate(dst, src).

    - 2 points  -> SimilarityTransform (uniform scale + rotation + translation)
    - >=3 points -> Thin Plate Spline (smooth, exact at landmarks)
    """
    src = np.asarray(src_pts, dtype=np.float64)
    dst = np.asarray(dst_pts, dtype=np.float64)
    n = min(len(src), len(dst))

    if n < 2:
        raise ValueError("At least 2 landmark pairs are required.")

    if n == 2:
        # from_estimate is the non-deprecated API (estimate() is deprecated
        # since scikit-image 0.26).
        return SimilarityTransform.from_estimate(dst[:n], src[:n])

    return ThinPlateSplineTransform.from_estimate(dst[:n], src[:n])


def _compute_warped(depth, normalized_points, histo_path=None):
    """
    Core warp computation shared by replace_mask and save_mask_pair.

    Returns:
        blended     : (H, W, 3) uint8 — histology with the colored mask overlaid.
        warped_rgb  : (H, W, 3) uint8 — colored warped labels (atlas RGB), incl. background.
        warped_labels: (H, W) int32 — warped integer region labels.

    `histo_path` selects the histology image used as the target. It defaults to
    "Histo.png" for backward compatibility, but the UI passes the currently
    displayed ROI jpeg so the overlay matches what the user sees.
    """
    if len(normalized_points) < 2:
        raise ValueError("Select at least 2 points")

    # --- 1. Full-resolution atlas mask (native resolution, oriented like UI) ---
    labels = get_atlas_slice_fullres(depth)  # (Hm, Wm) int label array
    mask_h, mask_w = labels.shape

    # --- 2. Histology image (target resolution) ---
    if histo_path is None:
        histo_path = _resolve_path("Histo.png")
    elif not os.path.isabs(histo_path):
        histo_path = _resolve_path(histo_path)
    histo_img = Image.open(histo_path).convert("RGB")
    histo_arr = np.asarray(histo_img)
    histo_h, histo_w = histo_img.size[1], histo_img.size[0]

    # --- 3. Convert normalized landmarks to pixel coordinates in each space ---
    # tl (source) points live in the mask image; tr (target) points in the histology.
    src_pts = [(nx * mask_w, ny * mask_h) for (nx, ny), _ in normalized_points]
    dst_pts = [(nx * histo_w, ny * histo_h) for _, (nx, ny) in normalized_points]

    # --- 4. Warp the label array (nearest-neighbor to keep region IDs intact) ---
    tform = _build_backward_transform(src_pts, dst_pts)
    warped_labels = warp(
        labels.astype(np.float64),
        tform,
        output_shape=(histo_h, histo_w),
        order=0,                 # nearest-neighbor: preserve label values
        mode="constant",
        cval=BACKGROUND_LABEL,
        preserve_range=True,
    )
    warped_labels = np.rint(warped_labels).astype(np.int32)

    # --- 5. Color the warped mask and build an alpha mask (non-background only) ---
    warped_rgb = _labels_to_rgb(warped_labels)
    region_mask = (warped_labels != BACKGROUND_LABEL).astype(np.float64) * ALPHA

    # --- 6. Blend over the histology ---
    blended = histo_arr.astype(np.float64) * (1.0 - region_mask[..., None]) \
        + warped_rgb.astype(np.float64) * region_mask[..., None]
    blended = np.clip(blended, 0, 255).astype(np.uint8)

    return blended, warped_rgb, warped_labels


def replace_mask(depth, normalized_points, output_path=OUTPUT_PATH, histo_path=None):
    """
    Warp the full-resolution atlas mask onto the histology image using the
    landmark pairs and return the path of the resulting overlay image.
    """
    blended, _, _ = _compute_warped(depth, normalized_points, histo_path=histo_path)
    Image.fromarray(blended, mode="RGB").save(output_path)
    return output_path


def save_mask_pair(depth, normalized_points, overlay_path, mask_only_path, histo_path=None):
    """
    Produce and save BOTH mask variants for a validated ROI:

      - overlay_path    : histology + colored mask (the visualization).
      - mask_only_path  : colored warped labels alone, background (label 0) in
                          black — the actual mask usable for downstream quantification.

    Returns (overlay_path, mask_only_path).
    """
    blended, warped_rgb, warped_labels = _compute_warped(
        depth, normalized_points, histo_path=histo_path
    )
    Image.fromarray(blended, mode="RGB").save(overlay_path)

    mask_only = warped_rgb.copy()
    mask_only[warped_labels == BACKGROUND_LABEL] = 0  # black background
    Image.fromarray(mask_only, mode="RGB").save(mask_only_path)

    return overlay_path, mask_only_path


if __name__ == "__main__":
    # Quick manual test: 2-point alignment.
    import sys
    test_depth = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    test_points = [
        ((0.25, 0.25), (0.30, 0.20)),
        ((0.75, 0.75), (0.70, 0.80)),
    ]
    print(replace_mask(test_depth, test_points))