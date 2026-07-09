"""
services/classifier.py — Clasificación en cascada
"""
import unicodedata
from collections import defaultdict

# Comercios que SIEMPRE son de una categoría concreta
# IDs actualizados según config.py
MERCHANT_CATEGORY_OVERRIDES = {
    # Comida (1)
    "mercadona": 1, "carrefour": 1, "consum": 1, "lidl": 1, "aldi": 1,
    "dia": 1, "alcampo": 1, "supermercado": 1, "bonpreu": 1,
    # Farmacia y Salud (2)
    "farmacia": 2, "droguería": 2, "drogueria": 2,
    "mercadona farmacia": 2, "carrefour salud": 2,
    # Limpieza y Hogar (3)
    "leroy merlin": 3, "ikea": 3, "bricomart": 3, "bauhaus": 3,
    # Transporte (4) y subcategorías (11-14)
    "repsol": 11, "cepsa": 11, "bp": 11, "shell": 11, "galp": 11,
    "gas express": 11, "es gasexpress": 11, "gasexpress": 11, "total": 11,
    "autopista": 14, "ap-7": 14, "ap-6": 14, "saba": 13,
    # Cuidado personal (5)
    "zara": 5, "mango": 5, "decathlon": 5, "pull and bear": 5,
    "stradivarius": 5, "new look": 5, "bershka": 5, "massimo dutti": 5, "hm": 5,
    "primark": 5, "perfumeria": 5, "peluqueria": 5,
    # Educación (6)
    "academia": 6, "colegio": 6, "libreria": 6,
    # Ocio (7)
    "restaurante": 7, "bar ": 7, "cine": 7, "burguer king": 7, "mcdonalds": 7,
    # Servicios (8)
    "iberdrola": 8, "endesa": 8, "naturgy": 8,
    "vodafone": 8, "movistar": 8, "orange": 8,
    "lycamobile": 8, "masmovil": 8, "seguros": 8,
}

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
        "analgésico": 1.0, "analogesico": 1.0, "suero": 0.7, "gasas": 0.9,
    },
    "Gasolina": {
        "gasolina": 1.0, "diésel": 1.0, "diesel": 1.0,
        "gasoleo": 1.0, "combustible": 1.0, "95 e5": 1.0, "98 e10": 1.0,
    },
}

# Mapeo nombre → categoría ID
CATEGORY_MAP = {
    "Comida": 1,
    "Farmacia y Salud": 2,
    "Limpieza y Hogar": 3,
    "Transporte": 4,
    "Cuidado personal": 5,
    "Educación": 6,
    "Ocio": 7,
    "Servicios": 8,
    "Otros": 9,
    "Mixto": 10,
    "Gasolina": 11,
    "Diésel": 12,
    "Parking": 13,
    "Peaje": 14,
}

# Mapeo de sugerencias del VLM (Qwen3.5-9B) a IDs internos
VLM_SUGGESTION_MAP = {
    "Comida": 1,
    "Ropa": 5, # En nuestro schema 'Ropa' va a 'Cuidado personal'
    "Farmacia": 2,
    "Carburante": 11,
    "Banco": 9, # 'Banco' no existe como cat principal, va a 'Otros'
    "Otros": 9,
}

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    return "".join(c for c in unicodedata.normalize('NFD', text)
                  if unicodedata.category(c) != 'Mn')

def clasificar_por_comercio_override(comercio: str) -> int | None:
    norm = normalize_text(comercio)
    if not norm:
        return None
    for merchant_name, cat_id in MERCHANT_CATEGORY_OVERRIDES.items():
        if merchant_name in norm:
            return cat_id
    return None

def clasificar_por_items(items: list[dict]) -> tuple[int, float]:
    scores = defaultdict(float)
    total_amount = 0.0

    for item in items:
        desc = normalize_text(item.get("descripcion", ""))
        precio = item.get("precio", 0)
        qty = float(item.get("cantidad", 1.0))
        item_total = precio * qty
        total_amount += item_total

        for cat, kws in KEYWORDS.items():
            for kw, peso in kws.items():
                if kw in desc:
                    scores[cat] += item_total * peso
                    break

    if not scores or total_amount == 0:
        return (9, 0.0)

    cat_dominante = max(scores, key=scores.get)
    ratio = scores[cat_dominante] / total_amount

    if ratio < 0.4: # Umbral algo más bajo para items
        return (9, ratio)

    return (CATEGORY_MAP[cat_dominante], ratio)

def unified_classify(comercio: str = None, items: list[dict] = None, vlm_suggestion: str = None, db_conn = None) -> int:
    """Clasificación centralizada con cascada:
    1. Override explícito por comercio (ej: Mercadona -> 1)
    2. Búsqueda en DB de comercios (merchants table)
    3. Heurística por items (scoring de keywords)
    4. Sugerencia del VLM
    5. Fallback a 'Otros' (9)
    """
    # 1. Override
    cat_id = clasificar_por_comercio_override(comercio)
    if cat_id:
        return cat_id

    # 2. DB Lookup
    if comercio and db_conn:
        c = db_conn.cursor()
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?", (f"%{comercio}%",))
        row = c.fetchone()
        if row and row['default_category_id']:
            return row['default_category_id']

    # 3. Items
    if items:
        cat_id, ratio = clasificar_por_items(items)
        if ratio >= 0.5:
            return cat_id

    # 4. VLM Suggestion
    if vlm_suggestion:
        cat_id = VLM_SUGGESTION_MAP.get(vlm_suggestion)
        if cat_id:
            return cat_id

    # 5. Fallback
    return 9
