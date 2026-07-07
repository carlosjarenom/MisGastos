#!/bin/bash
# start-misgastos.sh — Arrancar MisGastos (llama.cpp 9B en puerto 8005 + Flask)

set -e

echo "🟢 Arrancando servicio llama.cpp (MisGastos)..."
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

echo ""
echo "🚀 Arrancando Flask..."
cd "$(dirname "$0")/.."
python3 app.py
