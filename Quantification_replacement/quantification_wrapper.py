#!/usr/bin/env python3
"""
Reusable wrapper around the QuPath cell-quantification workflow.

This module deliberately keeps the detection parameters identical to the
standalone project in ProjetQuantification.temp/app_constants.py, while exposing
clean progress events and structured final results for UI/CLI integrations.

Two-pass detection (always on):
  - Pass 1 (dark bg): Hematoxylin OD — sensitive on dark backgrounds.
  - Pass 2 (light bg): Optical density sum — sensitive on light backgrounds.
  - Merge: relative-position deduplication. When a light-bg cell overlaps a
    dark-bg cell (within MERGE_THRESHOLD_PX pixels), the dark-bg cell is trusted
    and the light-bg duplicate is dropped.

Primary output:
  - one standalone detected-cell mask per image (combined dark+light)
  - one per-image CSV with every detected cell's relative coordinates + pass origin
  - one combined CSV containing all image counts and cell coordinates
"""

from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Callable, Iterable


BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_QUPATH_EXE = BASE_DIR / "ProjetQuantification.temp" / "QuPath" / "QuPath-0.7.0 (console).exe"

# Source of truth copied unchanged from ProjetQuantification.temp/app_constants.py.
# These values are passed to QuPath WatershedCellDetection without changing the
# quantification logic/parameters.

# ---------------------------------------------------------------------------
# Pass 1 — dark background (Hematoxylin OD)
# ---------------------------------------------------------------------------
DEFAULT_DETECTION_PARAMS = {
    # --- Image / canal ---
    "image_type": "BRIGHTFIELD_H_DAB",
    "detection_channel": "Hematoxylin OD",
    # --- Setup ---
    "pixel_size": 3,
    "background_radius": 1,
    "background_by_reconstruction": True,
    "median_radius": 0.5,
    "sigma": 5.0,
    # --- Noyau ---
    "min_area": 60.0,
    "max_area": 300.0,
    # --- Intensité ---
    "threshold": 0.085,
    "max_background": 8.0,
    # --- Watershed ---
    "split_by_shape": True,
    "exclude_dab": False,
    # --- Cellule ---
    "cell_expansion": 3.0,
    "include_nuclei": False,
    "smooth_boundaries": True,
    "make_measurements": True,
    # --- Normalisation ---
    "norm_method": "local",
    "norm_p_low": 1.0,
    "norm_p_high": 99.0,
    "norm_per_channel": True,
    "norm_sigma_mean": 4.0,
    "norm_sigma_var": 1.0,
    "norm_local_radius": 5,
    "norm_local_sigma": 0.4,
}

# ---------------------------------------------------------------------------
# Pass 2 — light background (Optical density sum)
# ---------------------------------------------------------------------------
DEFAULT_DETECTION_PARAMS_LIGHT = {
    # --- Image / canal ---
    # image_type and norm_* are shared with pass 1 (same QuPath session).
    "detection_channel": "Optical density sum",
    # --- Setup ---
    "pixel_size": 0.6,
    "background_radius": 6,
    "background_by_reconstruction": True,
    "median_radius": 3,
    "sigma": 3.5,
    # --- Noyau ---
    "min_area": 70.0,
    "max_area": 150.0,
    # --- Intensité ---
    "threshold": 0.5,
    "max_background": 1.0,
    # --- Watershed ---
    "split_by_shape": False,
    "exclude_dab": False,
    # --- Cellule ---
    "cell_expansion": 3.0,
    "include_nuclei": False,
    "smooth_boundaries": True,
    "make_measurements": True,
}

# Centroid overlap threshold (image pixels).  When a pass-2 (light) cell is
# within this distance of a pass-1 (dark) cell, the dark cell wins and the light
# duplicate is dropped.
MERGE_THRESHOLD_PX = 10.0

NORM_METHODS = {
    "none": {
        "label": "None (disabled)",
        "groovy": "// Normalisation : aucune",
    },
    "minmax": {
        "label": "Min-Max [0–1]",
        "groovy": """\
// Normalisation Min-Max
def normOp = ImageOps.Normalize.minMax()
print("  Normalisation MinMax appliquee")""",
    },
    "percentile": {
        "label": "Percentile",
        "groovy": """\
// Normalisation Percentile
double normPLow  = __NORM_P_LOW__
double normPHigh = __NORM_P_HIGH__
def normOp = ImageOps.Normalize.percentile(normPLow, normPHigh)
print("  Normalisation Percentile appliquee: " + normPLow + " - " + normPHigh)""",
    },
    "zeromean": {
        "label": "Z-score (μ=0, σ=1)",
        "groovy": """\
// Normalisation Z-score
boolean normPerChannel = __NORM_PER_CHANNEL__
def normOp = ImageOps.Normalize.zeroMeanUnitVariance(normPerChannel)
print("  Normalisation ZeroMean appliquee, perChannel=" + normPerChannel)""",
    },
    "local": {
        "label": "Locale Gaussienne",
        "groovy": """\
// Normalisation locale
double normSigmaMean = __NORM_SIGMA_MEAN__
double normSigmaVar  = __NORM_SIGMA_VAR__
def normOp = ImageOps.Normalize.localNormalization(normSigmaMean, normSigmaVar)
print("  Normalisation Locale appliquee: sigmaMean=" + normSigmaMean + " sigmaVar=" + normSigmaVar)""",
    },
    "localminmax": {
        "label": "Locale Min-Max",
        "groovy": """\
// Normalisation locale MinMax
int    normLocalRadius = __NORM_LOCAL_RADIUS__
double normLocalSigma  = __NORM_LOCAL_SIGMA__
def normOp = ImageOps.Normalize.localNormalizationMinMax(normLocalRadius, normLocalSigma)
print("  Normalisation Locale MinMax appliquee: radius=" + normLocalRadius + " sigma=" + normLocalSigma)""",
    },
}


ProgressCallback = Callable[[dict], None]


@dataclass
class CellCoordinate:
    """Cellcoordinate.
    
    Attributs et methodes definis ci-dessous.
    """
    cell_id: int
    x_relative: float
    y_relative: float
    x_pixel: float
    y_pixel: float
    pass_origin: str = "dark"  # "dark" (pass 1) or "light" (pass 2)


@dataclass
class ImageQuantificationResult:
    """Imagequantificationresult.
    
    Attributs et methodes definis ci-dessous.
    """
    image: str
    source_path: str
    num_cells: int
    status: str
    mask_path: str = ""
    mask_dark_path: str = ""
    mask_light_path: str = ""
    cells_csv_path: str = ""
    result_json_path: str = ""
    error: str = ""
    num_cells_dark: int = 0
    num_cells_light: int = 0
    cells: list[CellCoordinate] = field(default_factory=list)


@dataclass
class QuantificationResult:
    """Quantificationresult.
    
    Attributs et methodes definis ci-dessous.
    """
    status: str
    output_dir: str
    summary_csv_path: str
    total_images: int
    successful_images: int
    total_cells: int
    results: list[ImageQuantificationResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Two-pass Groovy template
# ---------------------------------------------------------------------------
# Runs WatershedCellDetection twice in one QuPath session (normalisation once):
#   pass 1 = dark bg (Hematoxylin OD),  pass 2 = light bg (Optical density sum)
# then merges by centroid distance: dark wins, light kept only if not within
# MERGE_THRESHOLD_PX of any dark centroid.
GROOVY_TEMPLATE = Template(r"""
import qupath.lib.images.servers.ImageServerProvider
import qupath.lib.images.ImageData
import qupath.lib.objects.PathObjects
import qupath.lib.roi.ROIs
import qupath.lib.regions.ImagePlane
import qupath.opencv.ops.ImageOps
import qupath.lib.images.servers.LabeledImageServer
import qupath.lib.common.ColorTools
import qupath.lib.objects.classes.PathClass
import com.google.gson.GsonBuilder
import java.awt.image.BufferedImage
import static qupath.lib.gui.scripting.QPEx.*

def imagePath = $IMAGE_PATH
def imageFile = new File(imagePath)
def uri       = imageFile.toURI()
def support   = ImageServerProvider.getPreferredUriImageSupport(BufferedImage.class, uri.toString())

if (support == null || support.builders.isEmpty()) {
    print("ERROR: Cannot open image: " + imagePath)
    return
}

def server    = support.builders.get(0).build()
def imageData = new ImageData(server)

setBatchProjectAndImage(null, imageData)
setImageType('$IMAGE_TYPE')
print("TRIGGER:IMAGE_OPENED")

int w     = server.getWidth()
int h     = server.getHeight()
def plane = ImagePlane.getDefaultPlane()

// Normalisation (shared by both passes): $NORM_METHOD_LABEL
$NORM_GROOVY_BLOCK
print("TRIGGER:NORMALIZED")

// ============================================================
// PASS 1 — DARK BACKGROUND  ($DARK_DETECTION_CHANNEL)
// ============================================================
removeAllObjects()
def roi1 = ROIs.createRectangleROI(0, 0, w, h, plane)
def ann1 = PathObjects.createAnnotationObject(roi1)
addObject(ann1)
fireHierarchyUpdate()
selectObjects(ann1)
print("TRIGGER:DARK_ROI_CREATED")

runPlugin(
    'qupath.imagej.detect.cells.WatershedCellDetection',
    '{"detectionImageBrightfield": "$DARK_DETECTION_CHANNEL",' +
    ' "requestedPixelSizeMicrons": $DARK_PIXEL_SIZE,' +
    ' "backgroundRadiusMicrons": $DARK_BACKGROUND_RADIUS,' +
    ' "backgroundByReconstruction": $DARK_BACKGROUND_BY_RECONSTRUCTION,' +
    ' "medianRadiusMicrons": $DARK_MEDIAN_RADIUS,' +
    ' "sigmaMicrons": $DARK_SIGMA,' +
    ' "minAreaMicrons": $DARK_MIN_AREA,' +
    ' "maxAreaMicrons": $DARK_MAX_AREA,' +
    ' "threshold": $DARK_THRESHOLD,' +
    ' "maxBackground": $DARK_MAX_BACKGROUND,' +
    ' "watershedPostProcess": $DARK_SPLIT_BY_SHAPE,' +
    ' "excludeDAB": $DARK_EXCLUDE_DAB,' +
    ' "cellExpansionMicrons": $DARK_CELL_EXPANSION,' +
    ' "includeNuclei": $DARK_INCLUDE_NUCLEI,' +
    ' "smoothBoundaries": $DARK_SMOOTH_BOUNDARIES,' +
    ' "makeMeasurements": $DARK_MAKE_MEASUREMENTS}'
)
print("TRIGGER:DARK_DETECTION_DONE")

def darkRois = getDetectionObjects().collect { it.getROI() }
def darkCentroids = darkRois.collect { roi -> [roi.getCentroidX(), roi.getCentroidY()] }
print("TRIGGER:DARK_COORDINATES_EXTRACTED")

// ============================================================
// Write dark-pass cell mask (mask1)
// ============================================================
def darkMaskPath = $OUTPUT_MASK_DARK
def darkLabelServer = new LabeledImageServer.Builder(imageData)
    .backgroundLabel(0, ColorTools.BLACK)
    .addLabel("Detected cell", 255, ColorTools.WHITE)
    .useDetections()
    .multichannelOutput(false)
    .build()
writeImage(darkLabelServer, darkMaskPath)
print("TRIGGER:DARK_MASK_WRITTEN")

// ============================================================
// PASS 2 — LIGHT BACKGROUND  ($LIGHT_DETECTION_CHANNEL)
// ============================================================
removeAllObjects()
def roi2 = ROIs.createRectangleROI(0, 0, w, h, plane)
def ann2 = PathObjects.createAnnotationObject(roi2)
addObject(ann2)
fireHierarchyUpdate()
selectObjects(ann2)
print("TRIGGER:LIGHT_ROI_CREATED")

runPlugin(
    'qupath.imagej.detect.cells.WatershedCellDetection',
    '{"detectionImageBrightfield": "$LIGHT_DETECTION_CHANNEL",' +
    ' "requestedPixelSizeMicrons": $LIGHT_PIXEL_SIZE,' +
    ' "backgroundRadiusMicrons": $LIGHT_BACKGROUND_RADIUS,' +
    ' "backgroundByReconstruction": $LIGHT_BACKGROUND_BY_RECONSTRUCTION,' +
    ' "medianRadiusMicrons": $LIGHT_MEDIAN_RADIUS,' +
    ' "sigmaMicrons": $LIGHT_SIGMA,' +
    ' "minAreaMicrons": $LIGHT_MIN_AREA,' +
    ' "maxAreaMicrons": $LIGHT_MAX_AREA,' +
    ' "threshold": $LIGHT_THRESHOLD,' +
    ' "maxBackground": $LIGHT_MAX_BACKGROUND,' +
    ' "watershedPostProcess": $LIGHT_SPLIT_BY_SHAPE,' +
    ' "excludeDAB": $LIGHT_EXCLUDE_DAB,' +
    ' "cellExpansionMicrons": $LIGHT_CELL_EXPANSION,' +
    ' "includeNuclei": $LIGHT_INCLUDE_NUCLEI,' +
    ' "smoothBoundaries": $LIGHT_SMOOTH_BOUNDARIES,' +
    ' "makeMeasurements": $LIGHT_MAKE_MEASUREMENTS}'
)
print("TRIGGER:LIGHT_DETECTION_DONE")

def lightRois = getDetectionObjects().collect { it.getROI() }
print("TRIGGER:LIGHT_COORDINATES_EXTRACTED")

// ============================================================
// MERGE — dark wins; light kept only if not near any dark centroid
// ============================================================
// ============================================================
// Write light-pass cell mask (mask2)
// ============================================================
def lightMaskPath = $OUTPUT_MASK_LIGHT
def lightLabelServer = new LabeledImageServer.Builder(imageData)
    .backgroundLabel(0, ColorTools.BLACK)
    .addLabel("Detected cell", 255, ColorTools.WHITE)
    .useDetections()
    .multichannelOutput(false)
    .build()
writeImage(lightLabelServer, lightMaskPath)
print("TRIGGER:LIGHT_MASK_WRITTEN")

double mergeThreshold = $MERGE_THRESHOLD

def mergedRois     = new ArrayList()
def mergedPasses   = new ArrayList()
int darkKept       = darkRois.size()
int lightKept      = 0

// All dark cells are kept
for (int i = 0; i < darkRois.size(); i++) {
    mergedRois.add(darkRois[i])
    mergedPasses.add("dark")
}

// Light cells: keep only those NOT within threshold of any dark centroid
for (int i = 0; i < lightRois.size(); i++) {
    def roi = lightRois[i]
    double cx = roi.getCentroidX()
    double cy = roi.getCentroidY()
    boolean isDup = false
    for (int j = 0; j < darkCentroids.size(); j++) {
        double dx = cx - darkCentroids[j][0]
        double dy = cy - darkCentroids[j][1]
        if (Math.sqrt(dx * dx + dy * dy) <= mergeThreshold) {
            isDup = true
            break
        }
    }
    if (!isDup) {
        mergedRois.add(roi)
        mergedPasses.add("light")
        lightKept++
    }
}
print("TRIGGER:MERGE_DONE")

// ============================================================
// Re-create detection objects from the merged ROI list
// ============================================================
removeAllObjects()
def cellClass = PathClass.fromString("Detected cell")
for (int i = 0; i < mergedRois.size(); i++) {
    def det = PathObjects.createDetectionObject(mergedRois[i], cellClass)
    addObject(det)
}
fireHierarchyUpdate()
int numCells = mergedRois.size()
print("TRIGGER:DETECTION_MERGED")

// ============================================================
// Extract cell coordinates (relative + pixel)
// ============================================================
def cells = new ArrayList()
for (int i = 0; i < mergedRois.size(); i++) {
    def roi = mergedRois[i]
    double cx = roi.getCentroidX()
    double cy = roi.getCentroidY()
    cells.add([
        cell_id    : i + 1,
        x_relative : cx / (double) w,
        y_relative : cy / (double) h,
        x_pixel    : cx,
        y_pixel    : cy,
        pass       : mergedPasses[i]
    ])
}
print("TRIGGER:COORDINATES_EXTRACTED")

// ============================================================
// Write detected-cell mask (merged dark + light)
// ============================================================
def maskPath = $OUTPUT_MASK
def labelServer = new LabeledImageServer.Builder(imageData)
    .backgroundLabel(0, ColorTools.BLACK)
    .addLabel("Detected cell", 255, ColorTools.WHITE)
    .useDetections()
    .multichannelOutput(false)
    .build()

writeImage(labelServer, maskPath)
print("TRIGGER:MASK_WRITTEN")

// ============================================================
// Write result JSON
// ============================================================
def resultMap = [
    image           : imageFile.getName(),
    source_path     : imagePath,
    width           : w,
    height          : h,
    num_cells       : numCells,
    num_cells_dark  : darkKept,
    num_cells_light : lightKept,
    mask_path       : maskPath,
    cells           : cells,
    status          : "success",
    timestamp       : new Date().toString()
]

def gson = new GsonBuilder().create()
new File($OUTPUT_JSON).text = gson.toJson(resultMap)
print("QUPATH_RESULT:" + numCells + " cells in " + imageFile.getName() + " (dark: " + darkKept + ", light: " + lightKept + ")")
""")


def _emit(progress_cb: ProgressCallback | None, event: dict) -> None:
    """Send one structured progress event to the caller."""
    if progress_cb is not None:
        progress_cb(event)


def _json_string(path_or_text: str | Path) -> str:
    """Return a Groovy-compatible string literal."""
    return json.dumps(str(path_or_text).replace("\\", "/"), ensure_ascii=False)


def _safe_stem(image_path: Path) -> str:
    """Safe Stem (usage interne).
    
    Args:
        image_path (Path): Chemin vers le fichier.
    
    Returns:
        str: Resultat.
    """
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in image_path.name)


def _build_norm_block(params: dict) -> tuple[str, str]:
    """Build Norm Block (usage interne).
    
    Args:
        params (dict): Parametre params.
    
    Returns:
        tuple[str, str]: Resultat.
    """
    method = params.get("norm_method", DEFAULT_DETECTION_PARAMS["norm_method"])
    info = NORM_METHODS.get(method, NORM_METHODS["none"])
    label = info["label"]
    groovy = info["groovy"]

    groovy = groovy.replace("__NORM_P_LOW__", str(params.get("norm_p_low", 1.0)))
    groovy = groovy.replace("__NORM_P_HIGH__", str(params.get("norm_p_high", 99.0)))
    groovy = groovy.replace("__NORM_PER_CHANNEL__", str(params.get("norm_per_channel", True)).lower())
    groovy = groovy.replace("__NORM_SIGMA_MEAN__", str(params.get("norm_sigma_mean", 8.0)))
    groovy = groovy.replace("__NORM_SIGMA_VAR__", str(params.get("norm_sigma_var", 4.0)))
    groovy = groovy.replace("__NORM_LOCAL_RADIUS__", str(params.get("norm_local_radius", 20)))
    groovy = groovy.replace("__NORM_LOCAL_SIGMA__", str(params.get("norm_local_sigma", 1.0)))

    return label, groovy


def _build_groovy_script(
    image_path: Path,
    dark_params: dict,
    light_params: dict,
    merge_threshold_px: float,
    out_json: Path,
    out_mask: Path,
    out_mask_dark: Path,
    out_mask_light: Path,
) -> str:
    """Build the two-pass Groovy script for a single image."""
    norm_label, norm_groovy = _build_norm_block(dark_params)

    return GROOVY_TEMPLATE.substitute(
        IMAGE_PATH=_json_string(image_path.resolve()),
        IMAGE_TYPE=dark_params["image_type"],
        # --- Dark pass (pass 1) ---
        DARK_DETECTION_CHANNEL=dark_params["detection_channel"],
        DARK_PIXEL_SIZE=dark_params["pixel_size"],
        DARK_BACKGROUND_RADIUS=dark_params["background_radius"],
        DARK_BACKGROUND_BY_RECONSTRUCTION=str(dark_params["background_by_reconstruction"]).lower(),
        DARK_MEDIAN_RADIUS=dark_params["median_radius"],
        DARK_SIGMA=dark_params["sigma"],
        DARK_MIN_AREA=dark_params["min_area"],
        DARK_MAX_AREA=dark_params["max_area"],
        DARK_THRESHOLD=dark_params["threshold"],
        DARK_MAX_BACKGROUND=dark_params["max_background"],
        DARK_SPLIT_BY_SHAPE=str(dark_params["split_by_shape"]).lower(),
        DARK_EXCLUDE_DAB=str(dark_params["exclude_dab"]).lower(),
        DARK_CELL_EXPANSION=dark_params["cell_expansion"],
        DARK_INCLUDE_NUCLEI=str(dark_params["include_nuclei"]).lower(),
        DARK_SMOOTH_BOUNDARIES=str(dark_params["smooth_boundaries"]).lower(),
        DARK_MAKE_MEASUREMENTS=str(dark_params["make_measurements"]).lower(),
        # --- Light pass (pass 2) ---
        LIGHT_DETECTION_CHANNEL=light_params["detection_channel"],
        LIGHT_PIXEL_SIZE=light_params["pixel_size"],
        LIGHT_BACKGROUND_RADIUS=light_params["background_radius"],
        LIGHT_BACKGROUND_BY_RECONSTRUCTION=str(light_params["background_by_reconstruction"]).lower(),
        LIGHT_MEDIAN_RADIUS=light_params["median_radius"],
        LIGHT_SIGMA=light_params["sigma"],
        LIGHT_MIN_AREA=light_params["min_area"],
        LIGHT_MAX_AREA=light_params["max_area"],
        LIGHT_THRESHOLD=light_params["threshold"],
        LIGHT_MAX_BACKGROUND=light_params["max_background"],
        LIGHT_SPLIT_BY_SHAPE=str(light_params["split_by_shape"]).lower(),
        LIGHT_EXCLUDE_DAB=str(light_params["exclude_dab"]).lower(),
        LIGHT_CELL_EXPANSION=light_params["cell_expansion"],
        LIGHT_INCLUDE_NUCLEI=str(light_params["include_nuclei"]).lower(),
        LIGHT_SMOOTH_BOUNDARIES=str(light_params["smooth_boundaries"]).lower(),
        LIGHT_MAKE_MEASUREMENTS=str(light_params["make_measurements"]).lower(),
        # --- Merge ---
        MERGE_THRESHOLD=merge_threshold_px,
        # --- Normalisation (shared) ---
        NORM_METHOD_LABEL=norm_label,
        NORM_GROOVY_BLOCK=norm_groovy,
        # --- Outputs ---
        OUTPUT_JSON=_json_string(out_json),
        OUTPUT_MASK=_json_string(out_mask),
        OUTPUT_MASK_DARK=_json_string(out_mask_dark),
        OUTPUT_MASK_LIGHT=_json_string(out_mask_light),
    )


def _write_cells_csv(path: Path, result: ImageQuantificationResult) -> None:
    """Write Cells Csv (usage interne).
    
    Args:
        path (Path): Chemin vers le fichier.
        result (ImageQuantificationResult): Parametre result.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "image",
            "source_path",
            "num_cells",
            "num_cells_dark",
            "num_cells_light",
            "cell_id",
            "x_relative",
            "y_relative",
            "x_pixel",
            "y_pixel",
            "pass_origin",
            "mask_path",
            "status",
            "error",
        ])
        if result.cells:
            for cell in result.cells:
                writer.writerow([
                    result.image,
                    result.source_path,
                    result.num_cells,
                    result.num_cells_dark,
                    result.num_cells_light,
                    cell.cell_id,
                    f"{cell.x_relative:.8f}",
                    f"{cell.y_relative:.8f}",
                    f"{cell.x_pixel:.3f}",
                    f"{cell.y_pixel:.3f}",
                    cell.pass_origin,
                    result.mask_path,
                    result.status,
                    result.error,
                ])
        else:
            writer.writerow([
                result.image,
                result.source_path,
                result.num_cells if result.status == "success" else "",
                result.num_cells_dark if result.status == "success" else "",
                result.num_cells_light if result.status == "success" else "",
                "",
                "",
                "",
                "",
                "",
                "",
                result.mask_path,
                result.status,
                result.error,
            ])


def _write_summary_csv(path: Path, results: list[ImageQuantificationResult]) -> None:
    """Write Summary Csv (usage interne).
    
    Args:
        path (Path): Chemin vers le fichier.
        results (list[ImageQuantificationResult]): Parametre results.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    total_cells = sum(r.num_cells for r in results if r.status == "success")
    success_count = sum(1 for r in results if r.status == "success")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["generated_at", datetime.now().isoformat(timespec="seconds")])
        writer.writerow([])
        writer.writerow([
            "image",
            "source_path",
            "num_cells",
            "num_cells_dark",
            "num_cells_light",
            "cell_id",
            "x_relative",
            "y_relative",
            "x_pixel",
            "y_pixel",
            "pass_origin",
            "mask_path",
            "cells_csv_path",
            "status",
            "error",
        ])

        for result in results:
            if result.cells:
                for cell in result.cells:
                    writer.writerow([
                        result.image,
                        result.source_path,
                        result.num_cells,
                        result.num_cells_dark,
                        result.num_cells_light,
                        cell.cell_id,
                        f"{cell.x_relative:.8f}",
                        f"{cell.y_relative:.8f}",
                        f"{cell.x_pixel:.3f}",
                        f"{cell.y_pixel:.3f}",
                        cell.pass_origin,
                        result.mask_path,
                        result.cells_csv_path,
                        result.status,
                        result.error,
                    ])
            else:
                writer.writerow([
                    result.image,
                    result.source_path,
                    result.num_cells if result.status == "success" else "",
                    result.num_cells_dark if result.status == "success" else "",
                    result.num_cells_light if result.status == "success" else "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    result.mask_path,
                    result.cells_csv_path,
                    result.status,
                    result.error,
                ])

        writer.writerow([])
        writer.writerow(["TOTAL", "", total_cells, "", "", "", "", "", "", "", "", "", "", f"{success_count}/{len(results)} success", ""])


def _write_volumetric_csv(
    path: Path,
    results: list[ImageQuantificationResult],
    thickness_um: float,
    *,
    slice_depth_um: float = 0.0,
    interslice_um: float = 0.0,
    region_contexts: dict[str | Path, dict] | None = None,
) -> None:
    """Write Volumetric Csv (usage interne).

    Emits the *desired* CSV: absolute cell count, volumetric concentration and
    an extrapolated absolute total, derived from the section thickness
    (``thickness_um = slice_depth_um + interslice_um``).

    Two modes:

    * Region-aware (when ``region_contexts[image]`` provides a warped atlas
      region mask + pixel size): each row is a (image, region) with
      num_cells, surface_mm2, concentration_cells_per_mm2,
      cell_volume_mm3 = surface_mm2 * thickness_um / 1e3, and an extrapolated
      absolute cell number = concentration_cells_per_mm2 * region_volume_mm3
      (from the atlas volumes table). This reuses the validated math from
      window4_validate.py / mask_replacer.py.
    * Whole-section fallback (no region mask): one row per image, area taken
      from pixel_size_um * image dimensions; concentration per mm3 and an
      extrapolated total are left blank when a volume cannot be derived.

    Args:
        path (Path): Fichier CSV de sortie.
        results (list[ImageQuantificationResult]): Parametre results.
        thickness_um (float): Epaisseur de coupe (slice_depth + interslice).
        slice_depth_um (float): Parametre slice_depth_um.
        interslice_um (float): Parametre interslice_um.
        region_contexts (dict[str | Path, dict] | None): Parametre region_contexts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atlas region volumes (mm3) keyed by region label id.
    atlas_volumes: dict[int, dict] = {}
    try:
        from mask_replacer import load_atlas_volumes
        atlas_volumes = load_atlas_volumes()
    except Exception:
        atlas_volumes = {}

    rows: list[dict] = []
    has_region_data = bool(region_contexts)

    for result in results:
        if result.status != "success":
            continue
        ctx = (region_contexts or {}).get(result.source_path) or {}
        mask_png = ctx.get("mask_png")
        pixel_size_um = float(ctx.get("pixel_size_um") or 0.0)
        image_w = int(ctx.get("image_width") or 0)
        image_h = int(ctx.get("image_height") or 0)

        # --- Region-aware path ---
        if mask_png and pixel_size_um > 0:
            try:
                from mask_replacer import (
                    compute_region_surface_areas_mm2,
                    count_cells_per_region,
                )
                cell_rows = count_cells_per_region(str(mask_png), [
                    {"x_relative": c.x_relative, "y_relative": c.y_relative} for c in result.cells
                ])
                cell_counts = {r["label"]: r["count"] for r in cell_rows}
                surface = compute_region_surface_areas_mm2(
                    str(mask_png), pixel_size_um, downsample=20
                )
            except Exception:
                cell_counts, surface = {}, {}
            all_labels = sorted(set(list(cell_counts.keys()) + list(surface.keys())))
            for lid in all_labels:
                n_cells = int(cell_counts.get(lid, 0))
                surf = surface.get(lid, {})
                surf_mm2 = float(surf.get("surface_mm2", 0.0))
                region_name = surf.get("name") or next(
                    (r["name"] for r in cell_rows if r["label"] == lid), str(lid)
                )
                # concentration per mm2 on the section (validated formula).
                concentration = (n_cells / surf_mm2) if surf_mm2 > 0 else ""
                # extrapolated volume of the detected cells within this slice.
                cell_volume_mm3 = (surf_mm2 * thickness_um / 1e3) if (surf_mm2 > 0 and thickness_um > 0) else ""
                # extrapolated absolute total across the whole atlas region.
                region_vol = atlas_volumes.get(int(lid), {}).get("volume_mm3", 0.0)
                extrapolated = (concentration * region_vol) if (concentration != "" and region_vol > 0) else ""
                rows.append({
                    "scope": "region",
                    "image": result.image,
                    "region_id": lid,
                    "region_name": region_name,
                    "num_cells": n_cells,
                    "surface_mm2": f"{surf_mm2:.6f}" if surf_mm2 else "",
                    "slice_depth_um": f"{slice_depth_um:.4f}" if slice_depth_um > 0 else "",
                    "interslice_um": f"{interslice_um:.4f}" if interslice_um > 0 else "0.0000",
                    "thickness_um": f"{thickness_um:.4f}" if thickness_um > 0 else "",
                    "concentration_cells_per_mm2": f"{concentration:.4f}" if concentration != "" else "",
                    "cell_volume_mm3": f"{cell_volume_mm3:.6f}" if cell_volume_mm3 != "" else "",
                    "extrapolated_absolute_cells": f"{extrapolated:.2f}" if extrapolated != "" else "",
                })
            continue

        # --- Whole-section fallback (no region mask available) ---
        area_mm2 = 0.0
        if pixel_size_um > 0 and image_w > 0 and image_h > 0:
            px_mm2 = (pixel_size_um / 1000.0) ** 2
            area_mm2 = image_w * image_h * px_mm2
        concentration = (result.num_cells / area_mm2) if area_mm2 > 0 else ""
        cell_volume_mm3 = (area_mm2 * thickness_um / 1e3) if (area_mm2 > 0 and thickness_um > 0) else ""
        rows.append({
            "scope": "whole_section",
            "image": result.image,
            "region_id": "",
            "region_name": "",
            "num_cells": result.num_cells,
            "surface_mm2": f"{area_mm2:.6f}" if area_mm2 else "",
            "slice_depth_um": f"{slice_depth_um:.4f}" if slice_depth_um > 0 else "",
            "interslice_um": f"{interslice_um:.4f}" if interslice_um > 0 else "0.0000",
            "thickness_um": f"{thickness_um:.4f}" if thickness_um > 0 else "",
            "concentration_cells_per_mm2": f"{concentration:.4f}" if concentration != "" else "",
            "cell_volume_mm3": f"{cell_volume_mm3:.6f}" if cell_volume_mm3 != "" else "",
            "extrapolated_absolute_cells": "",
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "scope", "image", "region_id", "region_name",
            "num_cells", "surface_mm2",
            "slice_depth_um", "interslice_um", "thickness_um",
            "concentration_cells_per_mm2", "cell_volume_mm3",
            "extrapolated_absolute_cells",
        ])
        for row in rows:
            writer.writerow([
                row["scope"], row["image"], row["region_id"], row["region_name"],
                row["num_cells"], row["surface_mm2"],
                row["slice_depth_um"], row["interslice_um"], row["thickness_um"],
                row["concentration_cells_per_mm2"], row["cell_volume_mm3"],
                row["extrapolated_absolute_cells"],
            ])
        writer.writerow([])
        writer.writerow([
            "TOTAL", "", "", "",
            sum(r["num_cells"] for r in rows), "",
            "", "", "", "", "", "",
        ])

def _reader_thread(stream, out_queue: queue.Queue[str]) -> None:
    """Reader Thread (usage interne).
    
    Args:
        stream (Any): Parametre stream.
        out_queue (queue.Queue[str]): File d'attente (queue).
    """
    try:
        for line in iter(stream.readline, ""):
            if line:
                out_queue.put(line.rstrip())
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_qupath_script(
    qupath_exe: Path,
    script_path: Path,
    *,
    progress_cb: ProgressCallback | None,
    file_index: int,
    file_total: int,
    image_name: str,
    base_global_pct: float,
    file_global_span: float,
    timeout_seconds: int = 1800,
) -> tuple[int, list[str]]:
    """
    Run QuPath with continuous heartbeat progress.

    Two-pass detection takes roughly twice as long, so the timeout is doubled
    and the heartbeat ceiling is tuned for the two-pass trigger sequence.
    """
    cmd = [str(qupath_exe), "script", str(script_path)]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(BASE_DIR),
    )

    assert proc.stdout is not None
    lines: list[str] = []
    out_queue: queue.Queue[str] = queue.Queue()
    t = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue), daemon=True)
    t.start()

    start = time.time()
    last_emit = 0.0
    file_pct = 25.0

    # Two-pass trigger map (percentages spread across dark → light → merge).
    trigger_pct = {
        "TRIGGER:IMAGE_OPENED": 20.0,
        "TRIGGER:NORMALIZED": 25.0,
        "TRIGGER:DARK_ROI_CREATED": 28.0,
        "TRIGGER:DARK_DETECTION_DONE": 48.0,
        "TRIGGER:DARK_COORDINATES_EXTRACTED": 50.0,
        "TRIGGER:LIGHT_ROI_CREATED": 52.0,
        "TRIGGER:LIGHT_DETECTION_DONE": 75.0,
        "TRIGGER:LIGHT_COORDINATES_EXTRACTED": 77.0,
        "TRIGGER:MERGE_DONE": 84.0,
        "TRIGGER:DETECTION_MERGED": 86.0,
        "TRIGGER:COORDINATES_EXTRACTED": 89.0,
        "TRIGGER:MASK_WRITTEN": 93.0,
        "QUPATH_RESULT:": 97.0,
    }

    while True:
        while True:
            try:
                line = out_queue.get_nowait()
            except queue.Empty:
                break

            lines.append(line)
            for marker, pct in trigger_pct.items():
                if marker in line:
                    file_pct = max(file_pct, pct)
                    break

            _emit(progress_cb, {
                "type": "log",
                "level": "info" if "ERROR" not in line and "Exception" not in line else "error",
                "message": line,
                "file_index": file_index,
                "file_total": file_total,
                "image": image_name,
                "file_pct": file_pct,
                "global_pct": min(100.0, base_global_pct + file_global_span * file_pct / 100.0),
            })

        return_code = proc.poll()
        now = time.time()
        elapsed = now - start

        if return_code is not None:
            # Drain any final lines.
            while True:
                try:
                    line = out_queue.get_nowait()
                except queue.Empty:
                    break
                lines.append(line)
            return return_code, lines

        if elapsed > timeout_seconds:
            proc.kill()
            return 124, lines + [f"ERROR: QuPath timeout after {timeout_seconds}s"]

        if now - last_emit >= 0.5:
            # Heartbeat: climb slowly but never claim completion before triggers.
            heartbeat_pct = min(72.0, 25.0 + (elapsed / max(timeout_seconds, 1)) * 47.0)
            file_pct = max(file_pct, heartbeat_pct)
            _emit(progress_cb, {
                "type": "heartbeat",
                "message": f"QuPath running on {image_name} ({int(elapsed)}s)",
                "file_index": file_index,
                "file_total": file_total,
                "image": image_name,
                "file_pct": file_pct,
                "global_pct": min(100.0, base_global_pct + file_global_span * file_pct / 100.0),
            })
            last_emit = now

        time.sleep(0.1)


def _parse_result_json(json_path: Path, image_path: Path, mask_path: Path, cells_csv_path: Path, mask_dark_path=None, mask_light_path=None) -> ImageQuantificationResult:
    """Parse Result Json (usage interne).
    
    Args:
        json_path (Path): Chemin vers le fichier.
        image_path (Path): Chemin vers le fichier.
        mask_path (Path): Chemin vers le fichier.
        cells_csv_path (Path): Chemin vers le fichier.
        mask_dark_path (Any): Chemin vers le fichier.
        mask_light_path (Any): Chemin vers le fichier.
    
    Returns:
        ImageQuantificationResult: Resultat.
    """
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)

    cells = [
        CellCoordinate(
            cell_id=int(cell.get("cell_id", i + 1)),
            x_relative=float(cell.get("x_relative", 0.0)),
            y_relative=float(cell.get("y_relative", 0.0)),
            x_pixel=float(cell.get("x_pixel", 0.0)),
            y_pixel=float(cell.get("y_pixel", 0.0)),
            pass_origin=str(cell.get("pass", cell.get("pass_origin", "dark"))),
        )
        for i, cell in enumerate(raw.get("cells", []))
    ]

    return ImageQuantificationResult(
        image=str(raw.get("image", image_path.name)),
        source_path=str(raw.get("source_path", image_path)),
        num_cells=int(raw.get("num_cells", len(cells))),
        status=str(raw.get("status", "success")),
        mask_path=str(raw.get("mask_path", mask_path)),
        mask_dark_path=str(mask_dark_path) if mask_dark_path else "",
        mask_light_path=str(mask_light_path) if mask_light_path else "",
        cells_csv_path=str(cells_csv_path),
        result_json_path=str(json_path),
        cells=cells,
        num_cells_dark=int(raw.get("num_cells_dark", 0)),
        num_cells_light=int(raw.get("num_cells_light", 0)),
    )


def run_quantification(
    image_paths: Iterable[str | Path],
    output_dir: str | Path,
    *,
    progress_cb: ProgressCallback | None = None,
    qupath_exe: str | Path | None = None,
    detection_params: dict | None = None,
    light_detection_params: dict | None = None,
    refresh_images_cb: Callable[[], Iterable[str | Path]] | None = None,
    input_complete_cb: Callable[[], bool] | None = None,
    poll_interval_seconds: float = 1.5,
    slice_depth_um: float = 0.0,
    interslice_um: float = 0.0,
    region_contexts: dict[str | Path, dict] | None = None,
) -> QuantificationResult:
    """
    Run QuPath cell quantification with two passes (dark + light background).

    Detection always runs in two passes per image:
      - Pass 1 (dark bg): Hematoxylin OD — catches cells on dark backgrounds.
      - Pass 2 (light bg): Optical density sum — catches cells on light backgrounds.
    Results are merged by centroid distance (dark wins on overlap).

    By default, ``image_paths`` is treated as a static list. If ``refresh_images_cb``
    is provided, the wrapper refreshes the image list after each quantification
    and keeps processing newly discovered images until ``input_complete_cb``
    returns True and no unprocessed image remains.
    """
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    # Two param sets: pass 1 (dark) + pass 2 (light). Normalisation is shared.
    dark_params = dict(DEFAULT_DETECTION_PARAMS)
    if detection_params:
        dark_params.update(detection_params)

    light_params = dict(DEFAULT_DETECTION_PARAMS_LIGHT)
    if light_detection_params:
        light_params.update(light_detection_params)

    qupath = Path(qupath_exe).resolve() if qupath_exe is not None else DEFAULT_QUPATH_EXE
    if not qupath.exists():
        raise FileNotFoundError(f"QuPath executable not found: {qupath}")

    def _normalize(paths: Iterable[str | Path]) -> list[Path]:
        """Normalize (usage interne).
        
        Args:
            paths (Iterable[str | Path]): Parametre paths.
        
        Returns:
            list[Path]: Resultat.
        """
        normalized = []
        seen = set()
        for p in paths:
            path = Path(p).resolve()
            if path.exists() and path.is_file() and path not in seen:
                normalized.append(path)
                seen.add(path)
        return sorted(normalized)

    initial_images = _normalize(image_paths)
    pending: list[Path] = list(initial_images)
    pending_set = set(pending)
    processed: set[Path] = set()
    results: list[ImageQuantificationResult] = []
    summary_csv = output_root / "cell_quantification_summary.csv"

    def _input_complete() -> bool:
        """Input Complete (usage interne).
        
        Returns:
            bool: Resultat.
        """
        if input_complete_cb is None:
            return True
        try:
            return bool(input_complete_cb())
        except Exception:
            return True

    def _refresh_pending() -> None:
        """Refresh Pending (usage interne)."""
        if refresh_images_cb is None:
            return
        try:
            refreshed = _normalize(refresh_images_cb())
        except Exception as exc:
            _emit(progress_cb, {
                "type": "refresh_error",
                "level": "error",
                "message": str(exc),
                "file_pct": 0.0,
                "global_pct": 0.0,
            })
            return

        added = 0
        for path in refreshed:
            if path in processed or path in pending_set:
                continue
            pending.append(path)
            pending_set.add(path)
            added += 1

        if added:
            _emit(progress_cb, {
                "type": "refreshed",
                "message": f"{added} new image(s) discovered",
                "new_images": added,
                "pending": len(pending),
                "processed": len(processed),
                "file_pct": 0.0,
                "global_pct": 0.0,
            })

    _emit(progress_cb, {
        "type": "started",
        "message": f"Quantification started (two-pass): {len(pending)} image(s)",
        "file_index": 0,
        "file_total": len(pending),
        "file_pct": 0.0,
        "global_pct": 0.0,
        "output_dir": str(output_root),
    })

    while True:
        _refresh_pending()

        if not pending:
            if _input_complete():
                break

            _emit(progress_cb, {
                "type": "waiting_for_images",
                "message": "Waiting for new 4x JPEG images...",
                "file_index": len(processed),
                "file_total": len(processed),
                "file_pct": 0.0,
                "global_pct": 0.0 if not processed else min(99.0, float(len(processed))),
            })
            time.sleep(max(0.1, float(poll_interval_seconds)))
            continue

        image_path = pending.pop(0)
        pending_set.discard(image_path)

        index = len(processed) + 1
        dynamic_total = max(index + len(pending), index)
        file_span = 100.0 / float(dynamic_total)
        base_global = (index - 1) * file_span

        safe = _safe_stem(image_path)
        image_out = output_root / safe
        image_out.mkdir(parents=True, exist_ok=True)

        out_json = image_out / f"{safe}_result.json"
        out_mask = image_out / f"{safe}_cell_mask.tif"
        out_mask_dark = image_out / f"{safe}_cell_mask_dark.tif"
        out_mask_light = image_out / f"{safe}_cell_mask_light.tif"
        out_cells_csv = image_out / f"{safe}_cells.csv"

        _emit(progress_cb, {
            "type": "file_started",
            "message": f"[{index}/{dynamic_total}] {image_path.name}",
            "file_index": index,
            "file_total": dynamic_total,
            "image": image_path.name,
            "source_path": str(image_path),
            "file_pct": 0.0,
            "global_pct": base_global,
        })

        script_path = Path("")
        try:
            script = _build_groovy_script(
                image_path, dark_params, light_params, MERGE_THRESHOLD_PX,
                out_json, out_mask, out_mask_dark, out_mask_light,
            )
            with tempfile.NamedTemporaryFile(mode="w", suffix=".groovy", delete=False, encoding="utf-8") as tf:
                tf.write(script)
                script_path = Path(tf.name)

            _emit(progress_cb, {
                "type": "file_step",
                "message": "Groovy script generated (two-pass)",
                "file_index": index,
                "file_total": dynamic_total,
                "image": image_path.name,
                "file_pct": 15.0,
                "global_pct": base_global + file_span * 0.15,
            })

            return_code, qupath_lines = _run_qupath_script(
                qupath,
                script_path,
                progress_cb=progress_cb,
                file_index=index,
                file_total=dynamic_total,
                image_name=image_path.name,
                base_global_pct=base_global,
                file_global_span=file_span,
            )

            if return_code != 0:
                raise RuntimeError(
                    f"QuPath failed with code {return_code}. "
                    + "\n".join(qupath_lines[-10:])
                )
            if not out_json.exists():
                raise RuntimeError("QuPath did not generate the expected result JSON.")

            result = _parse_result_json(out_json, image_path, out_mask, out_cells_csv, out_mask_dark, out_mask_light)
            _write_cells_csv(out_cells_csv, result)
            result.cells_csv_path = str(out_cells_csv)
            results.append(result)

            _emit(progress_cb, {
                "type": "file_done",
                "message": f"{result.num_cells} cell(s) detected (dark: {result.num_cells_dark}, light: {result.num_cells_light})",
                "file_index": index,
                "file_total": dynamic_total,
                "image": image_path.name,
                "source_path": str(image_path),
                "num_cells": result.num_cells,
                "num_cells_dark": result.num_cells_dark,
                "num_cells_light": result.num_cells_light,
                "mask_path": result.mask_path,
                "mask_dark_path": result.mask_dark_path,
                "mask_light_path": result.mask_light_path,
                "cells_csv_path": result.cells_csv_path,
                "file_pct": 100.0,
                "global_pct": min(99.0, base_global + file_span),
            })

        except Exception as exc:
            result = ImageQuantificationResult(
                image=image_path.name,
                source_path=str(image_path),
                num_cells=-1,
                status="error",
                mask_path=str(out_mask) if out_mask.exists() else "",
                cells_csv_path=str(out_cells_csv),
                result_json_path=str(out_json) if out_json.exists() else "",
                error=str(exc),
                cells=[],
            )
            _write_cells_csv(out_cells_csv, result)
            results.append(result)

            _emit(progress_cb, {
                "type": "file_error",
                "level": "error",
                "message": str(exc),
                "file_index": index,
                "file_total": dynamic_total,
                "image": image_path.name,
                "source_path": str(image_path),
                "file_pct": 100.0,
                "global_pct": min(99.0, base_global + file_span),
                "error": str(exc),
            })

        finally:
            processed.add(image_path)
            if script_path and script_path.exists():
                try:
                    script_path.unlink()
                except Exception:
                    pass

    _write_summary_csv(summary_csv, results)

    # ------------------------------------------------------------------
    # Desired volumetric / absolute / extrapolated CSV.
    # thickness_um = slice_depth_um + interslice_um (user-defined).
    # Region-aware when region_contexts supplies a warped atlas mask per
    # image; otherwise a whole-section estimate is emitted from the image's
    # own physical area (pixel size x dims).
    # ------------------------------------------------------------------
    thickness_um = float(slice_depth_um) + float(interslice_um)
    volumetric_csv = output_root / "cell_quantification_volumetric.csv"
    _write_volumetric_csv(
        volumetric_csv, results, thickness_um,
        slice_depth_um=float(slice_depth_um),
        interslice_um=float(interslice_um),
        region_contexts=region_contexts,
    )

    total = len(processed)
    successful = sum(1 for r in results if r.status == "success")
    total_cells = sum(r.num_cells for r in results if r.status == "success")
    status = "success" if total > 0 and successful == total else ("partial" if successful > 0 else "error")

    final = QuantificationResult(
        status=status,
        output_dir=str(output_root),
        summary_csv_path=str(summary_csv),
        total_images=total,
        successful_images=successful,
        total_cells=total_cells,
        results=results,
    )

    summary_json = output_root / "cell_quantification_summary.json"
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(asdict(final), f, indent=2, ensure_ascii=False)

    _emit(progress_cb, {
        "type": "done",
        "message": f"Quantification complete (two-pass): {total_cells} cell(s), {successful}/{total} image(s)",
        "file_index": total,
        "file_total": total,
        "file_pct": 100.0,
        "global_pct": 100.0,
        "output_dir": str(output_root),
        "summary_csv_path": str(summary_csv),
        "summary_json_path": str(summary_json),
        "total_cells": total_cells,
        "successful_images": successful,
        "total_images": total,
        "status": status,
    })

    return final


def discover_jpeg_images(root: str | Path, recursive: bool = True) -> list[Path]:
    """Return JPEG images below root, sorted deterministically."""
    root_path = Path(root)
    if not root_path.exists():
        return []
    patterns = ["**/*.jpeg", "**/*.jpg"] if recursive else ["*.jpeg", "*.jpg"]
    found: set[Path] = set()
    for pattern in patterns:
        for p in root_path.glob(pattern):
            if p.is_file():
                found.add(p)
    return sorted(found)


__all__ = [
    "DEFAULT_DETECTION_PARAMS",
    "DEFAULT_DETECTION_PARAMS_LIGHT",
    "MERGE_THRESHOLD_PX",
    "DEFAULT_QUPATH_EXE",
    "CellCoordinate",
    "ImageQuantificationResult",
    "QuantificationResult",
    "discover_jpeg_images",
    "run_quantification",
]