"""Window 1 — quick .czi preview (classe Screen)."""

import os
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from PIL import Image, ImageTk

from app.base_screen import BaseScreen
from app.theme import BG_COLOR, FG_COLOR, SMALL_FONT, FONT, CLICK_BOXES_COLOR
from app.image_utils import load_and_resize_image
from app.common_widgets import ScrollableList, FooterBar, PreviewZoomPanMixin, add_help_button
import convert_czi_to_jpeg

PREVIEW_DOWNSAMPLE = 50
TEMP_VIZU_SUBDIR = f"downsampled{PREVIEW_DOWNSAMPLE}_jpeg"


class Window1Screen(BaseScreen, PreviewZoomPanMixin):
    """Window1screen.

    Attributs et methodes definis ci-dessous.
    """
    def __init__(self, app):
        """Initialise l'objet et son etat.
        
        Args:
            app (Any): Instance de l'application.
        """
        super().__init__(app)
        self.base = self.state.base_dir()
        self.temp_vizu_dir = self.base / "WorkInProgress" / "temp_vizu"
        self.temp_vizu_subdir = self.temp_vizu_dir / TEMP_VIZU_SUBDIR

        # État d'instance (remplace les globals _w1_*).
        self.source_var = None
        self.name_list = None
        self.preview_label = None
        self.preview_photo = None
        self.preview_status = None
        self.selected_stem = None
        self.selected_scenes = []
        self.scene_index = 0
        self.temp_poll_id = None
        self.depth_entry = None

    # ------------------------------------------------------------------ build
    def build(self):
        """Build"""
        self._stop_temp_polling()

        # Footer plein largeur (packé en premier pour réserver l'espace).
        footer = FooterBar(self.frame)
        footer.add_button("Next", command=lambda: self._go_next())

        # Top header: title (left) + help button (right).
        header = tk.Frame(self.frame, bg=BG_COLOR)
        header.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(8, 2))
        tk.Label(header, text="Window 1 — Quick .czi preview",
                 font=("Arial", 16, "bold"), bg=BG_COLOR, fg=FG_COLOR
                 ).pack(side=tk.LEFT)
        add_help_button(
            header, "Window 1 — Help",
            "WINDOW 1 — Quick .czi preview\n"
            "------------------------------------------------\n\n"
            "LEFT\n"
            "  • Source checkboxes : pick where .czi files are read from.\n"
            "  • Slice depth / Interslice (µm) : physical spacing of Z layers.\n"
            "  • Name list : click a .czi to preview its converted scenes.\n\n"
            "RIGHT (preview)\n"
            "  • Mouse wheel : zoom in / out, centered on the cursor.\n"
            "  • Middle mouse button (drag) : pan the image when zoomed.\n"
            "  • 'Reset zoom' button : return to fit-to-window.\n"
            "  • 'Previous' / 'Next' : move between ROI scenes.\n\n"
            "FOOTER\n"
            "  • 'Next' (bottom-right) : continue to Window 2 (mask +\n"
            "    alignment) once you have previewed the ROIs you need.\n",
        )

        main = tk.Frame(self.frame, bg=BG_COLOR)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1, uniform="w1")   # left 1/3
        main.columnconfigure(1, weight=2, uniform="w1")   # right 2/3
        main.rowconfigure(0, weight=1)

        # --- Left 1/3: checkboxes + name list ---
        left = tk.Frame(main, bg=BG_COLOR)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)

        cb_frame = tk.Frame(left, bg=BG_COLOR)
        cb_frame.grid(row=0, column=0, sticky="ew")
        self.source_var = tk.IntVar(value=1)
        tk.Checkbutton(
            cb_frame, text=".czi in the Input folder", font=SMALL_FONT,
            variable=self.source_var, onvalue=1, command=self._select_input_folder,
            bg=BG_COLOR,
        ).pack(anchor="w")
        tk.Checkbutton(
            cb_frame, text=".czi in another folder", font=SMALL_FONT,
            variable=self.source_var, onvalue=2, command=self._select_other_folder,
            bg=BG_COLOR,
        ).pack(anchor="w")

        ttk.Separator(left, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=6)

        depth_frame = tk.Frame(left, bg=BG_COLOR)
        depth_frame.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        tk.Label(
            depth_frame, text="Slice depth (µm):", font=SMALL_FONT,
            bg=BG_COLOR, fg=FG_COLOR,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.depth_entry = tk.Entry(depth_frame, font=SMALL_FONT, width=8, justify="center")
        self.depth_entry.insert(0, str(self.state.slice_depth_um))
        self.depth_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.depth_entry.bind("<FocusOut>", lambda e: self._on_depth_validate())
        self.depth_entry.bind("<Return>", lambda e: self._on_depth_validate())

        tk.Label(
            depth_frame, text="Interslice (µm):", font=SMALL_FONT,
            bg=BG_COLOR, fg=FG_COLOR,
        ).pack(side=tk.LEFT, padx=(6, 6))
        self.interslice_entry = tk.Entry(depth_frame, font=SMALL_FONT, width=8, justify="center")
        self.interslice_entry.insert(0, str(self.state.interslice_um))
        self.interslice_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.interslice_entry.bind("<FocusOut>", lambda e: self._on_depth_validate())
        self.interslice_entry.bind("<Return>", lambda e: self._on_depth_validate())

        self.name_list = ScrollableList(left, bg="white")
        self.name_list.grid(
            canvas_kw={"row": 3, "column": 0, "sticky": "nsew"},
            scroll_kw={"row": 3, "column": 1, "sticky": "ns"},
        )

        # --- Right 2/3: preview + navigation ---
        right = tk.Frame(main, bg=BG_COLOR)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.preview_label = tk.Label(
            right, text="Select a .czi", font=FONT, bg="white", fg="gray"
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        # Zoom + pan bindings (parity with Window 2 / Window 4).
        self._init_zoom_pan()
        self.preview_label.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview_label.bind("<Button-4>", self._on_preview_wheel)
        self.preview_label.bind("<Button-5>", self._on_preview_wheel)
        self.preview_label.bind("<Button-2>", self._on_preview_pan_start)
        self.preview_label.bind("<B2-Motion>", self._on_preview_pan_motion)
        self.preview_label.bind("<ButtonRelease-2>", self._on_preview_pan_end)

        self.preview_status = tk.Label(right, text="", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR)
        self.preview_status.grid(row=1, column=0, sticky="ew")

        nav = tk.Frame(right, bg=BG_COLOR)
        nav.grid(row=2, column=0, pady=6)
        tk.Button(nav, text="Previous", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._prev_scene).pack(side=tk.LEFT, padx=8)
        tk.Button(nav, text="Next", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._next_scene).pack(side=tk.LEFT, padx=8)
        tk.Button(nav, text="Reset zoom", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._reset_zoom).pack(side=tk.LEFT, padx=8)

        # Resize handler sur la frame (via le root, unbinding géré par BaseScreen).
        self.root.bind("<Configure>", self._on_configure)

        self.frame.pack(fill=tk.BOTH, expand=True)
        self._rebuild_name_list()
        self.temp_vizu_dir.mkdir(parents=True, exist_ok=True)
        self.state.w1_preview_running = True
        threading.Thread(
            target=self._convert_all_czi_to_temp_vizu,
            args=(self.state.czi_folder_path,), daemon=True,
        ).start()

    # ----------------------------------------------------------------- helpers
    def _on_depth_validate(self):
        """Validate and store the slice-depth and inter-slice values from the entries.

        Parses both entry fields (accepting comma or dot decimals) and updates
        ``state.slice_depth_um`` (if > 0) and ``state.interslice_um`` (if >= 0).
        Silently ignores non-numeric input.
        """
        try:
            val = float(self.depth_entry.get().replace(",", "."))
            if val > 0:
                self.state.slice_depth_um = val
        except ValueError:
            pass
        try:
            val = float(self.interslice_entry.get().replace(",", "."))
            if val >= 0:
                self.state.interslice_um = val
        except ValueError:
            pass

    def _get_preview_size(self):
        """Get Preview Size (usage interne).
        
        Returns:
            Any: Resultat.
        """
        win_w = self.root.winfo_width()
        win_h = self.root.winfo_height()
        if win_w < 100:
            win_w = 800
        if win_h < 100:
            win_h = 600
        avail_w = int(win_w * 2 / 3) - 30
        avail_h = win_h - 120
        return max(50, avail_w), max(50, avail_h)

    def _scene_folders_for_stem(self, stem):
        """Scene Folders For Stem (usage interne).
        
        Args:
            stem (Any): Nom de base (sans extension).
        
        Returns:
            Any: Resultat.
        """
        if not self.temp_vizu_subdir.is_dir():
            return []
        prefix = stem + "_"
        found = []
        for d in self.temp_vizu_subdir.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                rest = d.name[len(prefix):]
                if rest.isdigit():
                    found.append((int(rest), d))
        found.sort(key=lambda t: t[0])
        return [d for _, d in found]

    def _first_z_image(self, scene_dir):
        """First couche Z Image (usage interne).
        
        Args:
            scene_dir (Any): Repertoire (dossier).
        
        Returns:
            Any: Resultat.
        """
        imgs = sorted(p for p in Path(scene_dir).iterdir()
                      if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        return imgs[0] if imgs else None

    def _refresh_preview(self):
        """Refresh Preview (usage interne)."""
        if self.preview_label is None or not self.preview_label.winfo_exists():
            return
        if self.selected_stem is not None and not self.selected_scenes:
            self.selected_scenes = self._scene_folders_for_stem(self.selected_stem)

        if self.selected_stem is None:
            self.preview_label.config(image="", text="Select a .czi")
            if self.preview_status is not None:
                self.preview_status.config(text="")
            return

        if not self.selected_scenes or not (0 <= self.scene_index < len(self.selected_scenes)):
            self.preview_label.config(image="", text="Converting...")
            if self.preview_status is not None:
                self.preview_status.config(text="50x conversion in progress, please wait...")
            self._start_temp_polling()
            return

        scene_dir = self.selected_scenes[self.scene_index]
        img_path = self._first_z_image(scene_dir)
        if img_path is None or not img_path.exists():
            self.preview_label.config(image="", text="Image non disponible")
            self._start_temp_polling()
            return

        avail_w, avail_h = self._get_preview_size()
        # Use the live label size when laid out so the zoomed crop fills the
        # canvas (label is sticky="nsew" and expands); fall back to estimate.
        lw = self.preview_label.winfo_width()
        lh = self.preview_label.winfo_height()
        if lw > 20 and lh > 20:
            disp_w, disp_h = lw, lh
        else:
            disp_w, disp_h = avail_w, avail_h
        try:
            base_img = Image.open(str(img_path)).convert("RGB")
            base_img = self._zoom_crop(base_img)
            # Aspect-preserving fit into the label box (no deformation).
            photo = self._fit_photo(base_img, disp_w, disp_h)
        except Exception as exc:
            print(f"[window1] cannot load preview {img_path}: {exc}")
            photo = None
        if photo is not None:
            self.preview_photo = photo
            self.preview_label.config(image=photo, text="")

        if self.preview_status is not None:
            self.preview_status.config(
                text=f"{self.selected_stem}   —   ROI {self.scene_index + 1} / {len(self.selected_scenes)}"
            )
        self._stop_temp_polling()

    def _start_temp_polling(self):
        """Start Temp Polling (usage interne)."""
        if self.temp_poll_id is not None:
            return
        self.temp_poll_id = self.root.after(1500, self._poll_temp_once)

    def _stop_temp_polling(self):
        """Stop Temp Polling (usage interne)."""
        if self.temp_poll_id is not None:
            try:
                self.root.after_cancel(self.temp_poll_id)
            except Exception:
                pass
            self.temp_poll_id = None

    def _poll_temp_once(self):
        """Poll Temp Once (usage interne)."""
        self.temp_poll_id = None
        if self.selected_stem is None:
            return
        self.selected_scenes = self._scene_folders_for_stem(self.selected_stem)
        if self.selected_scenes:
            self._refresh_preview()
        else:
            self._start_temp_polling()

    def _on_czi_name_click(self, stem):
        """On fichier .czi Name Click (usage interne).
        
        Args:
            stem (Any): Nom de base (sans extension).
        """
        self.selected_stem = stem
        self.scene_index = 0
        self.selected_scenes = self._scene_folders_for_stem(stem)
        self._stop_temp_polling()
        self._refresh_preview()

    def _prev_scene(self):
        """Prev Scene (usage interne)."""
        if self.scene_index > 0:
            self.scene_index -= 1
            self._reset_zoom()

    def _next_scene(self):
        """Next Scene (usage interne)."""
        if self.scene_index < len(self.selected_scenes) - 1:
            self.scene_index += 1
            self._reset_zoom()

    def _on_configure(self, event):
        """On Configure (usage interne)."""
        if self.selected_stem is not None:
            self._refresh_preview()

    def _rebuild_name_list(self):
        """Rebuild Name List (usage interne)."""
        if self.name_list is None:
            return
        for child in self.name_list.inner.winfo_children():
            child.destroy()
        folder = Path(self.state.czi_folder_path)
        try:
            czi_files = list(convert_czi_to_jpeg.iter_czi_files(folder, recursive=True))
        except Exception as e:
            print(f"[window1] cannot list .czi: {e}")
            czi_files = []
        if not czi_files:
            lbl = tk.Label(self.name_list.inner, text="(aucun .czi)", font=SMALL_FONT,
                           bg="white", fg="gray", anchor="w")
            lbl.pack(fill=tk.X, padx=5, pady=2)
            return
        for p in czi_files:
            stem = p.stem
            lbl = tk.Label(self.name_list.inner, text=stem, font=SMALL_FONT,
                           bg="white", fg=FG_COLOR, anchor="w", cursor="hand2", padx=4, pady=2)
            lbl.pack(fill=tk.X, padx=2, pady=1)
            lbl.bind("<Button-1>", lambda e, s=stem: self._on_czi_name_click(s))
            lbl.bind("<Enter>", lambda e, w=lbl: w.config(bg="#e5f3ff"))
            lbl.bind("<Leave>", lambda e, w=lbl: w.config(bg="white"))

    def _set_input_folder(self, folder):
        """Set Input Folder (usage interne).
        
        Args:
            folder (Any): Repertoire (dossier).
        """
        self.state.czi_folder_path = folder
        self._rebuild_name_list()
        self.temp_vizu_dir.mkdir(parents=True, exist_ok=True)
        threading.Thread(
            target=self._convert_all_czi_to_temp_vizu, args=(folder,), daemon=True
        ).start()

    def _select_input_folder(self):
        """Select Input Folder (usage interne)."""
        self.source_var.set(1)
        self._set_input_folder(str(self.base / "input"))

    def _select_other_folder(self):
        """Select Other Folder (usage interne)."""
        initial = self.state.czi_folder_path if os.path.isdir(self.state.czi_folder_path) else str(self.base / "input")
        folder = filedialog.askdirectory(
            title="Select the folder containing the .czi files",
            mustexist=True, initialdir=initial,
        )
        if folder:
            self.source_var.set(2)
            self._set_input_folder(folder)
        else:
            self.source_var.set(1)

    def _convert_all_czi_to_temp_vizu(self, folder):
        """Convert All fichier .czi To Temp Vizu (usage interne).
        
        Args:
            folder (Any): Repertoire (dossier).
        """
        input_dir = Path(folder)
        output_dir = self.temp_vizu_dir
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
                    czi_path=czi_path, input_dir=input_dir, output_dir=output_dir,
                    downsample=PREVIEW_DOWNSAMPLE, quality=85, recursive=True,
                )
                converted += len(out_paths)
                print(f"[temp_vizu][OK] {czi_path.name} -> {len(out_paths)} image(s)")
            except Exception as exc:
                failed += 1
                print(f"[temp_vizu][ERROR] {czi_path}: {exc}")
        print(f"[temp_vizu] Done: {converted} image(s) created, {failed} failed. Output: {output_dir}")

    # -------------------------------------------------------------- navigation
    def _go_next(self):
        """Go Next (usage interne)."""
        from screens.window2_mask import Window2Screen
        self.app.show(Window2Screen, self.state.czi_folder_path)
