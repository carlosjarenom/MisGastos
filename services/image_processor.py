"""
services/image_processor.py — Preprocesamiento de imagen
"""
import logging
from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError
from config import UPLOAD_DIR, MAX_IMAGE_DIM

log = logging.getLogger(__name__)


def _get_exif_orientation(img: Image.Image) -> int:
    """Extraer el tag EXIF Orientation (1-8) o devolver 1 (default) si no existe."""
    try:
        exif_data = img.getexif()
        if exif_data:
            orientation = exif_data.get(274, 1)
            return int(orientation)
    except (AttributeError, TypeError, IndexError, ValueError):
        pass
    return 1


def rotate_image(image_path: str) -> str:
    """Rotar la imagen según el tag EXIF Orientation.

    Returns:
        Ruta a la imagen rotada (o la misma ruta si no se necesitaba rotar).
    """
    try:
        img = Image.open(image_path)
    except Exception as e:
        log.warning(f"rotate_image: no se pudo abrir '{image_path}': {e}")
        return image_path

    orientation = _get_exif_orientation(img)
    if orientation == 1:
        return image_path  # No necesita rotación

    log.info(f"Rotando imagen por EXIF Orientation={orientation}")

    transforms = {
        1: None,
        2: Image.FLIP_LEFT_RIGHT,
        3: Image.ROTATE_180,
        4: Image.ROTATE_180 | Image.FLIP_LEFT_RIGHT,
        5: Image.ROTATE_270 | Image.FLIP_LEFT_RIGHT,
        6: Image.ROTATE_270,
        7: Image.FLIP_LEFT_RIGHT | Image.ROTATE_270,
        8: Image.ROTATE_90,
    }
    transform = transforms.get(orientation)
    if transform:
        img = img.transpose(transform)

    rotated_path = image_path.rsplit(".", 1)[0] + "_rotated.jpg"
    try:
        img.save(rotated_path, "JPEG", quality=90)
        log.info(f"Imagen rotada guardada en '{rotated_path}'")
        return rotated_path
    except Exception as e:
        log.warning(f"No se pudo guardar imagen rotada: {e}")
        return image_path


def enhance_image(image_path: str) -> str:
    """Mejorar brillo, contraste y nitidez de la imagen para facilitar el OCR.

    Returns:
        Ruta a la imagen mejorada (o la misma ruta si no se pudo procesar).
    """
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        log.warning(f"enhance_image: no se pudo abrir '{image_path}': {e}")
        return image_path

    try:
        img = ImageEnhance.Brightness(img).enhance(1.2)   # +20% brillo
        img = ImageEnhance.Contrast(img).enhance(1.3)      # +30% contraste
        img = ImageEnhance.Sharpness(img).enhance(1.5)     # +50% nitidez

        enhanced_path = image_path.rsplit(".", 1)[0] + "_enhanced.jpg"
        img.save(enhanced_path, "JPEG", quality=95)
        log.info(f"Imagen mejorada guardada en '{enhanced_path}'")
        return enhanced_path
    except Exception as e:
        log.warning(f"No se pudo mejorar la imagen: {e}")
        return image_path


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
