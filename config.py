"""
config.py — Configuración de MisGastos
"""
import os

# --- Llama.cpp (OCR VLM) ---
LLAMA_ENDPOINT = "http://localhost:8005/v1"
LLAMA_MODEL = "qwen3.5-9b"
LLAMA_TEMPERATURE = 0.1
LLAMA_MAX_TOKENS = 1024
DOUBLE_CHECK_THRESHOLD = 50.0  # Tickets > 50€ se leen dos veces

# --- Flask ---
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

# --- Rutas ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "gastos.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

# --- Imagen ---
MAX_IMAGE_DIM = 1024  # Redimensionar a max 1024px lado largo

# --- Categorías ---
CATEGORIES = [
    (1, "Comida", None, "#10b981"),
    (2, "Farmacia y Salud", None, "#3b82f6"),
    (3, "Limpieza y Hogar", None, "#f59e0b"),
    (4, "Transporte", None, "#8b5cf6"),
    (5, "Cuidado personal", None, "#ec4899"),
    (6, "Educación", None, "#06b6d4"),
    (7, "Ocio", None, "#ef4444"),
    (8, "Servicios", None, "#6b7280"),
    (9, "Otros", None, "#9ca3af"),
    (10, "Mixto", None, "#fbbf24"),
]

# Subcategorías de Transporte
TRANSPORT_SUBCATEGORIES = [
    (11, "Carburante", 4, "#8b5cf6"),
    (12, "Parking", 4, "#8b5cf6"),
    (13, "Transporte público", 4, "#8b5cf6"),
    (14, "Mantenimiento coche", 4, "#8b5cf6"),
]
