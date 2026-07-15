"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import tkinter as tk
import pytest

@pytest.fixture(scope="session")
def tk_root():
    """Tk Root"""
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()
