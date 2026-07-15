#!/usr/bin/env python3
"""
Convertit un dossier de fichiers Zeiss .czi en images .jpeg downsamplées.

Cette version :
- conserve la lecture mosaic rapide par aicspylibczi (read_mosaic, scale_factor)
  qui est le chemin validé pour préserver la couleur (BGR->RGB) ;
- détecte chaque scène (ROI) individuellement via
  get_mosaic_scene_bounding_box(index=s) et lit le mosaic de CETTE scène
  (pas le WSI global) ;
- exporte TOUTES les couches Z (une image .jpeg par plan Z), en passant
  Z=<index> à read_mosaic quand le fichier possède une dimension Z ;
- garde le fallback aicsimageio (AICSImage) pour les fichiers non-mosaic, en
  itérant également sur toutes les scènes et couches Z ;
- place chaque scène dans son propre dossier <stem>_<scene+1>/ et chaque plan Z
  dans un fichier <stem>_z_slice_<z+1>.jpeg (index scène et Z en base 1).

Structure de sortie :
    ./<output_dir>/downsampled<factor>_jpeg/[sous-dossier/]
        <stem>_1/<stem>_z_slice_1.jpeg ... <stem>_z_slice_<Z>.jpeg
        <stem>_2/<stem>_z_slice_1.jpeg ...
        ...

Dépendances recommandées :
    py -m pip install "aicsimageio[czi]" "aicspylibczi>=3.1.1" pillow numpy

Exemple :
    py convert_czi_to_jpeg.py ./input ./output
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from PIL import Image


# ----------------------------------------------------------------------------
# Découverte des fichiers
# ----------------------------------------------------------------------------

def iter_czi_files(input_dir: Path, recursive: bool) -> Iterable[Path]:
    """Retourne les fichiers .czi du dossier d'entrée, triés."""
    pattern = "**/*.czi" if recursive else "*.czi"
    yield from sorted(input_dir.glob(pattern))


# ----------------------------------------------------------------------------
# Métadonnées CZI
# ----------------------------------------------------------------------------

def _czi_metadata_to_xml_text(metadata: object) -> str:
    """Convert aicspylibczi metadata (Element/bytes/str) to XML text."""
    if metadata is None:
        return ""
    if isinstance(metadata, bytes):
        return metadata.decode("utf-8", errors="replace")
    if isinstance(metadata, str):
        return metadata
    if isinstance(metadata, ET.Element):
        return ET.tostring(metadata, encoding="unicode")
    return str(metadata)


def _xml_name_without_namespace(tag: str) -> str:
    """Xml Name Without Namespace (usage interne).
    
    Args:
        tag (str): Parametre tag.
    
    Returns:
        str: Resultat.
    """
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


@lru_cache(maxsize=256)
def get_czi_pixel_size_um(czi_path: str | Path) -> float | None:
    """
    Return the physical X/Y pixel size of the original CZI in micrometres.

    The Zeiss metadata usually stores scaling distances in metres:
        Metadata/Scaling/Items/Distance[@Id='X']/Value

    The app uses square pixels for surface estimation. If both X and Y are
    available they are averaged; if only one is available it is used. Returns
    None when metadata cannot be read or does not contain a usable scale.
    """
    try:
        from aicspylibczi import CziFile
    except ImportError:
        return None

    try:
        czi_file = CziFile(Path(czi_path))
        metadata_obj = getattr(czi_file, "meta", None)
        metadata = metadata_obj() if callable(metadata_obj) else metadata_obj
        xml_text = _czi_metadata_to_xml_text(metadata)
        if not xml_text.strip():
            return None
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    distances: dict[str, float] = {}
    for elem in root.iter():
        if _xml_name_without_namespace(elem.tag) != "Distance":
            continue
        axis = (elem.attrib.get("Id") or elem.attrib.get("id") or "").upper()
        if axis not in {"X", "Y"}:
            continue
        value = None
        for child in elem:
            if _xml_name_without_namespace(child.tag) == "Value" and child.text:
                try:
                    value = float(child.text.strip())
                except ValueError:
                    value = None
                break
        if value is not None and value > 0:
            # CZI scaling values are stored in metres; convert to µm.
            distances[axis] = value * 1_000_000.0

    values = [distances[a] for a in ("X", "Y") if a in distances]
    if not values:
        return None
    return float(sum(values) / len(values))


# ----------------------------------------------------------------------------
# Utilitaires numpy / couleur (inchangés par rapport à la version validée)
# ----------------------------------------------------------------------------

def normalize_to_uint8(array: np.ndarray) -> np.ndarray:
    """
    Convertit une image numérique en uint8 pour l'enregistrement JPEG.

    Les images microscopie sont souvent en uint16. On fait une normalisation
    robuste par percentiles pour éviter qu'un pixel très brillant ne rende
    l'image globale trop sombre.
    """
    array = np.asarray(array)

    if array.dtype == np.uint8:
        return array

    array = array.astype(np.float32, copy=False)

    finite_mask = np.isfinite(array)
    if not finite_mask.any():
        return np.zeros(array.shape, dtype=np.uint8)

    finite_values = array[finite_mask]
    p_low, p_high = np.percentile(finite_values, (0.5, 99.5))

    if p_high <= p_low:
        p_low = float(finite_values.min())
        p_high = float(finite_values.max())

    if p_high <= p_low:
        return np.zeros(array.shape, dtype=np.uint8)

    array = np.clip((array - p_low) / (p_high - p_low), 0, 1)
    return (array * 255).round().astype(np.uint8)


def dim_size_from_czi_shape(czi_file: Any, dim: str) -> int | None:
    """Retourne la taille d'une dimension CZI depuis aicspylibczi."""
    for shape in czi_file.get_dims_shape():
        if dim in shape:
            start, end = shape[dim]
            return int(end - start)
    return None


def squeeze_mosaic_array(array: np.ndarray) -> np.ndarray:
    """
    Supprime les dimensions singleton ajoutées par aicspylibczi sans modifier
    l'ordre spatial Y,X ni l'éventuelle dimension couleur finale A/RGB.
    """
    array = np.asarray(array)

    while array.ndim > 3 and array.shape[0] == 1:
        array = array[0]

    return np.squeeze(array)


def array_to_2d_plane(array: np.ndarray) -> np.ndarray:
    """Réduit une sortie mosaic en plan Y,X."""
    array = squeeze_mosaic_array(array)

    if array.ndim == 2:
        return array

    if array.ndim == 3:
        if array.shape[-1] >= 1:
            return array[..., 0]
        if array.shape[0] >= 1:
            return array[0]

    raise ValueError(f"Impossible de réduire la tuile mosaic en Y,X. Forme : {array.shape}")


def sample_axis_to_rgb(array: np.ndarray, pixel_type: object) -> np.ndarray:
    """
    Convertit la dimension sample A de libCZI en RGB pour PIL.

    Les CZI Zeiss brightfield sont souvent en pixel_type bgr24 : libCZI renvoie
    alors les samples dans l'ordre B,G,R, alors que PIL attend R,G,B.
    """
    rgb = np.asarray(array)[..., :3]
    pixel_type_text = str(pixel_type).lower()

    if pixel_type_text.startswith("bgr") or pixel_type_text.startswith("bgra"):
        rgb = rgb[..., ::-1]

    return rgb


def cyx_to_jpeg_array(cyx: np.ndarray) -> np.ndarray:
    """Transforme un tableau C,Y,X en tableau compatible PIL/JPEG."""
    channels = cyx.shape[0]

    if channels == 1:
        return normalize_to_uint8(cyx[0])

    if channels >= 3:
        rgb = np.stack([cyx[0], cyx[1], cyx[2]], axis=-1)
        return normalize_to_uint8(rgb)

    # JPEG ne gère pas directement 2 canaux : on sauvegarde le premier canal.
    return normalize_to_uint8(cyx[0])


def downsample_image(image: Image.Image, factor: int) -> Image.Image:
    """Downsample spatial d'une image PIL par un facteur entier."""
    if factor <= 1:
        return image

    new_width = max(1, image.width // factor)
    new_height = max(1, image.height // factor)

    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


# ----------------------------------------------------------------------------
# Lecture mosaic rapide (aicspylibczi) avec itération scène × Z
# ----------------------------------------------------------------------------

def read_fast_mosaic_scene_z_planes(
    czi_path: Path,
    downsample: int,
) -> Iterator[tuple[int, int, np.ndarray]] | None:
    """
    Lit un CZI mosaic avec aicspylibczi et yield (scene_index, z_index, jpeg_array)
    pour chaque (scène, couche Z).

    Pour chaque scène, on demande à libCZI une version downsamplée du mosaic de
    CETTE scène uniquement (region = get_mosaic_scene_bounding_box(index=s)),
    puis on itère sur les couches Z. C'est le chemin rapide validé qui préserve
    la couleur (BGR->RGB via l'axe A) et qui évite de générer le WSI global.

    Retourne None si le fichier n'est pas un mosaic.
    """
    try:
        from aicspylibczi import CziFile
    except ImportError as exc:
        raise RuntimeError(
            "Le module 'aicspylibczi' est introuvable. Installez-le avec :\n"
            '    py -m pip install "aicspylibczi>=3.1.1" "fsspec>=2022.7.1"'
        ) from exc

    czi_file = CziFile(czi_path)
    if not czi_file.is_mosaic():
        return None

    scale_factor = 1.0 / downsample

    # Détection des scènes : get_all_scene_bounding_boxes() retourne {index: BBox}.
    scene_bboxes = czi_file.get_all_scene_bounding_boxes()
    scene_indices = sorted(scene_bboxes.keys())

    # On ne passe Z= que si le fichier possède réellement une dimension Z,
    # sinon read_mosaic lève "Coordinate for dimension 'Z' is not expected".
    z_size_raw = dim_size_from_czi_shape(czi_file, "Z")
    has_z = bool(z_size_raw and z_size_raw > 0)
    z_count = z_size_raw or 1
    channel_count = dim_size_from_czi_shape(czi_file, "C") or 1

    def read_channel(channel_index: int, z_index: int, region: tuple) -> np.ndarray:
        """Read Channel
        
        Args:
            channel_index (int): Index (base 0).
            z_index (int): Index (base 0).
            region (tuple): Region anatomique.
        
        Returns:
            np.ndarray: Resultat.
        """
        kwargs: dict[str, int] = {"C": channel_index}
        for dim in czi_file.dims:
            # S et M ne doivent pas être fournis à read_mosaic ; Y/X/A sont
            # définis par region ou par la sortie couleur.
            if dim in {"C", "S", "M", "Y", "X", "A"}:
                continue
            if dim == "Z":
                # Z est fourni explicitement ci-dessous (uniquement si has_z).
                continue
            kwargs[dim] = 0
        if has_z:
            kwargs["Z"] = z_index

        read_mosaic: Any = czi_file.read_mosaic
        return read_mosaic(
            region=region,
            scale_factor=scale_factor,
            **kwargs,
        )

    def build_jpeg_array(z_index: int, region: tuple) -> np.ndarray:
        """Build image JPEG Array
        
        Args:
            z_index (int): Index (base 0).
            region (tuple): Region anatomique.
        
        Returns:
            np.ndarray: Resultat.
        """
        first_channel = squeeze_mosaic_array(read_channel(0, z_index, region))

        # Cas fréquent en brightfield : C=1 et A=3. Pour bgr24, libCZI renvoie
        # B,G,R alors que PIL/JPEG attend R,G,B.
        if first_channel.ndim == 3 and first_channel.shape[-1] >= 3:
            return normalize_to_uint8(sample_axis_to_rgb(first_channel, czi_file.pixel_type))

        # Cas fluorescence / multicanal : on compose RGB avec les 3 premiers C.
        if channel_count >= 3:
            rgb = np.stack(
                [array_to_2d_plane(read_channel(channel, z_index, region)) for channel in range(3)],
                axis=-1,
            )
            return normalize_to_uint8(rgb)

        # 1 ou 2 canaux : on sauvegarde le premier canal.
        return normalize_to_uint8(array_to_2d_plane(first_channel))

    def generator() -> Iterator[tuple[int, int, np.ndarray]]:
        """Generator"""
        for scene_index in scene_indices:
            # Bounding box du mosaic de CETTE scène (pas le WSI global).
            scene_bbox = czi_file.get_mosaic_scene_bounding_box(index=scene_index)
            region = (scene_bbox.x, scene_bbox.y, scene_bbox.w, scene_bbox.h)

            for z_index in range(z_count):
                yield scene_index, z_index, build_jpeg_array(z_index, region)

    return generator()


# ----------------------------------------------------------------------------
# Fallback aicsimageio (non-mosaic) avec itération scène × Z
# ----------------------------------------------------------------------------

def read_czi_scene_z_planes_aics(czi_path: Path) -> Iterator[tuple[int, int, np.ndarray]]:
    """
    Lit un CZI avec aicsimageio et yield (scene_index, z_index, jpeg_array)
    pour chaque (scène, couche Z). aicsimageio stitch les tuiles de chaque scène
    automatiquement. Les autres dimensions non spatiales (T/M/I/V...) -> index 0.
    """
    try:
        from aicsimageio.aics_image import AICSImage
    except ImportError as exc:
        raise RuntimeError(
            "Le module 'aicsimageio' est introuvable. Installez les dépendances avec :\n"
            '    pip install "aicsimageio[czi]" pillow numpy'
        ) from exc

    image = AICSImage(czi_path)

    for scene_index, scene_name in enumerate(image.scenes):
        image.set_scene(scene_name)
        dims = image.dims.order
        z_size = image.dims.Z
        z_count = max(1, int(z_size)) if z_size else 1

        for z_index in range(z_count):
            # Paramètres d'indexation pour toutes les dimensions présentes mais
            # non demandées dans la sortie CYX, sauf Z qui varie.
            kwargs: dict[str, int] = {}
            for dim in dims:
                if dim in {"C", "Y", "X"}:
                    continue
                if dim == "Z":
                    kwargs[dim] = z_index
                else:
                    kwargs[dim] = 0

            data = image.get_image_data("CYX", **kwargs)
            data = np.asarray(data)
            data = np.squeeze(data)

            if data.ndim == 2:
                data = data[np.newaxis, :, :]

            if data.ndim != 3:
                raise ValueError(
                    f"Impossible de réduire {czi_path.name} "
                    f"(scène {scene_index + 1}, Z={z_index + 1}) en C,Y,X. "
                    f"Forme obtenue : {data.shape}"
                )

            yield scene_index, z_index, cyx_to_jpeg_array(data)


# ----------------------------------------------------------------------------
# Conversion d'un fichier
# ----------------------------------------------------------------------------

def convert_one_file(
    czi_path: Path,
    input_dir: Path,
    output_dir: Path,
    downsample: int,
    quality: int,
    recursive: bool,
    fast_mosaic: bool = True,
) -> list[Path]:
    """
    Convertit un fichier .czi en une liste de JPEG (un par scène × couche Z).

    Sortie :
        <output_dir>/downsampled<factor>_jpeg/[sous-dossier/]
            <stem>_<scene+1>/<stem>_z_slice_<z+1>.jpeg

    Retourne la liste de tous les fichiers .jpeg créés.
    """
    # 1) Tente le chemin mosaic rapide (préserve la couleur) — yield déjà downsamplé.
    planes: Iterator[tuple[int, int, np.ndarray]]
    already_downsampled = False

    if fast_mosaic:
        gen = read_fast_mosaic_scene_z_planes(czi_path, downsample=downsample)
        if gen is not None:
            planes = gen
            already_downsampled = True
        else:
            planes = read_czi_scene_z_planes_aics(czi_path)
    else:
        planes = read_czi_scene_z_planes_aics(czi_path)

    # 2) Chemin de sortie de base.
    stem = czi_path.stem
    if recursive:
        subfolder = czi_path.relative_to(input_dir).parent
    else:
        subfolder = Path()
    base_dir = output_dir / f"downsampled{downsample}_jpeg" / subfolder

    created: list[Path] = []
    for scene_index, z_index, jpeg_array in planes:
        scene_dir = base_dir / f"{stem}_{scene_index + 1}"
        scene_dir.mkdir(parents=True, exist_ok=True)

        pil_image = Image.fromarray(jpeg_array)
        if not already_downsampled:
            pil_image = downsample_image(pil_image, downsample)

        out_path = scene_dir / f"{stem}_z_slice_{z_index + 1}.jpeg"
        pil_image.save(out_path, format="JPEG", quality=quality, optimize=True)
        created.append(out_path)

    return created


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def positive_int(value: str) -> int:
    """Valide un entier strictement positif pour argparse."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("la valeur doit être >= 1")
    return parsed


def parse_args() -> argparse.Namespace:
    """Parse Args
    
    Returns:
        argparse.Namespace: Resultat.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Extrait les images de fichiers .czi Zen Blue vers des .jpeg "
            "downsamplés, en séparant chaque scène (ROI) dans son propre dossier "
            "et en exportant toutes les couches Z."
        )
    )
    parser.add_argument("input_dir", type=Path, help="Dossier contenant les .czi.")
    parser.add_argument("output_dir", type=Path, help="Dossier de sortie.")
    parser.add_argument("--downsample", type=positive_int, default=4, help="Facteur de downsample. Défaut : 4.")
    parser.add_argument("--quality", type=positive_int, default=95, help="Qualité JPEG 1-100. Défaut : 95.")
    parser.add_argument("--no-recursive", action="store_true", help="Ne pas chercher dans les sous-dossiers.")
    parser.add_argument(
        "--no-fast-mosaic",
        action="store_true",
        help="Désactive la lecture mosaic rapide (aicspylibczi) et force aicsimageio.",
    )
    return parser.parse_args()


def main() -> int:
    """Main
    
    Returns:
        int: Resultat.
    """
    args = parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir
    recursive = not args.no_recursive

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Erreur : dossier d'entrée invalide : {input_dir}", file=sys.stderr)
        return 1

    if not (1 <= args.quality <= 100):
        print("Erreur : --quality doit être entre 1 et 100.", file=sys.stderr)
        return 1

    czi_files = list(iter_czi_files(input_dir, recursive=recursive))
    if not czi_files:
        print(f"Aucun fichier .czi trouvé dans : {input_dir}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    converted = 0
    failed = 0

    for czi_path in czi_files:
        try:
            out_paths = convert_one_file(
                czi_path=czi_path,
                input_dir=input_dir,
                output_dir=output_dir,
                downsample=args.downsample,
                quality=args.quality,
                recursive=recursive,
                fast_mosaic=not args.no_fast_mosaic,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[ERREUR] {czi_path} : {exc}", file=sys.stderr)
            continue

        converted += len(out_paths)
        for out_path in out_paths:
            print(f"[OK] {czi_path} -> {out_path}")

    print(
        f"Terminé : {converted} image(s) JPEG créée(s), {failed} échec(s). "
        f"Dossier de sortie : {output_dir / f'downsampled{args.downsample}_jpeg'}"
    )

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())