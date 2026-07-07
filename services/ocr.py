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
6. items: lista de objetos con descripcion, cantidad (entero, default 1),
 precio (decimal). Incluye solo productos, no líneas de IVA ni subtotales.
7. Si el ticket está en multicolumna, lee de izquierda a derecha,
 arriba a abajo.
8. confidence: para cada campo, indica tu confianza (0.0 a 1.0).
 Sé honesto — si el texto está borroso o ambiguo, baja la confidence.
9. Si el ticket está arrugado, mal iluminado, o no se lee bien en
 general, pon overall_confidence bajo (<0.7).
10. Si el ticket NO es legible en absoluto, devuelve:
 {"overall_confidence": 0.0, "error": "ticket_no_legible"}"""

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
 {"descripcion": "string", "cantidad": int, "precio": float}
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

    return text.strip()


def _call_vlm(image_path: str) -> OCRResult:
    t0 = time.time()

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": USER_PROMPT},
        ]},
    ]

    try:
        raw = call_vlm(messages)
    except ValueError as e:
        # VLM devolvió respuesta vacía — probar formato alternativo
        # Algunas versiones de llama.cpp no aceptan data:image/jpeg;base64,
        # solo el base64 crudo
        duration_ms = int((time.time() - t0) * 1000)
        log.warning(f"Primer intento falló ({e}). Probando formato alternativo...")

        messages_alt = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": img_b64}},
                {"type": "text", "text": USER_PROMPT},
            ]},
        ]

        try:
            raw = call_vlm(messages_alt)
        except (ValueError, Exception) as e2:
            # Ambos formatos fallaron
            return OCRResult(
                fecha=None, comercio=None, nif=None, items=[], total=None,
                metodo_pago=None, overall_confidence=0.0,
                field_confidence={}, model="qwen3.5-9b",
                raw_output="", duration_ms=duration_ms,
                error=f"vlm_empty_response: {e2}"
            )
    except Exception as e:
        # Error de conexión (VLM caído, timeout, etc.)
        duration_ms = int((time.time() - t0) * 1000)
        return OCRResult(
            fecha=None, comercio=None, nif=None, items=[], total=None,
            metodo_pago=None, overall_confidence=0.0,
            field_confidence={}, model="qwen3.5-9b",
            raw_output="", duration_ms=duration_ms,
            error=f"vlm_unavailable: {type(e).__name__}: {e}"
        )

    duration_ms = int((time.time() - t0) * 1000)

    # Limpiar markdown fences antes de parsear JSON
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


def _passes_sanity_check(r: OCRResult) -> bool:
    if not r.total or r.total <= 0 or r.total > 10000:
        return False
    if r.items and r.total:
        items_sum = sum(i.get("precio", 0) * i.get("cantidad", 1) for i in r.items)
        if items_sum > 0 and abs(items_sum - r.total) > 0.05:
            return False
    if r.fecha:
        try:
            year = int(r.fecha[:4])
            if year < 2020 or year > 2027:
                return False
        except (ValueError, IndexError):
            return False
    return True
