"""
services/ocr.py — Backend OCR vía VLM (Qwen3.5-9B)
"""
import base64
import json
import logging
import re
import time
from dataclasses import dataclass

from config import DOUBLE_CHECK_THRESHOLD, LLAMA_MAX_TOKENS
from services.image_processor import preprocess_image
from services.llama_client import call_vlm

log = logging.getLogger(__name__)

# ============================================================
# PROMPTS
# ============================================================

SYSTEM_PROMPT = """Eres un asistente experto en extraer información estructurada
de tickets de compra españoles. Tu única salida es un objeto JSON válido,
sin texto adicional, sin explicaciones, sin markdown.

Reglas críticas:
1. Formato de fecha ISO: YYYY-MM-DD. Si el ticket muestra "06/07/26",
 interpreta como 2026-07-06 (siglo XXI).
2. Números: usa punto como separador decimal (1234.56), nunca coma ni
 separador de miles. "1.234,56" → 1234.56
3. Tarjeta: extrae los últimos 4 dígitos del número de tarjeta que aparezca
 en el ticket (ej: ****6133 → "6133"). Si el método es efectivo o no aparece,
 usa null.
4. metodo_pago debe ser uno de: "Efectivo", "Tarjeta", "Bizum",
 "Transferencia", o null si no se puede leer. NUNCA uses "desconocido".
5. Si un campo no se puede leer con seguridad, usa null Y pon su
 confidence a 0.0. NO inventes.
6. items: lista de objetos con descripcion, cantidad (float),
 precio (decimal = PRECIO UNITARIO en euros, no total).
 - Para productos por unidad: cantidad = unidades, precio = €/unidad
 - Para productos por peso (fruta, carne, pescado, verdura): cantidad = kg, precio = €/kg
 - Para gasolina/diésel: cantidad = litros, precio = €/litro
 Ejemplo: {"descripcion": "Plátanos", "cantidad": 0.85, "precio": 1.89}
 significa 0.85 kg a 1.89€/kg.
 Incluye solo productos, no líneas de IVA ni subtotales.
7. Si el ticket está en multicolumna, lee de izquierda a derecha,
 arriba a abajo.
8. confidence: para cada campo, indica tu confianza (0.0 a 1.0).
 Sé honesto — si el texto está borroso o ambiguo, baja la confidence.
9. Si el ticket está arrugado, mal iluminado, o no se lee bien en
 general, pon overall_confidence bajo (<0.7).
10. Si el ticket NO es legible en absoluto, devuelve:
 {"overall_confidence": 0.0, "error": "ticket_no_legible"}
11. Tras razonar internamente, tu respuesta FINAL debe ser SOLO el JSON.
 Puedes pensar antes de responder, pero el output final debe ser JSON válido.
12. DESCUENTOS: Si el ticket tiene descuentos u ofertas, inclúyelos como
 un item con descripcion="DESCUENTO" y precio negativo (ej: -8.98).
 Así la suma de items cuadrará con el total.
13. CATEGORÍA: Sugiere una categoría basándote en el contenido del ticket.
Opciones válidas: "Comida", "Ropa", "Farmacia", "Carburante", "Banco", "Otros".
- "Comida": supermercados, restaurantes, comida
- "Ropa": tiendas de ropa, El Corte Inglés (moda)
- "Farmacia": farmacias, medicinas
- "Carburante": gasolineras, combustible
- "Banco": recibos bancarios, reintegros, transferencias
- "Otros": todo lo demás
Incluye "categoria_sugerida" en el JSON."""

USER_PROMPT = """Extrae los datos de este ticket en JSON.

Esquema esperado:
{
 "overall_confidence": float,
 "field_confidence": {
 "fecha": float,
 "comercio": float,
 "card_last4": float,
 "items": float,
 "total": float,
 "metodo_pago": float
 },
 "fecha": "YYYY-MM-DD" | null,
 "comercio": "string" | null,
 "card_last4": "string (últimos 4 dígitos de la tarjeta, ej: 6133) o null si es efectivo" | null,
 "items": [
 {"descripcion": "string", "cantidad": float, "precio": float}
 ],
 "subtotal": float | null,
 "iva": float | null,
 "total": float | null,
 "metodo_pago": "Efectivo" | "Tarjeta" | "Bizum" | "Transferencia" | null,
 "categoria_sugerida": "Comida" | "Ropa" | "Farmacia" | "Carburante" | "Banco" | "Otros" | null
}

Devuelve SOLO el JSON. Nada más."""

SYSTEM_PROMPT_FAST = """Eres un asistente experto en extraer información estructurada
de tickets de compra españoles. Tu única salida es un objeto JSON válido,
sin texto adicional, sin explicaciones, sin markdown.

Reglas críticas:
1. Formato de fecha ISO: YYYY-MM-DD. Si el ticket muestra "06/07/26",
 interpreta como 2026-07-06 (siglo XXI).
2. Números: usa punto como separador decimal (1234.56), nunca coma.
3. Tarjeta: extrae los últimos 4 dígitos del número de tarjeta que aparezca
 en el ticket (ej: ****6133 → "6133"). Si el método es efectivo o no aparece,
 usa null.
4. metodo_pago debe ser uno de: "Efectivo", "Tarjeta", "Bizum", "Transferencia", o null.
5. Si un campo no se puede leer, usa null.
6. NO extraigas items individuales. Solo fecha, comercio, card_last4, total y metodo_pago.
7. Tras razonar internamente, tu respuesta FINAL debe ser SOLO el JSON.
8. DESCUENTOS: el total es el importe final tras descuentos.
9. CATEGORÍA: Sugiere una categoría. Opciones: "Comida", "Ropa", "Farmacia", "Carburante", "Banco", "Otros".
Incluye "categoria_sugerida" en el JSON.

Esquema:
{
 "overall_confidence": float,
 "field_confidence": {"fecha": float, "comercio": float, "card_last4": float, "total": float, "metodo_pago": float},
 "fecha": "YYYY-MM-DD" | null,
 "comercio": "string" | null,
 "card_last4": "string (últimos 4 dígitos de la tarjeta, ej: 6133) o null si es efectivo" | null,
 "total": float | null,
 "metodo_pago": "Efectivo" | "Tarjeta" | "Bizum" | "Transferencia" | null,
 "categoria_sugerida": "Comida" | "Ropa" | "Farmacia" | "Carburante" | "Banco" | "Otros" | null
}

Devuelve SOLO el JSON. Nada más."""


@dataclass
class OCRResult:
    fecha: str | None
    comercio: str | None
    card_last4: str | None
    items: list[dict]
    total: float | None
    metodo_pago: str | None
    overall_confidence: float
    field_confidence: dict
    model: str
    raw_output: str
    duration_ms: int
    categoria_sugerida: str | None = None
    error: str | None = None


def extract_ticket(image_path: str, deep_analysis: bool = True, enable_thinking: bool = True) -> OCRResult:
    """Extraer datos de un ticket usando Qwen3.5-9B."""
    processed = preprocess_image(image_path)
    result = _call_vlm(processed, deep_analysis=deep_analysis, enable_thinking=enable_thinking)

    if not _passes_sanity_check(result):
        result.overall_confidence = 0.0

    if deep_analysis and result.total and result.total > DOUBLE_CHECK_THRESHOLD:
        second = _call_vlm(processed, deep_analysis=deep_analysis, enable_thinking=enable_thinking)
        if result.total and second.total:
            discrepancy = abs(result.total - second.total) / max(result.total, 0.01)
            if discrepancy > 0.05:
                result.overall_confidence = 0.0
                result.raw_output += f"\n\n[SEGUNDA OPINIÓN — discrepancia {discrepancy*100:.1f}%]\n{second.raw_output}"

    return result


def _clean_json_response(raw: str) -> str:
    """Extraer JSON válido de la respuesta del VLM."""
    if not raw: return ""
    text = raw.strip()
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    fence_pattern = r'```(?:json)?\s*\n?(.*?)\n?\s*```'
    fence_match = re.search(fence_pattern, text, re.DOTALL)
    if fence_match: text = fence_match.group(1).strip()
    first_brace, last_brace = text.find('{'), text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]
    return text.strip() if '{' in text else ""


def _call_vlm(image_path: str, deep_analysis: bool = True, enable_thinking: bool = True) -> OCRResult:
    """Llamar al VLM con reintento direct por bug de llama.cpp."""
    t0 = time.time()
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode().replace('\n', '').replace('\r', '')
    data_uri = f"data:image/jpeg;base64,{img_b64}"

    system_prompt = SYSTEM_PROMPT if deep_analysis else SYSTEM_PROMPT_FAST
    user_prompt = USER_PROMPT if deep_analysis else "Extrae los datos de este ticket en JSON. Devuelve SOLO el JSON."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": user_prompt},
        ]},
    ]

    raw = None
    try:
        raw = call_vlm(messages, enable_thinking=enable_thinking)
    except Exception as e:
        log.warning(f"VLM call failed: {e}. Retrying with direct requests...")
        try:
            raw = _call_vlm_direct(img_b64, deep_analysis=deep_analysis)
        except Exception as e2:
            duration_ms = int((time.time() - t0) * 1000)
            return OCRResult(None, None, None, [], None, None, 0.0, {}, "qwen3.5-9b", "", duration_ms, error=str(e2))

    duration_ms = int((time.time() - t0) * 1000)
    cleaned = _clean_json_response(raw)

    try:
        data = json.loads(cleaned)
    except:
        return OCRResult(None, None, None, [], None, None, 0.0, {}, "qwen3.5-9b", raw, duration_ms, error="json_parse_error")

    return OCRResult(
        fecha=data.get("fecha"), comercio=data.get("comercio"), card_last4=data.get("card_last4"),
        items=data.get("items", []), total=data.get("total"), metodo_pago=data.get("metodo_pago"),
        categoria_sugerida=data.get("categoria_sugerida"), overall_confidence=data.get("overall_confidence", 0.0),
        field_confidence=data.get("field_confidence", {}), model="qwen3.5-9b", raw_output=raw, duration_ms=duration_ms
    )

def _call_vlm_direct(img_b64: str, deep_analysis: bool = True) -> str:
    import requests
    from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS
    sys_p = SYSTEM_PROMPT if deep_analysis else SYSTEM_PROMPT_FAST
    user_p = USER_PROMPT if deep_analysis else "Extrae los datos de este ticket en JSON. Devuelve SOLO el JSON."
    payload = {
        "model": LLAMA_MODEL, "temperature": LLAMA_TEMPERATURE, "max_tokens": LLAMA_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}, {"type": "text", "text": user_p}]}
        ],
        "enable_thinking": True
    }
    r = requests.post(f"{LLAMA_ENDPOINT}/chat/completions", json=payload, timeout=120)
    if r.status_code != 200: raise ValueError(f"Direct VLM error {r.status_code}")
    return r.json()["choices"][0]["message"]["content"]

def _passes_sanity_check(r: OCRResult) -> bool:
    if not r.total or r.total <= 0 or r.total > 10000: return False
    if r.items and r.total:
        s = sum(i.get("precio", 0) * float(i.get("cantidad", 1)) for i in r.items)
        if s > 0 and abs(s - r.total) > 0.50: return False
    if r.fecha:
        try:
            y = int(r.fecha[:4])
            if y < 2020 or y > 2027: return False
        except: return False
    return True
