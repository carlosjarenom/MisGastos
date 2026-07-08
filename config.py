"""
config.py — Configuración de MisGastos
"""
import os

# --- Llama.cpp (OCR VLM) ---
LLAMA_ENDPOINT = "http://localhost:8005/v1"
LLAMA_MODEL = "qwen3.5-9b"
LLAMA_TEMPERATURE = 0.1
LLAMA_MAX_TOKENS = 8192
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
    (1, "Comida", None, "#22c55e"),      # Verde
    (2, "Ropa", None, "#ec4899"),       # Rosa
    (3, "Farmacia", None, "#3b82f6"),   # Azul
    (4, "Carburante", None, "#f97316"), # Naranja
    (5, "Banco", None, "#0f766e"),      # Verde oscuro/teal
    (6, "Otros", None, "#6b7280"),      # Gris
]

# Sin subcategorías
TRANSPORT_SUBCATEGORIES = []
