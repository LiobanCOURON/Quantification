"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import tkinter as tk
from tkinter import ttk

from app.theme import FONT, CLICK_BOXES_COLOR, BG_COLOR, FG_COLOR


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
