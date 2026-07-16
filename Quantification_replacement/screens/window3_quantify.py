"""Window 3 — cell quantification (wrapper autour de quantification_wrapper)."""

import os
import queue
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk

from app.base_screen import BaseScreen
from app.theme import (
    BG_COLOR, FG_COLOR, SMALL_FONT, FONT, CLICK_BOXES_COLOR,
    ACCENT_COLOR_BLUE, ACCENT_COLOR_GREEN, ERROR_COLOR,
)
from app.common_widgets import PreviewZoomPanMixin
from workers.czi_converter import (
    QUANTIFICATION_JPEG_OUTPUT_SUBDIR,
    _conversion_running as quantification_conversion_running,
)
from quantification_wrapper import discover_jpeg_images, run_quantification

_W3_WARN_COLOR = "#b36b00"


class Window3Screen(BaseScreen, PreviewZoomPanMixin):
    """Window3screen.
    
    Attributs et methodes definis ci-dessous.
    """
    def __init__(self, app):
        """Initialise l'objet et son etat.
        
        Args:
            app (Any): Instance de l'application.
        """
        super().__init__(app)
        self.base = self.state.base_dir()
        self.output_dir = self.base / "output"

        self.progress_queue = None
        self.poll_id = None
        self.running = False
        self.global_var = None
        self.file_var = None
        self.status_label = None
        self.file_label = None
        self.count_label = None
        self.log_text = None
        self.preview_label = None
        self.preview_photo = None
        self.start_button = None
        self.next_button = None
        self.last_result = None
        self.log_states = {}

    # ================================================================ build
    def build(self):
        """Build"""
        self._cancel_poll()
        try:
            self.root.unbind("<Configure>")
        except tk.TclError:
            pass

        self.preview_photo = None
        outer = tk.Frame(self.frame, bg=BG_COLOR)
        outer.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(outer, bg=BG_COLOR)
        header.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(header, text="Window 3 — Quantification cellulaire",
                 font=("Arial", 16, "bold"), bg=BG_COLOR, fg=FG_COLOR).pack(anchor="w")

        input_root = self._input_root()
        image_count = len(discover_jpeg_images(input_root, recursive=True))
        tk.Label(header, text=f"Source JPEG 4x : {input_root}  —  {image_count} image(s) détectée(s)",
                 font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR).pack(anchor="w", pady=(2, 0))

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

        self.global_var = tk.DoubleVar(value=0.0)
        self.file_var = tk.DoubleVar(value=0.0)

        tk.Label(progress_box, text="Global", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR
                 ).grid(row=0, column=0, sticky="w", padx=8, pady=5)
        ttk.Progressbar(progress_box, variable=self.global_var, maximum=100
                        ).grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        tk.Label(progress_box, text="Image", font=SMALL_FONT, bg=BG_COLOR, fg=FG_COLOR
                 ).grid(row=1, column=0, sticky="w", padx=8, pady=5)
        ttk.Progressbar(progress_box, variable=self.file_var, maximum=100
                        ).grid(row=1, column=1, sticky="ew", padx=8, pady=5)
        self.status_label = tk.Label(progress_box, text="Prêt.", font=SMALL_FONT,
                                     bg=BG_COLOR, fg=FG_COLOR, anchor="w")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(5, 2))
        self.file_label = tk.Label(progress_box, text="", font=SMALL_FONT,
                                   bg=BG_COLOR, fg=FG_COLOR, anchor="w")
        self.file_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5))
        self.count_label = tk.Label(progress_box, text="", font=FONT,
                                    bg=BG_COLOR, fg=ACCENT_COLOR_BLUE, anchor="w")
        self.count_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        log_box = tk.LabelFrame(left, text="Journal / triggers", font=FONT, bg=BG_COLOR, fg=FG_COLOR)
        log_box.pack(fill=tk.BOTH, expand=True)
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, height=14, font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("info", foreground=FG_COLOR)
        self.log_text.tag_configure("ok", foreground=ACCENT_COLOR_GREEN)
        self.log_text.tag_configure("error", foreground=ERROR_COLOR)
        self.log_text.tag_configure("warn", foreground=_W3_WARN_COLOR)
        log_scroll = tk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        right = tk.LabelFrame(content, text="Prévisualisation masque", font=FONT, bg=BG_COLOR, fg=FG_COLOR)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.preview_label = tk.Label(right, text="Le dernier masque détecté apparaîtra ici.",
                                      font=FONT, bg="white", fg="gray")
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        # Zoom + pan bindings (parity with Window 2 / Window 4).
        self._init_zoom_pan()
        self.preview_label.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview_label.bind("<Button-4>", self._on_preview_wheel)
        self.preview_label.bind("<Button-5>", self._on_preview_wheel)
        self.preview_label.bind("<Button-2>", self._on_preview_pan_start)
        self.preview_label.bind("<B2-Motion>", self._on_preview_pan_motion)
        self.preview_label.bind("<ButtonRelease-2>", self._on_preview_pan_end)

        button_frame = tk.Frame(outer, bg=BG_COLOR, height=60)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=20)
        button_frame.pack_propagate(False)
        tk.Button(button_frame, text="Previous", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._go_prev).pack(side=tk.LEFT, pady=12)
        tk.Button(button_frame, text="Reset zoom", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                  command=self._reset_zoom).pack(side=tk.LEFT, padx=12, pady=12)
        self.start_button = tk.Button(button_frame, text="Start quantification", font=FONT,
                                      bg=ACCENT_COLOR_GREEN, fg=FG_COLOR,
                                      command=self._start_quantification)
        self.start_button.pack(side=tk.LEFT, padx=12, pady=12)
        self.next_button = tk.Button(button_frame, text="Next", font=FONT, bg=CLICK_BOXES_COLOR,
                                     fg=FG_COLOR, command=self._go_next)
        self.next_button.pack(side=tk.RIGHT, pady=12)

        self.frame.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()

    # ================================================================ logic
    def _input_root(self):
        """Input Root (usage interne).
        
        Returns:
            Any: Resultat.
        """
        return self.output_dir / QUANTIFICATION_JPEG_OUTPUT_SUBDIR

    def _log(self, message, level="info"):
        """Log (usage interne).
        
        Args:
            message (Any): Parametre message.
            level (Any): Parametre level.
        """
        if self.log_text is None or not self.log_text.winfo_exists():
            return
        tag = level if level in ("info", "ok", "error", "warn") else "info"
        self.log_text.insert(tk.END, str(message) + "\n", tag)
        self.log_text.see(tk.END)

    def _set_preview(self, mask_path):
        """Set Preview (usage interne).
        
        Args:
            mask_path (Any): Chemin vers le fichier.
        """
        if self.preview_label is None or not self.preview_label.winfo_exists():
            return
        if not mask_path or not os.path.exists(mask_path):
            return
        win_w = self.root.winfo_width() if self.root.winfo_width() > 100 else 800
        win_h = self.root.winfo_height() if self.root.winfo_height() > 100 else 600
        max_w = max(200, int(win_w * 0.42))
        max_h = max(180, int(win_h * 0.55))
        try:
            img = Image.open(mask_path).convert("RGB")
            img = self._zoom_crop(img)
            img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
        except Exception as exc:
            print(f"[window3] cannot load preview {mask_path}: {exc}")
            photo = None
        if photo is not None:
            self.preview_photo = photo
            self.preview_label.config(image=photo, text="")

    def _reset_progress(self):
        """Reset Progress (usage interne)."""
        self.log_states = {}
        if self.global_var is not None:
            self.global_var.set(0.0)
        if self.file_var is not None:
            self.file_var.set(0.0)
        if self.status_label is not None:
            self.status_label.config(text="Prêt.")
        if self.file_label is not None:
            self.file_label.config(text="")
        if self.count_label is not None:
            self.count_label.config(text="")
        if self.preview_label is not None:
            self.preview_label.config(image="", text="Le dernier masque détecté apparaîtra ici.")
        if self.log_text is not None:
            self.log_text.delete("1.0", tk.END)

    def _handle_event(self, event):
        """Handle Event (usage interne)."""
        event_type = event.get("type", "")
        global_pct = event.get("global_pct")
        file_pct = event.get("file_pct")
        if isinstance(global_pct, (int, float)) and self.global_var is not None:
            self.global_var.set(max(0.0, min(100.0, float(global_pct))))
        if isinstance(file_pct, (int, float)) and self.file_var is not None:
            self.file_var.set(max(0.0, min(100.0, float(file_pct))))
        image = event.get("image", "")
        message = event.get("message", "")

        if event_type == "started":
            if self.status_label is not None:
                self.status_label.config(text=message)
        elif event_type == "file_started":
            image_key = image or str(event.get("file_index", ""))
            self.log_states[image_key] = {}
            if self.status_label is not None:
                self.status_label.config(text="Pré-traitement...")
            if self.file_label is not None:
                self.file_label.config(
                    text=f"Image {event.get('file_index', '?')} / {event.get('file_total', '?')} : {image}")
            self._log(f"Image {image} : pré-traitement", "info")
        elif event_type in ("file_step", "heartbeat"):
            if self.status_label is not None:
                self.status_label.config(text="Quantification (two-pass)...")
        elif event_type == "log":
            if "TRIGGER:DARK_ROI_CREATED" in message or "TRIGGER:DARK_DETECTION_DONE" in message:
                image_key = image or str(event.get("file_index", ""))
                state = self.log_states.setdefault(image_key, {})
                if not state.get("dark_logged"):
                    state["dark_logged"] = True
                    self._log(f"Image {image} : pass dark (Hematoxylin OD)...", "info")
            elif "TRIGGER:LIGHT_ROI_CREATED" in message or "TRIGGER:LIGHT_DETECTION_DONE" in message:
                image_key = image or str(event.get("file_index", ""))
                state = self.log_states.setdefault(image_key, {})
                if not state.get("light_logged"):
                    state["light_logged"] = True
                    self._log(f"Image {image} : pass light (Optical density sum)...", "info")
            elif "TRIGGER:MERGE_DONE" in message:
                self._log(f"Image {image} : fusion dark + light...", "info")
            elif "ERROR" in message or "Exception" in message:
                self._log(f"Image {image} : erreur - {message}", "error")
        elif event_type == "file_done":
            num_cells = event.get("num_cells", "")
            mask_path = event.get("mask_path", "")
            if mask_path:
                self._set_preview(mask_path)
            self._log(f"Image {image} : quantification terminée ! {num_cells} cellules", "ok")
            if self.count_label is not None:
                self.count_label.config(text=f"Dernier résultat : {num_cells} cellule(s)")
        elif event_type == "file_error":
            self._log(f"Image {image} : erreur - {message}", "error")
            if self.count_label is not None:
                self.count_label.config(text=f"Erreur sur {image}")
        elif event_type == "waiting_for_images":
            if self.status_label is not None:
                self.status_label.config(text="En attente des prochains JPEG 4x...")
        elif event_type == "done":
            self.running = False
            total_cells = event.get("total_cells", 0)
            successful = event.get("successful_images", 0)
            total = event.get("total_images", 0)
            text = f"Terminé : {total_cells} cellule(s), {successful}/{total} image(s)"
            if self.status_label is not None:
                self.status_label.config(text=text)
            if self.count_label is not None:
                self.count_label.config(text=f"Total : {total_cells} cellule(s)")
            if self.start_button is not None:
                self.start_button.config(state=tk.NORMAL)
            if self.next_button is not None:
                self.next_button.config(state=tk.NORMAL)
        elif event_type == "worker_done":
            self.last_result = event.get("result")
        elif event_type == "worker_error":
            self.running = False
            if self.status_label is not None:
                self.status_label.config(text="Erreur quantification.")
            self._log(event.get("error", "Erreur inconnue"), "error")
            if self.start_button is not None:
                self.start_button.config(state=tk.NORMAL)
            if self.next_button is not None:
                self.next_button.config(state=tk.NORMAL)

    def _poll_queue(self):
        """Poll Queue (usage interne)."""
        self.poll_id = None
        if self.progress_queue is None:
            return
        drained = 0
        while drained < 100:
            try:
                event = self.progress_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
            drained += 1
        if self.running:
            self.poll_id = self.root.after(100, self._poll_queue)

    def _cancel_poll(self):
        """Cancel Poll (usage interne)."""
        if self.poll_id is not None:
            try:
                self.root.after_cancel(self.poll_id)
            except Exception:
                pass
            self.poll_id = None

    def _start_quantification(self):
        """Start Quantification (usage interne)."""
        if self.running:
            return
        input_root = self._input_root()
        image_paths = discover_jpeg_images(input_root, recursive=True)
        if not image_paths and not quantification_conversion_running():
            messagebox.showwarning(
                "Aucune image",
                f"Aucun JPEG trouvé dans:\n{input_root}\n\n"
                "Lancer d'abord la conversion .czi → jpeg depuis les fenêtres précédentes.",
            )
            return

        output_dir = self.output_dir / f"cell_quantification_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        event_queue = queue.Queue()
        self.progress_queue = event_queue
        self.running = True
        self._reset_progress()
        if self.start_button is not None:
            self.start_button.config(state=tk.DISABLED)
        if self.next_button is not None:
            self.next_button.config(state=tk.DISABLED)

        def progress_cb(event):
            """Progress Cb"""
            event_queue.put(event)

        def worker():
            """Worker"""
            try:
                result = run_quantification(
                    image_paths=image_paths,
                    output_dir=output_dir,
                    progress_cb=progress_cb,
                    refresh_images_cb=lambda: discover_jpeg_images(input_root, recursive=True),
                    input_complete_cb=lambda: not quantification_conversion_running(),
                    poll_interval_seconds=1.5,
                    slice_depth_um=float(getattr(self.state, "slice_depth_um", 0.0) or 0.0),
                    interslice_um=float(getattr(self.state, "interslice_um", 0.0) or 0.0),
                )
                event_queue.put({"type": "worker_done", "result": result})
            except Exception as exc:
                event_queue.put({"type": "worker_error", "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()
        self._cancel_poll()
        self.poll_id = self.root.after(100, self._poll_queue)

    # ============================================================= navigation
    def _go_prev(self):
        """Go Prev (usage interne)."""
        from screens.window2_mask import Window2Screen
        self.app.show(Window2Screen)

    def _go_next(self):
        """Go Next (usage interne)."""
        from screens.window4_validate import Window4Screen
        self.app.show(Window4Screen)
