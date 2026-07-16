"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppState:
    """Données partagées entre les fenêtres (remplace ~60 variables globales)."""

    czi_folder_path: str = "./input"
    slice_depth_um: float = 40.0
    interslice_um: float = 0.0
    quantification_running: bool = False
    # Rempli par Window1, lu par Window4.
    selected_stem: str | None = None
    # Drapeaux transitoires de conversion.
    w1_preview_running: bool = False

    def base_dir(self) -> Path:
        """Base repertoire
        
        Returns:
            Path: Resultat.
        """
        from app.app import APP_BASE_DIR
        return Path(APP_BASE_DIR)
