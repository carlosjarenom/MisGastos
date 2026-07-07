#!/bin/bash
# stop-misgastos.sh — Parar MisGastos

set -e

echo "🔴 Parando servicio llama.cpp (MisGastos)..."
systemctl --user stop llama-cpp-server-misgastos 2>/dev/null || true

# Parar Flask si está corriendo
pkill -f "python3 app.py" 2>/dev/null || true

echo "✅ Listo"
