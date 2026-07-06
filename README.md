# MisGastos

Contabilización automatizada de gastos familiares para Sonia.

## Requisitos

- Python 3.12+
- Arch Linux
- NVIDIA RTX 3090 (24GB VRAM)
- Qwen3.5-9B Q4_K_M + mmproj en `~/.cache/llama.cpp/models/`

## Instalación

```bash
# 1. Instalar dependencias
cd MisGastos/
pip install -r requirements.txt

# 2. Copiar servicio systemd
cp systemd/llama-cpp-server-misgastos.service ~/.config/systemd/user/
systemctl --user daemon-reload

# 3. Descargar modelo (si no está ya)
# Qwen_Qwen3.5-9B-Q4_K_M.gguf (~5.5GB)
# mmproj-Qwen_Qwen3.5-9B-f16.gguf (~few MB)

# 4. Hacer scripts ejecutables
chmod +x scripts/*.sh
```

## Uso

```bash
# Arrancar (para OpenClaw, enciende MisGastos)
./scripts/start-misgastos.sh

# Parar (apaga MisGastos, reencende OpenClaw)
./scripts/stop-misgastos.sh
```

Acceder desde cualquier dispositivo en la red:
- http://100.110.97.30:5000

## Flujo

1. Sonia abre la app en su tablet
2. Sube foto del ticket
3. Revisa los campos extraídos (edición manual si es necesario)
4. Confirma → guardado en SQLite
5. Dashboard con estadísticas y comparativas

## Estructura

```
MisGastos/
├── app.py                    # Flask app
├── config.py                 # Configuración
├── requirements.txt
├── services/
│   ├── ocr.py               # VLM OCR
│   ├── classifier.py        # Clasificación en cascada
│   ├── excel.py             # Import/Export
│   ├── image_processor.py   # Preprocesamiento imagen
│   └── llama_client.py      # Cliente llama.cpp
├── models/
│   └── schema.py            # DB schema
├── templates/               # Jinja2 + HTMX
├── scripts/                 # start/stop
├── systemd/                 # Servicio llama.cpp
└── data/
    ├── gastos.db            # SQLite (auto-generado)
    └── uploads/             # Fotos temporales
```

## Modelo

- Qwen3.5-9B (Q4_K_M) en puerto 8005
- ctx-size: 16384
- gpu-layers: 99 (todo en GPU)
- Sin cuantización KV cache (fp16 nativo)
- Flash attention activado
