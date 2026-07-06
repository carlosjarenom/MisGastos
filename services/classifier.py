"""
services/classifier.py — Clasificación en cascada
"""
from collections import defaultdict

# Keywords con peso por categoría
KEYWORDS = {
    "Comida": {
        "pan": 1.0, "leche": 1.0, "huevos": 1.0, "aceite": 1.0,
        "arroz": 1.0, "pasta": 1.0, "jamón": 1.0, "jamon": 1.0,
        "queso": 1.0, "yogur": 1.0, "fruta": 1.0, "verdura": 1.0,
        "carne": 1.0, "pescado": 1.0, "galletas": 0.8,
        "café": 0.8, "cafe": 0.8, "azúcar": 0.8, "azucar": 0.8,
        "harina": 0.8, "legumbres": 1.0, "mermelada": 0.9,
        "cereales": 0.9, "agua mineral": 0.6, "refresco": 0.6,
        "cerveza": 0.6, "vino": 0.7, "coca cola": 0.6,
    },
    "Farmacia y Salud": {
        "ibuprofeno": 1.0, "paracetamol": 1.0, "vitamina": 1.0,
        "antibiotico": 1.0, "antibiótico": 1.0, "jarabe": 0.8,
        "crema": 0.5, "protector solar": 0.6, "analgésico": 1.0,
        "analogesico": 1.0, "suero": 0.7, "gasas": 0.9,
    },
    "Limpieza y Hogar": {
        "detergente": 1.0, "lejía": 1.0, "lechia": 1.0, "limpiador": 1.0,
        "lavavajillas": 1.0, "suavizante": 1.0, "desengrasante": 1.0,
        "bayeta": 1.0, "cepillo": 0.6, "esponja": 0.6,
        "bolsa basura": 1.0, "papel aluminio": 0.7,
    },
    "Cuidado personal": {
        "champú": 1.0, "champu": 1.0, "gel ducha": 1.0,
        "pasta dientes": 1.0, "cepillo dental": 1.0,
        "papel higiénico": 1.0, "papel higienico": 1.0,
        "compresas": 1.0, "pañales": 1.0, "pañal": 1.0,
        "desodorante": 1.0, "crema hidratante": 0.8,
    },
    "Transporte": {
        "gasolina": 1.0, "diésel": 1.0, "diesel": 1.0,
        "parking": 1.0, "peaje": 1.0, "bus": 1.0, "metro": 1.0,
        "tren": 1.0, "avión": 1.0, "avion": 1.0,
        "taxis": 0.8, "uber": 0.9, "cabify": 0.9,
    },
    "Educación": {
        "academia": 1.0, "libro": 0.7, "cuaderno": 0.8,
        "bolígrafo": 0.7, "material escolar": 1.0,
        "curso": 0.8, "formación": 0.8,
    },
    "Ocio": {
        "cine": 1.0, "restaurante": 0.5, "bar": 0.6,
        "juego": 0.7, "videojuego": 0.8, "spotify": 0.9,
        "netflix": 0.9, "amazon prime": 0.9,
    },
    "Servicios": {
        "luz": 1.0, "agua": 0.8, "electricidad": 1.0,
        "internet": 1.0, "móvil": 0.8, "movil": 0.8,
        "teléfono": 0.8, "telefono": 0.8, "seguro": 0.9,
        "fibra": 1.0, "vodafone": 0.9, "orange": 0.8,
        "movistar": 0.9, "lyca": 0.7, "masmovil": 0.7,
    },
}

# Mapeo nombre → categoría ID
CATEGORY_MAP = {
    "Comida": 1, "Farmacia y Salud": 2, "Limpieza y Hogar": 3,
    "Transporte": 4, "Cuidado personal": 5, "Educación": 6,
    "Ocio": 7, "Servicios": 8, "Otros": 9, "Mixto": 10,
}


def clasificar_por_items(items: list[dict]) -> tuple[int, float]:
    """Clasificación por scoring de importe (no conteo).

    Returns: (category_id, confidence_ratio)
    """
    scores = defaultdict(float)
    total_amount = 0.0

    for item in items:
        desc = item.get("descripcion", "").lower()
        precio = item.get("precio", 0)
        total_amount += precio

        for cat, kws in KEYWORDS.items():
            for kw, peso in kws.items():
                if kw in desc:
                    scores[cat] += precio * peso
                    break  # Una keyword por item por categoría

    if not scores or total_amount == 0:
        return (CATEGORY_MAP["Otros"], 0.0)

    cat_dominante = max(scores, key=scores.get)
    ratio = scores[cat_dominante] / total_amount

    if ratio < 0.5:
        return (CATEGORY_MAP["Mixto"], ratio)

    return (CATEGORY_MAP[cat_dominante], ratio)


def clasificar_por_comercio(merchant_name: str, merchant_db=None) -> int | None:
    """Nivel 1: Buscar en diccionario de comercios.
    Returns: category_id or None
    """
    if not merchant_name:
        return None
    name_lower = merchant_name.lower()

    # Buscar por nombre exacto o parcial
    if merchant_db:
        for merchant in merchant_db:
            if name_lower in merchant["name"].lower():
                return merchant["category_id"]
            # Check aliases
            if merchant.get("aliases"):
                import json as j
                aliases = j.loads(merchant["aliases"]) if isinstance(merchant["aliases"], str) else merchant["aliases"]
                for alias in aliases:
                    if name_lower in alias.lower():
                        return merchant["category_id"]

    return None
