"""
services/classifier.py — Clasificación en cascada
"""
from collections import defaultdict
import unicodedata

# Comercios que SIEMPRE son de una categoría concreta
MERCHANT_CATEGORY_OVERRIDES = {
    # Comida (1)
    "mercadona": 1, "carrefour": 1, "consum": 1, "lidl": 1, "aldi": 1,
    "dia": 1, "alcampo": 1, "supermercado": 1, "bonpreu": 1,
    # Ropa (2)
    "el corte ingles": 2, "hipercor": 2, "zara": 2, "mango": 2,
    "decathlon": 2, "pull and bear": 2, "stradivarius": 2,
    "new look": 2, "bershka": 2, "massimo dutti": 2, "hm": 2,
    # Farmacia (3)
    "farmacia": 3, "droguería": 3, "drogueria": 3,
    "mercadona farmacia": 3, "carrefour salud": 3,
    # Carburante (4)
    "repsol": 4, "cepsa": 4, "bp": 4, "shell": 4, "galp": 4,
    "gas express": 4, "es gasexpress": 4, "gasexpress": 4, "total": 4,
    # Banco (5)
    "cashzone": 5, "santander": 5, "bbva": 5, "caixabank": 5,
    "sabadell": 5, "ibercaja": 5, "kutxabank": 5,
    # Otros (6) — lo que antes era Servicios/Limpieza y Hogar
    "iberdrola": 6, "endesa": 6, "naturgy": 6,
    "leroy merlin": 6, "ikea": 6,
    "vodafone": 6, "movistar": 6, "orange": 6,
    "lycamobile": 6, "masmovil": 6,
}


def clasificar_por_comercio_override(comercio: str) -> int | None:
    """Comercios que siempre son de una categoría concreta.
    Returns: category_id or None
    """
    if not comercio:
        return None
    # Normalizar: minúsculas y quitar tildes para matching robusto
    name_lower = comercio.lower().strip()
    name_normalized = unicodedata.normalize('NFD', name_lower).encode('ascii', 'ignore').decode('ascii')
    for merchant_name, cat_id in MERCHANT_CATEGORY_OVERRIDES.items():
        if merchant_name in name_normalized:
            return cat_id
    return None

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
    "Farmacia": {
        "ibuprofeno": 1.0, "paracetamol": 1.0, "vitamina": 1.0,
        "antibiotico": 1.0, "antibiótico": 1.0, "jarabe": 0.8,
        "analgésico": 1.0, "analogesico": 1.0, "suero": 0.7, "gasas": 0.9,
    },
    "Carburante": {
        "gasolina": 1.0, "diésel": 1.0, "diesel": 1.0,
        "gasoleo": 1.0, "combustible": 1.0,
    },
    "Banco": {
        "cajero": 1.0, "retirada": 1.0, "transferencia": 1.0,
        "comisión": 0.8, "banco": 1.0, "ingreso": 0.8,
    },
}

# Mapeo nombre → categoría ID
CATEGORY_MAP = {
    "Comida": 1, "Ropa": 2, "Farmacia": 3,
    "Carburante": 4, "Banco": 5, "Otros": 6,
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
        return (CATEGORY_MAP["Otros"], ratio)

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
