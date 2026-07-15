"""Quantification — point d'entrée (refactorisé).

Toute la logique d'interface vit désormais dans app/ (App, AppState) et
screens/ (Window1..4). Ce fichier ne fait qu'amorcer l'application sur la
première fenêtre (Window1 — aperçu .czi).
"""

import os
import shutil

from app import App, APP_BASE_DIR
from screens.window1_preview import TEMP_VIZU_SUBDIR
from workers.czi_converter import JPEG_OUTPUT_SUBDIR, QUANTIFICATION_JPEG_OUTPUT_SUBDIR
from screens.window1_preview import Window1Screen


def cleanup_on_startup():
    """Nettoyage des fichiers temporaires de downsample au lancement.

    Efface puis recrée les arbres de JPEG sous-échantillonnés non finaux
    produits par la session précédente, afin que l'application démarre
    toujours dans un état cohérent.

    Supprimés et recréés vides :
      - ./WorkInProgress/                 (masques en cours, aperçus temp_vizu)
      - ./output/downsampled4_jpeg        (JPEG quantification, Window 3)
      - ./output/downsampled20_jpeg       (JPEG alignement/masque, Window 2)

    Préservés volontairement :
      - ./AtlasImgs/                      (slices atlas en cache, coûteuses)
      - ./input/                          (fichiers .czi source)
      - ./output/<timestamp>/, ./output/cell_quantification_*/  (exports finaux)
    """
    # 1) Arbre WorkInProgress complet (masques en cours + aperçus temp_vizu).
    wip_dir = os.path.join(APP_BASE_DIR, "WorkInProgress")
    if os.path.isdir(wip_dir):
        shutil.rmtree(wip_dir, ignore_errors=True)
    os.makedirs(os.path.join(wip_dir, "temp_vizu", TEMP_VIZU_SUBDIR), exist_ok=True)

    # 2) Arbres JPEG de downsample non finaux sous ./output.
    temp_output_folders = [
        os.path.join(APP_BASE_DIR, "output", QUANTIFICATION_JPEG_OUTPUT_SUBDIR),
        os.path.join(APP_BASE_DIR, "output", JPEG_OUTPUT_SUBDIR),
    ]
    for folder in temp_output_folders:
        if not os.path.isdir(folder):
            continue
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)


def main():
    """Main"""
    cleanup_on_startup()
    app = App()
    app.show(Window1Screen)
    app.run()


if __name__ == "__main__":
    main()
