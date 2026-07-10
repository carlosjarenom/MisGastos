"""
services/llama_client.py — Cliente HTTP para llama.cpp
"""
import logging
from openai import OpenAI
from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

log = logging.getLogger(__name__)

client = OpenAI(base_url=LLAMA_ENDPOINT, api_key="dummy", timeout=90.0)


def call_vlm(messages: list, model: str = LLAMA_MODEL, enable_thinking: bool = True, max_tokens: int = None) -> str:
    """Enviar prompt multimodal a llama.cpp y devolver texto raw.

    Sin extra_body ni fallbacks. Si thinking=False, el llamador pasa
    max_tokens=2048 (no hay razonamiento, JSON corto). Si thinking=True,
    pasa max_tokens=8192 (suficiente para razonar + JSON).
    """
    if max_tokens is None:
        max_tokens = LLAMA_MAX_TOKENS

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=LLAMA_TEMPERATURE,
            top_p=0.9,
            max_tokens=max_tokens,
        )
    except Exception as e:
        log.error(f"Error llamando al VLM: {type(e).__name__}: {e}")
        raise

    choice = response.choices[0]
    finish_reason = getattr(choice, 'finish_reason', 'unknown')
    usage = getattr(response, 'usage', None)
    log.info(f"VLM response: finish_reason={finish_reason}, usage={usage}, thinking={'ON' if enable_thinking else 'OFF'}, max_tokens={max_tokens}")

    content = choice.message.content

    if not content:
        reasoning = getattr(choice.message, 'reasoning_content', None)
        if reasoning:
            log.warning(f"content vacío, usando reasoning_content ({len(reasoning)} chars)")
            content = reasoning
        else:
            log.error(f"VLM devolvió content y reasoning_content vacíos. finish_reason={finish_reason}")
            raise ValueError(
                f"VLM response vacío (content y reasoning_content ambos vacíos). "
                f"finish_reason={finish_reason}. max_tokens={max_tokens}"
            )

    content = content.strip()
    if not content:
        log.error(f"VLM devolvió string vacío tras strip. finish_reason={finish_reason}")
        raise ValueError(f"VLM response vacío. finish_reason={finish_reason}. max_tokens={max_tokens}")

    log.info(f"VLM response OK: {len(content)} chars (finish_reason={finish_reason})")
    return content
