"""Window 2 — image management (2x2 grid: MRI, Histology, Atlas, Alignment).

Classe Screen : tout l'état (zoom, markers, ROI, slider) est encapsulé en
attributs d'instance. Reproduit fidèlement le comportement de l'ancien
window2() (zoom/pan molette, placement de marqueurs, slider atlas, workflow
multi-.czi avec validation/annulation).
"""

import os
import re
import threading
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk, ImageDraw, ImageFont

from app.base_screen import BaseScreen
from app.theme import (
    BG_COLOR, FG_COLOR, SMALL_FONT, FONT, CLICK_BOXES_COLOR,
    ACCENT_COLOR_BLUE, ACCENT_COLOR_GREEN, ERROR_COLOR,
)
from app.image_utils import get_img_dims, _resolve_image_path
from app.common_widgets import add_help_button
from workers.czi_converter import (
    DOWNSAMPLE_FACTOR, JPEG_OUTPUT_SUBDIR,
    start_conversions,
)

from atlas_position_getter import get_depth_range, get_or_create_slice_images
from mask_replacer import replace_mask, save_mask_pair

_ZOOM_MIN = 1.0
_ZOOM_MAX = 20.0
_ZOOM_STEP = 1.25
_Z_SLICE_RE = re.compile(r"_z_slice_(\d+)\.jpeg$", re.IGNORECASE)


class Window2Screen(BaseScreen):
    """Window2screen.
    
    Attributs et methodes definis ci-dessous.
    """
    def __init__(self, app, czi_folder_path=None):
        """Initialise l'objet et son etat.
        
        Args:
            app (Any): Instance de l'application.
            czi_folder_path (Any): Chemin vers le fichier.
        """
        super().__init__(app)
        self.base = self.state.base_dir()
        self.output_dir = self.base / "output"
        self.wip_dir = self.base / "WorkInProgress"
        if czi_folder_path is not None:
            self.state.czi_folder_path = czi_folder_path

        # État d'instance.
        self.labels = {}
        self.tl_scale = None
        self.tl_value_label = None
        self.marker_active = False
        self.drag_state = None
        self.marker_points = {"tl": [], "tr": []}
        self.marker_order = []
        self.marker_buttons = {}
        self.br_result_path = None
        self.current_coronal_path = None
        self.current_atlas_path = None
        self.pending_atlas_update_id = None
        self.current_atlas_depth = 0
        self.images = {}
        self.zoom_state = {k: {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
                           for k in ("tl", "tr", "bl", "br")}
        self.viewports = {k: (0.0, 0.0, 1.0, 1.0) for k in ("tl", "tr", "bl", "br")}
        self.pan_state = {}
        self.source_cache = {}
        self.roi_items = []
        self.roi_index = -1
        self.current_histology_path = None
        self.roi_poll_id = None
        self.awaiting_next_roi = False
        self.mask_opacity = 10          # % visibility of the atlas label overlay over MRI (90% transparent)
        self.mask_scale = None

    # ============================================================ build (layout)
    def build(self):
        """Build"""
        for k in self.zoom_state:
            self.zoom_state[k] = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
            self.viewports[k] = (0.0, 0.0, 1.0, 1.0)
        self.source_cache.clear()
        self._cancel_roi_poll()
        self._cancel_pending_atlas()

        outer = tk.Frame(self.frame, bg=BG_COLOR)
        outer.pack(fill=tk.BOTH, expand=True)
        # Three explicit rows: header (fixed), image grid (absorbs slack),
        # buttons (fixed). This guarantees the button bar can never be
        # compressed by the 2x2 image grid or the header.
        outer.grid_rowconfigure(0, weight=0)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_rowconfigure(2, weight=0)
        outer.grid_columnconfigure(0, weight=1)

        # Top header: title (left) + help button (right).
        header = tk.Frame(outer, bg=BG_COLOR)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        tk.Label(header, text="Window 2 — Mask + alignment 2x2",
                 font=("Arial", 16, "bold"), bg=BG_COLOR, fg=FG_COLOR
                 ).pack(side=tk.LEFT)
        add_help_button(
            header, "Window 2 — Help",
            "WINDOW 2 — Mask placement + 2x2 alignment\\n"
            "------------------------------------------------\\n\\n"
            "FOUR PANES (MRI / Histology / Atlas / Alignment)\\n"
            "  • Markers (dots) can be placed on the MRI and Histology\\n"
            "    panes only (2 aligned points per pane).\\n"
            "  • To PLACE a marker : click 'Place markers' first (toggles ON,\\n"
            "    button turns blue), then click on the MRI or Histology pane.\\n"
            "  • To MOVE a marker : press and hold the LEFT mouse button on an\\n"
            "    existing dot and drag it to a new position (works whether or\\n"
            "    not 'Place markers' mode is active).\\n"
            "  • MOUSE WHEEL : zoom a pane in / out, centered on the cursor.\\n"
            "  • MIDDLE MOUSE BUTTON (hold + drag) : pan a pane when zoomed.\\n"
            "  • 'Reset zoom' : reset all four panes to fit-to-window.\\n\\n"
            "ATLAS VALUE (top-left)\\n"
            "  • Double-click the atlas value to edit it inline (Enter / click\\n"
            "    away to confirm, Esc to cancel).\\n"
            "  • The vertical 'Atlas overlay' slider fades the labeled atlas\\n"
            "    mask over the MRI image (0% = hidden).\\n\\n"
            "ATLAS SLIDER (bottom)\\n"
            "  • Scrolls through the atlas levels.\\n\\n"
            "BOTTOM BUTTONS\\n"
            "  • 'Place markers' : toggle marker-placement mode ON/OFF.\\n"
            "  • 'Cancel point' : remove the last marker you placed.\\n"
            "  • 'Replace mask' : load a different mask for the current ROI.\\n"
            "  • 'Validate slice' : save the aligned points for this ROI and\\n"
            "    advance to the next one.\\n"
            "  • 'Cancel validation' : discard the last validated ROI.\\n"
            "  • 'Previous' / 'Next' : move between ROIs.\\n",
        )

        grid_frame = tk.Frame(outer, bg=BG_COLOR)
        grid_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        grid_frame.columnconfigure(0, weight=1, uniform="quad")
        grid_frame.columnconfigure(1, weight=1, uniform="quad")
        grid_frame.rowconfigure(0, weight=1, uniform="quad")
        grid_frame.rowconfigure(1, weight=1, uniform="quad")

        self.labels = {}

        # Top-left: MRI (image + atlas slider) with a vertical mask-opacity
        # slider on the right so the labeled mask can be blended over the MRI.
        tl_frame = tk.Frame(grid_frame, bg=BG_COLOR)
        tl_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        tl_frame.rowconfigure(0, weight=1)
        tl_frame.columnconfigure(0, weight=1)
        tl_frame.columnconfigure(1, weight=0)

        tl_main = tk.Frame(tl_frame, bg=BG_COLOR)
        tl_main.grid(row=0, column=0, sticky="nsew")
        tl_image_label = tk.Label(tl_main, text="MRI", font=FONT, bg="white", fg="gray")
        tl_image_label.pack(fill=tk.BOTH, expand=True)
        self.labels["tl"] = tl_image_label

        tl_control_bar = tk.Frame(tl_main, bg=BG_COLOR, height=30)
        tl_control_bar.pack(fill=tk.X, side=tk.BOTTOM)
        tl_control_bar.pack_propagate(False)
        try:
            min_depth, max_depth = get_depth_range()
        except Exception as e:
            print(f"Error reading atlas depth range: {e}")
            messagebox.showerror("Atlas depth error", f"Could not read atlas depth range:\n{e}")
            min_depth, max_depth = 0, 1000
        self.tl_scale = tk.Scale(
            tl_control_bar, from_=min_depth, to=max_depth, orient="horizontal",
            font=SMALL_FONT, showvalue=False, command=self._on_tl_scale_changed,
            bg=ACCENT_COLOR_BLUE, fg=FG_COLOR, highlightthickness=1, borderwidth=1,
            sliderrelief="flat",
        )
        self.tl_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))
        self.tl_value_label = tk.Label(
            tl_control_bar, text="0", font=SMALL_FONT,
            bg="white", fg=FG_COLOR, width=5, relief="sunken", cursor="hand2",
        )
        self.tl_value_label.pack(side=tk.LEFT, padx=(0, 5))
        self.tl_value_label.bind("<Double-Button-1>", self._make_tl_value_editable)

        # Second (vertical) slider: atlas label overlay visibility over the MRI
        # (same image as the bottom-left canvas, faded to the chosen opacity).
        tl_mask_slider_frame = tk.Frame(tl_frame, bg=BG_COLOR)
        tl_mask_slider_frame.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        tl_mask_slider_frame.rowconfigure(0, weight=1)
        self.mask_scale = tk.Scale(
            tl_mask_slider_frame, from_=0, to=100, orient="vertical",
            font=SMALL_FONT, showvalue=False, command=self._on_mask_opacity_changed,
            bg=ACCENT_COLOR_GREEN, fg=FG_COLOR, highlightthickness=1, borderwidth=1,
            sliderrelief="flat",
        )
        self.mask_scale.set(self.mask_opacity)
        self.mask_scale.grid(row=0, column=0, sticky="ns")
        tk.Label(tl_mask_slider_frame, text="Atlas\noverlay", font=SMALL_FONT,
                 bg=BG_COLOR, fg=FG_COLOR, justify="center"
                 ).grid(row=1, column=0, sticky="n")

        # Top-right: Histology.
        tr_label = tk.Label(grid_frame, text="Histology", font=FONT, bg="white", fg="gray")
        tr_label.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        self.labels["tr"] = tr_label
        # Bottom-left: Atlas.
        bl_label = tk.Label(grid_frame, text="Atlas", font=FONT, bg="white", fg="gray")
        bl_label.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.labels["bl"] = bl_label
        # Bottom-right: Alignment.
        br_label = tk.Label(grid_frame, text="Alignment", font=FONT, bg="white", fg="gray")
        br_label.grid(row=1, column=1, padx=5, pady=5, sticky="nsew")
        self.labels["br"] = br_label

        # Bindings clic / zoom / pan / drag-dots.
        self.labels["tl"].bind("<Button-1>", lambda e: self._on_image_button1("tl", e))
        self.labels["tr"].bind("<Button-1>", lambda e: self._on_image_button1("tr", e))
        for qkey in ("tl", "tr"):
            self.labels[qkey].bind("<B1-Motion>", lambda e, k=qkey: self._on_image_drag_motion(k, e))
            self.labels[qkey].bind("<ButtonRelease-1>", lambda e, k=qkey: self._on_image_drag_end(k, e))
        for qkey in ("tl", "tr", "bl", "br"):
            lbl = self.labels[qkey]
            lbl.bind("<MouseWheel>", lambda e, k=qkey: self._on_image_wheel(k, e))
            lbl.bind("<Button-4>", lambda e, k=qkey: self._on_image_wheel(k, e))
            lbl.bind("<Button-5>", lambda e, k=qkey: self._on_image_wheel(k, e))
            lbl.bind("<Button-2>", lambda e, k=qkey: self._on_image_pan_start(k, e))
            lbl.bind("<B2-Motion>", self._on_image_pan_motion)
            lbl.bind("<ButtonRelease-2>", self._on_image_pan_end)

        # Barre de boutons.
        button_frame = tk.Frame(outer, bg=BG_COLOR, height=60)
        button_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        button_frame.grid_propagate(False)

        tk.Button(button_frame, text="Previous", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._go_prev).pack(side=tk.LEFT)
        place_button = tk.Button(button_frame, text="Place markers", font=FONT,
                                 bg=ACCENT_COLOR_BLUE, fg=FG_COLOR, command=self._toggle_marker_mode)
        place_button.pack(side=tk.LEFT, padx=10)
        self.marker_buttons["place"] = place_button
        tk.Button(button_frame, text="Cancel point", font=FONT, bg=CLICK_BOXES_COLOR,
                  fg=FG_COLOR, command=self._undo_last_point).pack(side=tk.LEFT, padx=10)
        tk.Button(button_frame, text="Replace mask", font=FONT, bg=CLICK_BOXES_COLOR,
                  fg=FG_COLOR, command=self._replace_mask).pack(side=tk.LEFT, padx=10)
        tk.Button(button_frame, text="Reset zoom", font=FONT, bg=CLICK_BOXES_COLOR,
                  fg=FG_COLOR, command=self._reset_all_zoom).pack(side=tk.LEFT, padx=10)

        next_button = tk.Button(button_frame, text="Next", font=FONT, bg=CLICK_BOXES_COLOR,
                                fg=FG_COLOR, command=self._go_next)
        next_button.pack(side=tk.RIGHT)
        tk.Button(button_frame, text="Cancel validation", font=FONT, bg=ERROR_COLOR,
                  fg=FG_COLOR, command=self._cancel_last_validation).pack(side=tk.RIGHT, padx=10)
        tk.Button(button_frame, text="Validate slice", font=FONT, bg=ACCENT_COLOR_GREEN,
                  fg=FG_COLOR, command=self._validate_current_roi).pack(side=tk.RIGHT, padx=10)

        self.root.bind("<Configure>", lambda e: self._update_images())

        self.frame.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()
        if self.tl_scale is not None:
            self.tl_scale.set(self.current_atlas_depth)
            initial_depth = self.tl_scale.get()
            self.root.after(50, lambda: self._load_atlas_images_for_depth(initial_depth))

        # Conversions en arrière-plan (threads daemon) + ROI polling.
        start_conversions(self.app, self.state.czi_folder_path)
        self._refresh_roi_list()
        self._load_current_roi()
        if not self.roi_items:
            self._start_roi_polling()

    # ============================================================= atlas slider
    def _make_tl_value_editable(self, event):
        """Make quadrant haut-gauche (MRI/atlas) Value Editable (usage interne)."""
        label = event.widget
        current_val = label.cget("text")
        parent = label.master
        pack_info = label.pack_info()
        label.destroy()
        entry = tk.Entry(parent, font=SMALL_FONT, width=6, justify="center")
        entry.insert(0, current_val)
        entry.select_range(0, tk.END)
        entry.focus_set()
        entry.pack(pack_info)

        def finish_edit(event_confirm=None):
            """Finish Edit
            
            Args:
                event_confirm (Any): Parametre event_confirm.
            """
            if self.tl_scale is None:
                self._restore_tl_label(parent, current_val, pack_info)
                return
            try:
                new_val = int(entry.get())
                if new_val < self.tl_scale["from"]:
                    new_val = self.tl_scale["from"]
                if new_val > self.tl_scale["to"]:
                    new_val = self.tl_scale["to"]
                self.tl_scale.set(new_val)
                self._restore_tl_label(parent, str(new_val), pack_info)
                self._schedule_atlas_images_update(new_val)
            except ValueError:
                self._restore_tl_label(parent, current_val, pack_info)

        entry.bind("<Return>", finish_edit)
        entry.bind("<FocusOut>", finish_edit)
        entry.bind("<Escape>", lambda e: self._restore_tl_label(parent, current_val, pack_info))

    def _restore_tl_label(self, parent, text, pack_info):
        """Restore quadrant haut-gauche (MRI/atlas) Label (usage interne).
        
        Args:
            parent (Any): Parametre parent.
            text (Any): Texte.
            pack_info (Any): Parametre pack_info.
        """
        for child in parent.winfo_children():
            if isinstance(child, tk.Entry):
                child.destroy()
        new_label = tk.Label(parent, text=text, font=SMALL_FONT,
                             bg="white", fg=FG_COLOR, width=5, relief="sunken", cursor="hand2")
        new_label.pack(pack_info)
        new_label.bind("<Double-Button-1>", self._make_tl_value_editable)
        self.tl_value_label = new_label

    def _on_tl_scale_changed(self, val):
        """On quadrant haut-gauche (MRI/atlas) Scale Changed (usage interne).
        
        Args:
            val (Any): Parametre val.
        """
        depth = int(float(val))
        if self.tl_value_label is not None:
            self.tl_value_label.config(text=str(depth))
        self._schedule_atlas_images_update(depth)

    def _on_mask_opacity_changed(self, val):
        """On the second (vertical) slider: change the labeled-mask overlay
        opacity over the MRI and re-render the MRI pane (usage interne).

        Args:
            val (Any): Parametre val (0-100).
        """
        self.mask_opacity = int(float(val))
        self._update_images()

    def _schedule_atlas_images_update(self, depth):
        """Schedule atlas Images Update (usage interne).

        Args:
            depth (Any): Profondeur / indice de coupe coronaire.
        """
        self._cancel_pending_atlas()
        self.pending_atlas_update_id = self.root.after(
            250, lambda: self._load_atlas_images_for_depth(depth))

    def _load_mask_overlay(self, max_w, max_h, base_size=None):
        """Load the atlas label image (= bottom-left canvas) cropped/zoomed to
        the SAME viewport as the MRI, oriented to match the MRI, and returned
        on a transparent canvas of exactly ``base_size`` so it composites 1:1
        with the MRI under any zoom/pan.

        Args:
            max_w (Any): Parametre max_w.
            max_h (Any): Parametre max_h.
            base_size (tuple|None): (w, h) of the MRI display image; the overlay
                is fitted (preserving aspect) and centered into this exact size
                using the SAME uniform scale/centering rule as the MRI, so the
                two never drift apart during zoom or middle-mouse pan.

        Returns:
            Any: Resultat (PIL RGBA image or None).
        """
        if not self.current_atlas_path or not os.path.isfile(self.current_atlas_path):
            return None
        # Crop the atlas to the *same* normalized viewport the MRI uses, then
        # orient it exactly like the MRI (rotate 90 CW to 12h + mirror L/R).
        # Reusing the MRI viewport (instead of an independent fit + a non-uniform
        # post-rotation stretch) is what keeps the overlay locked to the MRI.
        vp = self.viewports.get("tl", (0.0, 0.0, 1.0, 1.0))
        src = self._get_source("tl", self.current_atlas_path)
        if src is None:
            return None
        sw, sh = src.size
        if sw <= 0 or sh <= 0:
            return None
        # IMPORTANT: ROTATE_270 + FLIP_LEFT_RIGHT is a *transpose* — it maps an
        # atlas source pixel (x, y) to display (y, x). So the overlay's display
        # X comes from the atlas source Y and display Y from source X. To have
        # the overlay display the SAME (nx0, ny0, nx1, ny1) region as the MRI
        # (and therefore pan/zoom in the same direction), we must crop the atlas
        # source with the TRANSPOSED viewport: source X <- [ny0, ny1],
        # source Y <- [nx0, nx1]. Cropping with the raw viewport instead makes
        # the overlay show the transposed region and move perpendicular to the
        # MRI during pan (e.g. pan right -> overlay slides down).
        nx0, ny0, nx1, ny1 = vp
        left = int(round(ny0 * sw))
        upper = int(round(nx0 * sh))
        right = max(left + 1, int(round(ny1 * sw)))
        lower = max(upper + 1, int(round(nx1 * sh)))
        cropped = src.crop((left, upper, right, lower))
        # The atlas label image is stored rotated 90 deg (pointing left / 9h)
        # off the MRI orientation; rotate 90 deg clockwise (ROTATE_270) so it
        # points up (12h), then mirror horizontally (FLIP_LEFT_RIGHT) so it
        # matches the MRI (atlas was left-right reversed vs the MRI). This pair
        # is a transpose, which is why the crop above uses the transposed vp.
        cropped = cropped.transpose(Image.Transpose.ROTATE_270).transpose(
            Image.Transpose.FLIP_LEFT_RIGHT)
        if cropped.mode != "RGBA":
            cropped = cropped.convert("RGBA")
        target = base_size if base_size is not None else (max_w, max_h)
        fw, fh = get_img_dims(cropped.width, cropped.height, target[0], target[1])
        resized = cropped.resize((fw, fh), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", target, (0, 0, 0, 0))
        off_x = (target[0] - fw) // 2
        off_y = (target[1] - fh) // 2
        canvas.paste(resized, (off_x, off_y))
        return canvas

    def _load_atlas_images_for_depth(self, depth):
        """Load atlas Images For Depth (usage interne).
        
        Args:
            depth (Any): Profondeur / indice de coupe coronaire.
        """
        self.pending_atlas_update_id = None
        depth = int(depth)
        try:
            coronal_path, atlas_path = get_or_create_slice_images(depth)
        except Exception as e:
            print(f"Error loading/generating atlas images for depth {depth}: {e}")
            messagebox.showerror("Atlas image error",
                                 f"Could not load/generate atlas images for depth {depth}:\n{e}")
            return
        self.current_atlas_depth = depth
        self.current_coronal_path = coronal_path
        self.current_atlas_path = atlas_path
        self._update_images()

    # =============================================================== zoom/pan
    def _get_quadrant_sizes(self):
        """Get Quadrant Sizes (usage interne).
        
        Returns:
            Any: Resultat.
        """
        win_w = self.root.winfo_width()
        win_h = self.root.winfo_height()
        grid_avail_h = win_h - 80
        if grid_avail_h < 100:
            grid_avail_h = 100
        if win_w < 200:
            win_w = 200
        quad_w = (win_w - 40) // 2
        quad_h = (grid_avail_h - 40) // 2
        quad_w = max(20, quad_w)
        quad_h = max(20, quad_h)
        tl_w = quad_w
        tl_h = quad_h - 35
        if tl_h < 20:
            tl_h = 20
        return {"quad_w": quad_w, "quad_h": quad_h, "tl_w": tl_w, "tl_h": tl_h}

    def _reset_zoom(self, key=None):
        """Reset Zoom (usage interne).
        
        Args:
            key (Any): Parametre key.
        """
        keys = (key,) if key is not None else ("tl", "tr", "bl", "br")
        for k in keys:
            self.zoom_state[k] = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
            self.viewports[k] = (0.0, 0.0, 1.0, 1.0)
        self.source_cache.clear()

    def _zoom_viewport(self, key):
        """Zoom Viewport (usage interne).
        
        Args:
            key (Any): Parametre key.
        
        Returns:
            Any: Resultat.
        """
        st = self.zoom_state[key]
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
            self.zoom_state[key]["cx"] = (nx0 + nx1) / 2
        if (ny1 - ny0) < 2 * half and (ny1 - ny0) < 1.0:
            cy = (ny0 + ny1) / 2
            ny0 = max(0.0, cy - half)
            ny1 = min(1.0, cy + half)
            self.zoom_state[key]["cy"] = (ny0 + ny1) / 2
        return (nx0, ny0, nx1, ny1)

    def _get_source(self, key, file_path):
        """Get Source (usage interne).
        
        Args:
            key (Any): Parametre key.
            file_path (Any): Chemin vers le fichier.
        
        Returns:
            Any: Resultat.
        """
        full_path = file_path if os.path.isabs(file_path) else os.path.join(str(self.base), file_path)
        cache_key = (key, full_path)
        try:
            current_mtime = os.path.getmtime(full_path)
        except OSError:
            current_mtime = None
        cached = self.source_cache.get(cache_key)
        if cached is not None:
            cached_img, cached_mtime = cached
            if current_mtime is None or cached_mtime == current_mtime:
                return cached_img
        try:
            img = Image.open(full_path)
            img.load()
        except Exception as e:
            print(f"Error loading image '{file_path}': {e}")
            return None
        for ck in list(self.source_cache.keys()):
            if ck[0] == key and ck != cache_key:
                self.source_cache.pop(ck, None)
        self.source_cache[cache_key] = (img, current_mtime)
        return img

    def _load_zoomed_pil(self, file_path, max_w, max_h, key):
        """Load Zoomed Pil (usage interne).
        
        Args:
            file_path (Any): Chemin vers le fichier.
            max_w (Any): Parametre max_w.
            max_h (Any): Parametre max_h.
            key (Any): Parametre key.
        
        Returns:
            Any: Resultat.
        """
        src = self._get_source(key, file_path)
        if src is None:
            return None, None
        sw, sh = src.size
        if sw <= 0 or sh <= 0:
            return None, None
        viewport = self._zoom_viewport(key)
        self.viewports[key] = viewport
        nx0, ny0, nx1, ny1 = viewport
        left = int(round(nx0 * sw))
        upper = int(round(ny0 * sh))
        right = max(left + 1, int(round(nx1 * sw)))
        lower = max(upper + 1, int(round(ny1 * sh)))
        cropped = src.crop((left, upper, right, lower))
        new_w, new_h = get_img_dims(cropped.width, cropped.height, max_w, max_h)
        cropped = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
        if cropped.mode != "RGBA":
            cropped = cropped.convert("RGBA")
        return cropped, viewport

    def _draw_markers_zoomed(self, pil_img, points, color, viewport):
        """Draw Markers Zoomed (usage interne).
        
        Args:
            pil_img (Any): Image source (PIL ou chemin).
            points (Any): Parametre points.
            color (Any): Couleur (hex ou tuple RGB).
            viewport (Any): Parametre viewport.
        
        Returns:
            Any: Resultat.
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
                continue
            cx, cy = int(vx * w), int(vy * h)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                         fill=color, outline="white")
            text = str(idx + 1)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), text, fill="white", font=font)
        return img

    def _blend_mask_overlay(self, base_pil, max_w, max_h):
        """Blend the atlas label image (the same image shown in the bottom-left
        canvas) over the MRI at the opacity set by the second (vertical) slider
        (usage interne).

        The overlay is the exact same source as the bottom-left canvas, only
        faded: its alpha is scaled to `mask_opacity` % so the MRI shows through.

        Args:
            base_pil (Any): MRI PIL image (RGBA).
            max_w (Any): Parametre max_w.
            max_h (Any): Parametre max_h.

        Returns:
            Any: Resultat (MRI with atlas label blended in).
        """
        if self.mask_opacity <= 0:
            return base_pil
        # Build the overlay on a transparent canvas of exactly base_pil.size
        # (same viewport + same uniform fit/centering as the MRI) so it aligns
        # 1:1 with the MRI — no independent fit and no post-rotation stretch.
        mask = self._load_mask_overlay(max_w, max_h, base_size=base_pil.size)
        if mask is None:
            return base_pil
        if mask.mode != "RGBA":
            mask = mask.convert("RGBA")
        # Fade the (identical) atlas label image to the chosen visibility.
        alpha = int(round(255 * self.mask_opacity / 100.0))
        ch = list(mask.split())
        r, g, b, a = ch
        # The atlas label image uses a black ("none"/background) region (label 0)
        # outside the labeled structures. We must NOT paint that black region over
        # the MRI — it would wash the histology/MRI with a grey tint. Zero out the
        # overlay alpha wherever the pixel is effectively black so the MRI shows
        # through unchanged, and only the colored labeled regions are blended in.
        # Vectorized with numpy for speed (the mask can be large when zoomed).
        arr = np.asarray(mask, dtype=np.uint16)
        rgb = arr[..., :3]
        # "none" region = near-black across all channels (label 0 background).
        is_none = (rgb <= 8).all(axis=2)
        # Scale the source alpha by the chosen opacity, then force the black
        # "none" pixels fully transparent.
        new_alpha = (arr[..., 3].astype(np.uint16) * alpha) // 255
        new_alpha[is_none] = 0
        arr[..., 3] = new_alpha.astype(np.uint8)
        mask = Image.fromarray(arr.astype(np.uint8), "RGBA")
        return Image.alpha_composite(base_pil, mask)

    def _update_images(self):
        """Update Images (usage interne)."""
        if not self.labels:
            return
        sizes = self._get_quadrant_sizes()
        quad_w, quad_h = sizes["quad_w"], sizes["quad_h"]
        tl_w, tl_h = sizes["tl_w"], sizes["tl_h"]

        if "tl" in self.labels:
            tl_source = self.current_coronal_path or _resolve_image_path("MRI.png", str(self.base))
            pil, vp = self._load_zoomed_pil(tl_source, tl_w, tl_h, "tl")
            if pil is not None:
                pil = self._draw_markers_zoomed(pil, self.marker_points.get("tl", []),
                                                ACCENT_COLOR_BLUE, vp)
                pil = self._blend_mask_overlay(pil, tl_w, tl_h)
                photo = ImageTk.PhotoImage(pil)
                self.images["tl"] = photo
                self.labels["tl"].configure(image=photo)

        if "tr" in self.labels:
            tr_source = self.current_histology_path or _resolve_image_path("Histo.png", str(self.base))
            pil, vp = self._load_zoomed_pil(tr_source, quad_w, quad_h, "tr")
            if pil is not None:
                pil = self._draw_markers_zoomed(pil, self.marker_points.get("tr", []),
                                                ACCENT_COLOR_GREEN, vp)
                photo = ImageTk.PhotoImage(pil)
                self.images["tr"] = photo
                self.labels["tr"].configure(image=photo)

        if "bl" in self.labels:
            bl_source = self.current_atlas_path or _resolve_image_path("ATLAS.png", str(self.base))
            pil, _vp = self._load_zoomed_pil(bl_source, quad_w, quad_h, "bl")
            if pil is not None:
                # Match the MRI overlay orientation: rotate 90 CW to 12h, then
                # mirror horizontally (same chain as the atlas label overlay).
                pil = pil.transpose(Image.Transpose.ROTATE_270).transpose(
                    Image.Transpose.FLIP_LEFT_RIGHT)
                photo = ImageTk.PhotoImage(pil)
                self.images["bl"] = photo
                self.labels["bl"].configure(image=photo)

        if "br" in self.labels:
            br_source = self.br_result_path or _resolve_image_path("Alignment.png", str(self.base))
            pil, _vp = self._load_zoomed_pil(br_source, quad_w, quad_h, "br")
            if pil is not None:
                photo = ImageTk.PhotoImage(pil)
                self.images["br"] = photo
                self.labels["br"].configure(image=photo)

    def _displayed_image_size(self, key):
        """Displayed Image Size (usage interne).
        
        Args:
            key (Any): Parametre key.
        
        Returns:
            Any: Resultat.
        """
        photo = self.images.get(key)
        if photo is None:
            return None
        try:
            return photo.width(), photo.height()
        except Exception:
            return None

    def _on_image_click(self, key, event):
        """On Image Click (usage interne).
        
        Args:
            key (Any): Parametre key.
        """
        if not self.marker_active:
            return
        label = self.labels.get(key)
        if label is None:
            return
        disp = self._displayed_image_size(key)
        if disp is None:
            return
        img_w, img_h = disp
        if img_w <= 1 or img_h <= 1:
            return
        label_w = label.winfo_width()
        label_h = label.winfo_height()
        offset_x = (label_w - img_w) // 2
        offset_y = (label_h - img_h) // 2
        px = event.x - offset_x
        py = event.y - offset_y
        if px < 0 or py < 0 or px >= img_w or py >= img_h:
            return
        nx_view = px / img_w
        ny_view = py / img_h
        nx0, ny0, nx1, ny1 = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))
        nx = nx0 + nx_view * (nx1 - nx0)
        ny = ny0 + ny_view * (ny1 - ny0)
        nx = min(1.0, max(0.0, nx))
        ny = min(1.0, max(0.0, ny))
        self.marker_points[key].append((nx, ny))
        self.marker_order.append(key)
        self._update_images()

    def _on_tl_image_click(self, event):
        """On quadrant haut-gauche (MRI/atlas) Image Click (usage interne)."""
        self._on_image_click("tl", event)

    def _on_tr_image_click(self, event):
        """On quadrant haut-droit (histologie) Image Click (usage interne)."""
        self._on_image_click("tr", event)

    # ---- draggable markers (left-button hold + move) -----------------------
    def _hit_test_marker(self, key, event):
        """Return the index of the marker under the cursor, or None.

        Replicates the pixel position used by `_draw_markers_zoomed`
        (letterboxed image + normalized->viewport projection) and a small
        tolerance around the drawn dot radius.
        """
        points = self.marker_points.get(key, [])
        if not points:
            return None
        label = self.labels.get(key)
        disp = self._displayed_image_size(key)
        if label is None or disp is None:
            return None
        img_w, img_h = disp
        if img_w <= 1 or img_h <= 1:
            return None
        label_w = label.winfo_width()
        label_h = label.winfo_height()
        offset_x = (label_w - img_w) // 2
        offset_y = (label_h - img_h) // 2
        px = event.x - offset_x
        py = event.y - offset_y
        if px < 0 or py < 0 or px >= img_w or py >= img_h:
            return None
        nx0, ny0, nx1, ny1 = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))
        span_x = max(1e-6, nx1 - nx0)
        span_y = max(1e-6, ny1 - ny0)
        radius = max(4, min(img_w, img_h) // 35)
        tol = radius + 4
        for idx, (nx, ny) in enumerate(points):
            vx = (nx - nx0) / span_x
            vy = (ny - ny0) / span_y
            cx = vx * img_w
            cy = vy * img_h
            if abs(px - cx) <= tol and abs(py - cy) <= tol:
                return idx
        return None

    def _on_image_button1(self, key, event):
        """Left-button press on a marker pane.

        If the press lands on an existing dot, start dragging it (works
        regardless of marker-placement mode). Otherwise fall back to the
        existing click-to-place behaviour (only when marker mode is active).
        """
        if self.frame is None or not self.frame.winfo_exists():
            return
        hit = self._hit_test_marker(key, event)
        if hit is not None:
            self.drag_state = {"key": key, "index": hit}
            lbl = self.labels.get(key)
            if lbl is not None:
                lbl.config(cursor="hand2")
            return
        self._on_image_click(key, event)

    def _on_image_drag_motion(self, key, event):
        """While dragging, move the captured dot to the cursor (usage interne)."""
        ds = getattr(self, "drag_state", None)
        if ds is None or ds.get("key") != key:
            return
        if self.frame is None or not self.frame.winfo_exists():
            return
        points = self.marker_points.get(key)
        if points is None or ds["index"] >= len(points):
            self.drag_state = None
            return
        src_nx, src_ny = self._screen_to_source_normalized(key, event)
        if src_nx is None:
            return
        points[ds["index"]] = (
            min(1.0, max(0.0, src_nx)),
            min(1.0, max(0.0, src_ny)),
        )
        self._update_images()

    def _on_image_drag_end(self, key, _event=None):
        """End an in-progress dot drag and restore the cursor (usage interne)."""
        if getattr(self, "drag_state", None) is None:
            return
        self.drag_state = None
        lbl = self.labels.get(key)
        if lbl is not None:
            cursor = "crosshair" if self.marker_active else ""
            lbl.config(cursor=cursor)

    def _on_image_wheel(self, key, event):
        """On Image Wheel (usage interne).
        
        Args:
            key (Any): Parametre key.
        """
        if event.num in (4, 5):
            direction = 1 if event.num == 4 else -1
        else:
            direction = 1 if (event.delta or 0) > 0 else -1
        if direction == 0:
            return
        st = self.zoom_state.get(key)
        if st is None:
            return
        old_zoom = st["zoom"]
        new_zoom = old_zoom * _ZOOM_STEP if direction > 0 else old_zoom / _ZOOM_STEP
        new_zoom = min(_ZOOM_MAX, max(_ZOOM_MIN, new_zoom))
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        src_nx, src_ny = self._screen_to_source_normalized(key, event)
        if src_nx is None:
            st["zoom"] = new_zoom
            self._update_images()
            return
        st["zoom"] = new_zoom
        nx0, ny0, nx1, ny1 = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))
        span_x = max(1e-6, nx1 - nx0)
        span_y = max(1e-6, ny1 - ny0)
        fx = (src_nx - nx0) / span_x
        fy = (src_ny - ny0) / span_y
        half_new = 0.5 / new_zoom
        cx = src_nx + (0.5 - fx) * half_new * 2
        cy = src_ny + (0.5 - fy) * half_new * 2
        st["cx"] = min(1.0, max(0.0, cx))
        st["cy"] = min(1.0, max(0.0, cy))
        self._update_images()

    def _screen_to_source_normalized(self, key, event):
        """Screen To Source Normalized (usage interne).
        
        Args:
            key (Any): Parametre key.
        
        Returns:
            Any: Resultat.
        """
        label = self.labels.get(key)
        disp = self._displayed_image_size(key)
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
        nx0, ny0, nx1, ny1 = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))
        nx = nx0 + nx_view * (nx1 - nx0)
        ny = ny0 + ny_view * (ny1 - ny0)
        return nx, ny

    def _on_image_pan_start(self, key, event):
        """On Image Pan Start (usage interne).
        
        Args:
            key (Any): Parametre key.
        """
        self.pan_state["key"] = key
        self.pan_state["start_x"] = event.x
        self.pan_state["start_y"] = event.y
        self.pan_state["start_cx"] = self.zoom_state[key]["cx"]
        self.pan_state["start_cy"] = self.zoom_state[key]["cy"]
        self.pan_state["viewport"] = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))

    def _on_image_pan_motion(self, event):
        """On Image Pan Motion (usage interne)."""
        key = self.pan_state.get("key")
        if key is None:
            return
        label = self.labels.get(key)
        disp = self._displayed_image_size(key)
        if label is None or disp is None:
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
        self.zoom_state[key]["cx"] = min(1.0, max(0.0, self.pan_state["start_cx"] + dnx))
        self.zoom_state[key]["cy"] = min(1.0, max(0.0, self.pan_state["start_cy"] + dny))
        self._update_images()

    def _on_image_pan_end(self, _event=None):
        """On Image Pan End (usage interne).
        
        Args:
            _event (Any): Parametre _event.
        """
        self.pan_state.clear()

    def _reset_all_zoom(self):
        """Reset All Zoom (usage interne)."""
        self._reset_zoom()
        self._update_images()

    # =============================================================== markers
    def _toggle_marker_mode(self):
        """Toggle Marker Mode (usage interne)."""
        self.marker_active = not self.marker_active
        btn = self.marker_buttons.get("place")
        if btn is not None:
            btn.config(bg=ACCENT_COLOR_GREEN if self.marker_active else ACCENT_COLOR_BLUE)
        cursor = "crosshair" if self.marker_active else ""
        for key in ("tl", "tr"):
            lbl = self.labels.get(key)
            if lbl is not None:
                lbl.config(cursor=cursor)

    def _undo_last_point(self):
        """Undo Last Point (usage interne)."""
        if not self.marker_order:
            return
        key = self.marker_order.pop()
        if self.marker_points[key]:
            self.marker_points[key].pop()
        self._update_images()

    def _replace_mask(self):
        """Replace Mask (usage interne)."""
        tl_pts = self.marker_points["tl"]
        tr_pts = self.marker_points["tr"]
        n = min(len(tl_pts), len(tr_pts))
        pairs = [(tl_pts[i], tr_pts[i]) for i in range(n)]
        if n < 2:
            messagebox.showwarning("Not enough points", "Select at least 2 points")
            return
        try:
            out_path = replace_mask(self.current_atlas_depth, pairs,
                                    histo_path=self.current_histology_path)
        except Exception as e:
            messagebox.showerror("Mask replacement error", str(e))
            return
        if out_path:
            self.br_result_path = out_path
            self._update_images()

    # =============================================================== ROI list
    def _first_z_image(self, roi_dir):
        """First couche Z Image (usage interne).
        
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
        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0])
        return candidates[0][1]

    def _fallback_czi_stem(self, roi_name):
        """Fallback fichier .czi Stem (usage interne).
        
        Args:
            roi_name (Any): Nom de base (sans extension).
        
        Returns:
            Any: Resultat.
        """
        m = re.match(r"^(.*)_(\d+)$", roi_name)
        return m.group(1) if m else roi_name

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
        return self._fallback_czi_stem(roi_name)

    def _build_roi_work_items(self):
        """Build region d'interet (ROI) Work Items (usage interne).
        
        Returns:
            Any: Resultat.
        """
        import convert_czi_to_jpeg
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
            first_img = self._first_z_image(roi_dir)
            if first_img is None:
                continue
            roi_name = roi_dir.name
            czi_stem = self._match_czi_stem(roi_name, czi_stems)
            mask_dir = self.wip_dir / czi_stem / "masks"
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

    def _refresh_roi_list(self):
        """Refresh region d'interet (ROI) List (usage interne)."""
        prev_name = None
        if 0 <= self.roi_index < len(self.roi_items):
            prev_name = self.roi_items[self.roi_index].get("roi_folder_name")
        self.roi_items = self._build_roi_work_items()
        if prev_name is not None:
            for i, item in enumerate(self.roi_items):
                if item["roi_folder_name"] == prev_name:
                    self.roi_index = i
                    return
        self.roi_index = 0 if self.roi_items else -1

    def _load_current_roi(self):
        """Load Current region d'interet (ROI) (usage interne)."""
        self.br_result_path = None
        if 0 <= self.roi_index < len(self.roi_items):
            item = self.roi_items[self.roi_index]
            self.current_histology_path = str(item["image_path"])
        else:
            self.current_histology_path = None
        self._reset_zoom("tr")
        self._reset_zoom("br")
        self._update_images()

    def _clear_markers(self):
        """Clear Markers (usage interne)."""
        self.marker_points = {"tl": [], "tr": []}
        self.marker_order = []
        self.marker_active = False
        btn = self.marker_buttons.get("place")
        if btn is not None:
            btn.config(bg=ACCENT_COLOR_BLUE)
        for key in ("tl", "tr"):
            lbl = self.labels.get(key)
            if lbl is not None:
                lbl.config(cursor="")

    def _write_marker_txt(self, path, depth, tl_points, tr_points, order):
        """Write Marker Txt (usage interne).
        
        Args:
            path (Any): Chemin vers le fichier.
            depth (Any): Profondeur / indice de coupe coronaire.
            tl_points (Any): Parametre tl_points.
            tr_points (Any): Parametre tr_points.
            order (Any): Parametre order.
        """
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

    def _read_marker_txt(self, path):
        """Read Marker Txt (usage interne).
        
        Args:
            path (Any): Chemin vers le fichier.
        
        Returns:
            Any: Resultat.
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

    def _start_roi_polling(self):
        """Start region d'interet (ROI) Polling (usage interne)."""
        self._cancel_roi_poll()
        self.roi_poll_id = self.root.after(1500, self._poll_roi_once)

    def _cancel_roi_poll(self):
        """Cancel region d'interet (ROI) Poll (usage interne)."""
        if self.roi_poll_id is not None:
            try:
                self.root.after_cancel(self.roi_poll_id)
            except Exception:
                pass
            self.roi_poll_id = None

    def _cancel_pending_atlas(self):
        """Cancel Pending atlas (usage interne)."""
        if self.pending_atlas_update_id is not None:
            try:
                self.root.after_cancel(self.pending_atlas_update_id)
            except Exception:
                pass
            self.pending_atlas_update_id = None

    def destroy(self):
        """Détruit la vue en annulant les timers after() encore en file.

        Sans cela, un timer (ROI poll / atlas update) planifié avant la
        navigation pouvait se déclencher sur une vue détruite et lever
        TclError ('invalid command name ...button'), faisant planter l'app.
        """
        self._cancel_roi_poll()
        self._cancel_pending_atlas()
        if self.frame is not None and self.frame.winfo_exists():
            try:
                self.root.unbind("<Configure>")
            except tk.TclError:
                pass
            try:
                self.frame.destroy()
            except tk.TclError:
                pass

    def _poll_roi_once(self):
        """Poll region d'interet (ROI) Once (usage interne)."""
        self.roi_poll_id = None
        # Garde : le timer peut se déclencher après destruction de la vue
        # (navigation entre fenêtres). On n'agit que si la vue est vivante.
        if self.frame is None or not self.frame.winfo_exists():
            return

        if not self.roi_items:
            # La conversion arrière-plan (start_conversions) peut ne pas avoir
            # fini au premier scan : on re-scanner le dossier ici, sinon le poll
            # ne ferait que se relancer indéfiniment sans jamais découvrir la
            # ROI nouvellement écrite -> quadrant TR bloqué sur l'image fall-back.
            self._refresh_roi_list()
            if not self.roi_items:
                self._start_roi_polling()
                return
        if self.awaiting_next_roi:
            if self.roi_index + 1 < len(self.roi_items):
                self.roi_index += 1
                self.awaiting_next_roi = False
                self._clear_markers()
                self._load_current_roi()
            else:
                self._start_roi_polling()
        elif self.current_histology_path is None:
            self.roi_index = 0
            self._clear_markers()
            self._load_current_roi()

    def _validate_current_roi(self):
        """Validate Current region d'interet (ROI) (usage interne)."""
        if not (0 <= self.roi_index < len(self.roi_items)):
            messagebox.showwarning("No slice", "No ROI image to validate.")
            return
        item = self.roi_items[self.roi_index]
        tl_pts = list(self.marker_points["tl"])
        tr_pts = list(self.marker_points["tr"])
        order = list(self.marker_order)
        n = min(len(tl_pts), len(tr_pts))
        if n < 2:
            messagebox.showwarning("Not enough points", "Place at least 2 marker pairs.")
            return
        pairs = [(tl_pts[i], tr_pts[i]) for i in range(n)]
        try:
            os.makedirs(item["mask_dir"], exist_ok=True)
            save_mask_pair(
                depth=self.current_atlas_depth,
                normalized_points=pairs,
                overlay_path=str(item["mask_overlay_png"]),
                mask_only_path=str(item["mask_png"]),
                histo_path=str(item["image_path"]),
            )
        except Exception as e:
            messagebox.showerror("Mask save error", str(e))
            return
        self._write_marker_txt(str(item["mask_txt"]), self.current_atlas_depth,
                               tl_pts, tr_pts, order)
        current_name = item["roi_folder_name"]
        self._refresh_roi_list()
        next_index = len(self.roi_items)
        for i, it in enumerate(self.roi_items):
            if it["roi_folder_name"] == current_name:
                next_index = i + 1
                break
        if next_index >= len(self.roi_items):
            messagebox.showinfo(
                "Validation",
                "Slice validated. No other slice available for now\n"
                "(.czi conversion may still be running).",
            )
            self._clear_markers()
            self.br_result_path = str(item["mask_overlay_png"])
            self.current_histology_path = str(item["image_path"])
            self.awaiting_next_roi = True
            self._update_images()
            self._start_roi_polling()
            return
        self.roi_index = next_index
        self.awaiting_next_roi = False
        self._clear_markers()
        self._load_current_roi()

    def _cancel_last_validation(self):
        """Cancel Last Validation (usage interne)."""
        if self.roi_index <= 0 or self.roi_index >= len(self.roi_items):
            messagebox.showinfo("Cancellation", "No previous validation to cancel.")
            return
        self.awaiting_next_roi = False
        self.roi_index -= 1
        item = self.roi_items[self.roi_index]
        depth, pts, order = self._read_marker_txt(str(item["mask_txt"]))
        self._clear_markers()
        if pts is not None:
            self.marker_points = {"tl": list(pts.get("tl", [])), "tr": list(pts.get("tr", []))}
            self.marker_order = list(order) if order else []
            if depth is not None and self.tl_scale is not None:
                try:
                    self.tl_scale.set(depth)
                except Exception:
                    pass
        for p in (item["mask_png"], item["mask_overlay_png"], item["mask_txt"]):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception as e:
                print(f"[cancel] could not remove {p}: {e}")
        self.current_histology_path = str(item["image_path"])
        self.br_result_path = None
        self._update_images()

    # ============================================================= navigation
    def _go_prev(self):
        """Go Prev (usage interne)."""
        from screens.window1_preview import Window1Screen
        self.app.show(Window1Screen)

    def _go_next(self):
        """Go Next (usage interne)."""
        from screens.window3_quantify import Window3Screen
        self.app.show(Window3Screen)
