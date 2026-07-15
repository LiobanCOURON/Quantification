"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import os
import tkinter as tk

from app.state import AppState
from app.base_screen import BaseScreen
from app.theme import BG_COLOR

# Racine du projet = parent de app/ (.../Quantification_replacement)
APP_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class App:
    """Propriétaire unique de tk.Tk(). Le switch de vues se fait via show() :
    la vue courante est détruite proprement (plus de root.winfo_children()
    global), et la suivante est construite dans son propre frame."""

    def __init__(self):
        """Initialise l'objet et son etat."""
        self.root = tk.Tk()
        self.root.title("Quantification")
        self.root.geometry("800x600")
        self.root.configure(bg=BG_COLOR)
        self.state = AppState(
            czi_folder_path=os.path.join(APP_BASE_DIR, "input"),
        )
        self.current: BaseScreen | None = None

    def show(self, screen_cls, *args, **kwargs):
        """Show
        
        Args:
            screen_cls (Any): Parametre screen_cls.
            args (tuple): Arguments positionnels variables.
            kwargs (dict): Arguments nommes variables.
        """
        if self.current is not None:
            self.current.destroy()
        self.current = screen_cls(self, *args, **kwargs)
        self.current.build()
        self.current.on_show()

    def run(self):
        """Run"""
        self.root.mainloop()
