"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import tkinter as tk
from abc import ABC, abstractmethod

from app.theme import BG_COLOR


class BaseScreen(ABC):
    """Classe de base d'une 'fenêtre' (vue). Chaque vue construit ses widgets
    DANS self.frame (plus de root.winfo_children() global), encapsule son état
    en attributs d'instance, et expose build()/destroy()/on_resize()."""

    def __init__(self, app):
        """Initialise l'objet et son etat.
        
        Args:
            app (Any): Instance de l'application.
        """
        self.app = app
        self.root = app.root
        self.state = app.state
        self.frame = tk.Frame(self.root, bg=BG_COLOR)

    @abstractmethod
    def build(self):
        """Construit les widgets DANS self.frame (et pack le frame)."""

    def on_show(self):
        """Appelé après build() (hooks optionnels)."""

    def on_resize(self, event=None):
        """Rafraîchit les images à redimensionnement (override si besoin)."""

    def destroy(self):
        """Détache les bindings et détruit la vue proprement."""
        try:
            self.root.unbind("<Configure>")
        except tk.TclError:
            pass
        self.frame.destroy()
