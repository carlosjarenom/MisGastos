"""
services/ocr.py — Backend OCR vía VLM (Qwen3.5-9B)
"""
import json
import base64
import time
import logging
import re
from dataclasses import dataclass
from services.llama_client import call_vlm
from services.image_processor import preprocess_image
from config import DOUBLE_CHECK_THRESHOLD

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
3. NIF español: formato letra+8 dígitos (A12345678). Si no aparece,
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
 Así la suma de items cuadrará con el total."""

USER_PROMPT = """Extrae los datos de este ticket en JSON.

Esquema esperado:
{
 "overall_confidence": float,
 "field_confidence": {
 "fecha": float,
 "comercio": float,
 "nif": float,
 "items": float,
 "total": float,
 "metodo_pago": float
 },
 "fecha": "YYYY-MM-DD" | null,
 "comercio": "string" | null,
 "nif": "string" | null,
 "items": [
 {"descripcion": "string", "cantidad": float, "precio": float}
 ],
 "subtotal": float | null,
 "iva": float | null,
 "total": float | null,
 "metodo_pago": "Efectivo" | "Tarjeta" | "Bizum" | "Transferencia" | null
}

Devuelve SOLO el JSON. Nada más."""


@dataclass
class OCRResult:
    fecha: str | None
    comercio: str | None
    nif: str | None
    items: list[dict]
    total: float | None
    metodo_pago: str | None
    overall_confidence: float
    field_confidence: dict
    model: str
    raw_output: str
    duration_ms: int
    error: str | None = None


def extract_ticket(image_path: str) -> OCRResult:
    """Extraer datos de un ticket usando Qwen3.5-9B."""
    # Preprocesar
    processed = preprocess_image(image_path)

    # Primera llamada
    result = _call_vlm(processed)

    # Sanity checks
    if not _passes_sanity_check(result):
        result.overall_confidence = 0.0

    # Doble check para tickets grandes
    if result.total and result.total > DOUBLE_CHECK_THRESHOLD:
        second = _call_vlm(processed)
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

    Esta función extrae solo el JSON válido.
    """
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

    return text.strip()


def _call_vlm(image_path: str) -> OCRResult:
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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": USER_PROMPT},
        ]},
    ]

    try:
        raw = call_vlm(messages)
    except ValueError as e:
        # Si el formato data URI falla, intentar con requests directo
        # (bypass de la librería openai)
        duration_ms = int((time.time() - t0) * 1000)
        log.warning(f"Formato data URI falló ({e}). Probando con requests directo...")

        try:
            raw = _call_vlm_direct(image_path, img_b64)
        except Exception as e2:
            return OCRResult(
                fecha=None, comercio=None, nif=None, items=[], total=None,
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
                fecha=None, comercio=None, nif=None, items=[], total=None,
                metodo_pago=None, overall_confidence=0.0,
                field_confidence={}, model="qwen3.5-9b",
                raw_output="", duration_ms=duration_ms,
                error=f"llama_cpp_invalid_url: llama.cpp no acepta el formato de imagen. "
                f"Error: {error_msg}. "
                f"Verifica versión de llama.cpp (necesita soporte vision)."
            )
        return OCRResult(
            fecha=None, comercio=None, nif=None, items=[], total=None,
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
        raw_preview = raw[:300] if raw else "(empty response)"
        return OCRResult(
            fecha=None, comercio=None, nif=None, items=[], total=None,
            metodo_pago=None, overall_confidence=0.0,
            field_confidence={}, model="qwen3.5-9b",
            raw_output=raw, duration_ms=duration_ms,
            error=f"json_parse_error | VLM response (first 300 chars): {raw_preview}"
        )

    return OCRResult(
        fecha=data.get("fecha"),
        comercio=data.get("comercio"),
        nif=data.get("nif"),
        items=data.get("items", []),
        total=data.get("total"),
        metodo_pago=data.get("metodo_pago"),
        overall_confidence=data.get("overall_confidence", 0.0),
        field_confidence=data.get("field_confidence", {}),
        model="qwen3.5-9b",
        raw_output=raw,
        duration_ms=duration_ms,
    )


def _call_vlm_direct(image_path: str, img_b64: str) -> str:
    """Llamar al VLM con requests directo (bypass de librería openai).

    Algunas versiones de la librería openai manipulan el base64
    y rompen el formato. Usar requests directamente es más fiable.
    """
    import requests
    from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

    data_uri = f"data:image/jpeg;base64,{img_b64}"

    payload = {
        "model": LLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": USER_PROMPT},
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
        items_sum = sum((i.get("precio") or 0) * float(i.get("cantidad") or 1.0) for i in r.items)
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
