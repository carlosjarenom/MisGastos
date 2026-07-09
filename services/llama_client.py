"""
services/llama_client.py — Cliente HTTP para llama.cpp
"""
import logging
from openai import OpenAI
from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

log = logging.getLogger(__name__)

client = OpenAI(base_url=LLAMA_ENDPOINT, api_key="dummy", timeout=90.0)


def call_vlm(messages: list, model: str = LLAMA_MODEL, enable_thinking: bool = True) -> str:
    """Enviar prompt multimodal a llama.cpp."""
    response = None

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
        log.warning(f"Error con enable_thinking={enable_thinking} ({e}), reintentando sin extra_body...")

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
            log.error(f"Error llamando al VLM: {e}")
            raise

    choice = response.choices[0]
    content = choice.message.content

    # Fix para cuando el JSON viene en reasoning_content (FIX 19B)
    if not (content and ('{' in content or '```' in content)):
        reasoning = getattr(choice.message, 'reasoning_content', None)
        if reasoning and ('{' in reasoning or '```' in reasoning):
            content = reasoning

    if not content:
        raise ValueError("VLM response vacío")

    return content.strip()
