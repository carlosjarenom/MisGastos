#!/bin/bash
# stop-misgastos.sh — Parar MisGastos y reencender OpenClaw

set -e

echo "🔴 Parando servicio 9B (MisGastos)..."
systemctl --user stop llama-cpp-server-misgastos 2>/dev/null || true

# Parar Flask si está corriendo
pkill -f "python3 app.py" 2>/dev/null || true

echo "🟢 Reencendiendo servicio 27B (OpenClaw)..."
systemctl --user start llama-cpp-server

echo "✅ Listo"
