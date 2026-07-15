"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
# The goal of this module is to provide two images from a position in an MRI/ATLAS file.
# Input: depth of the slice, the MRI scan file path, and the atlas file path.
#
# Output: two images, one for the MRI coronal view and one for the corresponding atlas/mask view.

import os
from typing import Any, cast

import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from PIL import Image

# r".\Rat atlas\WHS_SD_rat_atlas_v4.nii"
# r".\Rat atlas\WHS_SD_rat_T2star_v1.01.nii"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IRM_PATH = os.path.join(BASE_DIR, "Rat atlas", "WHS_SD_rat_T2star_v1.01.nii")
ATLAS_PATH = os.path.join(BASE_DIR, "Rat atlas", "WHS_SD_rat_atlas_v4.nii")
ATLAS_IMGS_DIR = os.path.join(BASE_DIR, "AtlasImgs")


def get_depth_range(image_path=IRM_PATH, atlas_path=ATLAS_PATH):
    """
    Return the valid inclusive depth range for coronal slices as (min_depth, max_depth).

    The slider in the UI should use this range so it cannot request slices outside
    the NIfTI volume.
    """
    img = cast(Any, nib.load(image_path))
    atlas_img = cast(Any, nib.load(atlas_path))

    if img.shape != atlas_img.shape:
        raise ValueError(f"MRI and atlas shapes differ: {img.shape} vs {atlas_img.shape}")

    return 0, img.shape[1] - 1


def get_coronal_slice(image_path, atlas_path, depth):
    """
    Return the matching MRI and atlas slices for a given coronal depth.
    """
    depth = int(depth)

    # Load the MRI scan. nib.load(...) returns a Nifti1Image object.
    # The NumPy data array is obtained with get_fdata().
    img = cast(Any, nib.load(image_path))
    data = img.get_fdata()

    # Load the atlas as integer labels. The atlas voxel values are region IDs.
    atlas_img = cast(Any, nib.load(atlas_path))
    labels = atlas_img.get_fdata().astype(int)

    if data.shape != labels.shape:
        raise ValueError(f"MRI and atlas shapes differ: {data.shape} vs {labels.shape}")

    if depth < 0 or depth >= data.shape[1]:
        raise ValueError(f"Depth {depth} is outside valid range [0, {data.shape[1] - 1}]")

    # Get matching coronal-like slices from MRI and atlas.
    # Here, "depth" fixes axis 1 for both volumes.
    coronal_slice = data[:, depth, :]
    atlas_slice = labels[:, depth, :]

    return coronal_slice, atlas_slice


def get_atlas_slice_fullres(depth, atlas_path=ATLAS_PATH):
    """
    Return the atlas/mask coronal slice at `depth` at full (native) resolution,
    oriented exactly like the displayed atlas image (transpose + 180° rotation),
    as a 2D integer NumPy array of region labels.

    Unlike the cached PNG produced by save_slices_as_images (saved at the array's
    current resolution via plt.imsave), this returns the raw native-resolution
    label array so the mask can be warped and overlaid at full detail on the
    histology image. The orientation matches save_slices_as_images so that
    normalized coordinates from the UI line up correctly.
    """
    depth = int(depth)
    atlas_img = cast(Any, nib.load(atlas_path))
    labels = atlas_img.get_fdata().astype(int)

    if depth < 0 or depth >= labels.shape[1]:
        raise ValueError(f"Depth {depth} is outside valid range [0, {labels.shape[1] - 1}]")

    atlas_slice = labels[:, depth, :]
    # Match save_slices_as_images orientation: plt.imsave(..., slice.T) then rotate 180°.
    oriented = np.asarray(atlas_slice.T)
    oriented = np.flipud(np.fliplr(oriented))  # equivalent to a 180° rotation
    return oriented


def get_slice_image_paths(depth, output_dir=ATLAS_IMGS_DIR):
    """
    Return the expected image file paths for a given depth without creating them.
    """
    depth = int(depth)
    coronal_filename = os.path.join(output_dir, f"coronal_slice_depth_{depth}.png")
    atlas_filename = os.path.join(output_dir, f"atlas_slice_depth_{depth}.png")
    return coronal_filename, atlas_filename


def save_slices_as_images(coronal_slice, atlas_slice, depth, output_dir=ATLAS_IMGS_DIR):
    """
    Save matching MRI and atlas slices as PNG images and return their file paths.
    """
    depth = int(depth)

    # Check if the output directory exists, if not create it
    os.makedirs(output_dir, exist_ok=True)

    coronal_filename, atlas_filename = get_slice_image_paths(depth, output_dir)

    plt.imsave(coronal_filename, coronal_slice.T, cmap="gray")
    plt.imsave(atlas_filename, atlas_slice.T, cmap="tab20")

    # Fix images orientation by rotating them 180 degrees clockwise
    with Image.open(coronal_filename) as coronal_img:
        coronal_img.rotate(180, expand=True).save(coronal_filename)

    with Image.open(atlas_filename) as atlas_img:
        atlas_img.rotate(180, expand=True).save(atlas_filename)

    return coronal_filename, atlas_filename


def get_or_create_slice_images(depth, image_path=IRM_PATH, atlas_path=ATLAS_PATH, output_dir=ATLAS_IMGS_DIR):
    """
    Return the image paths for depth.

    If both images already exist in ./AtlasImgs, they are reused directly.
    Otherwise, the slice is generated once and saved before returning the paths.
    """
    depth = int(depth)
    coronal_filename, atlas_filename = get_slice_image_paths(depth, output_dir)

    if os.path.exists(coronal_filename) and os.path.exists(atlas_filename):
        return coronal_filename, atlas_filename

    coronal_slice, atlas_slice = get_coronal_slice(image_path, atlas_path, depth)
    return save_slices_as_images(coronal_slice, atlas_slice, depth, output_dir)


if __name__ == "__main__":
    depth = 500
    coronal_filename, atlas_filename = get_or_create_slice_images(depth)

    print("Images available as")
    print(coronal_filename)
    print(atlas_filename)