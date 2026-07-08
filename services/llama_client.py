"""
services/llama_client.py — Cliente HTTP para llama.cpp
"""
import logging
from openai import OpenAI
from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

log = logging.getLogger(__name__)

client = OpenAI(base_url=LLAMA_ENDPOINT, api_key="dummy", timeout=90.0)


def call_vlm(messages: list, model: str = LLAMA_MODEL, enable_thinking: bool = True) -> str:
    """Enviar prompt multimodal a llama.cpp y devolver texto raw.

    Maneja el modo thinking de Qwen3.5:
    - Si content tiene texto, usarlo
    - Si content está vacío pero reasoning_content tiene texto, usar reasoning_content
    - Si ambos están vacíos, lanzar ValueError

    Usa enable_thinking param (True por defecto).
    Si falla, reintenta sin extra_body.
    """
    response = None
    last_error = None

    # Intentar primero con enable_thinking (FIX 11 / 25B)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=LLAMA_TEMPERATURE,
            top_p=0.9,
            max_tokens=LLAMA_MAX_TOKENS,
            extra_body={"enable_thinking": enable_thinking},
        )
    except Exception as e:
        last_error = e
        log.warning(f"Error con enable_thinking={enable_thinking} ({e}), reintentando sin extra_body...")

    # Fallback: sin enable_thinking
    if response is None:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=LLAMA_TEMPERATURE,
                top_p=0.9,
                max_tokens=LLAMA_MAX_TOKENS,
            )
        except Exception as e:
            log.error(f"Error llamando al VLM (sin extra_body): {type(e).__name__}: {e}")
            raise

    choice = response.choices[0]
    finish_reason = getattr(choice, 'finish_reason', 'unknown')
    usage = getattr(response, 'usage', None)
    log.info(f"VLM response: finish_reason={finish_reason}, usage={usage}")

    content = choice.message.content

    # Detectar si content es razonamiento (no empieza con { o ```)
    # En ese caso, intentar con reasoning_content que puede tener el JSON (FIX 19B)
    is_content_json = content and (content.strip().startswith('{') or
                                    content.strip().startswith('```') or
                                    content.strip().startswith('<think>'))

    if not is_content_json:
        reasoning = getattr(choice.message, 'reasoning_content', None)
        if reasoning:
            # reasoning_content podría tener el JSON
            is_reasoning_json = reasoning.strip().startswith('{') or \
                                reasoning.strip().startswith('```') or \
                                reasoning.strip().startswith('<think>')
            if is_reasoning_json:
                log.warning(f"content es razonamiento, usando reasoning_content que tiene JSON ({len(reasoning)} chars)")
                content = reasoning
            elif not content:
                # content vacío y reasoning tampoco tiene JSON
                log.error(f"Ni content ni reasoning_content tienen JSON. finish_reason={finish_reason}")
                raise ValueError(
                    f"VLM response sin JSON. finish_reason={finish_reason}. "
                    f"content (first 200): {content[:200] if content else '(empty)'}"
                )
            # Si content tiene texto (razonamiento) y reasoning también tiene texto,
            # pero ninguno es JSON, usar content (que es lo que devuelve el modelo)
        elif not content:
            log.error(f"VLM devolvió content y reasoning_content vacíos. finish_reason={finish_reason}")
            raise ValueError(
                f"VLM response vacío (content y reasoning_content ambos vacíos). "
                f"finish_reason={finish_reason}. "
                f"max_tokens={LLAMA_MAX_TOKENS}"
            )

    content = content.strip()
    if not content:
        log.error(f"VLM devolvió string vacío tras strip. finish_reason={finish_reason}")
        raise ValueError(
            f"VLM response vacío (string vacío tras strip). "
            f"finish_reason={finish_reason}."
        )

    log.info(f"VLM response OK: {len(content)} chars (finish_reason={finish_reason})")
    return content
