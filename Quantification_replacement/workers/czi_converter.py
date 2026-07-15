"""Conversion thread-safe .czi -> jpeg pour les pipelines Window2/Window3.

Réutilise convert_czi_to_jpeg.convert_one_file. À lancer dans des threads daemon
(depuis le main thread) car Tkinter n'est pas thread-safe.
"""
import threading
from pathlib import Path

import convert_czi_to_jpeg

DOWNSAMPLE_FACTOR = 20
JPEG_OUTPUT_SUBDIR = f"downsampled{DOWNSAMPLE_FACTOR}_jpeg"
QUANTIFICATION_DOWNSAMPLE = 4
QUANTIFICATION_JPEG_OUTPUT_SUBDIR = f"downsampled{QUANTIFICATION_DOWNSAMPLE}_jpeg"

_conversion_running = False


def convert_folder_to_jpeg(folder, output_dir, downsample, quality=95, log_prefix="convert_czi"):
    """Convert Folder To image JPEG
    
    Args:
        folder (Any): Repertoire (dossier).
        output_dir (Any): Repertoire (dossier).
        downsample (Any): Facteur de sous-echantillonnage (entier >= 1).
        quality (Any): Qualite JPEG (1-100).
        log_prefix (Any): Parametre log_prefix.
    """
    input_dir = Path(folder)
    output_dir = Path(output_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[{log_prefix}] Input folder not found: {input_dir}")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    czi_files = list(convert_czi_to_jpeg.iter_czi_files(input_dir, recursive=True))
    if not czi_files:
        print(f"[{log_prefix}] No .czi files found in: {input_dir}")
        return
    converted = 0
    failed = 0
    for czi_path in czi_files:
        try:
            out_paths = convert_czi_to_jpeg.convert_one_file(
                czi_path=czi_path, input_dir=input_dir, output_dir=output_dir,
                downsample=downsample, quality=quality, recursive=True,
            )
            converted += len(out_paths)
            print(f"[{log_prefix}][OK] {czi_path.name} -> {len(out_paths)} image(s)")
        except Exception as exc:
            failed += 1
            print(f"[{log_prefix}][ERROR] {czi_path}: {exc}")
    print(f"[{log_prefix}] Done: {converted} image(s) created, {failed} failed. Output: {output_dir}")


def convert_czi_to_png(czi_folder_path, base_output_dir):
    """20x conversion pour le pipeline Window2 (alignement/masque)."""
    convert_folder_to_jpeg(
        czi_folder_path,
        Path(base_output_dir) / JPEG_OUTPUT_SUBDIR,
        downsample=DOWNSAMPLE_FACTOR, quality=95,
        log_prefix="convert_czi_to_png",
    )


def convert_czi_to_quantification_jpeg(czi_folder_path, base_output_dir):
    """4x conversion indépendante pour le pipeline Window3 (quantification)."""
    global _conversion_running
    _conversion_running = True
    try:
        convert_folder_to_jpeg(
            czi_folder_path,
            Path(base_output_dir) / QUANTIFICATION_JPEG_OUTPUT_SUBDIR,
            downsample=QUANTIFICATION_DOWNSAMPLE, quality=95,
            log_prefix="convert_czi_to_quantification_jpeg",
        )
    finally:
        _conversion_running = False


def start_conversions(app, czi_folder_path):
    """Lance les deux conversions dans des threads daemon (appelé depuis le
    main thread)."""
    base_output = app.state.base_dir() / "output"
    t1 = threading.Thread(
        target=convert_czi_to_png, args=(czi_folder_path, base_output), daemon=True)
    t2 = threading.Thread(
        target=convert_czi_to_quantification_jpeg, args=(czi_folder_path, base_output), daemon=True)
    t1.start()
    t2.start()
    return t1, t2
