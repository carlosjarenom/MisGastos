"""
services/ocr.py — Backend OCR vía VLM (Qwen3.5-9B)
"""
import json
import base64
import time
import logging
from dataclasses import dataclass
from services.llama_client import call_vlm
from services.image_processor import preprocess_image
from config import DOUBLE_CHECK_THRESHOLD, LLAMA_MAX_TOKENS

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


# Prompt para modo RÁPIDO (sin items, solo total)
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
    """Extraer datos de un ticket usando Qwen3.5-9B.

    Args:
        image_path: ruta a la imagen
        deep_analysis: si True, extrae items individuales (lento).
                       si False, solo fecha/comercio/total (rápido).
        enable_thinking: si True, el modelo razona antes (más preciso, lento).
                         si False, responde directo (más rápido, ~5-20s).
    """
    # Preprocesar
    processed = preprocess_image(image_path)

    # Primera llamada
    result = _call_vlm(processed, deep_analysis=deep_analysis, enable_thinking=enable_thinking)

    # Sanity checks
    if not _passes_sanity_check(result):
        result.overall_confidence = 0.0

    # Doble check para tickets grandes (solo en modo profundo)
    if deep_analysis and result.total and result.total > DOUBLE_CHECK_THRESHOLD:
        second = _call_vlm(processed, deep_analysis=deep_analysis, enable_thinking=enable_thinking)
        if result.total and second.total:
            discrepancy = abs(result.total - second.total) / max(result.total, 0.01)
            if discrepancy > 0.05:
                result.overall_confidence = 0.0
                result.raw_output += f"\n\n[SEGUNDA OPINIÓN — discrepancia {discrepancy*100:.1f}%]\n{second.raw_output}"

    return result


def _clean_json_response(raw: str) -> str:
    """Extraer JSON válido de la respuesta del VLM.

    Qwen y otros VLMs envuelven su output de muchas formas:
    - ```json ... ``` (markdown fences)
    - ``` ... ``` (fences sin 'json')
    - "Aquí tienes: {...}" (texto antes/después)
    - <think>reasoning</think>{...} (tags de reasoning)
    - {...} texto extra después
    - Razonamiento puro sin JSON (devuelve vacío)

    Esta función extrae solo el JSON válido.
    """
    import re

    if not raw:
        return ""

    text = raw.strip()

    # 1. Quitar <think>...</think> blocks (modelos de reasoning)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # 2. Si hay ```json ... ``` o ``` ... ```, extraer el contenido del primer fence
    fence_pattern = r'```(?:json)?\s*\n?(.*?)\n?\s*```'
    fence_match = re.search(fence_pattern, text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 3. Buscar el primer { y el último } — extraer lo que hay entre ellos
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    # 4. Si no hay { ni }, el texto es razonamiento puro sin JSON
    if '{' not in text:
        return ""  # json.loads fallará con mensaje claro

    return text.strip()


def _call_vlm(image_path: str, deep_analysis: bool = True, enable_thinking: bool = True) -> OCRResult:
    """Llamar al VLM con formato data URI limpio (sin newlines en base64).

    llama.cpp tiene un bug con el formato data:image/jpeg;base64,
    devuelve "Invalid url value" (500).
    Solución: codificar base64 sin newlines y usar data URI estricto.
    Si falla, intentar con requests directo (bypass de librería openai).
    """
    t0 = time.time()

    with open(image_path, "rb") as f:
        img_bytes = f.read()
    # Codificar base64 y quitar TODOS los newlines (llama.cpp es estricto)
    img_b64 = base64.b64encode(img_bytes).decode().replace('\n', '').replace('\r', '')

    # Formato data URI estricto (sin espacios, sin newlines)
    data_uri = f"data:image/jpeg;base64,{img_b64}"

    # Elegir prompt según modo
    system_prompt = SYSTEM_PROMPT if deep_analysis else SYSTEM_PROMPT_FAST
    user_prompt = USER_PROMPT if deep_analysis else "Extrae los datos de este ticket en JSON. Devuelve SOLO el JSON."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": user_prompt},
        ]},
    ]

    try:
        raw = call_vlm(messages, enable_thinking=enable_thinking)
    except ValueError as e:
        # Si el formato data URI falla, intentar con requests directo
        # (bypass de la librería openai)
        duration_ms = int((time.time() - t0) * 1000)
        log.warning(f"Formato data URI falló ({e}). Probando con requests directo...")

        try:
            raw = _call_vlm_direct(image_path, img_b64, deep_analysis=deep_analysis)
        except Exception as e2:
            return OCRResult(
                fecha=None, comercio=None, card_last4=None, items=[], total=None,
                metodo_pago=None, overall_confidence=0.0,
                field_confidence={}, model="qwen3.5-9b",
                raw_output="", duration_ms=duration_ms,
                error=f"vlm_empty_response: {e2}"
            )

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        error_msg = str(e)
        # Si el error contiene "Invalid url value", es el bug de llama.cpp
        if "Invalid url value" in error_msg:
            return OCRResult(
                fecha=None, comercio=None, card_last4=None, items=[], total=None,
                metodo_pago=None, overall_confidence=0.0,
                field_confidence={}, model="qwen3.5-9b",
                raw_output="", duration_ms=duration_ms,
                error=f"llama_cpp_invalid_url: llama.cpp no acepta el formato de imagen. "
                f"Error: {error_msg}. "
                f"Verifica versión de llama.cpp (necesita soporte vision)."
            )
        return OCRResult(
            fecha=None, comercio=None, card_last4=None, items=[], total=None,
            metodo_pago=None, overall_confidence=0.0,
            field_confidence={}, model="qwen3.5-9b",
            raw_output="", duration_ms=duration_ms,
            error=f"vlm_unavailable: {type(e).__name__}: {error_msg}"
        )

    duration_ms = int((time.time() - t0) * 1000)

    cleaned = _clean_json_response(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        raw_preview = raw[:500] if raw else "(empty response)"
        # Detectar si es razonamiento sin JSON (FIX 19D)
        if '{' not in raw:
            error_msg = f"vlm_no_json | El modelo razonó pero no produjo JSON. "
            error_msg += f"Probablemente se quedó sin tokens (finish_reason=length). "
            error_msg += f"max_tokens={LLAMA_MAX_TOKENS}. "
            error_msg += f"Response (first 500 chars): {raw_preview}"
        else:
            error_msg = f"json_parse_error | VLM response (first 500 chars): {raw_preview}"
        return OCRResult(
            fecha=None, comercio=None, card_last4=None, items=[], total=None,
            metodo_pago=None, overall_confidence=0.0,
            field_confidence={}, model="qwen3.5-9b",
            raw_output=raw, duration_ms=duration_ms,
            error=error_msg
        )

    return OCRResult(
        fecha=data.get("fecha"),
        comercio=data.get("comercio"),
        card_last4=data.get("card_last4"),
        items=data.get("items", []),
        total=data.get("total"),
        metodo_pago=data.get("metodo_pago"),
        categoria_sugerida=data.get("categoria_sugerida"),
        overall_confidence=data.get("overall_confidence", 0.0),
        field_confidence=data.get("field_confidence", {}),
        model="qwen3.5-9b",
        raw_output=raw,
        duration_ms=duration_ms,
    )


def _call_vlm_direct(image_path: str, img_b64: str, deep_analysis: bool = True) -> str:
    """Llamar al VLM con requests directo (bypass de librería openai).

    Algunas versiones de la librería openai manipulan el base64
    y rompen el formato. Usar requests directamente es más fiable.
    """
    import requests
    from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

    data_uri = f"data:image/jpeg;base64,{img_b64}"

    system_prompt = SYSTEM_PROMPT if deep_analysis else SYSTEM_PROMPT_FAST
    user_prompt = USER_PROMPT if deep_analysis else "Extrae los datos de este ticket en JSON. Devuelve SOLO el JSON."

    payload = {
        "model": LLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": user_prompt},
            ]},
        ],
        "temperature": LLAMA_TEMPERATURE,
        "top_p": 0.9,
        "max_tokens": LLAMA_MAX_TOKENS,
        "enable_thinking": True,
    }

    response = requests.post(
        f"{LLAMA_ENDPOINT}/chat/completions",
        json=payload,
        timeout=120,
    )

    if response.status_code != 200:
        raise ValueError(
            f"VLM error {response.status_code}: {response.text[:300]}"
        )

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    if not content:
        finish_reason = data["choices"][0].get("finish_reason", "unknown")
        raise ValueError(f"VLM response vacío. finish_reason={finish_reason}")

    return content.strip()


def _passes_sanity_check(r: OCRResult) -> bool:
    if not r.total or r.total <= 0 or r.total > 10000:
        return False
    if r.items and r.total:
        items_sum = sum(i.get("precio", 0) * float(i.get("cantidad", 1)) for i in r.items)
        if items_sum > 0 and abs(items_sum - r.total) > 0.50: # 0.50€ tolerancia por redondeos
            return False
    if r.fecha:
        try:
            year = int(r.fecha[:4])
            if year < 2020 or year > 2027:
                return False
        except (ValueError, IndexError):
            return False
    return True
