"""Window 4 — validation visuelle finale + export volumétrique.

Classe Screen : tout l'état (_w4_*) en attributs d'instance. Reproduit
fidèlement window4() (preview image/diagramme, scroll Z, validation/rejet,
exports CSV volumétriques).
"""

import os
import re
import csv
import json
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, filedialog

import numpy as np
from PIL import Image, ImageTk, ImageDraw
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from app.base_screen import BaseScreen
from app.theme import BG_COLOR, FG_COLOR, SMALL_FONT, FONT, CLICK_BOXES_COLOR, ACCENT_COLOR_BLUE
from app.common_widgets import add_help_button
from app.image_utils import get_img_dims
from workers.czi_converter import DOWNSAMPLE_FACTOR, JPEG_OUTPUT_SUBDIR, QUANTIFICATION_JPEG_OUTPUT_SUBDIR
import convert_czi_to_jpeg
from quantification_wrapper import run_quantification
from mask_replacer import (
    filter_cells_by_region, count_cells_per_region,
    combine_and_filter_cell_mask, compute_region_surface_areas_mm2,
    compute_slice_area_mm2,
)
from atlas_position_getter import get_or_create_slice_images  # noqa: F401 (kept for parity)

from app import APP_BASE_DIR

_Z_SLICE_RE = re.compile(r"_z_slice_(\d+)\.jpeg$", re.IGNORECASE)

_ZOOM_MIN = 1.0
_ZOOM_MAX = 20.0
_ZOOM_STEP = 1.25


class Window4Screen(BaseScreen):
    """Window4screen.
    
    Attributs et methodes definis ci-dessous.
    """
    def __init__(self, app):
        """Initialise l'objet et son etat.
        
        Args:
            app (Any): Instance de l'application.
        """
        super().__init__(app)
        self.base = Path(APP_BASE_DIR)
        self.output_dir = self.base / "output"
        self.wip_dir = self.base / "WorkInProgress"

        self.items = []
        self.index = 0
        self.z_index = 0
        self.mode = "image"  # "image" | "diagram"
        self.preview_label = None
        self.preview_photo = None
        self.status_label = None
        self.z_scale = None
        self.toggle_button = None
        self.reject_button = None
        self.quant_map = {}
        self._pixel_size_cache = {}
        self.zoom_state = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
        self.viewport = (0.0, 0.0, 1.0, 1.0)
        self.pan_state = {}
        self.region_opacity = tk.DoubleVar(value=35.0)   # %
        self.cell_opacity = tk.DoubleVar(value=82.0)     # %
        self._composite_cache = None   # (cache_key, PIL RGB image at native res)
        self.reset_zoom_button = None

    # ================================================================ build
    def build(self):
        """Build"""
        try:
            self.root.unbind("<Configure>")
        except tk.TclError:
            pass
        for widget in ():
            pass

        outer = tk.Frame(self.frame, bg=BG_COLOR)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=0)

        header = tk.Frame(outer, bg=BG_COLOR)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(header, text="Window 4 — Validation and export",
                 font=("Arial", 18, "bold"), bg=BG_COLOR, fg=FG_COLOR
                 ).pack(side=tk.LEFT)
        add_help_button(
            header, "Window 4 — Help",
            "WINDOW 4 — Validation and volumetric export\n"
            "------------------------------------------------------------\n\n"
            "PREVIEW (left)\n"
            "  • Mouse wheel : zoom in / out, centered on the cursor.\n"
            "  • Middle mouse button (drag) : pan the image when zoomed.\n"
            "  • 'Reset zoom' button (right) : return to fit-to-window.\n\n"
            "Z SLIDER (right, vertical)\n"
            "  • Scrolls through the Z layers of the current slice.\n\n"
            "TRANSPARENCY (right)\n"
            "  • 'Region mask opacity' : how transparent the colored\n"
            "    anatomical-region overlay is (0% = invisible, 100% = solid).\n"
            "  • 'Cell mask opacity' : how transparent the detected-cell\n"
            "    overlay (and cell dots) is.\n\n"
            "NAVIGATION (right)\n"
            "  • 'Previous slice' / 'Next slice' : move between brain slices.\n"
            "  • 'Show diagram' / 'Show image' : toggle between the bar-chart\n"
            "    of cells-per-region and the overlaid histology image.\n"
            "  • 'Validate slide' : run quantification checks and save to\n"
            "    WorkInProgress/Validation.\n"
            "  • 'Save' / 'Save to...' : export the image, graph and CSVs.\n"
            "  • 'Reject slide' : re-run QuPath quantification on this slice.\n\n"
            "BOTTOM-LEFT\n"
            "  • 'Previous' : go back to Window 3.",
        )

        content = tk.Frame(outer, bg=BG_COLOR, relief="solid", borderwidth=1)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        content.rowconfigure(0, weight=1)

        nav_bar = tk.Frame(outer, bg=BG_COLOR)
        nav_bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        tk.Button(nav_bar, text="Previous", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._go_prev).pack(side=tk.LEFT, padx=4)

        preview_frame = tk.Frame(content, bg=BG_COLOR)
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 4), pady=10)
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self.preview_label = tk.Label(preview_frame, text="Loading preview...",
                                      font=FONT, bg="white", fg="gray")
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        # Zoom + pan bindings (parity with Window 2).
        self.preview_label.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview_label.bind("<Button-4>", self._on_preview_wheel)
        self.preview_label.bind("<Button-5>", self._on_preview_wheel)
        self.preview_label.bind("<Button-2>", self._on_preview_pan_start)
        self.preview_label.bind("<B2-Motion>", self._on_preview_pan_motion)
        self.preview_label.bind("<ButtonRelease-2>", self._on_preview_pan_end)

        self.status_label = tk.Label(preview_frame, text="", font=SMALL_FONT,
                                     bg=BG_COLOR, fg=FG_COLOR, anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        right = tk.Frame(content, bg=BG_COLOR)
        right.grid(row=0, column=1, sticky="ns", padx=(4, 10), pady=10)
        right.rowconfigure(0, weight=1)

        self.z_scale = tk.Scale(right, from_=1, to=1, orient="vertical",
                                showvalue=True, command=self._on_z_changed,
                                bg=ACCENT_COLOR_BLUE, fg=FG_COLOR, length=260, label="Z")
        self.z_scale.grid(row=0, column=0, rowspan=7, sticky="ns", padx=(0, 8))

        buttons = tk.Frame(right, bg=BG_COLOR)
        buttons.grid(row=0, column=1, sticky="n")

        tk.Button(buttons, text="Previous slice", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._prev_slice).pack(fill=tk.X, pady=2)
        tk.Button(buttons, text="Next slice", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._next_slice).pack(fill=tk.X, pady=2)
        self.toggle_button = tk.Button(buttons, text="Show diagram", font=FONT,
                                        bg=CLICK_BOXES_COLOR, fg=FG_COLOR, command=self._toggle_mode)
        self.toggle_button.pack(fill=tk.X, pady=2)
        tk.Button(buttons, text="Validate slide", font=FONT, bg="#00cc66", fg=FG_COLOR,
                  command=self._validate_slide).pack(fill=tk.X, pady=2)
        tk.Button(buttons, text="Save", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._save_to_output).pack(fill=tk.X, pady=2)
        tk.Button(buttons, text="Save to...", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._save_to_selected_folder).pack(fill=tk.X, pady=2)
        self.reject_button = tk.Button(buttons, text="Reject slide", font=FONT,
                                        bg="#ff0000", fg=FG_COLOR, command=self._reject_slide)
        self.reject_button.pack(fill=tk.X, pady=2)
        self.reset_zoom_button = tk.Button(buttons, text="Reset zoom", font=FONT,
                                            bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                                            command=self._reset_zoom)
        self.reset_zoom_button.pack(fill=tk.X, pady=2)

        # --- Transparency controls ---
        transp = tk.LabelFrame(buttons, text="Transparency", font=SMALL_FONT,
                               bg=BG_COLOR, fg=FG_COLOR)
        transp.pack(fill=tk.X, pady=(6, 2))
        tk.Label(transp, text="Region mask opacity", font=SMALL_FONT,
                 bg=BG_COLOR, fg=FG_COLOR).pack(anchor="w", padx=4)
        tk.Scale(transp, from_=0, to=100, orient="horizontal",
                 variable=self.region_opacity, font=SMALL_FONT,
                 command=lambda _: self._schedule_refresh()).pack(fill=tk.X, padx=4)
        tk.Label(transp, text="Cell mask opacity", font=SMALL_FONT,
                 bg=BG_COLOR, fg=FG_COLOR).pack(anchor="w", padx=4)
        tk.Scale(transp, from_=0, to=100, orient="horizontal",
                 variable=self.cell_opacity, font=SMALL_FONT,
                 command=lambda _: self._schedule_refresh()).pack(fill=tk.X, padx=4)

        self.root.bind("<Configure>", self._on_configure)

        self.frame.pack(fill=tk.BOTH, expand=True)
        self.items = self._build_items()
        self.index = 0
        self.z_index = 0
        self._update_z_scale()
        self._refresh_preview()

    # ============================================================== data build
    def _safe_folder_name(self, name):
        """Safe Folder Name (usage interne).
        
        Args:
            name (Any): Parametre name.
        
        Returns:
            Any: Resultat.
        """
        return re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip(" .") or "unnamed"

    def _timestamp_folder_name(self):
        """Timestamp Folder Name (usage interne)."""
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _find_czi_path(self, czi_stem):
        """Find Czi Path (usage interne).

        Resolves the original .czi file for a given czi stem, searching
        recursively under self.state.czi_folder_path. Returns the Path or None.

        Args:
            czi_stem (Any): Parametre czi_stem.

        Returns:
            Any: Resultat.
        """
        if not czi_stem:
            return None
        input_dir = Path(getattr(self.state, "czi_folder_path", "./input"))
        try:
            for p in convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True):
                if p.stem == czi_stem:
                    return p
        except Exception:
            return None
        return None

    def _latest_quantification_dir(self):
        """Latest Quantification repertoire (usage interne).
        
        Returns:
            Any: Resultat.
        """
        candidates = [p for p in self.output_dir.glob("cell_quantification_*") if p.is_dir()]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def _build_quant_map(self):
        """Build Quant Map (usage interne).
        
        Returns:
            Any: Resultat.
        """
        qdirs = [p for p in self.output_dir.glob("cell_quantification_*") if p.is_dir()]
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

    def _sorted_z_images(self, roi_dir):
        """Sorted couche Z Images (usage interne).
        
        Args:
            roi_dir (Any): Repertoire (dossier).
        
        Returns:
            Any: Resultat.
        """
        candidates = []
        for f in roi_dir.glob("*_z_slice_*.jpeg"):
            m = _Z_SLICE_RE.search(f.name)
            if m:
                candidates.append((int(m.group(1)), f))
        candidates.sort(key=lambda t: t[0])
        return [p for _, p in candidates]

    def _match_czi_stem(self, roi_name, czi_stems):
        """Match fichier .czi Stem (usage interne).
        
        Args:
            roi_name (Any): Nom de base (sans extension).
            czi_stems (Any): Parametre czi_stems.
        
        Returns:
            Any: Resultat.
        """
        for stem in czi_stems:
            prefix = stem + "_"
            if roi_name.startswith(prefix) and roi_name[len(prefix):].isdigit():
                return stem
        m = re.match(r"^(.*)_(\d+)$", roi_name)
        return m.group(1) if m else roi_name

    def _build_items(self):
        """Build Items (usage interne).
        
        Returns:
            Any: Resultat.
        """
        self.quant_map = self._build_quant_map()
        output_base = self.output_dir / JPEG_OUTPUT_SUBDIR
        if not output_base.is_dir():
            return []
        input_dir = Path(self.state.czi_folder_path)
        try:
            czi_stems = sorted(
                (p.stem for p in convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True)),
                key=len, reverse=True,
            )
        except Exception:
            czi_stems = []
        items = []
        for roi_dir in sorted(output_base.iterdir()):
            if not roi_dir.is_dir():
                continue
            z_images = self._sorted_z_images(roi_dir)
            if not z_images:
                continue
            roi_name = roi_dir.name
            czi_stem = self._match_czi_stem(roi_name, czi_stems)
            mask_dir = self.wip_dir / czi_stem / "masks"
            first_image_name = z_images[0].name
            quant = self.quant_map.get(first_image_name)
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

    # ================================================================ accessors
    def _current_item(self):
        """Current Item (usage interne).
        
        Returns:
            Any: Resultat.
        """
        if not self.items:
            return None
        idx = max(0, min(self.index, len(self.items) - 1))
        return self.items[idx]

    def _current_image_path(self, item=None):
        """Current Image Path (usage interne).
        
        Args:
            item (Any): Parametre item.
        
        Returns:
            Any: Resultat.
        """
        item = item or self._current_item()
        if item is None:
            return None
        z_images = item.get("z_images", [])
        if not z_images:
            return None
        idx = max(0, min(self.z_index, len(z_images) - 1))
        return z_images[idx]

    def _cells(self, item=None):
        """Cells (usage interne).
        
        Args:
            item (Any): Parametre item.
        
        Returns:
            Any: Resultat.
        """
        item = item or self._current_item()
        if item is None:
            return []
        return list((item.get("quant_data") or {}).get("cells") or [])

    # ================================================================ preview
    def _load_mask_rgba(self, mask_path, target_size, alpha=None):
        """Load Mask Rgba (usage interne).

        Args:
            mask_path (Any): Chemin vers le fichier.
            target_size (Any): Parametre target_size.
            alpha (Any): Parametre alpha (None -> self.region_opacity %).

        Returns:
            Any: Resultat.
        """
        if alpha is None:
            alpha = max(0.0, min(1.0, self.region_opacity.get() / 100.0))
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

    def _overlay_cell_mask(self, mask_path, target_size, alpha=None):
        """Overlay Cell Mask (usage interne).

        Mirrors _load_mask_rgba exactly: reads self.cell_opacity and returns a
        single opacity-aware RGBA overlay (no separate drawn dots), so the cell
        transparency behaves identically to the region transparency.

        Args:
            mask_path (Any): Chemin vers le fichier.
            target_size (Any): Parametre target_size.
            alpha (Any): Cell overlay strength 0..1 (None -> self.cell_opacity %).

        Returns:
            Any: Resultat (RGBA overlay or None).
        """
        if alpha is None:
            alpha = max(0.0, min(1.0, self.cell_opacity.get() / 100.0))
        if not mask_path or not os.path.exists(mask_path):
            return None
        try:
            mask = Image.open(mask_path).convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)
            arr = np.asarray(mask).copy()
            non_bg = np.any(arr[:, :, :3] > 8, axis=2)
            # Tint precise QuPath cells cyan so they read distinctly from regions.
            arr[:, :, 0] = non_bg.astype(np.uint8) * 0
            arr[:, :, 1] = non_bg.astype(np.uint8) * 200
            arr[:, :, 2] = non_bg.astype(np.uint8) * 255
            arr[:, :, 3] = (non_bg.astype(np.uint8) * int(255 * alpha))
            return Image.fromarray(arr, mode="RGBA")
        except Exception as exc:
            print(f"[window4] cannot load cell mask {mask_path}: {exc}")
            return None

    def _filtered_cells(self, item=None):
        """Filtered Cells (usage interne).
        
        Args:
            item (Any): Parametre item.
        
        Returns:
            Any: Resultat.
        """
        item = item or self._current_item()
        if item is None:
            return []
        region_png = item.get("mask_png")
        cells = self._cells(item)
        return filter_cells_by_region(cells, str(region_png) if region_png else None)

    def _make_image_preview(self, item=None):
        """Make Image Preview (usage interne).
        
        Args:
            item (Any): Parametre item.
        
        Returns:
            Any: Resultat.
        """
        item = item or self._current_item()
        image_path = self._current_image_path(item)
        if item is None or image_path is None or not os.path.exists(image_path):
            return None
        region_png = item.get("mask_png")
        base = Image.open(image_path).convert("RGBA")
        region_overlay = self._load_mask_rgba(region_png, base.size)
        if region_overlay is not None:
            base = Image.alpha_composite(base, region_overlay)
        cell_overlay = self._overlay_cell_mask(item.get("cell_mask_path"), base.size)
        if cell_overlay is not None:
            base = Image.alpha_composite(base, cell_overlay)
        return base.convert("RGB")

    def _make_diagram_preview(self, item=None):
        """Make Diagram Preview (usage interne).
        
        Args:
            item (Any): Parametre item.
        
        Returns:
            Any: Resultat.
        """
        item = item or self._current_item()
        if item is None:
            return None
        mask_png = item.get("mask_png")
        cells = self._filtered_cells(item)
        fig = Figure(figsize=(9, 6), dpi=100)
        ax = fig.add_subplot(111)
        fig.patch.set_facecolor("white")
        if not mask_png or not os.path.exists(str(mask_png)):
            ax.text(0.5, 0.5, "Region mask missing", ha="center", va="center",
                    transform=ax.transAxes, fontsize=14, color="#b00020")
            ax.set_axis_off()
        elif not cells:
            ax.text(0.5, 0.5, "No cells detected", ha="center", va="center",
                    transform=ax.transAxes, fontsize=14, color="#666666")
            ax.set_axis_off()
        else:
            rows = count_cells_per_region(str(mask_png), cells)
            if not rows:
                ax.text(0.5, 0.5, "No cells in labelled regions", ha="center",
                        va="center", transform=ax.transAxes, fontsize=13, color="#666666")
                ax.set_axis_off()
            else:
                names = [r["name"] for r in rows]
                counts = [r["count"] for r in rows]
                colors = [(r["rgb"][0] / 255.0, r["rgb"][1] / 255.0, r["rgb"][2] / 255.0) for r in rows]
                y_pos = list(range(len(rows)))
                ax.barh(y_pos, counts, color=colors, edgecolor="black", linewidth=0.5)
                ax.set_yticks(y_pos)
                ax.set_yticklabels(names, fontsize=9)
                ax.invert_yaxis()
                ax.set_xlabel("Number of cells", fontsize=10)
                ax.grid(axis="x", linestyle="--", alpha=0.4)
                ax.set_title(
                    f"{item.get('roi_folder_name', '')} — {sum(counts)} cell(s) in {len(rows)} region(s)",
                    fontsize=11,
                )
                max_count = max(counts) if counts else 1
                for i, c in enumerate(counts):
                    ax.text(c + max_count * 0.012, i, str(c), va="center", fontsize=9)
                ax.set_xlim(0, max_count * 1.12 if max_count else 1)
        fig.tight_layout()
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        rgba_buf = np.asarray(canvas.buffer_rgba())
        rgb = rgba_buf[..., :3].copy()
        return Image.fromarray(rgb, mode="RGB")

    # ============================================================ zoom / pan
    def _composite_preview_base(self, item):
        """Build (and cache) the fully-composited image at native resolution.

        Cache keyed on (item id, z_index, region_opacity, cell_opacity) so the
        expensive region+cell compositing runs only when inputs change; zoom/pan
        then just crops from the cached image.

        Args:
            item (dict): current item.

        Returns:
            PIL.Image.RGB or None.
        """
        if item is None:
            return None
        cache_key = (
            id(item), self.z_index,
            round(self.region_opacity.get(), 1),
            round(self.cell_opacity.get(), 1),
        )
        if self._composite_cache is not None and self._composite_cache[0] == cache_key:
            return self._composite_cache[1]
        base = self._make_image_preview(item)
        if base is None:
            self._composite_cache = None
            return None
        self._composite_cache = (cache_key, base)
        return base

    def _invalidate_composite_cache(self):
        """Drop the cached composited image (e.g. on slice/nav change)."""
        self._composite_cache = None

    def _zoom_viewport(self):
        """Compute the normalized crop rectangle for the current zoom state.

        Returns:
            tuple[float, float, float, float]: (nx0, ny0, nx1, ny1) in 0..1.
        """
        st = self.zoom_state
        zoom = max(1.0, st["zoom"])
        half = 0.5 / zoom
        cx, cy = st["cx"], st["cy"]
        nx0 = max(0.0, cx - half)
        ny0 = max(0.0, cy - half)
        nx1 = min(1.0, cx + half)
        ny1 = min(1.0, cy + half)
        if (nx1 - nx0) < 2 * half and (nx1 - nx0) < 1.0:
            cx = (nx0 + nx1) / 2
            nx0 = max(0.0, cx - half)
            nx1 = min(1.0, cx + half)
            self.zoom_state["cx"] = (nx0 + nx1) / 2
        if (ny1 - ny0) < 2 * half and (ny1 - ny0) < 1.0:
            cy = (ny0 + ny1) / 2
            ny0 = max(0.0, cy - half)
            ny1 = min(1.0, cy + half)
            self.zoom_state["cy"] = (ny0 + ny1) / 2
        return (nx0, ny0, nx1, ny1)

    def _on_preview_wheel(self, event):
        """Mouse-wheel zoom on the preview (parity with Window 2)."""
        if event.num in (4, 5):
            direction = 1 if event.num == 4 else -1
        else:
            direction = 1 if (event.delta or 0) > 0 else -1
        if direction == 0:
            return
        st = self.zoom_state
        old_zoom = st["zoom"]
        new_zoom = old_zoom * _ZOOM_STEP if direction > 0 else old_zoom / _ZOOM_STEP
        new_zoom = min(_ZOOM_MAX, max(_ZOOM_MIN, new_zoom))
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        # Keep the point under the cursor stable.
        disp = self._displayed_size()
        label = self.preview_label
        if disp is not None and label is not None:
            img_w, img_h = disp
            if img_w > 1 and img_h > 1:
                offset_x = (label.winfo_width() - img_w) // 2
                offset_y = (label.winfo_height() - img_h) // 2
                px = event.x - offset_x
                py = event.y - offset_y
                if 0 <= px < img_w and 0 <= py < img_h:
                    nx_view = px / img_w
                    ny_view = py / img_h
                    nx0, ny0, nx1, ny1 = self.viewport
                    span_x = max(1e-6, nx1 - nx0)
                    span_y = max(1e-6, ny1 - ny0)
                    src_nx = nx0 + nx_view * span_x
                    src_ny = ny0 + ny_view * span_y
                    st["zoom"] = new_zoom
                    half_new = 0.5 / new_zoom
                    cx = src_nx + (0.5 - nx_view) * half_new * 2
                    cy = src_ny + (0.5 - ny_view) * half_new * 2
                    self.zoom_state["cx"] = min(1.0, max(0.0, cx))
                    self.zoom_state["cy"] = min(1.0, max(0.0, cy))
                    self._schedule_refresh()
                    return
        st["zoom"] = new_zoom
        self._schedule_refresh()

    def _displayed_size(self):
        """Return (w, h) of the currently displayed PhotoImage, or None."""
        photo = self.preview_photo
        if photo is None:
            return None
        try:
            return photo.width(), photo.height()
        except Exception:
            return None

    def _on_preview_pan_start(self, event):
        """Middle-mouse pan start (parity with Window 2)."""
        self.pan_state["start_x"] = event.x
        self.pan_state["start_y"] = event.y
        self.pan_state["start_cx"] = self.zoom_state["cx"]
        self.pan_state["start_cy"] = self.zoom_state["cy"]
        self.pan_state["viewport"] = self.viewport

    def _on_preview_pan_motion(self, event):
        """Middle-mouse pan motion (parity with Window 2)."""
        if not self.pan_state:
            return
        disp = self._displayed_size()
        if disp is None:
            return
        img_w, img_h = disp
        if img_w <= 1 or img_h <= 1:
            return
        dx = event.x - self.pan_state.get("start_x", 0)
        dy = event.y - self.pan_state.get("start_y", 0)
        nx0, ny0, nx1, ny1 = self.pan_state.get("viewport", (0.0, 0.0, 1.0, 1.0))
        span_x = max(1e-6, nx1 - nx0)
        span_y = max(1e-6, ny1 - ny0)
        dnx = -(dx / img_w) * span_x
        dny = -(dy / img_h) * span_y
        self.zoom_state["cx"] = min(1.0, max(0.0, self.pan_state["start_cx"] + dnx))
        self.zoom_state["cy"] = min(1.0, max(0.0, self.pan_state["start_cy"] + dny))
        self._schedule_refresh()

    def _on_preview_pan_end(self, _event=None):
        """Middle-mouse pan end."""
        self.pan_state.clear()

    def _reset_zoom(self):
        """Reset zoom to fit-to-window."""
        self.zoom_state = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
        self.viewport = (0.0, 0.0, 1.0, 1.0)
        self._refresh_preview()

    def _get_preview_size(self):
        """Get Preview Size (usage interne).
        
        Returns:
            Any: Resultat.
        """
        win_w = self.root.winfo_width() if self.root.winfo_width() > 100 else 800
        win_h = self.root.winfo_height() if self.root.winfo_height() > 100 else 600
        return max(250, int(win_w * 0.66) - 45), max(220, win_h - 150)

    def _set_status(self, text):
        """Set Status (usage interne).
        
        Args:
            text (Any): Texte.
        """
        if self.status_label is not None and self.status_label.winfo_exists():
            self.status_label.config(text=text)

    def _update_z_scale(self):
        """Update couche Z Scale (usage interne)."""
        if self.z_scale is None:
            return
        item = self._current_item()
        count = len(item.get("z_images", [])) if item else 1
        count = max(1, count)
        try:
            self.z_scale.config(from_=1, to=count, state=(tk.NORMAL if count > 1 else tk.DISABLED))
            self.z_scale.set(max(1, min(self.z_index + 1, count)))
        except Exception:
            pass

    def _schedule_refresh(self, delay_ms=40):
        """Debounced refresh for rapid-input callbacks (opacity drag, zoom, pan).

        Coalesces a burst of events into a single repaint per `delay_ms`, so a
        slider drag or pan motion doesn't trigger one full re-composite per event.
        The composite cache still prevents redundant work when inputs are unchanged.
        """
        token = (getattr(self, "_refresh_token", 0) + 1)
        self._refresh_token = token
        try:
            self.root.after(delay_ms, lambda t=token: self._refresh_if_current(t))
        except Exception:
            self._refresh_preview()

    def _refresh_if_current(self, token):
        if getattr(self, "_refresh_token", 0) == token:
            self._refresh_preview()

    def _refresh_preview(self):
        """Refresh Preview (usage interne)."""
        if self.preview_label is None or not self.preview_label.winfo_exists():
            return
        item = self._current_item()
        if item is None:
            self.preview_label.config(image="", text="No slide available.\nRun Windows 2 and 3 first.")
            self._set_status("No items found in ./output/downsampled20_jpeg.")
            return
        if self.mode == "image":
            pil_img = self._composite_preview_base(item)
        else:
            pil_img = self._make_diagram_preview(item)
        if pil_img is None:
            self.preview_label.config(image="", text="Preview unavailable")
            return
        # Apply zoom cropping for image mode (diagram stays fit-to-window).
        if self.mode == "image" and self.zoom_state["zoom"] > 1.0 + 1e-6:
            self.viewport = self._zoom_viewport()
            nx0, ny0, nx1, ny1 = self.viewport
            sw, sh = pil_img.size
            left = int(round(nx0 * sw))
            upper = int(round(ny0 * sh))
            right = max(left + 1, int(round(nx1 * sw)))
            lower = max(upper + 1, int(round(ny1 * sh)))
            pil_img = pil_img.crop((left, upper, right, lower))
        max_w, max_h = self._get_preview_size()
        new_w, new_h = get_img_dims(pil_img.width, pil_img.height, max_w, max_h)
        display = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(display)
        self.preview_photo = photo
        self.preview_label.config(image=photo, text="")
        z_count = len(item.get("z_images", []))
        cells = self._filtered_cells(item)
        missing = []
        if not os.path.exists(item.get("mask_png", "")):
            missing.append("region mask")
        if not item.get("quant_json"):
            missing.append("quantification")
        missing_txt = f" — missing: {', '.join(missing)}" if missing else ""
        zoom_txt = f" | zoom {self.zoom_state['zoom']:.1f}x" if self.zoom_state["zoom"] > 1.0 + 1e-6 else ""
        self._set_status(
            f"{self.index + 1}/{len(self.items)} | {item['roi_folder_name']} | "
            f"Z {self.z_index + 1}/{max(1, z_count)} | total: {len(cells)}"
            f"{zoom_txt}"
            f"{missing_txt}"
        )
        if self.toggle_button is not None and self.toggle_button.winfo_exists():
            self.toggle_button.config(text=("Show diagram" if self.mode == "image" else "Show image"))

    # ================================================================ handlers
    def _on_z_changed(self, value):
        """On couche Z Changed (usage interne).

        Args:
            value (Any): Valeur.
        """
        try:
            self.z_index = max(0, int(float(value)) - 1)
        except Exception:
            self.z_index = 0
        self._invalidate_composite_cache()
        self._refresh_preview()

    def _prev_slice(self):
        """Prev Slice (usage interne)."""
        if self.index > 0:
            self.index -= 1
            self.z_index = 0
            self._reset_zoom()
            self._update_z_scale()
            self._refresh_preview()

    def _next_slice(self):
        """Next Slice (usage interne)."""
        if self.index < len(self.items) - 1:
            self.index += 1
            self.z_index = 0
            self._reset_zoom()
            self._update_z_scale()
            self._refresh_preview()

    def _toggle_mode(self):
        """Toggle Mode (usage interne)."""
        self.mode = "diagram" if self.mode == "image" else "image"
        self._refresh_preview()

    def _on_configure(self, _event=None):
        """On Configure (usage interne)."""
        self._schedule_refresh()

    # ================================================================ exports
    def _write_czi_summary_csvs(self, dest_dir):
        """Write Czi Summary Csvs (usage interne).

        One combined CSV per .czi stem, each containing two tables:

        Table 1 — region rollup (one row per region):
            region_name, cell_volume (mm3), cell_number
          where cell_volume = surface_mm2 * (depth_um + interslice_um) / 1e3.

        Table 2 — per-ROI x region detail:
            ROI_name, region_name, brain_area (mm2, full section), num_cell,
            surface (mm2, region), slice_depth (um), interslice (um),
            cell concentration (cells/mm2 = num_cell / surface_mm2)

        Only .czi stems that have at least one region with data are emitted.
        """
        from convert_czi_to_jpeg import get_czi_pixel_size_um

        dest_dir.mkdir(parents=True, exist_ok=True)
        depth_um = self.state.slice_depth_um
        interslice_um = self.state.interslice_um
        depth_mm = depth_um * 1e-3
        thickness_um = depth_um + interslice_um

        def get_pixel_size(czi_stem):
            """Get Pixel Size"""
            if czi_stem not in self._pixel_size_cache:
                czi_path = self._find_czi_path(czi_stem)
                self._pixel_size_cache[czi_stem] = None if czi_path is None else get_czi_pixel_size_um(str(czi_path))
            return self._pixel_size_cache[czi_stem]

        # Group items by czi stem, preserving first-seen order.
        by_czi = {}
        for item in self.items:
            czi_stem = item.get("czi_stem", "")
            by_czi.setdefault(czi_stem, []).append(item)

        written = []
        for czi_stem, items in by_czi.items():
            region_t1 = {}   # lid -> {"name", "volume", "count"}
            detail_t2 = []    # list of dict rows
            any_data = False

            for item in items:
                roi_name = item.get("roi_folder_name", "")
                mask_png = item.get("mask_png")
                cells = self._filtered_cells(item)
                if not mask_png or not os.path.exists(str(mask_png)):
                    continue
                pixel_size = get_pixel_size(czi_stem)
                cell_rows = count_cells_per_region(str(mask_png), cells)
                cell_counts = {r["label"]: r["count"] for r in cell_rows}
                surface = {}
                brain_area_mm2 = 0.0
                if pixel_size and pixel_size > 0:
                    surface = compute_region_surface_areas_mm2(
                        str(mask_png), pixel_size, downsample=DOWNSAMPLE_FACTOR)
                    brain_area_mm2 = compute_slice_area_mm2(
                        str(mask_png), pixel_size, downsample=DOWNSAMPLE_FACTOR)
                all_labels = sorted(set(list(cell_counts.keys()) + list(surface.keys())))
                for lid in all_labels:
                    n_cells = cell_counts.get(lid, 0)
                    surf_mm2 = surface.get(lid, {}).get("surface_mm2", 0.0)
                    region_name = surface.get(lid, {}).get("name") or next(
                        (r["name"] for r in cell_rows if r["label"] == lid), str(lid))
                    concentration = (n_cells / surf_mm2) if surf_mm2 > 0 else ""
                    # Table 1 rollup (cell volume per region, mm3).
                    if surf_mm2 > 0 and thickness_um > 0:
                        cell_volume = surf_mm2 * thickness_um / 1e3
                    else:
                        cell_volume = ""
                    agg = region_t1.setdefault(lid, {"name": region_name, "volume": 0.0, "count": 0})
                    agg["name"] = region_name
                    agg["count"] += n_cells
                    if cell_volume != "":
                        agg["volume"] += cell_volume
                    # Table 2 detail (per ROI x region).
                    detail_t2.append({
                        "roi_name": roi_name,
                        "region_name": region_name,
                        "brain_area_mm2": f"{brain_area_mm2:.6f}" if brain_area_mm2 else "",
                        "num_cell": n_cells,
                        "surface_mm2": f"{surf_mm2:.6f}" if surf_mm2 else "",
                        "slice_depth_um": f"{depth_um:.4f}" if depth_um > 0 else "",
                        "interslice_um": f"{interslice_um:.4f}" if interslice_um > 0 else "0.0000",
                        "cell_concentration_cells_per_mm2": f"{concentration:.4f}" if concentration != "" else "",
                    })
                    if n_cells or surf_mm2 > 0:
                        any_data = True

            if not any_data and not region_t1:
                continue

            safe_stem = self._safe_folder_name(czi_stem)
            out_csv = dest_dir / f"{safe_stem}_summary.csv"
            with open(out_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # ---- Table 1 : region rollup ----
                writer.writerow([f"# {czi_stem}"])
                writer.writerow(["region_name", "cell_volume (mm3)", "cell_number (cells)"])
                for lid in sorted(region_t1.keys()):
                    agg = region_t1[lid]
                    writer.writerow([
                        agg["name"],
                        f"{agg['volume']:.6f}" if agg["volume"] else "0.000000",
                        agg["count"],
                    ])
                # ---- Table 2 : ROI x region detail ----
                writer.writerow([])
                writer.writerow([f"# {czi_stem} — detail"])
                writer.writerow([
                    "ROI_name", "region_name", "brain_area (mm2)", "num_cell (cells)",
                    "surface (mm2)", "slice_depth (um)", "interslice (um)",
                    "cell concentration (cells/mm2)",
                ])
                for row in detail_t2:
                    writer.writerow([
                        row["roi_name"], row["region_name"], row["brain_area_mm2"],
                        row["num_cell"], row["surface_mm2"], row["slice_depth_um"],
                        row["interslice_um"], row["cell_concentration_cells_per_mm2"],
                    ])
            written.append(out_csv)
        return written

    def _save_bundle(self, dest_root):
        """Save Bundle (usage interne).
        
        Args:
            dest_root (Any): Parametre dest_root.
        
        Returns:
            Any: Resultat.
        """
        item = self._current_item()
        if item is None:
            messagebox.showwarning("Save", "No slide to save.")
            return None
        dest_dir = Path(dest_root) / self._safe_folder_name(item.get("czi_stem", "czi"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        image_preview = self._make_image_preview(item)
        diagram_preview = self._make_diagram_preview(item)
        if image_preview is None:
            raise RuntimeError("Cannot generate image+2masks.jpeg")
        if diagram_preview is None:
            raise RuntimeError("Cannot generate graph.jpeg")
        image_preview.save(dest_dir / "image+2masks.jpeg", format="JPEG", quality=95)
        diagram_preview.save(dest_dir / "graph.jpeg", format="JPEG", quality=95)
        cell_mask = item.get("cell_mask_path")
        region_png = item.get("mask_png")
        if cell_mask and os.path.exists(str(cell_mask)):
            combined_path = dest_dir / "combined_cell_mask.png"
            combine_and_filter_cell_mask(
                str(cell_mask),
                str(region_png) if region_png and os.path.exists(str(region_png)) else None,
                str(combined_path),
            )
        # One combined CSV per .czi stem (two tables: region rollup + detail).
        try:
            self._write_czi_summary_csvs(Path(dest_root))
        except Exception as exc:
            print(f"[volumetric] Could not generate per-czi CSVs: {exc}")
        return dest_dir

    def _validate_slide(self):
        """Validate Slide (usage interne)."""
        try:
            dest = self._save_bundle(self.wip_dir / "Validation")
        except Exception as exc:
            messagebox.showerror("Validation", str(exc))
            return
        messagebox.showinfo("Validation", f"Slide validated in:\n{dest}")

    def _save_to_output(self):
        """Save To Output (usage interne)."""
        try:
            dest = self._save_bundle(self.output_dir / self._timestamp_folder_name())
        except Exception as exc:
            messagebox.showerror("Save", str(exc))
            return
        messagebox.showinfo("Save", f"Results saved in:\n{dest}")

    def _save_to_selected_folder(self):
        """Save To Selected Folder (usage interne)."""
        folder = filedialog.askdirectory(title="Choose the save folder", mustexist=True)
        if not folder:
            return
        try:
            dest = self._save_bundle(Path(folder) / self._timestamp_folder_name())
        except Exception as exc:
            messagebox.showerror("Save", str(exc))
            return
        messagebox.showinfo("Save", f"Results saved in:\n{dest}")

    def _refresh_after_quantification(self, output_dir):
        """Refresh After Quantification (usage interne).

        Args:
            output_dir (Any): Repertoire (dossier).
        """
        self.quant_map = self._build_quant_map()
        self.items = self._build_items()
        self._update_z_scale()
        self._refresh_preview()
        if self.reject_button is not None and self.reject_button.winfo_exists():
            self.reject_button.config(state=tk.NORMAL)
        self._set_status(f"Re-quantification finished: {output_dir}")

    def _reject_slide(self):
        """Reject Slide (usage interne)."""
        item = self._current_item()
        image_path = self._current_image_path(item)
        if item is None or image_path is None:
            messagebox.showwarning("Reject", "No image to re-quantify.")
            return
        q4_dir = self.output_dir / QUANTIFICATION_JPEG_OUTPUT_SUBDIR / item["roi_folder_name"]
        q4_image = q4_dir / image_path.name
        if not q4_image.exists():
            messagebox.showerror("Reject", f"4x JPEG not found:\n{q4_image}")
            return
        if self.reject_button is not None:
            self.reject_button.config(state=tk.DISABLED)
        self._set_status("Re-quantification in background...")
        output_dir = self.output_dir / f"cell_quantification_{datetime.now().strftime('%Y%m%d_%H%M%S')}_rerun"

        def worker():
            """Worker"""
            try:
                run_quantification([q4_image], output_dir=output_dir)
                self.root.after(0, lambda: self._refresh_after_quantification(output_dir))
            except Exception as exc:
                def show_error():
                    """Show Error"""
                    if self.reject_button is not None and self.reject_button.winfo_exists():
                        self.reject_button.config(state=tk.NORMAL)
                    messagebox.showerror("Reject", str(exc))
                    self._refresh_preview()
                self.root.after(0, show_error)

        threading.Thread(target=worker, daemon=True).start()

    # ============================================================= navigation
    def _go_prev(self):
        """Go Prev (usage interne)."""
        from screens.window3_quantify import Window3Screen
        self.app.show(Window3Screen)

    def _go_next(self):
        """Go Next (usage interne)."""
        # Window 4 is the last step; "Next" closes the app cleanly.
        self.app.root.quit()
