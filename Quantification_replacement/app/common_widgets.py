"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import tkinter as tk
from tkinter import ttk

from app.theme import FONT, SMALL_FONT, CLICK_BOXES_COLOR, BG_COLOR, FG_COLOR


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
