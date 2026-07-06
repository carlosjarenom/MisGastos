#!/bin/bash
# start-misgastos.sh — Arrancar MisGastos
# Parar OpenClaw + 27B, arrancar 9B en puerto 8005, arrancar Flask

set -e

echo "🔴 Parando servicio 27B (OpenClaw)..."
systemctl --user stop llama-cpp-server 2>/dev/null || true
sleep 2

echo "🟢 Arrancando servicio 9B (MisGastos) en puerto 8005..."
systemctl --user daemon-reload
systemctl --user start llama-cpp-server-misgastos

echo "⏳ Esperando carga del modelo (~20s)..."
sleep 20

# Verificar que llama.cpp está corriendo
for i in {1..10}; do
    if curl -s http://localhost:8005/v1/models > /dev/null 2>&1; then
        echo "✅ Modelo cargado"
        break
    fi
    echo "  Esperando... ($i)"
    sleep 3
done

echo "🚀 Arrancando Flask..."
cd "$(dirname "$0")/.."
python3 app.py --host 0.0.0.0 --port 5000
