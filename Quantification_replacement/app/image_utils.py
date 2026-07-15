"""Module du projet Quantification (docstring generee).

Voir le README pour l'architecture generale.
"""
import os
from PIL import Image, ImageTk, ImageDraw, ImageFont


def get_img_dims(original_width, original_height, available_width, available_height):
    """Retourne (new_width, new_height) contenu dans (available_*) en préservant
    le ratio d'aspect."""
    if original_width <= 0 or original_height <= 0 or available_width <= 0 or available_height <= 0:
        return available_width, available_height

    aspect_ratio = original_width / original_height

    if available_width / available_height > aspect_ratio:
        new_height = available_height
        new_width = int(new_height * aspect_ratio)
    else:
        new_width = available_width
        new_height = int(new_width / aspect_ratio)

    return max(1, new_width), max(1, new_height)


def load_and_resize_image(file_path, max_width, max_height, base_dir="."):
    """Charge une image, la redimensionne (ratio préservé) et retourne un
    PhotoImage Tkinter. None si échec. Les chemins relatifs sont résolus depuis
    base_dir."""
    try:
        full_path = file_path if os.path.isabs(file_path) else os.path.join(base_dir, file_path)
        pil_img = Image.open(full_path)
        orig_w, orig_h = pil_img.size
        new_w, new_h = get_img_dims(orig_w, orig_h, max_width, max_height)
        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(pil_img)
    except Exception as e:
        print(f"Error loading image '{file_path}': {e}")
        return None


def _resolve_image_path(name, base_dir="."):
    """Retourne un chemin d'image existant pour `name`, en essayant la variante
    .temp.<ext> si besoin. Les chemins absolus sont retournés tels quels."""
    if os.path.isabs(name):
        return name
    full = os.path.join(base_dir, name)
    if os.path.exists(full):
        return name
    base, ext = os.path.splitext(name)
    temp_name = base + ".temp" + ext
    if os.path.exists(os.path.join(base_dir, temp_name)):
        return temp_name
    return name


def _load_resized_pil(file_path, max_width, max_height, base_dir="."):
    """Charge/redimensionne une image en RGBA (pour dessin de marqueurs)."""
    try:
        full_path = file_path if os.path.isabs(file_path) else os.path.join(base_dir, file_path)
        pil_img = Image.open(full_path)
        orig_w, orig_h = pil_img.size
        new_w, new_h = get_img_dims(orig_w, orig_h, max_width, max_height)
        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        return pil_img
    except Exception as e:
        print(f"Error loading image '{file_path}': {e}")
        return None


def _draw_markers_on_pil(pil_img, points, color):
    """Copie de pil_img avec des marqueurs numérotés aux coordonnées normalisées
    (x, y) dans [0, 1]."""
    img = pil_img.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    radius = max(4, min(w, h) // 35)
    font_size = max(9, int(radius * 1.1))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    for idx, (nx, ny) in enumerate(points):
        cx, cy = int(nx * w), int(ny * h)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color, outline="white",
        )
        text = str(idx + 1)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw // 2, cy - th // 2), text, fill="white", font=font)
    return img
