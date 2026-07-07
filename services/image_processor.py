"""
services/image_processor.py — Preprocesamiento de imagen
"""
from PIL import Image, ImageOps, UnidentifiedImageError
from config import UPLOAD_DIR, MAX_IMAGE_DIM

def preprocess_image(image_path: str) -> str:
    """Redimensionar a max 1024px lado largo. Corrige orientación EXIF."""
    try:
        img = Image.open(image_path)
    except UnidentifiedImageError:
        raise ValueError("El archivo no es una imagen válida")
    img = ImageOps.exif_transpose(img)  # Corrige rotación por metadata EXIF
    if max(img.size) > MAX_IMAGE_DIM:
        ratio = MAX_IMAGE_DIM / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    out_path = image_path.rsplit(".", 1)[0] + "_processed.jpg"
    img.save(out_path, "JPEG", quality=90)
    return out_path
