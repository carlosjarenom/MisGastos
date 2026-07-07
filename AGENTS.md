# 🤖 Instrucciones para Agentes (AGENTS.md)

Este archivo contiene guías técnicas, patrones comunes y decisiones de arquitectura para agentes de IA que trabajen en el proyecto **MisGastos**.

## 🏗️ Arquitectura y Flujo

- **Backend:** Flask 3.x.
- **Frontend:** Jinja2 + HTMX para actualizaciones parciales. Se usa `partial.html` como base para respuestas HTMX para evitar duplicar `<html>`/`<body>`.
- **Base de Datos:** SQLite. Los foreign keys están activados por defecto (`PRAGMA foreign_keys = ON`).
- **OCR:** Proceso asíncrono conceptualmente (aunque implementado síncrono en Flask por simplicidad) que usa Qwen3.5-9B vía `llama.cpp` (puerto 8005).

## 🛠️ Patrones Comunes y Reglas

### 1. Base de Datos
- **Inicialización:** No uses `@app.before_request` para `init_db()`. Llámalo solo una vez al arrancar la app.
- **SQLite y Cursores:** Si iteras sobre un cursor y necesitas hacer más queries dentro, usa `fetchall()` primero para evitar conflictos de cursor en SQLite.
- **Foreign Keys:** Siempre verifica que las FK existan antes de insertar (ej. `category_id`).

### 2. Clasificación
- Toda la lógica de clasificación debe pasar por `services/classifier.py:unified_classify`.
- La cascada es: 1. Merchant Dict -> 2. Items Heuristic -> 3. Fallback (Otros).

### 3. Manejo de Imágenes
- Las imágenes subidas se redimensionan a un máximo de 1024px (lado largo) y se guardan como `_processed.jpg`.
- Al borrar una transacción, asegúrate de borrar también el archivo de imagen del disco.

### 4. Tipos de Datos
- **Cantidades:** Usa `REAL` (float) para `quantity` en items, ya que Sonia compra productos al peso (kg, g).
- **Fechas:** Formato estándar `YYYY-MM-DD`.

## 🧪 Verificación y Testing

- Para verificar el Dashboard, asegúrate de que el JSON de Plotly se renderiza correctamente en el HTML.
- El endpoint `/health` da un resumen rápido del estado de la DB y la conexión con el VLM.

## 📌 Decisiones de Diseño

- **Plotly:** Se usa para gráficas en el dashboard. Se renderiza server-side (JSON) y se monta client-side con `Plotly.newPlot`.
- **HTMX:** Preferido sobre JS vanilla para interactividad simple.
