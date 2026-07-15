"""Quantification — point d'entrée (refactorisé).

Toute la logique d'interface vit désormais dans app/ (App, AppState) et
screens/ (Window1..4). Ce fichier ne fait qu'amorcer l'application sur la
première fenêtre (Window1 — aperçu .czi).
"""

from app import App
from screens.window1_preview import Window1Screen


def main():
    """Main"""
    app = App()
    app.show(Window1Screen)
    app.run()


if __name__ == "__main__":
    main()
