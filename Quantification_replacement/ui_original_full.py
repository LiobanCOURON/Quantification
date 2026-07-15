# Imports (tkinter/filedialog/messagebox/...)
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import sys
import PIL
import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont
import threading
import queue
from datetime import datetime
from pathlib import Path

import re
import csv
import json
import shutil

from atlas_position_getter import get_depth_range, get_or_create_slice_images
from mask_replacer import (
    replace_mask,
    save_mask_pair,
    count_cells_per_region,
    filter_cells_by_region,
    combine_and_filter_cell_mask,
    compute_region_surface_areas_mm2,
    load_atlas_volumes,
)
import convert_czi_to_jpeg
from convert_czi_to_jpeg import get_czi_pixel_size_um
from quantification_wrapper import discover_jpeg_images, run_quantification

# matplotlib is used only with a non-interactive (Agg) canvas so it never
# interferes with the Tkinter GUI backend. Importing it here (after tkinter)
# keeps all window-4 rendering on the Tk main thread.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

# Def des constantes (font/bg color/fg color/...)
FONT = ("Arial", 12)
SMALL_FONT = ("Arial", 10)
BG_COLOR = "#f0f0f0"
FG_COLOR = "#000000"
ACCENT_COLOR_BLUE = "#007acc"
ACCENT_COLOR_GREEN = "#00cc66"
ERROR_COLOR = "#ff0000"
CLICK_BOXES_COLOR = "#b1b1b1"
# --- Image helpers ---

# Base directory for images (same folder as this script)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_INPUT_DIR = r"./input"
BASE_OUTPUT_DIR = r"./output"

# Work-in-progress directory where validated masks are saved per .czi:
#   ./WorkInProgress/<czi_stem>/masks/<roi_folder>.{png,_overlay.png,txt}
WORK_IN_PROGRESS_DIR = r"./WorkInProgress"
# Must match the downsample factor used in convert_czi_to_png().
DOWNSAMPLE_FACTOR = 20
JPEG_OUTPUT_SUBDIR = f"downsampled{DOWNSAMPLE_FACTOR}_jpeg"

# Window 3 quantification uses a dedicated higher-resolution 4x JPEG tree.
# This is intentionally separate from window 2's 20x alignment/mask workflow.
QUANTIFICATION_DOWNSAMPLE = 4
QUANTIFICATION_JPEG_OUTPUT_SUBDIR = f"downsampled{QUANTIFICATION_DOWNSAMPLE}_jpeg"
_quantification_conversion_running = False

# Window 1 preview: low-precision (high downsample) JPEGs used only for quick
# visualisation. Window 2 keeps its own higher-precision (20x) working copy for
# the mask replacement, so the two pipelines are fully independent.
PREVIEW_DOWNSAMPLE = 50
TEMP_VIZU_DIR = os.path.join(WORK_IN_PROGRESS_DIR, "temp_vizu")
TEMP_VIZU_SUBDIR = f"downsampled{PREVIEW_DOWNSAMPLE}_jpeg"

# Creates input, output and WorkInProgress directories if they don't exist
os.makedirs(BASE_INPUT_DIR, exist_ok=True)
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
os.makedirs(WORK_IN_PROGRESS_DIR, exist_ok=True)
os.makedirs(TEMP_VIZU_DIR, exist_ok=True)

# Folder containing the .czi files to convert.
# For now it defaults to BASE_INPUT_DIR; it will be made user-editable later
# (e.g. via a folder picker in window 1).
czi_folder_path = BASE_INPUT_DIR

def open_file_path():
    """
    This function opens a file dialog to select a file and returns the selected file path. It also shows an info message box with the selected file path.
    """
    file_path = filedialog.askopenfilename()
    if file_path:
        messagebox.showinfo("File Selected", f"You selected: {file_path}")
    return file_path


def save_file_path():
    """
    This function opens a file dialog to select a location to save a file and returns the selected file path. It also shows an info message box with the selected file path.
    """
    file_path = filedialog.asksaveasfilename()
    if file_path:
        messagebox.showinfo("File Saved", f"You saved: {file_path}")
    return file_path


def get_img_dims(original_width, original_height, available_width, available_height):
    """
    Given the original image dimensions and the available space,
    returns (new_width, new_height) that fit within the available space
    while preserving the original aspect ratio.
    """
    if original_width <= 0 or original_height <= 0 or available_width <= 0 or available_height <= 0:
        return available_width, available_height

    aspect_ratio = original_width / original_height

    if available_width / available_height > aspect_ratio:
        # Available space is wider than the image → height-limited
        new_height = available_height
        new_width = int(new_height * aspect_ratio)
    else:
        # Available space is taller than the image → width-limited
        new_width = available_width
        new_height = int(new_width / aspect_ratio)

    return max(1, new_width), max(1, new_height)


def load_and_resize_image(file_path, max_width, max_height):
    """
    Load an image from file_path, resize it to fit within (max_width, max_height)
    while preserving aspect ratio, and return a PhotoImage that Tkinter can display.
    Returns None if the file cannot be loaded.

    Relative paths are resolved from the same folder as this script. Absolute paths
    are used as-is, which allows the atlas generator to return cached image paths.
    """
    try:
        full_path = file_path if os.path.isabs(file_path) else os.path.join(BASE_DIR, file_path)
        pil_img = Image.open(full_path)
        orig_w, orig_h = pil_img.size
        new_w, new_h = get_img_dims(orig_w, orig_h, max_width, max_height)
        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(pil_img)
    except Exception as e:
        print(f"Error loading image '{file_path}': {e}")
        return None


def _resolve_image_path(name):
    """
    Return an existing image path for `name`.
    If `name` does not exist next to this script, try the `.temp.<ext>` variant
    (e.g. Histo.png -> Histo.temp.png) so the UI stays testable with the temp assets.
    Absolute paths are returned unchanged.
    """
    if os.path.isabs(name):
        return name
    full = os.path.join(BASE_DIR, name)
    if os.path.exists(full):
        return name
    base, ext = os.path.splitext(name)
    temp_name = base + ".temp" + ext
    if os.path.exists(os.path.join(BASE_DIR, temp_name)):
        return temp_name
    return name


def _load_resized_pil(file_path, max_width, max_height):
    """
    Load and resize an image (aspect-ratio preserved) and return it as a PIL image
    in RGBA mode. Returns None on failure. Used when we need to draw on the image
    (markers) before converting it to a PhotoImage.
    """
    try:
        full_path = file_path if os.path.isabs(file_path) else os.path.join(BASE_DIR, file_path)
        pil_img = Image.open(full_path)
        orig_w, orig_h = pil_img.size
        new_w, new_h = get_img_dims(orig_w, orig_h, max_width, max_height)
        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        return pil_img
    except Exception as e:
        print(f"Error loading image '{file_path}': {e}")
        return None


def _draw_markers_on_pil(pil_img, points, color):
    """
    Return a copy of `pil_img` with numbered markers drawn at the given
    normalized (x, y) coordinates (values in [0, 1], relative to the displayed
    image). Marker numbering follows click order within that image.
    """
    img = pil_img.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    radius = max(4, min(w, h) // 35)
    font_size = max(9, int(radius * 1.1))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    for idx, (nx, ny) in enumerate(points):
        cx, cy = int(nx * w), int(ny * h)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color, outline="white",
        )
        text = str(idx + 1)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw // 2, cy - th // 2), text, fill="white", font=font)
    return img


# --- UI PART ---
root = tk.Tk()
root.title("Quantification")

root.geometry("800x600")
root.configure(bg=BG_COLOR)


# --- Window 1 state (preview + .czi name list) ---
# Two linked checkboxes (one shared IntVar, distinct onvalue) select the .czi
# source folder. The name list is built from the chosen folder; clicking a name
# shows the first z-slice of its first ROI (scene). Précédant/Suivant navigate
# the ROIs/scenes of the selected .czi. The 50x preview JPEGs are produced in a
# background thread into ./WorkInProgress/temp_vizu (independent from window 2's
# 20x working copy), so the UI never blocks.
_w1_source_var = None            # tk.IntVar binding the two linked checkboxes (1=Input, 2=Other)
_w1_name_frame = None            # inner frame holding the clickable .czi names
_w1_name_canvas = None
_w1_preview_label = None         # shows the preview JPEG
_w1_preview_photo = None         # keep ref to avoid garbage collection
_w1_preview_status = None        # small status line (ROI x/y, "converting...")
_w1_selected_stem = None         # stem of the currently selected .czi (or None)
_w1_selected_scenes = []         # list of scene folders (Path) for the selected .czi
_w1_scene_index = 0              # current scene index within _w1_selected_scenes
_w1_temp_poll_id = None          # root.after id while awaiting conversion results

# Physical thickness (µm) of each brain slice — set in Window 1, used by the
# Window 4 volumetric export (cells per mm³ = num_cells / (depth * surface)).
_slice_depth_um: float = 40.0
_w1_depth_entry = None           # tk.Entry widget bound to _slice_depth_um


def get_slice_depth_um() -> float:
    """Return the current slice depth in micrometres (default 40.0)."""
    return _slice_depth_um


def _convert_all_czi_to_temp_vizu(folder):
    """
    Background-thread converter: turn every .czi in `folder` into low-precision
    (downsample 50) JPEGs under ./WorkInProgress/temp_vizu for the window 1 preview.

    This intentionally mirrors convert_czi_to_png() but targets TEMP_VIZU_DIR and
    PREVIEW_DOWNSAMPLE so it stays fully independent from window 2's higher-quality
    pipeline. Runs in a daemon thread -> only print() is used (Tk is not thread-safe);
    the UI picks up results by polling temp_vizu from the main thread.
    """
    input_dir = Path(folder)
    output_dir = Path(TEMP_VIZU_DIR)

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[temp_vizu] Input folder not found: {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    czi_files = list(convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True))
    if not czi_files:
        print(f"[temp_vizu] No .czi files found in: {input_dir}")
        return

    converted = 0
    failed = 0
    for czi_path in czi_files:
        try:
            out_paths = convert_czi_to_jpeg.convert_one_file(
                czi_path=czi_path,
                input_dir=input_dir,
                output_dir=output_dir,
                downsample=PREVIEW_DOWNSAMPLE,
                quality=85,
                recursive=True,
            )
            converted += len(out_paths)
            print(f"[temp_vizu][OK] {czi_path.name} -> {len(out_paths)} image(s)")
        except Exception as exc:
            failed += 1
            print(f"[temp_vizu][ERROR] {czi_path}: {exc}")

    print(
        f"[temp_vizu] Done: {converted} image(s) created, {failed} failed. "
        f"Output: {output_dir}"
    )


def _scene_folders_for_stem(stem):
    """
    Return the sorted scene folders (Path) produced for `stem` in temp_vizu:
    <stem>_<scene+1>/. Returns [] if nothing is converted yet (caller may then poll).
    """
    base = Path(TEMP_VIZU_DIR) / TEMP_VIZU_SUBDIR
    if not base.is_dir():
        return []
    prefix = stem + "_"
    found = []
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            rest = d.name[len(prefix):]
            if rest.isdigit():
                found.append((int(rest), d))
    found.sort(key=lambda t: t[0])
    return [d for _, d in found]


def _get_w1_preview_size():
    """Available pixels for the preview, derived from the live window size."""
    win_w = root.winfo_width()
    win_h = root.winfo_height()
    if win_w < 100:
        win_w = 800
    if win_h < 100:
        win_h = 600
    # Right panel ~ 2/3 of the width; reserve ~110px for status + nav + Next.
    avail_w = int(win_w * 2 / 3) - 30
    avail_h = win_h - 120
    if avail_w < 50:
        avail_w = 50
    if avail_h < 50:
        avail_h = 50
    return avail_w, avail_h


def _refresh_w1_preview():
    """Render the currently selected .czi's current scene (first z-slice) in the preview."""
    global _w1_preview_photo, _w1_selected_scenes
    if _w1_preview_label is None or not _w1_preview_label.winfo_exists():
        return

    # (Re)discover scenes for the selected stem if not known yet.
    if _w1_selected_stem is not None and not _w1_selected_scenes:
        _w1_selected_scenes = _scene_folders_for_stem(_w1_selected_stem)

    if _w1_selected_stem is None:
        _w1_preview_label.config(image="", text="Sélectionnez un .czi")
        if _w1_preview_status is not None:
            _w1_preview_status.config(text="")
        return

    if not _w1_selected_scenes or not (0 <= _w1_scene_index < len(_w1_selected_scenes)):
        # Conversion not finished yet -> show a waiting state and keep polling.
        _w1_preview_label.config(image="", text="Conversion en cours...")
        if _w1_preview_status is not None:
            _w1_preview_status.config(text="Conversion 50x en cours, veuillez patienter...")
        _start_w1_temp_polling()
        return

    scene_dir = _w1_selected_scenes[_w1_scene_index]
    img_path = _first_z_image(scene_dir)
    if img_path is None or not os.path.exists(img_path):
        _w1_preview_label.config(image="", text="Image non disponible")
        _start_w1_temp_polling()
        return

    avail_w, avail_h = _get_w1_preview_size()
    photo = load_and_resize_image(str(img_path), avail_w, avail_h)
    if photo is not None:
        _w1_preview_photo = photo
        _w1_preview_label.config(image=photo, text="")

    if _w1_preview_status is not None:
        _w1_preview_status.config(
            text=f"{_w1_selected_stem}   —   ROI {_w1_scene_index + 1} / {len(_w1_selected_scenes)}"
        )
    _stop_w1_temp_polling()


def _start_w1_temp_polling():
    global _w1_temp_poll_id
    if _w1_temp_poll_id is not None:
        return
    _w1_temp_poll_id = root.after(1500, _poll_w1_temp_once)


def _stop_w1_temp_polling():
    global _w1_temp_poll_id
    if _w1_temp_poll_id is not None:
        try:
            root.after_cancel(_w1_temp_poll_id)
        except Exception:
            pass
        _w1_temp_poll_id = None


def _poll_w1_temp_once():
    """Main-thread poll: re-check temp_vizu for the selected stem and refresh when ready."""
    global _w1_temp_poll_id, _w1_selected_scenes
    _w1_temp_poll_id = None
    if _w1_selected_stem is None:
        return
    _w1_selected_scenes = _scene_folders_for_stem(_w1_selected_stem)
    if _w1_selected_scenes:
        _refresh_w1_preview()
    else:
        _start_w1_temp_polling()


def _on_czi_name_click(stem):
    """Show the first z-slice of the first ROI (scene) of the clicked .czi."""
    global _w1_selected_stem, _w1_selected_scenes, _w1_scene_index
    _w1_selected_stem = stem
    _w1_scene_index = 0
    _w1_selected_scenes = _scene_folders_for_stem(stem)
    _stop_w1_temp_polling()
    _refresh_w1_preview()


def _w1_prev_scene():
    """Précédant: move to the previous ROI/scene of the selected .czi."""
    global _w1_scene_index
    if _w1_scene_index > 0:
        _w1_scene_index -= 1
        _refresh_w1_preview()


def _w1_next_scene():
    """Suivant: move to the next ROI/scene of the selected .czi."""
    global _w1_scene_index
    if _w1_scene_index < len(_w1_selected_scenes) - 1:
        _w1_scene_index += 1
        _refresh_w1_preview()


def _on_w1_configure(event):
    """Re-render the preview (so it resizes) when the window is resized."""
    if _w1_selected_stem is not None:
        _refresh_w1_preview()


def _rebuild_w1_name_list():
    """Rebuild the scrollable, clickable list of .czi names from the current input folder."""
    if _w1_name_frame is None:
        return
    for child in _w1_name_frame.winfo_children():
        child.destroy()

    folder = Path(czi_folder_path)
    try:
        czi_files = list(convert_czi_to_jpeg.iter_czi_files(folder, recursive=True))
    except Exception as e:
        print(f"[window1] cannot list .czi: {e}")
        czi_files = []

    if not czi_files:
        tk.Label(
            _w1_name_frame, text="(aucun .czi)", font=SMALL_FONT,
            bg="white", fg="gray", anchor="w",
        ).pack(fill=tk.X, padx=5, pady=2)
        return

    for p in czi_files:
        stem = p.stem
        lbl = tk.Label(
            _w1_name_frame, text=stem, font=SMALL_FONT,
            bg="white", fg=FG_COLOR, anchor="w", cursor="hand2", padx=4, pady=2,
        )
        lbl.pack(fill=tk.X, padx=2, pady=1)
        lbl.bind("<Button-1>", lambda e, s=stem: _on_czi_name_click(s))
        lbl.bind("<Enter>", lambda e, w=lbl: w.config(bg="#e5f3ff"))
        lbl.bind("<Leave>", lambda e, w=lbl: w.config(bg="white"))


def _set_w1_input_folder(folder):
    """Apply a new .czi source folder: refresh the name list and restart the 50x conversion."""
    global czi_folder_path
    czi_folder_path = folder
    _rebuild_w1_name_list()
    os.makedirs(TEMP_VIZU_DIR, exist_ok=True)
    threading.Thread(
        target=_convert_all_czi_to_temp_vizu, args=(folder,), daemon=True
    ).start()


def _select_input_folder():
    """Linked checkbox #1 (default): use ./input as the .czi source."""
    if _w1_source_var is not None:
        _w1_source_var.set(1)
    _set_w1_input_folder(BASE_INPUT_DIR)


def _select_other_folder():
    """Linked checkbox #2: open a folder browser to pick another .czi source folder."""
    initial = czi_folder_path if os.path.isdir(czi_folder_path) else BASE_INPUT_DIR
    folder = filedialog.askdirectory(
        title="Sélectionner le dossier contenant les .czi",
        mustexist=True,
        initialdir=initial,
    )
    if folder:
        if _w1_source_var is not None:
            _w1_source_var.set(2)
        _set_w1_input_folder(folder)
    else:
        # Cancelled -> revert to the Input checkbox.
        if _w1_source_var is not None:
            _w1_source_var.set(1)


# --- Window 1 ---
def window1():
    """
    First window: quick .czi preview.

    Split 1/3 (left) / 2/3 (right):
      Left 1/3:
        - Two linked checkboxes (mutually exclusive via one shared IntVar):
            '.czi dans le dossier Input' (default, on) and
            '.czi dans un autre dossier' (opens a folder browser when checked).
        - A scrollable, clickable list of all .czi names in the chosen folder.
      Right 2/3:
        - Preview of the selected .czi's first z-slice of its first ROI (scene),
          read from the 50x temp_vizu JPEGs.
        - 'Précédant' / 'Suivant' navigate the ROIs/scenes of the selected .czi.
        - 'Next' goes to window 2 (mask replacement, keeps its 20x pipeline).

    On entry, a background thread starts converting every .czi in the input folder
    to 50x JPEGs in ./WorkInProgress/temp_vizu so the UI stays responsive.
    """
    global _w1_source_var, _w1_name_frame, _w1_name_canvas, _w1_depth_entry
    global _w1_preview_label, _w1_preview_photo, _w1_preview_status
    global _w1_selected_stem, _w1_selected_scenes, _w1_scene_index, _w1_temp_poll_id
    global _slice_depth_um

    # Cancel any leftover conversion poll from a previous window1.
    _stop_w1_temp_polling()

    for widget in root.winfo_children():
        widget.destroy()

    _w1_preview_photo = None
    _w1_selected_stem = None
    _w1_selected_scenes = []
    _w1_scene_index = 0
    _w1_source_var = tk.IntVar(value=1)

    # Full-width bottom bar (packed FIRST so Tk reserves the space before the
    # expanding content fills the rest). Mirrors window 2's button_frame so the
    # 'Next' button sits at the extreme right of the whole window, consistent
    # with the rest of the app.
    footer = tk.Frame(root, bg=BG_COLOR, height=60)
    footer.pack(fill=tk.X, side=tk.BOTTOM)
    footer.pack_propagate(False)

    main = tk.Frame(root, bg=BG_COLOR)
    main.pack(fill=tk.BOTH, expand=True)
    main.columnconfigure(0, weight=1, uniform="w1")   # left 1/3
    main.columnconfigure(1, weight=2, uniform="w1")   # right 2/3
    main.rowconfigure(0, weight=1)

    # --- Left 1/3: checkboxes + name list ---
    left = tk.Frame(main, bg=BG_COLOR)
    left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(3, weight=1)   # name list expands

    cb_frame = tk.Frame(left, bg=BG_COLOR)
    cb_frame.grid(row=0, column=0, sticky="ew")
    tk.Checkbutton(
        cb_frame, text=".czi dans le dossier Input", font=SMALL_FONT,
        variable=_w1_source_var, onvalue=1, command=_select_input_folder, bg=BG_COLOR,
    ).pack(anchor="w")
    tk.Checkbutton(
        cb_frame, text=".czi dans un autre dossier", font=SMALL_FONT,
        variable=_w1_source_var, onvalue=2, command=_select_other_folder, bg=BG_COLOR,
    ).pack(anchor="w")

    ttk.Separator(left, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=6)

    # Slice depth (µm) — physical thickness of the brain slice, used by the
    # Window 4 volumetric export to convert cell counts into concentrations
    # (cells per mm³ = num_cells / (depth × surface_area)).
    depth_frame = tk.Frame(left, bg=BG_COLOR)
    depth_frame.grid(row=2, column=0, sticky="ew", pady=(2, 4))
    tk.Label(
        depth_frame, text="Slice depth (µm):", font=SMALL_FONT,
        bg=BG_COLOR, fg=FG_COLOR,
    ).pack(side=tk.LEFT, padx=(0, 6))

    def _on_depth_validate():
        """Persist the slice depth from the Entry into _slice_depth_um."""
        global _slice_depth_um
        if _w1_depth_entry is None:
            return
        try:
            val = float(_w1_depth_entry.get().replace(",", "."))
            if val > 0:
                _slice_depth_um = val
        except ValueError:
            pass  # keep the previous valid value

    _w1_depth_entry = tk.Entry(depth_frame, font=SMALL_FONT, width=8, justify="center")
    _w1_depth_entry.insert(0, str(_slice_depth_um))
    _w1_depth_entry.pack(side=tk.LEFT, padx=(0, 4))
    _w1_depth_entry.bind("<FocusOut>", lambda e: _on_depth_validate())
    _w1_depth_entry.bind("<Return>", lambda e: _on_depth_validate())

    # Scrollable name list (Canvas + inner Frame + Scrollbar).
    _w1_name_canvas = tk.Canvas(left, bg="white", highlightthickness=0)
    name_scroll = tk.Scrollbar(left, orient="vertical", command=_w1_name_canvas.yview)
    _w1_name_canvas.configure(yscrollcommand=name_scroll.set)
    _w1_name_canvas.grid(row=3, column=0, sticky="nsew")
    name_scroll.grid(row=3, column=1, sticky="ns")

    _w1_name_frame = tk.Frame(_w1_name_canvas, bg="white")
    _w1_name_canvas.create_window((0, 0), window=_w1_name_frame, anchor="nw")

    def _update_name_scrollregion(_event=None):
        if _w1_name_canvas is not None:
            _w1_name_canvas.configure(scrollregion=_w1_name_canvas.bbox("all"))

    _w1_name_frame.bind("<Configure>", _update_name_scrollregion)

    # --- Right 2/3: preview + navigation ---
    right = tk.Frame(main, bg=BG_COLOR)
    right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(0, weight=1)

    _w1_preview_label = tk.Label(
        right, text="Sélectionnez un .czi", font=FONT, bg="white", fg="gray"
    )
    _w1_preview_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

    _w1_preview_status = tk.Label(right, text="", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR)
    _w1_preview_status.grid(row=1, column=0, sticky="ew")

    nav = tk.Frame(right, bg=BG_COLOR)
    nav.grid(row=2, column=0, pady=6)
    tk.Button(
        nav, text="Précédant", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=_w1_prev_scene
    ).pack(side=tk.LEFT, padx=8)
    tk.Button(
        nav, text="Suivant", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=_w1_next_scene
    ).pack(side=tk.LEFT, padx=8)

    # 'Next' lives in the full-width footer (extreme right), matching window 2/3.
    tk.Button(
        footer, text="Next", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
        command=lambda: window2_transition(czi_folder_path),
    ).pack(side=tk.RIGHT, padx=20, pady=12)

    # Re-render the preview on window resize (mirrors window 2's <Configure> usage).
    try:
        root.unbind("<Configure>")
    except tk.TclError:
        pass
    root.bind("<Configure>", _on_w1_configure)

    root.update_idletasks()

    # Build the name list from the current input folder, then kick off the 50x
    # conversion in a background thread (non-blocking).
    _rebuild_w1_name_list()
    os.makedirs(TEMP_VIZU_DIR, exist_ok=True)
    threading.Thread(
        target=_convert_all_czi_to_temp_vizu, args=(czi_folder_path,), daemon=True
    ).start()

def window2_transition(czi_folder_path):
    # Tkinter widgets MUST be created and managed on the main thread (Tk is not
    # thread-safe). Previously window2() ran in a worker thread, which caused a
    # race between widget destruction/recreation and <Configure> handlers running
    # on the main thread (KeyError 'bl'/'br' in _update_window2_images). So we
    # build the window on the main thread and keep ONLY the heavy .czi -> jpeg
    # conversion in a background thread so it does not block the UI.
    window2()
    conversion_thread = threading.Thread(target=convert_czi_to_png, args=(czi_folder_path,))
    conversion_thread.start()
    quantification_conversion_thread = threading.Thread(
        target=convert_czi_to_quantification_jpeg, args=(czi_folder_path,)
    )
    quantification_conversion_thread.start()

def _convert_czi_folder_to_jpeg(czi_folder_path, downsample, quality=95, log_prefix="convert_czi"):
    """Shared background converter used by window 2 and window 3 pipelines."""
    input_dir = Path(czi_folder_path)
    output_dir = Path(BASE_OUTPUT_DIR)

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[{log_prefix}] Input folder not found: {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    czi_files = list(convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True))
    if not czi_files:
        print(f"[{log_prefix}] No .czi files found in: {input_dir}")
        return

    converted = 0
    failed = 0
    for czi_path in czi_files:
        try:
            out_paths = convert_czi_to_jpeg.convert_one_file(
                czi_path=czi_path,
                input_dir=input_dir,
                output_dir=output_dir,
                downsample=downsample,
                quality=quality,
                recursive=True,
            )
            converted += len(out_paths)
            for out_path in out_paths:
                print(f"[{log_prefix}][OK] {czi_path} -> {out_path}")
        except Exception as exc:
            failed += 1
            print(f"[{log_prefix}][ERROR] {czi_path}: {exc}")

    print(
        f"[{log_prefix}] Done: {converted} image(s) created, {failed} failed. "
        f"Output: {output_dir / f'downsampled{downsample}_jpeg'}"
    )


def convert_czi_to_png(czi_folder_path):
    """
    Convert all .czi files in `czi_folder_path` to downsampled JPEG images
    (factor 4) by reusing the logic from `convert_czi_to_jpeg.py`.

    Each scene of a .czi is exported in its own folder, and every Z plane becomes
    a separate .jpeg. See convert_czi_to_jpeg.convert_one_file for the output
    structure.

    NOTE: the name mentions 'png' for historical reasons, but files are written
    as .jpeg (matching convert_czi_to_jpeg.py).

    This runs in a worker thread (see window2_transition), so only print() is
    used for feedback — Tkinter is not thread-safe. Output goes to BASE_OUTPUT_DIR.
    The downsample factor MUST match DOWNSAMPLE_FACTOR so the generated
    downsampled<N>_jpeg folder is the same one the ROI scanner watches.
    """
    _convert_czi_folder_to_jpeg(
        czi_folder_path,
        downsample=DOWNSAMPLE_FACTOR,
        quality=95,
        log_prefix="convert_czi_to_png",
    )


def convert_czi_to_quantification_jpeg(czi_folder_path):
    """
    Convert all .czi files to 4x downsampled JPEGs for window 3 quantification.

    This does not replace the 20x conversion used by window 2; it creates an
    independent ./output/downsampled4_jpeg tree consumed only by the wrapper.
    """
    global _quantification_conversion_running
    _quantification_conversion_running = True
    try:
        _convert_czi_folder_to_jpeg(
            czi_folder_path,
            downsample=QUANTIFICATION_DOWNSAMPLE,
            quality=95,
            log_prefix="convert_czi_to_quantification_jpeg",
        )
    finally:
        _quantification_conversion_running = False

# --- Window 2 (the main image management window) ---
# Keep references to PhotoImage objects to prevent garbage collection
_window2_images: dict = {}

# Global references for the top-left slider and its value label
_tl_scale = None
_tl_value_label = None
_window2_labels = {}
_current_atlas_depth = 0
_current_coronal_path = None
_current_atlas_path = None
_pending_atlas_update_id = None

# --- Marker placement state (window 2) ---
_marker_active = False                      # toggle: are the images clickable?
_marker_points = {"tl": [], "tr": []}       # normalized (x, y) in [0,1] per image
_marker_order = []                          # global click order of "tl"/"tr" (for undo)
_marker_buttons = {}                        # refs to marker buttons (color toggling)
_br_result_path = None                      # custom image shown in BR after replacement

# --- Zoom / pan state (window 2) ---
# Each quadrant keeps its own zoom factor and center (cx, cy in [0,1] of the
# source image). _window2_viewports stores the visible normalized rectangle
# actually rendered last frame, used by click handling to map screen pixels
# back to source-normalized coordinates.
_zoom_state: dict = {
    k: {"zoom": 1.0, "cx": 0.5, "cy": 0.5} for k in ("tl", "tr", "bl", "br")
}
_window2_viewports: dict = {
    k: (0.0, 0.0, 1.0, 1.0) for k in ("tl", "tr", "bl", "br")
}
# Transient middle-button drag state for panning a zoomed image.
_pan_state: dict = {}
# Source-image cache per quadrant to avoid re-opening the same file on every
# wheel tick. Keyed by (key, source_path).
_window2_source_cache: dict = {}
_WINDOW2_ZOOM_MIN = 1.0
_WINDOW2_ZOOM_MAX = 20.0
_WINDOW2_ZOOM_STEP = 1.25   # multiplier per wheel notch

# --- ROI / multi-.czi validation state (window 2) ---
# _roi_items: list of dicts. Each describes one ROI (scene) to process, using
# only the FIRST z-slice of its stack. See _build_roi_work_items() for fields.
_roi_items = []
_roi_index = -1                 # index currently displayed in _roi_items (-1 = none)
_current_histology_path = None  # absolute path of the ROI jpeg shown in top-right
_roi_poll_id = None             # root.after id for the initial/awaiting ROI polling
_awaiting_next_roi = False      # True while waiting for conversion to produce the next ROI


def _make_tl_value_editable(event):
    """Turn the TL value label into an Entry on double-click. Replaces the label in-place."""
    global _tl_value_label
    label = event.widget
    current_val = label.cget("text")
    parent = label.master

    # Remember label's grid/pack info so we can restore it later
    pack_info = label.pack_info()

    # Destroy the label and create an Entry in its place
    label.destroy()

    entry = tk.Entry(parent, font=SMALL_FONT, width=6, justify="center")
    entry.insert(0, current_val)
    entry.select_range(0, tk.END)
    entry.focus_set()

    # Pack the entry with the same settings the label had
    entry.pack(pack_info)

    def finish_edit(event_confirm=None):
        global _tl_value_label
        if _tl_scale is None:
            _restore_tl_label(parent, current_val, pack_info)
            return

        try:
            new_val = int(entry.get())
            # Clamp to scale range
            if new_val < _tl_scale["from"]:
                new_val = _tl_scale["from"]
            if new_val > _tl_scale["to"]:
                new_val = _tl_scale["to"]
            _tl_scale.set(new_val)
            _restore_tl_label(parent, str(new_val), pack_info)
            _schedule_atlas_images_update(new_val)
        except ValueError:
            _restore_tl_label(parent, current_val, pack_info)

    def cancel_edit(event_escape):
        _restore_tl_label(parent, current_val, pack_info)

    entry.bind("<Return>", finish_edit)
    entry.bind("<FocusOut>", finish_edit)
    entry.bind("<Escape>", cancel_edit)


def _restore_tl_label(parent, text, pack_info):
    """Destroy the active Entry and re-create the value Label."""
    global _tl_value_label
    # Destroy any Entry children in the parent
    for child in parent.winfo_children():
        if isinstance(child, tk.Entry):
            child.destroy()

    new_label = tk.Label(
        parent, text=text, font=SMALL_FONT,
        bg="white", fg=FG_COLOR, width=5, relief="sunken", cursor="hand2"
    )
    new_label.pack(pack_info)
    new_label.bind("<Double-Button-1>", _make_tl_value_editable)
    _tl_value_label = new_label


def _on_tl_scale_changed(val):
    """Update the value label and schedule atlas image refresh when the slider moves."""
    global _tl_value_label
    depth = int(float(val))
    if _tl_value_label is not None:
        _tl_value_label.config(text=str(depth))
    _schedule_atlas_images_update(depth)


def _schedule_atlas_images_update(depth):
    """
    Debounce atlas loading/generation so dragging the slider does not generate
    every intermediate depth permanently.
    """
    global _pending_atlas_update_id
    if _pending_atlas_update_id is not None:
        root.after_cancel(_pending_atlas_update_id)
    _pending_atlas_update_id = root.after(250, lambda: _load_atlas_images_for_depth(depth))


def _load_atlas_images_for_depth(depth):
    """
    Load existing atlas images for depth from ./AtlasImgs, or generate them once
    through atlas_position_getter if they do not exist yet.
    """
    global _current_atlas_depth, _current_coronal_path, _current_atlas_path, _pending_atlas_update_id
    _pending_atlas_update_id = None
    depth = int(depth)

    try:
        coronal_path, atlas_path = get_or_create_slice_images(depth)
    except Exception as e:
        print(f"Error loading/generating atlas images for depth {depth}: {e}")
        messagebox.showerror("Atlas image error", f"Could not load/generate atlas images for depth {depth}:\n{e}")
        return

    _current_atlas_depth = depth
    _current_coronal_path = coronal_path
    _current_atlas_path = atlas_path
    _update_window2_images()


def _get_quadrant_sizes():
    """Compute available pixel sizes for each quadrant from the current window size."""
    win_w = root.winfo_width()
    win_h = root.winfo_height()
    grid_avail_h = win_h - 80  # 60 for buttons + 20 bottom padding
    if grid_avail_h < 100:
        grid_avail_h = 100
    if win_w < 200:
        win_w = 200

    # Each quadrant gets half the available width/height minus internal padding
    quad_w = (win_w - 40) // 2
    quad_h = (grid_avail_h - 40) // 2
    if quad_w < 20:
        quad_w = 20
    if quad_h < 20:
        quad_h = 20

    # For the top-left quadrant, reserve ~35px at the bottom for the slider + number
    tl_w = quad_w
    tl_h = quad_h - 35
    if tl_h < 20:
        tl_h = 20
    return {"quad_w": quad_w, "quad_h": quad_h, "tl_w": tl_w, "tl_h": tl_h}


def _reset_zoom(key=None):
    """Reset zoom/center for one quadrant (key) or all four (key=None)."""
    keys = (key,) if key is not None else ("tl", "tr", "bl", "br")
    for k in keys:
        _zoom_state[k] = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
        _window2_viewports[k] = (0.0, 0.0, 1.0, 1.0)
    _window2_source_cache.clear()


def _zoom_viewport(key):
    """
    Compute the visible normalized rectangle (nx0, ny0, nx1, ny1) for quadrant
    `key` from its zoom factor + center. At zoom=1 the whole image is visible.
    """
    st = _zoom_state[key]
    zoom = max(1.0, st["zoom"])
    half = 0.5 / zoom
    cx, cy = st["cx"], st["cy"]
    nx0 = max(0.0, cx - half)
    ny0 = max(0.0, cy - half)
    nx1 = min(1.0, cx + half)
    ny1 = min(1.0, cy + half)
    # If clamping shrank one side, re-center so the full zoom span is used when
    # possible (avoids a "stuck" view at the image border).
    if (nx1 - nx0) < 2 * half and (nx1 - nx0) < 1.0:
        cx = (nx0 + nx1) / 2
        nx0 = max(0.0, cx - half)
        nx1 = min(1.0, cx + half)
        _zoom_state[key]["cx"] = (nx0 + nx1) / 2
    if (ny1 - ny0) < 2 * half and (ny1 - ny0) < 1.0:
        cy = (ny0 + ny1) / 2
        ny0 = max(0.0, cy - half)
        ny1 = min(1.0, cy + half)
        _zoom_state[key]["cy"] = (ny0 + ny1) / 2
    return (nx0, ny0, nx1, ny1)


def _get_window2_source(key, file_path):
    """
    Open (and cache) the full-resolution source image for quadrant `key`.
    The cache is keyed by path so repeated wheel ticks don't re-open the file.

    The file's modification time (mtime) is stored alongside the image and
    checked on every lookup: if the file has been regenerated on disk (e.g. a
    new mask written to the same path while the quadrant is zoomed), the stale
    cache entry is discarded and the image reloaded so the quadrant stays in
    sync with the latest content.
    Returns a PIL image in its native mode, or None.
    """
    full_path = file_path if os.path.isabs(file_path) else os.path.join(BASE_DIR, file_path)
    cache_key = (key, full_path)
    try:
        current_mtime = os.path.getmtime(full_path)
    except OSError:
        # File may be missing or temporarily locked (Windows). Assume no cache
        # change is detectable; callers will handle a read failure below.
        current_mtime = None
    cached = _window2_source_cache.get(cache_key)
    if cached is not None:
        cached_img, cached_mtime = cached
        if current_mtime is None or cached_mtime == current_mtime:
            return cached_img
    try:
        img = Image.open(full_path)
        # Force load so the file handle can be closed implicitly.
        img.load()
    except Exception as e:
        print(f"Error loading image '{file_path}': {e}")
        return None
    # Keep the cache small (one entry per quadrant key is plenty).
    for ck in list(_window2_source_cache.keys()):
        if ck[0] == key and ck != cache_key:
            _window2_source_cache.pop(ck, None)
    _window2_source_cache[cache_key] = (img, current_mtime)
    return img


def _load_zoomed_pil(file_path, max_width, max_height, key):
    """
    Open the full source for quadrant `key`, crop to its visible normalized
    rectangle, resize to fit (max_width, max_height) preserving aspect, and
    return (pil_rgba, viewport) or (None, None) on failure.
    """
    src = _get_window2_source(key, file_path)
    if src is None:
        return None, None
    sw, sh = src.size
    if sw <= 0 or sh <= 0:
        return None, None

    viewport = _zoom_viewport(key)
    _window2_viewports[key] = viewport
    nx0, ny0, nx1, ny1 = viewport

    left = int(round(nx0 * sw))
    upper = int(round(ny0 * sh))
    right = max(left + 1, int(round(nx1 * sw)))
    lower = max(upper + 1, int(round(ny1 * sh)))
    cropped = src.crop((left, upper, right, lower))

    new_w, new_h = get_img_dims(cropped.width, cropped.height, max_width, max_height)
    cropped = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if cropped.mode != "RGBA":
        cropped = cropped.convert("RGBA")
    return cropped, viewport


def _draw_markers_zoomed(pil_img, points, color, viewport):
    """
    Draw numbered markers for the points that fall inside `viewport` onto
    `pil_img`. Points are normalized to the SOURCE image; viewport is the
    (nx0, ny0, nx1, ny1) rectangle currently shown. Marker numbers keep the
    original index in the full point list (so zoomed numbering stays consistent).
    """
    if not points:
        return pil_img
    nx0, ny0, nx1, ny1 = viewport
    span_x = nx1 - nx0
    span_y = ny1 - ny0
    if span_x <= 0 or span_y <= 0:
        return pil_img
    img = pil_img.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    radius = max(4, min(w, h) // 35)
    font_size = max(9, int(radius * 1.1))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    for idx, (nx, ny) in enumerate(points):
        vx = (nx - nx0) / span_x
        vy = (ny - ny0) / span_y
        if vx < -0.02 or vx > 1.02 or vy < -0.02 or vy > 1.02:
            continue  # outside the visible viewport
        cx, cy = int(vx * w), int(vy * h)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color, outline="white",
        )
        text = str(idx + 1)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw // 2, cy - th // 2), text, fill="white", font=font)
    return img


def _update_window2_images():
    """
    Recalculate image sizes based on current window dimensions and update the labels.
    Markers are redrawn from their normalized coordinates so they survive resizes.
    The top-left quadrant reserves space at the bottom for the slider + number.

    Defensive: <Configure> events can fire while window2() is rebuilding the
    layout (widgets destroyed then recreated), so each quadrant is updated only
    if its label still exists. If the dict is empty we bail out entirely.
    """
    if not _window2_labels:
        return

    sizes = _get_quadrant_sizes()
    quad_w, quad_h = sizes["quad_w"], sizes["quad_h"]
    tl_w, tl_h = sizes["tl_w"], sizes["tl_h"]

    # Top-left: MRI slice (where the user clicks) with markers drawn on top.
    # The zoom viewport is applied so wheel-zoom/pan are reflected here.
    if "tl" in _window2_labels:
        tl_source = _current_coronal_path or _resolve_image_path("MRI.png")
        pil, vp = _load_zoomed_pil(tl_source, tl_w, tl_h, "tl")
        if pil is not None:
            pil = _draw_markers_zoomed(pil, _marker_points.get("tl", []), ACCENT_COLOR_BLUE, vp)
            photo_tl = ImageTk.PhotoImage(pil)
            _window2_images["tl"] = photo_tl
            _window2_labels["tl"].configure(image=photo_tl)

    # Top-right: Histology (where the user clicks) with markers drawn on top.
    # When a ROI is loaded we display its first z-slice; otherwise fall back to
    # the static Histo.png asset. Zoom viewport is applied as for TL.
    if "tr" in _window2_labels:
        tr_source = _current_histology_path or _resolve_image_path("Histo.png")
        pil, vp = _load_zoomed_pil(tr_source, quad_w, quad_h, "tr")
        if pil is not None:
            pil = _draw_markers_zoomed(pil, _marker_points.get("tr", []), ACCENT_COLOR_GREEN, vp)
            photo_tr = ImageTk.PhotoImage(pil)
            _window2_images["tr"] = photo_tr
            _window2_labels["tr"].configure(image=photo_tr)

    # Bottom-left: atlas/mask slice (no markers). Zoom viewport is applied so
    # the user can inspect atlas details without affecting marker placement.
    if "bl" in _window2_labels:
        bl_source = _current_atlas_path or _resolve_image_path("ATLAS.png")
        pil, _vp = _load_zoomed_pil(bl_source, quad_w, quad_h, "bl")
        if pil is not None:
            photo_bl = ImageTk.PhotoImage(pil)
            _window2_images["bl"] = photo_bl
            _window2_labels["bl"].configure(image=photo_bl)

    # Bottom-right: result image. Use the replacement result if one was produced.
    if "br" in _window2_labels:
        br_source = _br_result_path or _resolve_image_path("Alignment.png")
        pil, _vp = _load_zoomed_pil(br_source, quad_w, quad_h, "br")
        if pil is not None:
            photo_br = ImageTk.PhotoImage(pil)
            _window2_images["br"] = photo_br
            _window2_labels["br"].configure(image=photo_br)


def _displayed_image_size(key):
    """Return (width, height) of the currently displayed PhotoImage for a quadrant."""
    photo = _window2_images.get(key)
    if photo is None:
        return None
    try:
        return photo.width(), photo.height()
    except Exception:
        return None


def _on_image_click(key, event):
    """
    Record a normalized marker point on quadrant `key` when marker mode is active.
    Coordinates are normalized relative to the displayed (resized) image, ignoring
    clicks that land in the letterboxed area around the image.
    """
    if not _marker_active:
        return
    label = _window2_labels.get(key)
    if label is None:
        return
    disp = _displayed_image_size(key)
    if disp is None:
        return
    img_w, img_h = disp
    if img_w <= 1 or img_h <= 1:
        return
    label_w = label.winfo_width()
    label_h = label.winfo_height()
    # The image is centered inside the label widget -> compute the offset.
    offset_x = (label_w - img_w) // 2
    offset_y = (label_h - img_h) // 2
    px = event.x - offset_x
    py = event.y - offset_y
    if px < 0 or py < 0 or px >= img_w or py >= img_h:
        return  # click landed in the letterboxed area, ignore it
    # Map screen pixels -> viewport-relative normalized coords, then back to
    # full-source-image normalized coords so the stored marker is correct even
    # when the quadrant is zoomed/panned.
    nx_view = px / img_w
    ny_view = py / img_h
    nx0, ny0, nx1, ny1 = _window2_viewports.get(key, (0.0, 0.0, 1.0, 1.0))
    nx = nx0 + nx_view * (nx1 - nx0)
    ny = ny0 + ny_view * (ny1 - ny0)
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))
    _marker_points[key].append((nx, ny))
    _marker_order.append(key)
    _update_window2_images()


def _on_tl_image_click(event):
    _on_image_click("tl", event)


def _on_tr_image_click(event):
    _on_image_click("tr", event)


# --- Mouse-wheel zoom + middle-button pan (window 2) ---

def _on_image_wheel(key, event):
    """
    Zoom quadrant `key` with the mouse wheel, keeping the source point under the
    cursor fixed. Windows uses <MouseWheel> with event.delta (multiples of 120);
    Linux/Mac use <Button-4>/<Button-5>.
    """
    # Determine zoom direction (in/out) from either event style.
    if event.num in (4, 5):  # Linux/Mac
        direction = 1 if event.num == 4 else -1
    else:  # Windows <MouseWheel>
        direction = 1 if (event.delta or 0) > 0 else -1
    if direction == 0:
        return

    st = _zoom_state.get(key)
    if st is None:
        return
    old_zoom = st["zoom"]
    if direction > 0:
        new_zoom = old_zoom * _WINDOW2_ZOOM_STEP
    else:
        new_zoom = old_zoom / _WINDOW2_ZOOM_STEP
    new_zoom = min(_WINDOW2_ZOOM_MAX, max(_WINDOW2_ZOOM_MIN, new_zoom))
    if abs(new_zoom - old_zoom) < 1e-6:
        return  # already at the clamp boundary

    # Source-normalized point currently under the cursor (so it stays put).
    src_nx, src_ny = _screen_to_source_normalized(key, event)
    if src_nx is None:
        st["zoom"] = new_zoom
        _update_window2_images()
        return

    st["zoom"] = new_zoom
    # Recenter so the same source point stays under the cursor. With a viewport
    # half-extent h = 0.5/zoom, the center must satisfy cx = src_nx + (0.5 - fv)*h
    # where fv is the cursor's fractional position within the viewport. Using the
    # pre-zoom viewport keeps the math stable.
    nx0, ny0, nx1, ny1 = _window2_viewports.get(key, (0.0, 0.0, 1.0, 1.0))
    span_x = max(1e-6, nx1 - nx0)
    span_y = max(1e-6, ny1 - ny0)
    fx = (src_nx - nx0) / span_x  # fractional cursor x in [0,1] of old viewport
    fy = (src_ny - ny0) / span_y
    half_new = 0.5 / new_zoom
    cx = src_nx + (0.5 - fx) * half_new * 2
    cy = src_ny + (0.5 - fy) * half_new * 2
    st["cx"] = min(1.0, max(0.0, cx))
    st["cy"] = min(1.0, max(0.0, cy))
    _update_window2_images()


def _screen_to_source_normalized(key, event):
    """
    Map a widget-relative (event.x, event.y) to source-image normalized coords,
    accounting for letterboxing and the current zoom viewport. Returns (nx, ny)
    or (None, None) if the cursor is outside the image.
    """
    label = _window2_labels.get(key)
    disp = _displayed_image_size(key)
    if label is None or disp is None:
        return None, None
    img_w, img_h = disp
    if img_w <= 1 or img_h <= 1:
        return None, None
    label_w = label.winfo_width()
    label_h = label.winfo_height()
    offset_x = (label_w - img_w) // 2
    offset_y = (label_h - img_h) // 2
    px = event.x - offset_x
    py = event.y - offset_y
    if px < 0 or py < 0 or px >= img_w or py >= img_h:
        return None, None
    nx_view = px / img_w
    ny_view = py / img_h
    nx0, ny0, nx1, ny1 = _window2_viewports.get(key, (0.0, 0.0, 1.0, 1.0))
    nx = nx0 + nx_view * (nx1 - nx0)
    ny = ny0 + ny_view * (ny1 - ny0)
    return nx, ny


def _on_image_pan_start(key, event):
    """Begin a middle-button drag pan for quadrant `key`."""
    _pan_state["key"] = key
    _pan_state["start_x"] = event.x
    _pan_state["start_y"] = event.y
    _pan_state["start_cx"] = _zoom_state[key]["cx"]
    _pan_state["start_cy"] = _zoom_state[key]["cy"]
    _pan_state["viewport"] = _window2_viewports.get(key, (0.0, 0.0, 1.0, 1.0))


def _on_image_pan_motion(event):
    """Update the center while dragging with the middle button."""
    key = _pan_state.get("key")
    if key is None:
        return
    label = _window2_labels.get(key)
    disp = _displayed_image_size(key)
    if label is None or disp is None:
        return
    img_w, img_h = disp
    if img_w <= 1 or img_h <= 1:
        return
    dx = event.x - _pan_state.get("start_x", 0)
    dy = event.y - _pan_state.get("start_y", 0)
    # Convert pixel delta to source-normalized delta via the viewport span.
    nx0, ny0, nx1, ny1 = _pan_state.get("viewport", (0.0, 0.0, 1.0, 1.0))
    span_x = max(1e-6, nx1 - nx0)
    span_y = max(1e-6, ny1 - ny0)
    dnx = -(dx / img_w) * span_x
    dny = -(dy / img_h) * span_y
    _zoom_state[key]["cx"] = min(1.0, max(0.0, _pan_state["start_cx"] + dnx))
    _zoom_state[key]["cy"] = min(1.0, max(0.0, _pan_state["start_cy"] + dny))
    _update_window2_images()


def _on_image_pan_end(_event=None):
    """Clear the active pan state."""
    _pan_state.clear()


def _reset_all_zoom():
    """Button callback: reset zoom/pan for all four quadrants."""
    _reset_zoom()
    _update_window2_images()


def _toggle_marker_mode():
    """Toggle marker placement on/off. The button turns green when active."""
    global _marker_active
    _marker_active = not _marker_active
    btn = _marker_buttons.get("place")
    if btn is not None:
        btn.config(bg=ACCENT_COLOR_GREEN if _marker_active else ACCENT_COLOR_BLUE)
    cursor = "crosshair" if _marker_active else ""
    for key in ("tl", "tr"):
        lbl = _window2_labels.get(key)
        if lbl is not None:
            lbl.config(cursor=cursor)


def _undo_last_point():
    """Remove the most recently placed marker (on whichever image it was added)."""
    if not _marker_order:
        return
    key = _marker_order.pop()
    if _marker_points[key]:
        _marker_points[key].pop()
    _update_window2_images()


def _replace_mask():
    """
    Send the current depth + marker pairs (normalized coordinates) to the mask
    replacement script and display the returned image in the bottom-right quadrant.
    Pairs are formed by click order: n-th TL point <-> n-th TR point.
    """
    global _br_result_path
    tl_pts = _marker_points["tl"]
    tr_pts = _marker_points["tr"]
    n = min(len(tl_pts), len(tr_pts))
    pairs = [(tl_pts[i], tr_pts[i]) for i in range(n)]
    if n < 2:
        messagebox.showwarning("Points insuffisants", "Sélectionner au moins 2 points")
        return
    try:
        out_path = replace_mask(_current_atlas_depth, pairs, histo_path=_current_histology_path)
    except Exception as e:
        messagebox.showerror("Mask replacement error", str(e))
        return
    if out_path:
        _br_result_path = out_path
        _update_window2_images()


# --- ROI list management (multi-.czi workflow) ---

# Z-slice filename pattern produced by convert_czi_to_jpeg.convert_one_file:
#   <stem>_z_slice_<n>.jpeg
_Z_SLICE_RE = re.compile(r"_z_slice_(\d+)\.jpeg$", re.IGNORECASE)


def _first_z_image(roi_dir):
    """Return the Path of the first (lowest-numbered) z-slice jpeg in `roi_dir`, or None."""
    candidates = []
    for f in roi_dir.glob("*_z_slice_*.jpeg"):
        m = _Z_SLICE_RE.search(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def _fallback_czi_stem(roi_name):
    """Strip the trailing '_<scene>' integer suffix from an ROI folder name."""
    m = re.match(r"^(.*)_(\d+)$", roi_name)
    return m.group(1) if m else roi_name


def _match_czi_stem(roi_name, czi_stems):
    """
    Match an ROI folder name (`<czi_stem>_<scene+1>`) to its .czi stem.
    `czi_stems` must be sorted longest-first to avoid ambiguous short prefixes.
    Falls back to a regex split if no input .czi matches.
    """
    for stem in czi_stems:
        prefix = stem + "_"
        if roi_name.startswith(prefix) and roi_name[len(prefix):].isdigit():
            return stem
    return _fallback_czi_stem(roi_name)


def _build_roi_work_items():
    """
    Scan the JPEG output tree and build one work item per ROI (scene), using only
    the FIRST z-slice of each stack. Items are ordered by folder name (i.e. by
    .czi stem then scene index).

    Each item dict contains:
        czi_stem          : stem of the source .czi (used for the WIP subfolder)
        roi_folder_name   : exact scene folder name (e.g. '20231121__1721_2')
        roi_folder_path   : Path of that scene folder
        image_path        : Path of the first z-slice jpeg to display
        mask_dir          : ./WorkInProgress/<czi_stem>/masks
        mask_png          : mask_dir / '<roi_folder_name>.png'        (mask alone)
        mask_overlay_png  : mask_dir / '<roi_folder_name>_overlay.png' (histo+mask)
        mask_txt          : mask_dir / '<roi_folder_name>.txt'        (marker coords)
    """
    output_base = Path(BASE_OUTPUT_DIR) / JPEG_OUTPUT_SUBDIR
    if not output_base.is_dir():
        return []

    input_dir = Path(czi_folder_path)
    try:
        czi_stems = sorted(
            (p.stem for p in convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True)),
            key=len, reverse=True,
        )
    except Exception:
        czi_stems = []

    wip_base = Path(WORK_IN_PROGRESS_DIR)
    items = []
    for roi_dir in sorted(output_base.iterdir()):
        if not roi_dir.is_dir():
            continue
        first_img = _first_z_image(roi_dir)
        if first_img is None:
            continue
        roi_name = roi_dir.name
        czi_stem = _match_czi_stem(roi_name, czi_stems)
        mask_dir = wip_base / czi_stem / "masks"
        items.append({
            "czi_stem": czi_stem,
            "roi_folder_name": roi_name,
            "roi_folder_path": roi_dir,
            "image_path": first_img,
            "mask_dir": mask_dir,
            "mask_png": mask_dir / f"{roi_name}.png",
            "mask_overlay_png": mask_dir / f"{roi_name}_overlay.png",
            "mask_txt": mask_dir / f"{roi_name}.txt",
        })
    return items


def _refresh_roi_list():
    """
    Re-scan the JPEG output tree (the 'file browser') and update _roi_items.
    Keeps the currently displayed ROI selected if still present.

    Because czi->jpeg conversion runs in a separate thread, new ROIs may appear
    over time; call this before navigating to the 'next' ROI so none are skipped.
    """
    global _roi_items, _roi_index
    prev_name = None
    if 0 <= _roi_index < len(_roi_items):
        prev_name = _roi_items[_roi_index].get("roi_folder_name")

    _roi_items = _build_roi_work_items()

    if prev_name is not None:
        for i, item in enumerate(_roi_items):
            if item["roi_folder_name"] == prev_name:
                _roi_index = i
                return
    _roi_index = 0 if _roi_items else -1


def _load_current_roi():
    """Display the ROI at _roi_index (image only; markers are managed separately)."""
    global _current_histology_path, _br_result_path
    _br_result_path = None
    if 0 <= _roi_index < len(_roi_items):
        _current_histology_path = str(_roi_items[_roi_index]["image_path"])
    else:
        _current_histology_path = None
    # Reset the top-right (histology) and bottom-right (result) zoom so a new
    # ROI always starts from the full-image view. The atlas quadrants keep
    # their zoom because they depend on the depth slider, not the ROI.
    _reset_zoom("tr")
    _reset_zoom("br")
    _update_window2_images()


def _clear_markers():
    """Reset marker placement state (points, order, mode) without touching the ROI image."""
    global _marker_points, _marker_order, _marker_active
    _marker_points = {"tl": [], "tr": []}
    _marker_order = []
    _marker_active = False
    btn = _marker_buttons.get("place")
    if btn is not None:
        btn.config(bg=ACCENT_COLOR_BLUE)
    for key in ("tl", "tr"):
        lbl = _window2_labels.get(key)
        if lbl is not None:
            lbl.config(cursor="")


def _write_marker_txt(path, depth, tl_points, tr_points, order):
    """Save the atlas depth + normalized TL/TR marker coords (+ click order) to a .txt."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"depth={int(depth)}\n")
        f.write("order=" + ",".join(order) + "\n")
        f.write("tl_normalized=\n")
        for (nx, ny) in tl_points:
            f.write(f"{nx:.6f} {ny:.6f}\n")
        f.write("tr_normalized=\n")
        for (nx, ny) in tr_points:
            f.write(f"{nx:.6f} {ny:.6f}\n")


def _read_marker_txt(path):
    """
    Read a marker .txt written by _write_marker_txt.
    Returns (depth, {"tl": [...], "tr": [...]}, order) or (None, None, None) on failure.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return None, None, None
    depth = None
    section = None
    pts = {"tl": [], "tr": []}
    order = []
    for ln in lines:
        if ln.startswith("depth="):
            try:
                depth = int(ln.split("=", 1)[1])
            except ValueError:
                depth = None
        elif ln.startswith("order="):
            order = [s for s in ln.split("=", 1)[1].split(",") if s in ("tl", "tr")]
        elif ln.startswith("tl_normalized="):
            section = "tl"
        elif ln.startswith("tr_normalized="):
            section = "tr"
        elif section in pts:
            parts = ln.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    pts[section].append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
    return depth, pts, order


def _start_roi_polling():
    """Schedule the next ROI-list poll (used while conversion is still running)."""
    global _roi_poll_id
    if _roi_poll_id is not None:
        try:
            root.after_cancel(_roi_poll_id)
        except Exception:
            pass
    _roi_poll_id = root.after(1500, _poll_roi_once)


def _poll_roi_once():
    """Refresh the ROI list; auto-advance when the awaited next ROI appears."""
    global _roi_poll_id, _roi_index, _current_histology_path, _awaiting_next_roi
    _roi_poll_id = None
    _refresh_roi_list()
    if not _roi_items:
        _start_roi_polling()
        return
    if _awaiting_next_roi:
        if _roi_index + 1 < len(_roi_items):
            _roi_index += 1
            _awaiting_next_roi = False
            _clear_markers()
            _load_current_roi()
        else:
            _start_roi_polling()
    elif _current_histology_path is None:
        # First poll that finds ROIs: show the very first one.
        _roi_index = 0
        _clear_markers()
        _load_current_roi()


def _validate_current_roi():
    """
    'Valider la coupe':
      - Build landmark pairs from the current markers.
      - Save mask-only + overlay + a .txt of the normalized marker coords into
        ./WorkInProgress/<czi>/masks/<roi_folder>.{png,_overlay.png,txt}.
      - Refresh the ROI list (conversion runs in another thread -> new slices may
        have appeared) and advance to the next ROI, clearing markers.
    """
    global _br_result_path, _current_histology_path, _roi_index, _awaiting_next_roi
    if not (0 <= _roi_index < len(_roi_items)):
        messagebox.showwarning("Aucune coupe", "Aucune image ROI à valider.")
        return
    item = _roi_items[_roi_index]
    tl_pts = list(_marker_points["tl"])
    tr_pts = list(_marker_points["tr"])
    order = list(_marker_order)
    n = min(len(tl_pts), len(tr_pts))
    if n < 2:
        messagebox.showwarning("Points insuffisants", "Placer au moins 2 paires de marqueurs.")
        return

    pairs = [(tl_pts[i], tr_pts[i]) for i in range(n)]
    try:
        os.makedirs(item["mask_dir"], exist_ok=True)
        save_mask_pair(
            depth=_current_atlas_depth,
            normalized_points=pairs,
            overlay_path=str(item["mask_overlay_png"]),
            mask_only_path=str(item["mask_png"]),
            histo_path=str(item["image_path"]),
        )
    except Exception as e:
        messagebox.showerror("Erreur sauvegarde masque", str(e))
        return

    _write_marker_txt(str(item["mask_txt"]), _current_atlas_depth, tl_pts, tr_pts, order)

    # Refresh (new slices may have appeared) then advance to the next ROI.
    current_name = item["roi_folder_name"]
    _refresh_roi_list()
    next_index = len(_roi_items)
    for i, it in enumerate(_roi_items):
        if it["roi_folder_name"] == current_name:
            next_index = i + 1
            break

    if next_index >= len(_roi_items):
        # No more ROIs available right now (conversion may still be running).
        messagebox.showinfo(
            "Validation",
            "Coupe validée. Aucune autre coupe disponible pour le moment\n"
            "(la conversion .czi peut encore être en cours).",
        )
        _clear_markers()
        _br_result_path = str(item["mask_overlay_png"])
        _current_histology_path = str(item["image_path"])
        _awaiting_next_roi = True
        _update_window2_images()
        _start_roi_polling()
        return

    _roi_index = next_index
    _awaiting_next_roi = False
    _clear_markers()
    _load_current_roi()


def _cancel_last_validation():
    """
    'Annuler la validation':
      - Step the histology back to the PREVIOUS ROI.
      - Reload the dots at their saved positions so the user can edit them.
      - Delete that ROI's saved mask artifacts (true undo).
    """
    global _roi_index, _marker_points, _marker_order, _current_histology_path, _br_result_path, _awaiting_next_roi
    if _roi_index <= 0 or _roi_index >= len(_roi_items):
        messagebox.showinfo("Annulation", "Aucune validation précédente à annuler.")
        return

    _awaiting_next_roi = False
    _roi_index -= 1
    item = _roi_items[_roi_index]

    # Reload dots from the saved .txt (if present), then delete all artifacts.
    depth, pts, order = _read_marker_txt(str(item["mask_txt"]))
    _clear_markers()
    if pts is not None:
        _marker_points = {"tl": list(pts.get("tl", [])), "tr": list(pts.get("tr", []))}
        _marker_order = list(order) if order else []
        if depth is not None and _tl_scale is not None:
            try:
                _tl_scale.set(depth)
            except Exception:
                pass

    for p in (item["mask_png"], item["mask_overlay_png"], item["mask_txt"]):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f"[cancel] could not remove {p}: {e}")

    _current_histology_path = str(item["image_path"])
    _br_result_path = None
    _update_window2_images()


def window2():
    """
    Clear the current window and set up the second window with a 2×2 image grid.

    Layout:
        Top-left (Frame)   |  Top-right
          MRI image         |  Histology
          [scale ][number]  |
        -------------------------
        Bottom-left         |  Bottom-right
        Atlas               |  Alignment (overlay)

    Navigation buttons are placed below the grid.
    """
    global _window2_labels, _tl_scale, _tl_value_label, _current_coronal_path, _current_atlas_path, _pending_atlas_update_id
    global _marker_active, _marker_points, _marker_order, _marker_buttons, _br_result_path
    global _roi_items, _roi_index, _current_histology_path, _roi_poll_id, _awaiting_next_roi

    for widget in root.winfo_children():
        widget.destroy()

    # Outer container: fills the whole window
    outer = tk.Frame(root, bg=BG_COLOR)
    outer.pack(fill=tk.BOTH, expand=True)

    # Grid frame holds the 2×2 image layout
    grid_frame = tk.Frame(outer, bg=BG_COLOR)
    grid_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Configure columns and rows for expansion
    grid_frame.columnconfigure(0, weight=1, uniform="quad")
    grid_frame.columnconfigure(1, weight=1, uniform="quad")
    grid_frame.rowconfigure(0, weight=1, uniform="quad")
    grid_frame.rowconfigure(1, weight=1, uniform="quad")

    _window2_labels = {}
    _marker_active = False
    _marker_points = {"tl": [], "tr": []}
    _marker_order = []
    _marker_buttons = {}
    _br_result_path = None
    _current_coronal_path = None
    _current_atlas_path = None
    _roi_items = []
    _roi_index = -1
    _current_histology_path = None
    _awaiting_next_roi = False
    # Reset every quadrant's zoom/pan when (re)entering window 2 so a new
    # session always starts from the full-image view.
    _reset_zoom()
    if _roi_poll_id is not None:
        try:
            root.after_cancel(_roi_poll_id)
        except Exception:
            pass
        _roi_poll_id = None
    if _pending_atlas_update_id is not None:
        root.after_cancel(_pending_atlas_update_id)
        _pending_atlas_update_id = None

    # --- Top-left: a Frame containing the image + slider bar ---
    tl_frame = tk.Frame(grid_frame, bg=BG_COLOR)
    tl_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
    # The image fills most of the frame
    tl_image_label = tk.Label(tl_frame, text="MRI", font=FONT, bg="white", fg="gray")
    tl_image_label.pack(fill=tk.BOTH, expand=True)
    _window2_labels["tl"] = tl_image_label

    # Bottom bar inside the top-left frame: slider + number
    tl_control_bar = tk.Frame(tl_frame, bg=BG_COLOR, height=30)
    tl_control_bar.pack(fill=tk.X, side=tk.BOTTOM)
    tl_control_bar.pack_propagate(False)

    # Precise horizontal slider (Scale) — fills all available space.
    # Its range follows the valid coronal depth range from the NIfTI volume.
    try:
        min_depth, max_depth = get_depth_range()
    except Exception as e:
        print(f"Error reading atlas depth range: {e}")
        messagebox.showerror("Atlas depth error", f"Could not read atlas depth range:\n{e}")
        min_depth, max_depth = 0, 1000

    _tl_scale = tk.Scale(
        tl_control_bar, from_=min_depth, to=max_depth, orient="horizontal",
        font=SMALL_FONT, showvalue=False,
        command=_on_tl_scale_changed,
        bg=ACCENT_COLOR_BLUE, fg=FG_COLOR,
        highlightthickness=1, borderwidth=1,
        sliderrelief="flat"
    )
    _tl_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))

    # Number label (editable by double-click)
    _tl_value_label = tk.Label(
        tl_control_bar, text="0", font=SMALL_FONT,
        bg="white", fg=FG_COLOR, width=5, relief="sunken", cursor="hand2"
    )
    _tl_value_label.pack(side=tk.LEFT, padx=(0, 5))
    _tl_value_label.bind("<Double-Button-1>", _make_tl_value_editable)

    # --- Top-right ---
    tr_label = tk.Label(grid_frame, text="Histology", font=FONT, bg="white", fg="gray")
    tr_label.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
    _window2_labels["tr"] = tr_label

    # --- Bottom-left ---
    bl_label = tk.Label(grid_frame, text="Atlas", font=FONT, bg="white", fg="gray")
    bl_label.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
    _window2_labels["bl"] = bl_label

    # --- Bottom-right ---
    br_label = tk.Label(grid_frame, text="Alignment", font=FONT, bg="white", fg="gray")
    br_label.grid(row=1, column=1, padx=5, pady=5, sticky="nsew")
    _window2_labels["br"] = br_label

    # Click bindings for marker placement (effective only when marker mode is on).
    _window2_labels["tl"].bind("<Button-1>", _on_tl_image_click)
    _window2_labels["tr"].bind("<Button-1>", _on_tr_image_click)

    # Zoom (mouse wheel) + pan (middle-button drag) on all four quadrants. The
    # <MouseWheel> binding is platform-dependent: Windows uses <MouseWheel>
    # (event.delta), Linux/X11 uses <Button-4>/<Button-5>. Both are bound so the
    # feature works regardless of the platform.
    for _qkey in ("tl", "tr", "bl", "br"):
        _lbl = _window2_labels[_qkey]
        _lbl.bind("<MouseWheel>", lambda e, k=_qkey: _on_image_wheel(k, e))
        _lbl.bind("<Button-4>", lambda e, k=_qkey: _on_image_wheel(k, e))
        _lbl.bind("<Button-5>", lambda e, k=_qkey: _on_image_wheel(k, e))
        # Pan with the middle mouse button (does not conflict with left-click
        # marker placement).
        _lbl.bind("<Button-2>", lambda e, k=_qkey: _on_image_pan_start(k, e))
        _lbl.bind("<B2-Motion>", _on_image_pan_motion)
        _lbl.bind("<ButtonRelease-2>", _on_image_pan_end)

    # Button bar at the bottom
    button_frame = tk.Frame(outer, bg=BG_COLOR, height=60)
    button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=20)
    button_frame.pack_propagate(False)

    prev_button = tk.Button(button_frame, text="Previous", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=lambda: window1())
    prev_button.pack(side=tk.LEFT)

    place_button = tk.Button(
        button_frame, text="Placer des marqueurs", font=FONT,
        bg=ACCENT_COLOR_BLUE, fg=FG_COLOR, command=_toggle_marker_mode,
    )
    place_button.pack(side=tk.LEFT, padx=10)
    _marker_buttons["place"] = place_button

    undo_button = tk.Button(
        button_frame, text="Annuler le point", font=FONT,
        bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=_undo_last_point,
    )
    undo_button.pack(side=tk.LEFT, padx=10)

    replace_button = tk.Button(
        button_frame, text="Replacer le masque", font=FONT,
        bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=_replace_mask,
    )
    replace_button.pack(side=tk.LEFT, padx=10)

    reset_zoom_button = tk.Button(
        button_frame, text="Réinitialiser le zoom", font=FONT,
        bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=_reset_all_zoom,
    )
    reset_zoom_button.pack(side=tk.LEFT, padx=10)

    # --- Validation controls (multi-.czi workflow) ---
    # Next is packed side=RIGHT first so it sits at the far right; the two
    # validation buttons are packed afterwards, appearing to its left:
    #   ... [Valider la coupe] [Annuler la validation] [Next]
    next_button = tk.Button(button_frame, text="Next", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=lambda: window3())
    next_button.pack(side=tk.RIGHT)

    annuler_val_button = tk.Button(
        button_frame, text="Annuler la validation", font=FONT,
        bg=ERROR_COLOR, fg=FG_COLOR, command=_cancel_last_validation,
    )
    annuler_val_button.pack(side=tk.RIGHT, padx=10)

    valider_button = tk.Button(
        button_frame, text="Valider la coupe", font=FONT,
        bg=ACCENT_COLOR_GREEN, fg=FG_COLOR, command=_validate_current_roi,
    )
    valider_button.pack(side=tk.RIGHT, padx=10)

    # Bind window resize to dynamically update images
    try:
        root.unbind("<Configure>")
    except tk.TclError:
        pass
    root.bind("<Configure>", lambda e: _update_window2_images())

    # Force initial layout then render images for the initial slider depth.
    root.update_idletasks()
    if _tl_scale is not None:
        _tl_scale.set(_current_atlas_depth)
        initial_depth = _tl_scale.get()
        root.after(50, lambda: _load_atlas_images_for_depth(initial_depth))

    # Initialize the ROI list (czi->jpeg conversion runs in another thread, so
    # the first jpegs may not exist yet -> poll until they appear).
    _refresh_roi_list()
    _load_current_roi()
    if not _roi_items:
        _start_roi_polling()


# --- Window 3 (cell quantification) ---
_w3_progress_queue = None
_w3_poll_id = None
_w3_running = False
_w3_global_var = None
_w3_file_var = None
_w3_status_label = None
_w3_file_label = None
_w3_count_label = None
_w3_log_text = None
_w3_preview_label = None
_w3_preview_photo = None
_w3_start_button = None
_w3_next_button = None
_w3_last_result = None
_w3_log_states = {}


def _w3_input_root():
    """Return the 4x JPEG tree produced for cell quantification."""
    return Path(BASE_OUTPUT_DIR) / QUANTIFICATION_JPEG_OUTPUT_SUBDIR


def _w3_log(message, level="info"):
    """Append one log line to the window 3 log widget."""
    if _w3_log_text is None or not _w3_log_text.winfo_exists():
        return
    tag = level if level in ("info", "ok", "error", "warn") else "info"
    _w3_log_text.insert(tk.END, str(message) + "\n", tag)
    _w3_log_text.see(tk.END)


def _w3_set_preview(mask_path):
    """Display the latest detected-cell mask in the preview area."""
    global _w3_preview_photo
    if _w3_preview_label is None or not _w3_preview_label.winfo_exists():
        return
    if not mask_path or not os.path.exists(mask_path):
        return

    win_w = root.winfo_width() if root.winfo_width() > 100 else 800
    win_h = root.winfo_height() if root.winfo_height() > 100 else 600
    max_w = max(200, int(win_w * 0.42))
    max_h = max(180, int(win_h * 0.55))

    photo = load_and_resize_image(mask_path, max_w, max_h)
    if photo is not None:
        _w3_preview_photo = photo
        _w3_preview_label.config(image=photo, text="")


def _w3_reset_progress():
    """Reset progress widgets before a new quantification run."""
    global _w3_log_states
    _w3_log_states = {}
    if _w3_global_var is not None:
        _w3_global_var.set(0.0)
    if _w3_file_var is not None:
        _w3_file_var.set(0.0)
    if _w3_status_label is not None:
        _w3_status_label.config(text="Prêt.")
    if _w3_file_label is not None:
        _w3_file_label.config(text="")
    if _w3_count_label is not None:
        _w3_count_label.config(text="")
    if _w3_preview_label is not None:
        _w3_preview_label.config(image="", text="Le dernier masque détecté apparaîtra ici.")
    if _w3_log_text is not None:
        _w3_log_text.delete("1.0", tk.END)


def _w3_handle_event(event):
    """Apply one structured progress event from quantification_wrapper to Tk."""
    global _w3_running, _w3_last_result, _w3_log_states

    event_type = event.get("type", "")
    global_pct = event.get("global_pct")
    file_pct = event.get("file_pct")

    if isinstance(global_pct, (int, float)) and _w3_global_var is not None:
        _w3_global_var.set(max(0.0, min(100.0, float(global_pct))))
    if isinstance(file_pct, (int, float)) and _w3_file_var is not None:
        _w3_file_var.set(max(0.0, min(100.0, float(file_pct))))

    image = event.get("image", "")
    message = event.get("message", "")

    if event_type == "started":
        if _w3_status_label is not None:
            _w3_status_label.config(text=message)

    elif event_type == "file_started":
        image_key = image or str(event.get("file_index", ""))
        _w3_log_states[image_key] = {}
        if _w3_status_label is not None:
            _w3_status_label.config(text="Pré-traitement...")
        if _w3_file_label is not None:
            _w3_file_label.config(
                text=f"Image {event.get('file_index', '?')} / {event.get('file_total', '?')} : {image}"
            )
        _w3_log(f"Image {image} : pré-traitement", "info")

    elif event_type in ("file_step", "heartbeat"):
        if _w3_status_label is not None:
            _w3_status_label.config(text="Quantification (two-pass)...")

    elif event_type == "log":
        if "TRIGGER:DARK_ROI_CREATED" in message or "TRIGGER:DARK_DETECTION_DONE" in message:
            image_key = image or str(event.get("file_index", ""))
            state = _w3_log_states.setdefault(image_key, {})
            if not state.get("dark_logged"):
                state["dark_logged"] = True
                _w3_log(f"Image {image} : pass dark (Hematoxylin OD)...", "info")
        elif "TRIGGER:LIGHT_ROI_CREATED" in message or "TRIGGER:LIGHT_DETECTION_DONE" in message:
            image_key = image or str(event.get("file_index", ""))
            state = _w3_log_states.setdefault(image_key, {})
            if not state.get("light_logged"):
                state["light_logged"] = True
                _w3_log(f"Image {image} : pass light (Optical density sum)...", "info")
        elif "TRIGGER:MERGE_DONE" in message:
            _w3_log(f"Image {image} : fusion dark + light...", "info")
        elif "ERROR" in message or "Exception" in message:
            _w3_log(f"Image {image} : erreur - {message}", "error")

    elif event_type == "file_done":
        num_cells = event.get("num_cells", "")
        mask_path = event.get("mask_path", "")
        if mask_path:
            _w3_set_preview(mask_path)
        _w3_log(
            f"Image {image} : quantification terminée ! {num_cells} cellules", "ok"
        )
        if _w3_count_label is not None:
            _w3_count_label.config(
                text=f"Dernier résultat : {num_cells} cellule(s)"
            )

    elif event_type == "file_error":
        _w3_log(f"Image {image} : erreur - {message}", "error")
        if _w3_count_label is not None:
            _w3_count_label.config(text=f"Erreur sur {image}")

    elif event_type == "waiting_for_images":
        if _w3_status_label is not None:
            _w3_status_label.config(text="En attente des prochains JPEG 4x...")

    elif event_type == "done":
        _w3_running = False
        total_cells = event.get("total_cells", 0)
        successful = event.get("successful_images", 0)
        total = event.get("total_images", 0)
        text = f"Terminé : {total_cells} cellule(s), {successful}/{total} image(s)"
        if _w3_status_label is not None:
            _w3_status_label.config(text=text)
        if _w3_count_label is not None:
            _w3_count_label.config(text=f"Total : {total_cells} cellule(s)")
        if _w3_start_button is not None:
            _w3_start_button.config(state=tk.NORMAL)
        if _w3_next_button is not None:
            _w3_next_button.config(state=tk.NORMAL)

    elif event_type == "worker_done":
        _w3_last_result = event.get("result")

    elif event_type == "worker_error":
        _w3_running = False
        if _w3_status_label is not None:
            _w3_status_label.config(text="Erreur quantification.")
        _w3_log(event.get("error", "Erreur inconnue"), "error")
        if _w3_start_button is not None:
            _w3_start_button.config(state=tk.NORMAL)
        if _w3_next_button is not None:
            _w3_next_button.config(state=tk.NORMAL)


def _w3_poll_queue():
    """Drain the wrapper event queue from the Tk main thread."""
    global _w3_poll_id
    _w3_poll_id = None
    if _w3_progress_queue is None:
        return

    drained = 0
    while drained < 100:
        try:
            event = _w3_progress_queue.get_nowait()
        except queue.Empty:
            break
        _w3_handle_event(event)
        drained += 1

    if _w3_running:
        _w3_poll_id = root.after(100, _w3_poll_queue)


def _w3_start_quantification():
    """
    Launch the reusable quantification wrapper in a worker thread.

    The wrapper owns all QuPath/detection logic and emits structured progress
    events. This function only bridges those events to Tk widgets.
    """
    global _w3_progress_queue, _w3_running, _w3_poll_id

    if _w3_running:
        return

    input_root = _w3_input_root()
    image_paths = discover_jpeg_images(input_root, recursive=True)
    if not image_paths and not _quantification_conversion_running:
        messagebox.showwarning(
            "Aucune image",
            f"Aucun JPEG trouvé dans:\n{input_root}\n\n"
            "Lancer d'abord la conversion .czi → jpeg depuis les fenêtres précédentes.",
        )
        return

    output_dir = Path(BASE_OUTPUT_DIR) / f"cell_quantification_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    event_queue = queue.Queue()
    _w3_progress_queue = event_queue
    _w3_running = True
    _w3_reset_progress()
    if _w3_start_button is not None:
        _w3_start_button.config(state=tk.DISABLED)
    if _w3_next_button is not None:
        _w3_next_button.config(state=tk.DISABLED)

    def progress_cb(event):
        event_queue.put(event)

    def worker():
        try:
            result = run_quantification(
                image_paths=image_paths,
                output_dir=output_dir,
                progress_cb=progress_cb,
                refresh_images_cb=lambda: discover_jpeg_images(input_root, recursive=True),
                input_complete_cb=lambda: not _quantification_conversion_running,
                poll_interval_seconds=1.5,
            )
            event_queue.put({"type": "worker_done", "result": result})
        except Exception as exc:
            event_queue.put({"type": "worker_error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()

    if _w3_poll_id is not None:
        try:
            root.after_cancel(_w3_poll_id)
        except Exception:
            pass
    _w3_poll_id = root.after(100, _w3_poll_queue)


def window3():
    """
    Window 3: cell quantification via a reusable wrapper around the standalone
    QuPath project.

    Outputs:
      - standalone detected-cell masks
      - per-image CSV files with relative cell coordinates
      - one combined summary CSV
    """
    global _w3_global_var, _w3_file_var, _w3_status_label, _w3_file_label
    global _w3_count_label, _w3_log_text, _w3_preview_label, _w3_preview_photo
    global _w3_start_button, _w3_next_button, _w3_poll_id

    if _w3_poll_id is not None:
        try:
            root.after_cancel(_w3_poll_id)
        except Exception:
            pass
        _w3_poll_id = None

    try:
        root.unbind("<Configure>")
    except tk.TclError:
        pass

    for widget in root.winfo_children():
        widget.destroy()

    _w3_preview_photo = None

    outer = tk.Frame(root, bg=BG_COLOR)
    outer.pack(fill=tk.BOTH, expand=True)

    header = tk.Frame(outer, bg=BG_COLOR)
    header.pack(fill=tk.X, padx=12, pady=(10, 4))

    title = tk.Label(
        header,
        text="Window 3 — Quantification cellulaire",
        font=("Arial", 16, "bold"),
        bg=BG_COLOR,
        fg=FG_COLOR,
    )
    title.pack(anchor="w")

    input_root = _w3_input_root()
    image_count = len(discover_jpeg_images(input_root, recursive=True))
    source_label = tk.Label(
        header,
        text=f"Source JPEG 4x : {input_root}  —  {image_count} image(s) détectée(s)",
        font=SMALL_FONT,
        bg=BG_COLOR,
        fg=FG_COLOR,
    )
    source_label.pack(anchor="w", pady=(2, 0))

    content = tk.Frame(outer, bg=BG_COLOR)
    content.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
    content.columnconfigure(0, weight=3)
    content.columnconfigure(1, weight=2)
    content.rowconfigure(0, weight=1)

    left = tk.Frame(content, bg=BG_COLOR)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    left.columnconfigure(0, weight=1)

    progress_box = tk.LabelFrame(left, text="Progression", font=FONT, bg=BG_COLOR, fg=FG_COLOR)
    progress_box.pack(fill=tk.X, pady=(0, 8))
    progress_box.columnconfigure(1, weight=1)

    _w3_global_var = tk.DoubleVar(value=0.0)
    _w3_file_var = tk.DoubleVar(value=0.0)

    tk.Label(progress_box, text="Global", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR).grid(row=0, column=0, sticky="w", padx=8, pady=5)
    ttk.Progressbar(progress_box, variable=_w3_global_var, maximum=100).grid(row=0, column=1, sticky="ew", padx=8, pady=5)

    tk.Label(progress_box, text="Image", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR).grid(row=1, column=0, sticky="w", padx=8, pady=5)
    ttk.Progressbar(progress_box, variable=_w3_file_var, maximum=100).grid(row=1, column=1, sticky="ew", padx=8, pady=5)

    _w3_status_label = tk.Label(progress_box, text="Prêt.", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR, anchor="w")
    _w3_status_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(5, 2))

    _w3_file_label = tk.Label(progress_box, text="", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR, anchor="w")
    _w3_file_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5))

    _w3_count_label = tk.Label(progress_box, text="", font=FONT, bg=BG_COLOR, fg=ACCENT_COLOR_BLUE, anchor="w")
    _w3_count_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

    log_box = tk.LabelFrame(left, text="Journal / triggers", font=FONT, bg=BG_COLOR, fg=FG_COLOR)
    log_box.pack(fill=tk.BOTH, expand=True)
    log_box.rowconfigure(0, weight=1)
    log_box.columnconfigure(0, weight=1)

    _w3_log_text = tk.Text(log_box, height=14, font=("Consolas", 9), wrap=tk.WORD)
    _w3_log_text.grid(row=0, column=0, sticky="nsew")
    _w3_log_text.tag_configure("info", foreground=FG_COLOR)
    _w3_log_text.tag_configure("ok", foreground=ACCENT_COLOR_GREEN)
    _w3_log_text.tag_configure("error", foreground=ERROR_COLOR)
    _w3_log_text.tag_configure("warn", foreground="#b36b00")
    log_scroll = tk.Scrollbar(log_box, orient="vertical", command=_w3_log_text.yview)
    log_scroll.grid(row=0, column=1, sticky="ns")
    _w3_log_text.configure(yscrollcommand=log_scroll.set)

    right = tk.LabelFrame(content, text="Prévisualisation masque", font=FONT, bg=BG_COLOR, fg=FG_COLOR)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(0, weight=1)
    right.columnconfigure(0, weight=1)

    _w3_preview_label = tk.Label(
        right,
        text="Le dernier masque détecté apparaîtra ici.",
        font=FONT,
        bg="white",
        fg="gray",
    )
    _w3_preview_label.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

    button_frame = tk.Frame(outer, bg=BG_COLOR, height=60)
    button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=20)
    button_frame.pack_propagate(False)

    prev_button = tk.Button(
        button_frame,
        text="Previous",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        command=lambda: window2(),
    )
    prev_button.pack(side=tk.LEFT, pady=12)

    _w3_start_button = tk.Button(
        button_frame,
        text="Start quantification",
        font=FONT,
        bg=ACCENT_COLOR_GREEN,
        fg=FG_COLOR,
        command=_w3_start_quantification,
    )
    _w3_start_button.pack(side=tk.LEFT, padx=12, pady=12)

    _w3_next_button = tk.Button(
        button_frame,
        text="Next",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        command=lambda: window4(),
    )
    _w3_next_button.pack(side=tk.RIGHT, pady=12)

    root.update_idletasks()


# --- Window 4 (validation / final export) ---
_w4_items = []
_w4_index = 0
_w4_z_index = 0
_w4_mode = "image"  # "image" or "diagram"
_w4_preview_label = None
_w4_preview_photo = None
_w4_status_label = None
_w4_z_scale = None
_w4_toggle_button = None
_w4_reject_button = None
_w4_quant_map = {}


def _safe_folder_name(name):
    """Return a Windows-safe folder name while preserving readability."""
    return re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip(" .") or "unnamed"


def _timestamp_folder_name():
    """
    Timestamp used for export folders.

    The user requested dd/mm/yyyy, but '/' is a path separator on Windows. This
    keeps the same date information in a filesystem-safe folder name.
    """
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _w4_latest_quantification_dir():
    """Return the newest ./output/cell_quantification_* directory, or None."""
    output_root = Path(BASE_OUTPUT_DIR)
    candidates = [p for p in output_root.glob("cell_quantification_*") if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _w4_build_quant_map():
    """
    Map a source JPEG filename to its newest quantification JSON.

    All ./output/cell_quantification_* folders are scanned newest-first. This
    lets a per-slice "Rejeter la lame" rerun override only the rejected image
    while keeping older valid quantification results available for all others.
    """
    output_root = Path(BASE_OUTPUT_DIR)
    qdirs = [p for p in output_root.glob("cell_quantification_*") if p.is_dir()]
    qdirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    result = {}
    for qdir in qdirs:
        for json_path in qdir.glob("**/*_result.json"):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                image_name = raw.get("image") or Path(raw.get("source_path", "")).name
                if image_name and image_name not in result:
                    result[image_name] = {"json_path": json_path, "data": raw}
            except Exception as exc:
                print(f"[window4] cannot read quantification JSON {json_path}: {exc}")
    return result


def _w4_sorted_z_images(roi_dir):
    """Return all z-slice JPEGs from a ROI folder, sorted by z index."""
    candidates = []
    for f in roi_dir.glob("*_z_slice_*.jpeg"):
        m = _Z_SLICE_RE.search(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    candidates.sort(key=lambda t: t[0])
    return [p for _, p in candidates]


def _w4_build_items():
    """
    Build one window4 item per ROI/scene from the 20x JPEG tree.

    Each item links:
      - original 20x z-slices,
      - window2 replaced atlas mask,
      - latest window3 quantification JSON/mask,
      - 4x source image used for possible reject/re-run.
    """
    global _w4_quant_map
    _w4_quant_map = _w4_build_quant_map()

    output_base = Path(BASE_OUTPUT_DIR) / JPEG_OUTPUT_SUBDIR
    if not output_base.is_dir():
        return []

    input_dir = Path(czi_folder_path)
    try:
        czi_stems = sorted(
            (p.stem for p in convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True)),
            key=len,
            reverse=True,
        )
    except Exception:
        czi_stems = []

    items = []
    for roi_dir in sorted(output_base.iterdir()):
        if not roi_dir.is_dir():
            continue
        z_images = _w4_sorted_z_images(roi_dir)
        if not z_images:
            continue

        roi_name = roi_dir.name
        czi_stem = _match_czi_stem(roi_name, czi_stems)
        mask_dir = Path(WORK_IN_PROGRESS_DIR) / czi_stem / "masks"

        first_image_name = z_images[0].name
        quant = _w4_quant_map.get(first_image_name)
        quant_data = quant.get("data") if quant else {}
        quant_json = quant.get("json_path") if quant else None
        cell_mask_path = Path(quant_data.get("mask_path", "")) if quant_data.get("mask_path") else None
        cells_csv_path = None
        if quant_json is not None:
            candidate_csv = quant_json.parent / quant_json.name.replace("_result.json", "_cells.csv")
            if candidate_csv.exists():
                cells_csv_path = candidate_csv

        items.append({
            "czi_stem": czi_stem,
            "roi_folder_name": roi_name,
            "roi_folder_path": roi_dir,
            "z_images": z_images,
            "mask_png": mask_dir / f"{roi_name}.png",
            "mask_overlay_png": mask_dir / f"{roi_name}_overlay.png",
            "quant_json": quant_json,
            "quant_data": quant_data,
            "cell_mask_path": cell_mask_path,
            "cells_csv_path": cells_csv_path,
        })

    return items


def _w4_current_item():
    if not _w4_items:
        return None
    idx = max(0, min(_w4_index, len(_w4_items) - 1))
    return _w4_items[idx]


def _w4_current_image_path(item=None):
    item = item or _w4_current_item()
    if item is None:
        return None
    z_images = item.get("z_images", [])
    if not z_images:
        return None
    idx = max(0, min(_w4_z_index, len(z_images) - 1))
    return z_images[idx]


def _w4_cells(item=None):
    """Return detected cells from the quantification JSON."""
    item = item or _w4_current_item()
    if item is None:
        return []
    return list((item.get("quant_data") or {}).get("cells") or [])


def _w4_load_mask_rgba(mask_path, target_size, alpha=0.35):
    """
    Load a colored region mask and convert black background to transparent alpha.
    """
    if not mask_path or not os.path.exists(mask_path):
        return None
    try:
        mask = Image.open(mask_path).convert("RGBA").resize(target_size, Image.Resampling.NEAREST)
        arr = np.asarray(mask).copy()
        non_bg = np.any(arr[:, :, :3] > 8, axis=2)
        arr[:, :, 3] = (non_bg.astype(np.uint8) * int(255 * alpha))
        return Image.fromarray(arr, mode="RGBA")
    except Exception as exc:
        print(f"[window4] cannot load region mask {mask_path}: {exc}")
        return None


def _w4_overlay_cell_mask(base_rgba, cell_mask_path, region_mask_path=None):
    """
    Overlay the detected-cell mask from window3, restricted to atlas regions.

    The cell mask has a dark/black background and white detected cells. The dark
    background is removed; cell pixels are drawn in yellow for visibility.

    When `region_mask_path` (the warped colored atlas mask) is provided, cell
    pixels falling outside any labeled region (atlas background) are deleted so
    only in-region cells are drawn.
    """
    if not cell_mask_path or not os.path.exists(cell_mask_path):
        return base_rgba
    try:
        mask = Image.open(cell_mask_path).convert("L").resize(base_rgba.size, Image.Resampling.NEAREST)
        arr = np.asarray(mask)
        cell_bin = (arr > 20).astype(np.uint8)

        # Gate the cell mask by the atlas region mask: keep cell pixels only
        # where a labeled region exists (non-black pixel).
        if region_mask_path and os.path.exists(region_mask_path):
            try:
                region = Image.open(region_mask_path).convert("RGB").resize(
                    base_rgba.size, Image.Resampling.NEAREST
                )
                region_arr = np.asarray(region)
                region_bin = (np.any(region_arr[:, :, :3] > 8, axis=2)).astype(np.uint8)
                cell_bin = cell_bin * region_bin
            except Exception as exc:
                print(f"[window4] cannot gate cell mask by region {region_mask_path}: {exc}")

        cell_alpha = cell_bin * 210
        overlay = np.zeros((base_rgba.size[1], base_rgba.size[0], 4), dtype=np.uint8)
        overlay[:, :, 0] = 255
        overlay[:, :, 1] = 235
        overlay[:, :, 2] = 0
        overlay[:, :, 3] = cell_alpha
        return Image.alpha_composite(base_rgba, Image.fromarray(overlay, mode="RGBA"))
    except Exception as exc:
        print(f"[window4] cannot overlay cell mask {cell_mask_path}: {exc}")
        return base_rgba


def _w4_draw_cell_points(img_rgba, cells, radius=None):
    """
    Draw cell centroids from relative coordinates on an RGBA image.

    All merged cells are drawn in a single color (yellow). The two detection
    passes (dark + light) are treated as one unified cell set: cells are
    merged upstream (dark wins on overlap) and no longer distinguished here.
    """
    if not cells:
        return img_rgba
    draw = ImageDraw.Draw(img_rgba, "RGBA")
    w, h = img_rgba.size
    r = radius if radius is not None else max(1, min(w, h) // 280)
    color = (255, 255, 0, 230)
    for cell in cells:
        try:
            x = int(float(cell.get("x_relative", 0.0)) * w)
            y = int(float(cell.get("y_relative", 0.0)) * h)
        except Exception:
            continue
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    return img_rgba


def _w4_filtered_cells(item=None):
    """
    Return detected cells restricted to those inside a labeled atlas region.

    Cells whose centroid falls on the atlas background (label 0) are deleted.
    Falls back to the full cell list when the region mask is missing.
    """
    item = item or _w4_current_item()
    if item is None:
        return []
    region_png = item.get("mask_png")
    cells = _w4_cells(item)
    return filter_cells_by_region(cells, str(region_png) if region_png else None)


def _w4_make_image_preview(item=None):
    """
    Compose: original 20x image + replaced atlas region mask + cell mask/points.

    Only in-region cells are shown: the cell-mask overlay is gated by the atlas
    region mask, and cell points are drawn from the filtered cell list.
    """
    item = item or _w4_current_item()
    image_path = _w4_current_image_path(item)
    if item is None or image_path is None or not os.path.exists(image_path):
        return None

    region_png = item.get("mask_png")
    region_path = str(region_png) if region_png and os.path.exists(region_png) else None

    base = Image.open(image_path).convert("RGBA")
    region_overlay = _w4_load_mask_rgba(region_png, base.size, alpha=0.35)
    if region_overlay is not None:
        base = Image.alpha_composite(base, region_overlay)
    base = _w4_overlay_cell_mask(base, item.get("cell_mask_path"), region_mask_path=region_path)
    base = _w4_draw_cell_points(base, _w4_filtered_cells(item))
    return base.convert("RGB")


def _w4_make_diagram_preview(item=None):
    """
    Build a horizontal bar graph: detected cell count per labeled atlas region.

    Each cell's relative position is sampled on the warped region mask (the RGB
    PNG saved by window 2) and mapped back to a region id via the atlas `.label`
    color table (see mask_replacer.count_cells_per_region). Regions with zero
    cells are omitted; bars are colored with the region's official RGB and
    sorted by descending count. Rendered off-screen with the Agg backend so it
    never interferes with Tkinter.
    """
    item = item or _w4_current_item()
    if item is None:
        return None

    mask_png = item.get("mask_png")
    cells = _w4_filtered_cells(item)

    fig = Figure(figsize=(9, 6), dpi=100)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor("white")

    if not mask_png or not os.path.exists(str(mask_png)):
        ax.text(0.5, 0.5, "Masque de régions manquant",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=14, color="#b00020")
        ax.set_axis_off()
    elif not cells:
        ax.text(0.5, 0.5, "Aucune cellule détectée",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=14, color="#666666")
        ax.set_axis_off()
    else:
        rows = count_cells_per_region(str(mask_png), cells)
        if not rows:
            ax.text(0.5, 0.5, "Aucune cellule dans les régions labellisées",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=13, color="#666666")
            ax.set_axis_off()
        else:
            names = [r["name"] for r in rows]
            counts = [r["count"] for r in rows]
            colors = [(r["rgb"][0] / 255.0, r["rgb"][1] / 255.0, r["rgb"][2] / 255.0) for r in rows]
            y_pos = list(range(len(rows)))
            ax.barh(y_pos, counts, color=colors, edgecolor="black", linewidth=0.5)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(names, fontsize=9)
            ax.invert_yaxis()  # highest count on top
            ax.set_xlabel("Nombre de cellules", fontsize=10)
            ax.grid(axis="x", linestyle="--", alpha=0.4)
            ax.set_title(
                f"{item.get('roi_folder_name', '')} — {sum(counts)} cellule(s) dans {len(rows)} région(s)",
                fontsize=11,
            )
            # Count labels at the end of each bar.
            max_count = max(counts) if counts else 1
            for i, c in enumerate(counts):
                ax.text(c + max_count * 0.012, i, str(c), va="center", fontsize=9)
            # Give a little headroom on the right so the count labels fit.
            ax.set_xlim(0, max_count * 1.12 if max_count else 1)

    fig.tight_layout()
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    rgba_buf = np.asarray(canvas.buffer_rgba())
    rgb = rgba_buf[..., :3].copy()  # drop alpha, PIL wants RGB
    return Image.fromarray(rgb, mode="RGB")


def _w4_get_preview_size():
    win_w = root.winfo_width() if root.winfo_width() > 100 else 800
    win_h = root.winfo_height() if root.winfo_height() > 100 else 600
    return max(250, int(win_w * 0.66) - 45), max(220, win_h - 150)


def _w4_set_status(text):
    if _w4_status_label is not None and _w4_status_label.winfo_exists():
        _w4_status_label.config(text=text)


def _w4_update_z_scale():
    if _w4_z_scale is None:
        return
    item = _w4_current_item()
    count = len(item.get("z_images", [])) if item else 1
    count = max(1, count)
    try:
        _w4_z_scale.config(from_=1, to=count, state=(tk.NORMAL if count > 1 else tk.DISABLED))
        _w4_z_scale.set(max(1, min(_w4_z_index + 1, count)))
    except Exception:
        pass


def _w4_refresh_preview():
    """Render the current image/diagram in the left preview area."""
    global _w4_preview_photo

    if _w4_preview_label is None or not _w4_preview_label.winfo_exists():
        return

    item = _w4_current_item()
    if item is None:
        _w4_preview_label.config(image="", text="Aucune lame disponible.\nLancer les fenêtres 2 et 3 d'abord.")
        _w4_set_status("Aucun élément trouvé dans ./output/downsampled20_jpeg.")
        return

    pil_img = _w4_make_image_preview(item) if _w4_mode == "image" else _w4_make_diagram_preview(item)
    if pil_img is None:
        _w4_preview_label.config(image="", text="Prévisualisation indisponible")
        return

    max_w, max_h = _w4_get_preview_size()
    new_w, new_h = get_img_dims(pil_img.width, pil_img.height, max_w, max_h)
    display = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(display)
    _w4_preview_photo = photo
    _w4_preview_label.config(image=photo, text="")

    z_count = len(item.get("z_images", []))
    cells = _w4_filtered_cells(item)
    missing = []
    if not os.path.exists(item.get("mask_png", "")):
        missing.append("masque régions")
    if not item.get("quant_json"):
        missing.append("quantification")
    missing_txt = f" — manquant: {', '.join(missing)}" if missing else ""
    _w4_set_status(
        f"{_w4_index + 1}/{len(_w4_items)} | {item['roi_folder_name']} | "
        f"Z {_w4_z_index + 1}/{max(1, z_count)} | total: {len(cells)}"
        f"{missing_txt}"
    )

    if _w4_toggle_button is not None and _w4_toggle_button.winfo_exists():
        _w4_toggle_button.config(text=("Afficher le diagramme" if _w4_mode == "image" else "Afficher l'image"))


def _w4_on_z_changed(value):
    global _w4_z_index
    try:
        _w4_z_index = max(0, int(float(value)) - 1)
    except Exception:
        _w4_z_index = 0
    _w4_refresh_preview()


def _w4_prev_slice():
    global _w4_index, _w4_z_index
    if _w4_index > 0:
        _w4_index -= 1
        _w4_z_index = 0
        _w4_update_z_scale()
        _w4_refresh_preview()


def _w4_next_slice():
    global _w4_index, _w4_z_index
    if _w4_index < len(_w4_items) - 1:
        _w4_index += 1
        _w4_z_index = 0
        _w4_update_z_scale()
        _w4_refresh_preview()


def _w4_toggle_mode():
    global _w4_mode
    _w4_mode = "diagram" if _w4_mode == "image" else "image"
    _w4_refresh_preview()


def _w4_write_results_csv(dest_csv, item):
    """Write the filtered (in-region) result CSV for the current item.

    Cells outside the atlas region mask are excluded. When the region mask is
    missing the full cell list is written (non-destructive fallback).
    """
    cells = _w4_filtered_cells(item)
    dest_csv.parent.mkdir(parents=True, exist_ok=True)

    # NOTE: we no longer blindly copy the wrapper's CSV because it contains ALL
    # detected cells (including out-of-region ones). Write the filtered list so
    # the exported results match the preview.
    with open(dest_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["roi_folder", "czi_stem", "num_cells", "cell_id", "x_relative", "y_relative", "x_pixel", "y_pixel"])
        if cells:
            for cell in cells:
                writer.writerow([
                    item.get("roi_folder_name", ""),
                    item.get("czi_stem", ""),
                    len(cells),
                    cell.get("cell_id", ""),
                    cell.get("x_relative", ""),
                    cell.get("y_relative", ""),
                    cell.get("x_pixel", ""),
                    cell.get("y_pixel", ""),
                ])
        else:
            writer.writerow([item.get("roi_folder_name", ""), item.get("czi_stem", ""), 0, "", "", "", "", "", ""])


def _w4_find_czi_path(czi_stem: str) -> Path | None:
    """Find the original .czi file for a given stem in the input folder."""
    input_dir = Path(czi_folder_path)
    try:
        for czi in convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True):
            if czi.stem == czi_stem:
                return czi
    except Exception:
        pass
    return None


def _w4_write_volumetric_csvs(dest_dir: Path):
    """
    Compute and write the volumetric CSVs across ALL ROIs of ALL czis.

    Two files are produced:

    1. ``per_roi_volumetric.csv`` — one row per (czi, roi, region):
       num_cells, surface_area_mm2, slice_depth_um, concentration_cells_per_mm3.

    2. ``volumetric_summary.csv`` — one row per (czi, region):
       mean_concentration_cells_per_mm3 (averaged over the czis ROIs),
       region_volume_mm3 (from atlas_volumes.csv), estimated_absolute_cells.

    Requires:
      - ``_slice_depth_um`` > 0 (set in Window 1)
      - the original .czi pixel size (read from CZI metadata)
      - the warped atlas region mask per ROI (saved by Window 2)

    Edge cases are handled: missing pixel size -> surface/concentration blank;
    missing atlas volume -> estimated cells blank.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    depth_um = get_slice_depth_um()
    volumes = load_atlas_volumes()
    # Convert depth um -> mm for the concentration formula.
    depth_mm = depth_um * 1e-3

    pixel_size_cache: dict = {}

    def get_pixel_size(czi_stem: str) -> float | None:
        if czi_stem not in pixel_size_cache:
            czi_path = _w4_find_czi_path(czi_stem)
            if czi_path is None:
                pixel_size_cache[czi_stem] = None
            else:
                pixel_size_cache[czi_stem] = get_czi_pixel_size_um(str(czi_path))
        return pixel_size_cache[czi_stem]

    per_roi_rows = []
    czi_region_concentrations: dict[str, dict[int, list[float]]] = {}

    for item in _w4_items:
        czi_stem = item.get("czi_stem", "")
        roi_name = item.get("roi_folder_name", "")
        mask_png = item.get("mask_png")
        cells = _w4_filtered_cells(item)

        if not mask_png or not os.path.exists(str(mask_png)):
            continue

        cell_rows = count_cells_per_region(str(mask_png), cells)
        cell_counts = {r["label"]: r["count"] for r in cell_rows}

        pixel_size = get_pixel_size(czi_stem)
        if pixel_size and pixel_size > 0:
            surface = compute_region_surface_areas_mm2(
                str(mask_png), pixel_size, downsample=DOWNSAMPLE_FACTOR
            )
        else:
            surface = {}

        all_labels = sorted(set(list(cell_counts.keys()) + list(surface.keys())))
        for lid in all_labels:
            n_cells = cell_counts.get(lid, 0)
            surf_mm2 = surface.get(lid, {}).get("surface_mm2", 0.0)
            region_name = surface.get(lid, {}).get("name") or next(
                (r["name"] for r in cell_rows if r["label"] == lid), str(lid)
            )

            concentration = ""
            if surf_mm2 > 0 and depth_mm > 0:
                concentration = n_cells / (depth_mm * surf_mm2)
                czi_region_concentrations.setdefault(czi_stem, {}).setdefault(lid, []).append(concentration)

            per_roi_rows.append({
                "czi_stem": czi_stem,
                "roi_folder": roi_name,
                "region_id": lid,
                "region_name": region_name,
                "num_cells": n_cells,
                "surface_area_mm2": f"{surf_mm2:.6f}" if surf_mm2 else "",
                "slice_depth_um": depth_um if depth_um > 0 else "",
                "concentration_cells_per_mm3": f"{concentration:.4f}" if concentration != "" else "",
                "pixel_size_um": f"{pixel_size:.4f}" if pixel_size else "",
            })

    per_roi_csv = dest_dir / "per_roi_volumetric.csv"
    with open(per_roi_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "czi_stem", "roi_folder", "region_id", "region_name",
            "num_cells", "surface_area_mm2", "slice_depth_um",
            "concentration_cells_per_mm3", "pixel_size_um",
        ])
        for row in per_roi_rows:
            writer.writerow([
                row["czi_stem"], row["roi_folder"], row["region_id"],
                row["region_name"], row["num_cells"], row["surface_area_mm2"],
                row["slice_depth_um"], row["concentration_cells_per_mm3"],
                row["pixel_size_um"],
            ])

    summary_csv = dest_dir / "volumetric_summary.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "czi_stem", "region_id", "region_name",
            "mean_concentration_cells_per_mm3", "num_rois",
            "region_volume_mm3", "estimated_absolute_cells",
        ])
        for czi_stem, regions in sorted(czi_region_concentrations.items()):
            for lid, concs in sorted(regions.items()):
                vol_info = volumes.get(lid, {})
                region_name = vol_info.get("name") or str(lid)
                region_volume = vol_info.get("volume_mm3", 0.0)
                if concs:
                    mean_conc = sum(concs) / len(concs)
                else:
                    mean_conc = 0.0
                estimated = mean_conc * region_volume if region_volume > 0 else ""
                writer.writerow([
                    czi_stem, lid, region_name,
                    f"{mean_conc:.4f}" if mean_conc else "0",
                    len(concs),
                    f"{region_volume:.6f}" if region_volume else "",
                    f"{estimated:.2f}" if estimated != "" else "",
                ])

    return per_roi_csv, summary_csv


def _w4_save_bundle(dest_root):
    """
    Save image+2masks.jpeg, results.csv, graph.jpeg and volumetric CSVs.
    """
    item = _w4_current_item()
    if item is None:
        messagebox.showwarning("Sauvegarde", "Aucune lame à sauvegarder.")
        return None

    dest_dir = Path(dest_root) / _safe_folder_name(item.get("czi_stem", "czi"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    image_preview = _w4_make_image_preview(item)
    diagram_preview = _w4_make_diagram_preview(item)
    if image_preview is None:
        raise RuntimeError("Impossible de générer image+2masks.jpeg")
    if diagram_preview is None:
        raise RuntimeError("Impossible de générer graph.jpeg")

    image_preview.save(dest_dir / "image+2masks.jpeg", format="JPEG", quality=95)
    diagram_preview.save(dest_dir / "graph.jpeg", format="JPEG", quality=95)
    _w4_write_results_csv(dest_dir / "results.csv", item)

    # Standalone combined+filtered cell mask: detected cells (dark+light merged)
    # restricted to the labeled atlas regions. Out-of-region cells are deleted.
    cell_mask = item.get("cell_mask_path")
    region_png = item.get("mask_png")
    if cell_mask and os.path.exists(str(cell_mask)):
        combined_path = dest_dir / "combined_cell_mask.png"
        combine_and_filter_cell_mask(
            str(cell_mask),
            str(region_png) if region_png and os.path.exists(str(region_png)) else None,
            str(combined_path),
        )

    # Volumetric CSVs — cover ALL czis/ROIs, written at dest_root level.
    depth_um = get_slice_depth_um()
    if depth_um <= 0:
        print("[volumetric] Slice depth <= 0, skipping volumetric CSVs.")
    else:
        try:
            _w4_write_volumetric_csvs(Path(dest_root))
        except Exception as exc:
            print(f"[volumetric] Could not generate volumetric CSVs: {exc}")

    return dest_dir


def _w4_validate_slide():
    try:
        dest = _w4_save_bundle(Path(WORK_IN_PROGRESS_DIR) / "Validation")
    except Exception as exc:
        messagebox.showerror("Validation", str(exc))
        return
    messagebox.showinfo("Validation", f"Lame validée dans:\n{dest}")


def _w4_save_to_output():
    try:
        dest = _w4_save_bundle(Path(BASE_OUTPUT_DIR) / _timestamp_folder_name())
    except Exception as exc:
        messagebox.showerror("Sauvegarde", str(exc))
        return
    messagebox.showinfo("Sauvegarde", f"Résultats sauvegardés dans:\n{dest}")


def _w4_save_to_selected_folder():
    folder = filedialog.askdirectory(title="Choisir le dossier de sauvegarde", mustexist=True)
    if not folder:
        return
    try:
        dest = _w4_save_bundle(Path(folder) / _timestamp_folder_name())
    except Exception as exc:
        messagebox.showerror("Sauvegarde", str(exc))
        return
    messagebox.showinfo("Sauvegarde", f"Résultats sauvegardés dans:\n{dest}")


def _w4_refresh_after_quantification(output_dir):
    """Refresh the quantification map after a reject/re-run worker finishes."""
    global _w4_items, _w4_quant_map
    _w4_quant_map = _w4_build_quant_map()
    _w4_items = _w4_build_items()
    _w4_update_z_scale()
    _w4_refresh_preview()
    if _w4_reject_button is not None and _w4_reject_button.winfo_exists():
        _w4_reject_button.config(state=tk.NORMAL)
    _w4_set_status(f"Quantification relancée terminée: {output_dir}")


def _w4_reject_slide():
    """
    Re-run quantification for the current image in the background.
    """
    item = _w4_current_item()
    image_path = _w4_current_image_path(item)
    if item is None or image_path is None:
        messagebox.showwarning("Rejet", "Aucune image à re-quantifier.")
        return

    q4_dir = Path(BASE_OUTPUT_DIR) / QUANTIFICATION_JPEG_OUTPUT_SUBDIR / item["roi_folder_name"]
    q4_image = q4_dir / image_path.name
    if not q4_image.exists():
        messagebox.showerror("Rejet", f"JPEG 4x introuvable:\n{q4_image}")
        return

    if _w4_reject_button is not None:
        _w4_reject_button.config(state=tk.DISABLED)
    _w4_set_status("Re-quantification en arrière-plan...")

    output_dir = Path(BASE_OUTPUT_DIR) / f"cell_quantification_{datetime.now().strftime('%Y%m%d_%H%M%S')}_rerun"

    def worker():
        try:
            run_quantification([q4_image], output_dir=output_dir)
            root.after(0, lambda: _w4_refresh_after_quantification(output_dir))
        except Exception as exc:
            def show_error():
                if _w4_reject_button is not None and _w4_reject_button.winfo_exists():
                    _w4_reject_button.config(state=tk.NORMAL)
                messagebox.showerror("Rejet", str(exc))
                _w4_refresh_preview()
            root.after(0, show_error)

    threading.Thread(target=worker, daemon=True).start()


def _on_w4_configure(_event=None):
    _w4_refresh_preview()


def window4():
    """
    Window 4: final visual validation/export.

    Left side:
      - image mode: 20x original + replaced regions + detected-cell mask
      - diagram mode: colored atlas-region layout + cell repartition

    Right side:
      - Z scrollbar
      - previous/next slice buttons
      - validate/reject buttons
      - image/diagram switch
      - export buttons
    """
    global _w4_items, _w4_index, _w4_z_index, _w4_mode
    global _w4_preview_label, _w4_preview_photo, _w4_status_label, _w4_z_scale
    global _w4_toggle_button, _w4_reject_button

    try:
        root.unbind("<Configure>")
    except tk.TclError:
        pass

    for widget in root.winfo_children():
        widget.destroy()

    _w4_items = _w4_build_items()
    _w4_index = 0
    _w4_z_index = 0
    _w4_mode = "image"
    _w4_preview_photo = None

    outer = tk.Frame(root, bg=BG_COLOR)
    outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(1, weight=1)

    title = tk.Label(
        outer,
        text="Window n°4 — Validation et sauvegarde",
        font=("Arial", 18, "bold"),
        bg=BG_COLOR,
        fg=FG_COLOR,
    )
    title.grid(row=0, column=0, sticky="ew", pady=(0, 8))

    content = tk.Frame(outer, bg=BG_COLOR, relief="solid", borderwidth=1)
    content.grid(row=1, column=0, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.columnconfigure(1, weight=0)
    content.rowconfigure(0, weight=1)

    # --- Left: preview area ---
    preview_frame = tk.Frame(content, bg=BG_COLOR)
    preview_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 4), pady=10)
    preview_frame.rowconfigure(0, weight=1)
    preview_frame.columnconfigure(0, weight=1)

    _w4_preview_label = tk.Label(
        preview_frame,
        text="Chargement de la prévisualisation...",
        font=FONT,
        bg="white",
        fg="gray",
    )
    _w4_preview_label.grid(row=0, column=0, sticky="nsew")

    _w4_status_label = tk.Label(
        preview_frame,
        text="",
        font=SMALL_FONT,
        bg=BG_COLOR,
        fg=FG_COLOR,
        anchor="w",
    )
    _w4_status_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))

    # --- Right: z scrollbar + command buttons ---
    right = tk.Frame(content, bg=BG_COLOR)
    right.grid(row=0, column=1, sticky="ns", padx=(4, 10), pady=10)
    right.rowconfigure(0, weight=1)

    _w4_z_scale = tk.Scale(
        right,
        from_=1,
        to=1,
        orient="vertical",
        showvalue=True,
        command=_w4_on_z_changed,
        bg=ACCENT_COLOR_BLUE,
        fg=FG_COLOR,
        length=260,
        label="Z",
    )
    _w4_z_scale.grid(row=0, column=0, rowspan=5, sticky="ns", padx=(0, 8))

    buttons = tk.Frame(right, bg=BG_COLOR)
    buttons.grid(row=0, column=1, sticky="n")

    tk.Button(
        buttons,
        text="Coupe précédente",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        width=16,
        command=_w4_prev_slice,
    ).pack(fill=tk.X, pady=(0, 6))

    tk.Button(
        buttons,
        text="Coupe suivante",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        width=16,
        command=_w4_next_slice,
    ).pack(fill=tk.X, pady=6)

    tk.Button(
        buttons,
        text="Valider la lame",
        font=FONT,
        bg=ACCENT_COLOR_GREEN,
        fg=FG_COLOR,
        width=16,
        command=_w4_validate_slide,
    ).pack(fill=tk.X, pady=6)

    _w4_reject_button = tk.Button(
        buttons,
        text="Rejeter la lame",
        font=FONT,
        bg=ERROR_COLOR,
        fg=FG_COLOR,
        width=16,
        command=_w4_reject_slide,
    )
    _w4_reject_button.pack(fill=tk.X, pady=6)

    _w4_toggle_button = tk.Button(
        buttons,
        text="Afficher le diagramme",
        font=FONT,
        bg=ACCENT_COLOR_BLUE,
        fg=FG_COLOR,
        width=16,
        command=_w4_toggle_mode,
    )
    _w4_toggle_button.pack(fill=tk.X, pady=(20, 6))

    # --- Bottom navigation/export bar ---
    footer = tk.Frame(outer, bg=BG_COLOR, height=60)
    footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    footer.columnconfigure(1, weight=1)
    footer.pack_propagate(False)

    tk.Button(
        footer,
        text="Previous",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        command=lambda: window3(),
    ).pack(side=tk.LEFT, padx=(0, 12), pady=10)

    tk.Button(
        footer,
        text="Sauvegarder dans ./output",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        command=_w4_save_to_output,
    ).pack(side=tk.LEFT, padx=8, pady=10)

    tk.Button(
        footer,
        text="Sauvegarder les résultats",
        font=FONT,
        bg=CLICK_BOXES_COLOR,
        fg=FG_COLOR,
        command=_w4_save_to_selected_folder,
    ).pack(side=tk.RIGHT, padx=(12, 0), pady=10)

    root.bind("<Configure>", _on_w4_configure)
    root.update_idletasks()
    _w4_update_z_scale()
    _w4_refresh_preview()


def _cleanup_on_startup():
    """
    Wipe all work-in-progress and non-final temporary output on launch so the
    app always starts from a clean state.

    Removed entirely (then recreated empty):
      - ./WorkInProgress/   (validated masks, temp_vizu previews, Validation
                             bundles — every restart discards in-progress work)
    Recreated empty:
      - ./output/downsampled4_jpeg    (window 3 quantification JPEG tree)
      - ./output/downsampled20_jpeg   (window 2 alignment/mask JPEG tree)

    Intentionally preserved:
      - ./AtlasImgs/                  (cached atlas slices — expensive to
                                       regenerate, see atlas_position_getter)
      - ./input/                      (source .czi files)
      - ./output/<timestamp>/         (final volumetric exports)
      - ./output/cell_quantification_*/ (final quantification results)
    """
    # 1) Remove the entire WorkInProgress tree and recreate it empty. The
    #    subfolders (temp_vizu, per-czi masks, Validation) are all disposable
    #    and repopulated as the user works through the windows again.
    if os.path.isdir(WORK_IN_PROGRESS_DIR):
        shutil.rmtree(WORK_IN_PROGRESS_DIR, ignore_errors=True)
    os.makedirs(WORK_IN_PROGRESS_DIR, exist_ok=True)
    os.makedirs(TEMP_VIZU_DIR, exist_ok=True)

    # 2) Wipe the non-final downsampled JPEG trees under ./output, but leave
    #    the timestamped final export folders (cell_quantification_*,
    #    <date>_<time>) untouched.
    temp_output_folders = [
        os.path.join(BASE_OUTPUT_DIR, QUANTIFICATION_JPEG_OUTPUT_SUBDIR),
        os.path.join(BASE_OUTPUT_DIR, JPEG_OUTPUT_SUBDIR),
    ]
    for folder in temp_output_folders:
        if not os.path.isdir(folder):
            continue
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)


_cleanup_on_startup()
window1()  # Start with the first window
root.mainloop()
