"""
services/llama_client.py — Cliente HTTP para llama.cpp
"""
from openai import OpenAI
from config import LLAMA_ENDPOINT, LLAMA_MODEL, LLAMA_TEMPERATURE, LLAMA_MAX_TOKENS

client = OpenAI(base_url=LLAMA_ENDPOINT, api_key="dummy")

def call_vlm(messages: list, model: str = LLAMA_MODEL) -> str:
    """Enviar prompt multimodal a llama.cpp y devolver texto raw."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=LLAMA_TEMPERATURE,
        top_p=0.9,
        max_tokens=LLAMA_MAX_TOKENS,
    )
    return response.choices[0].message.content
