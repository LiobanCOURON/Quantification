"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import tkinter as tk
from tkinter import ttk
from PIL import Image

from app.theme import FONT, SMALL_FONT, CLICK_BOXES_COLOR, BG_COLOR, FG_COLOR

_ZOOM_MIN = 1.0
_ZOOM_MAX = 20.0
_ZOOM_STEP = 1.25


class PreviewZoomPanMixin:
    """Wheel-zoom + middle-mouse pan for a single preview image (parity with W2/W4).

    The host Screen must have:
        self.preview_label  (tk.Label showing the PhotoImage)
        self.preview_photo   (current tk.PhotoImage)
        self._refresh_preview()  (re-renders into preview_label)
    Call ``self._init_zoom_pan()`` in build(), bind the preview label to
    ``self._on_preview_wheel`` / ``_on_preview_pan_start`` / ``_on_preview_pan_motion``
    / ``_on_preview_pan_end``, and crop via ``self._zoom_crop(pil_img)`` inside
    ``_refresh_preview`` when ``zoom_state['zoom'] > 1``.
    """

    def _init_zoom_pan(self):
        self.zoom_state = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
        self.pan_state = {}
        self.viewport = (0.0, 0.0, 1.0, 1.0)

    def _zoom_viewport(self):
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

    def _displayed_size(self):
        photo = getattr(self, "preview_photo", None)
        if photo is None:
            return None
        try:
            return photo.width(), photo.height()
        except Exception:
            return None

    def _on_preview_wheel(self, event):
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
                    st["cx"] = min(1.0, max(0.0, cx))
                    st["cy"] = min(1.0, max(0.0, cy))
                    self._refresh_preview()
                    return
        st["zoom"] = new_zoom
        self._refresh_preview()

    def _on_preview_pan_start(self, event):
        self.pan_state["start_x"] = event.x
        self.pan_state["start_y"] = event.y
        self.pan_state["start_cx"] = self.zoom_state["cx"]
        self.pan_state["start_cy"] = self.zoom_state["cy"]
        self.pan_state["viewport"] = self.viewport

    def _on_preview_pan_motion(self, event):
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
        self._refresh_preview()

    def _on_preview_pan_end(self, _event=None):
        self.pan_state.clear()

    def _reset_zoom(self):
        self.zoom_state = {"zoom": 1.0, "cx": 0.5, "cy": 0.5}
        self.viewport = (0.0, 0.0, 1.0, 1.0)
        self._refresh_preview()

    def _zoom_crop(self, pil_img):
        """Crop pil_img to the current zoom viewport (identity if not zoomed)."""
        if self.zoom_state["zoom"] <= 1.0 + 1e-6:
            return pil_img
        self.viewport = self._zoom_viewport()
        nx0, ny0, nx1, ny1 = self.viewport
        sw, sh = pil_img.size
        left = int(round(nx0 * sw))
        upper = int(round(ny0 * sh))
        right = max(left + 1, int(round(nx1 * sw)))
        lower = max(upper + 1, int(round(ny1 * sh)))
        return pil_img.crop((left, upper, right, lower))


def add_help_button(parent, title, text, corner="ne", padx=8, pady=6):
    """Drop a small '?' button in the top-right of *parent* that opens a help pop-up.

    Args:
        parent (tk.Widget): container (usually the window header frame). The button
            is placed with pack(side=tk.RIGHT) so callers should pack it last.
        title (str): pop-up window title.
        text (str): multi-line help text shown in the pop-up.
        corner (str): ignored (kept for API stability); button is packed right.
        padx (int): horizontal padding.
        pady (int): vertical padding.

    Returns:
        tk.Button: the created help button.
    """
    btn = tk.Button(
        parent, text="?", font=("Arial", 12, "bold"),
        width=2, bg=ACCENT_COLOR_BLUE if False else CLICK_BOXES_COLOR,
        fg=FG_COLOR, relief="raised", cursor="question_arrow",
        command=lambda: _open_help(title, text),
    )
    btn.pack(side=tk.RIGHT, padx=padx, pady=pady)
    return btn


def _open_help(title, text):
    """Open a non-modal help pop-up (usage interne)."""
    win = tk.Toplevel()
    win.title(title)
    win.transient()
    win.resizable(True, True)
    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    body = tk.Text(win, font=SMALL_FONT, wrap=tk.WORD, bg="white", fg=FG_COLOR,
                   padx=12, pady=12, width=64, height=18)
    body.insert("1.0", text)
    body.config(state=tk.DISABLED)
    body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    close = tk.Button(win, text="Close", font=FONT, bg=CLICK_BOXES_COLOR, fg=FG_COLOR,
                      command=win.destroy)
    close.pack(pady=(0, 8))
    win.update_idletasks()
    try:
        win.geometry(f"+{win.winfo_screenwidth() // 2 - 200}+{win.winfo_screenheight() // 2 - 180}")
    except tk.TclError:
        pass


class ScrollableList:
    """Canvas + inner frame + scrollbar verticale. add_widget() ajoute un
    enfant empilé."""

    def __init__(self, parent, bg="white"):
        """Initialise l'objet et son etat.
        
        Args:
            parent (Any): Parametre parent.
            bg (Any): Parametre bg.
        """
        self.canvas = tk.Canvas(parent, bg=bg, highlightthickness=0)
        self.scroll = ttk.Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

    def grid(self, canvas_kw, scroll_kw):
        """Grid
        
        Args:
            canvas_kw (Any): Parametre canvas_kw.
            scroll_kw (Any): Parametre scroll_kw.
        """
        self.canvas.grid(**canvas_kw)
        self.scroll.grid(**scroll_kw)

    def add_widget(self, widget):
        """Add Widget
        
        Args:
            widget (Any): Widget Tkinter.
        """
        widget.pack(anchor="w", fill="x", padx=4, pady=2)


class FooterBar:
    """Barre pleine largeur en bas avec bouton principal à droite."""

    def __init__(self, parent, bg=BG_COLOR):
        """Initialise l'objet et son etat.
        
        Args:
            parent (Any): Parametre parent.
            bg (Any): Parametre bg.
        """
        self.frame = tk.Frame(parent, bg=bg, height=60)
        self.frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame.pack_propagate(False)

    def add_button(self, text, command, side=tk.RIGHT, **kw):
        """Add Button
        
        Args:
            text (Any): Texte.
            command (Any): Parametre command.
            side (Any): Parametre side.
            kw (dict): Arguments nommes variables.
        
        Returns:
            Any: Resultat.
        """
        btn = tk.Button(
            self.frame, text=text, font=FONT, bg=CLICK_BOXES_COLOR,
            fg=FG_COLOR, command=command, **kw,
        )
        btn.pack(side=side, padx=20, pady=12)
        return btn
