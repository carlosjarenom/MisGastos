"""
services/llama_client.py — Cliente HTTP para llama.cpp
"""
import logging
from openai import OpenAI
from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

log = logging.getLogger(__name__)

client = OpenAI(base_url=LLAMA_ENDPOINT, api_key="dummy", timeout=120.0)


def call_vlm(messages: list, model: str = LLAMA_MODEL) -> str:
    """Enviar prompt multimodal a llama.cpp y devolver texto raw.

    Si la respuesta es None o vacía, lanzar ValueError con info de debug.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=LLAMA_TEMPERATURE,
            top_p=0.9,
            max_tokens=LLAMA_MAX_TOKENS,
        )
    except Exception as e:
        log.error(f"Error llamando al VLM: {type(e).__name__}: {e}")
        raise

    # Debug: log del finish_reason y usage
    choice = response.choices[0]
    finish_reason = getattr(choice, 'finish_reason', 'unknown')
    usage = getattr(response, 'usage', None)
    log.info(f"VLM response: finish_reason={finish_reason}, usage={usage}")

    content = choice.message.content

    # Si content es None, el modelo no generó nada
    if content is None:
        # Log completo para debug
        log.error(f"VLM devolvió content=None. finish_reason={finish_reason}")
        log.error(f"Choice completo: {choice}")
        raise ValueError(
            f"VLM response vacío (content=None). "
            f"finish_reason={finish_reason}. "
            f"Posibles causas: mmproj no cargado, imagen no procesada, "
            f"o modelo sin capacidades de visión. "
            f"Verifica: systemctl --user status llama-cpp-server-misgastos"
        )

    content = content.strip()
    if not content:
        log.error(f"VLM devolvió string vacío. finish_reason={finish_reason}")
        raise ValueError(
            f"VLM response vacío (string vacío). "
            f"finish_reason={finish_reason}. "
            f"El modelo no generó ningún token."
        )

    log.info(f"VLM response OK: {len(content)} chars")
    return content
