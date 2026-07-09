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

    Usa enable_thinking param (True por defecto).
    """
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
        log.warning(f"Error con enable_thinking={enable_thinking} via extra_body ({e})")
        # Si extra_body falla, probar con el parámetro como string
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=LLAMA_TEMPERATURE,
                top_p=0.9,
                max_tokens=LLAMA_MAX_TOKENS,
                extra_body={"enable_thinking": str(enable_thinking).lower()},
            )
        except Exception as e2:
            log.warning(f"Error con enable_thinking como string ({e2})")
            # Último intento: sin enable_thinking
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=LLAMA_TEMPERATURE,
                    top_p=0.9,
                    max_tokens=LLAMA_MAX_TOKENS,
                )
            except Exception as e3:
                log.error(f"Error llamando al VLM: {type(e3).__name__}: {e3}")
                raise

    choice = response.choices[0]
    finish_reason = getattr(choice, 'finish_reason', 'unknown')
    usage = getattr(response, 'usage', None)
    log.info(f"VLM response: finish_reason={finish_reason}, usage={usage}, thinking={'ON' if enable_thinking else 'OFF'}")

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
