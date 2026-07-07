#!/bin/bash
# diagnose-vlm.sh — Diagnosticar si el VLM funciona correctamente
# Test directo a llama.cpp con una imagen de prueba

set -e

echo "🔍 Diagnóstico del VLM llama.cpp"
echo "================================"
echo

# 1. Verificar servicio
echo "1. Estado del servicio:"
systemctl --user is-active llama-cpp-server-misgastos 2>/dev/null && echo " ✅ Activo" || echo " ❌ Inactivo"
echo

# 2. Verificar que el modelo carga
echo "2. Modelos disponibles en el VLM:"
curl -s http://localhost:8005/v1/models | python3 -m json.tool 2>/dev/null || echo " ❌ No responde"
echo

# 3. Test con texto solo (sin imagen) - max_tokens alto + enable_thinking=false
echo "3. Test con texto solo (max_tokens=500, enable_thinking=false):"
RESPONSE=$(curl -s -X POST http://localhost:8005/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
 "model": "qwen3.5-9b",
 "messages": [{"role": "user", "content": "Di HOLA. Responde solo esa palabra, sin razonar."}],
 "max_tokens": 500,
 "enable_thinking": false
 }')
echo " Response: $RESPONSE" | head -c 500
echo
echo

# 4. Test con imagen 1x1 pixel - enable_thinking=false
echo "4. Test con imagen 1x1 pixel (max_tokens=500, enable_thinking=false):"
IMG_B64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
RESPONSE=$(curl -s -X POST http://localhost:8005/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d "{
 \"model\": \"qwen3.5-9b\",
 \"messages\": [
 {\"role\": \"user\", \"content\": [
 {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,$IMG_B64\"}},
 {\"type\": \"text\", \"text\": \"What color is this image? Responde en una palabra.\"}
 ]}
 ],
 \"max_tokens\": 500,
 \"enable_thinking\": false
 }")
echo " Response: $RESPONSE" | head -c 500
echo
echo

# 5. Test con imagen 1x1 pixel Y modo thinking (para comparar)
echo "5. Test con imagen 1x1 pixel (max_tokens=500, enable_thinking=true):"
RESPONSE=$(curl -s -X POST http://localhost:8005/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d "{
 \"model\": \"qwen3.5-9b\",
 \"messages\": [
 {\"role\": \"user\", \"content\": [
 {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,$IMG_B64\"}},
 {\"type\": \"text\", \"text\": \"What color is this image?\"}
 ]}
 ],
 \"max_tokens\": 500,
 \"enable_thinking\": true
 }")
echo " Response: $RESPONSE" | head -c 800
echo
echo

# 5. Verificar mmproj cargado
echo "5. Verificar mmproj en logs del servicio:"
journalctl --user -u llama-cpp-server-misgastos --no-pager -n 50 2>/dev/null | grep -i "mmproj\|clip\|vision\|image" | tail -10 || echo " (no se encontraron menciones)"
echo

# 6. Verificar archivos del modelo
echo "6. Archivos del modelo:"
ls -la ~/.cache/llama.cpp/models/ 2>/dev/null || echo " ❌ Directorio no existe"
echo

echo "================================"
echo "================================"
echo "Interpretación:"
echo "- Test 3 OK: el modelo responde a texto plano"
echo "- Test 4 OK (thinking=false): el VLM funciona sin modo thinking"
echo "- Test 5 OK (thinking=true): el modelo razona pero puede dejar content vacío"
echo "- Si test 4 funciona y test 5 deja content vacío → confirmar problema thinking"
echo "- Si test 4 falla con 'Invalid url value' → llama.cpp sin soporte vision"
