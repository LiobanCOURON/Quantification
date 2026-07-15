"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
# Tests sur les fonctions pures extraites dans app/image_utils.py.
# Ils ciblent l'API cible et échouent tant que image_utils.py n'existe pas.

from app.image_utils import get_img_dims


def test_get_img_dims_width_limited():
    """Test Get image Dims Width Limited"""
    w, h = get_img_dims(2000, 1000, 800, 600)
    assert w == 800
    assert h == 400  # ratio 2:1 préservé


def test_get_img_dims_height_limited():
    """Test Get image Dims Height Limited"""
    w, h = get_img_dims(1000, 2000, 800, 600)
    assert h == 600
    assert w == 300


def test_get_img_dims_zero_guard():
    """Test Get image Dims Zero Guard"""
    w, h = get_img_dims(0, 0, 800, 600)
    assert (w, h) == (800, 600)
