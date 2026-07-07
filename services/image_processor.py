"""
services/image_processor.py — Preprocesamiento de imagen
"""
import os
from PIL import Image, ImageOps, UnidentifiedImageError
from config import MAX_IMAGE_DIM


def preprocess_image(image_path: str) -> str:
    """Redimensionar a max 1024px lado largo. Corrige orientación EXIF."""
    try:
        img = Image.open(image_path)
    except UnidentifiedImageError:
        raise ValueError("El archivo no es una imagen válida")
    img = ImageOps.exif_transpose(img)
    if max(img.size) > MAX_IMAGE_DIM:
        ratio = MAX_IMAGE_DIM / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    # Sobrescribir el _processed.jpg existente (no acumular)
    base, ext = os.path.splitext(image_path)
    out_path = f"{base}_processed.jpg"
    img.save(out_path, "JPEG", quality=90)
    return out_path


def rotate_image(image_path: str, degrees: int) -> str:
    """Rotar imagen 90, 180, o 270 grados. Sobrescribe el archivo _current.

    Mantiene UN SOLO archivo de trabajo por imagen, sin acumular sufijos.
    """
    if degrees not in (90, 180, 270):
        raise ValueError(f"Grados deben ser 90, 180, o 270, no {degrees}")

    try:
        img = Image.open(image_path)
    except UnidentifiedImageError:
        raise ValueError("El archivo no es una imagen válida")

    # Rotar (PIL rotate es contra horario, invertimos)
    rotated = img.rotate(-degrees, expand=True)

    # Guardar SIEMPRE en el mismo archivo de trabajo
    base, ext = os.path.splitext(image_path)
    # Quitar sufijos previos para obtener el base real
    for suffix in ('_processed', '_rot90', '_rot180', '_rot270', '_enhanced', '_current'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    out_path = f"{base}_current.jpg"
    rotated.save(out_path, "JPEG", quality=90)
    return out_path


def enhance_image(image_path: str) -> str:
    """Mejorar contraste y nitidez. Sobrescribe el archivo _current."""
    try:
        img = Image.open(image_path)
    except UnidentifiedImageError:
        raise ValueError("El archivo no es una imagen válida")

    if img.mode != "L":
        img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=2)

    base, ext = os.path.splitext(image_path)
    for suffix in ('_processed', '_rot90', '_rot180', '_rot270', '_enhanced', '_current'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    out_path = f"{base}_current.jpg"
    img.save(out_path, "JPEG", quality=95)
    return out_path
