# 💸 MisGastos

[📖 English version](./README.en.md) · **Versión en español (predeterminada)**

Contabilización automatizada de gastos familiares. Una aplicación web local que lee tickets de compra con IA (Qwen3.5-9B vía llama.cpp), extrae los datos automáticamente, los clasifica por categoría y los guarda en SQLite — todo sin enviar nada a la nube.

> **Privacidad local primero.** Ningún dato financiero sale de tu red doméstica. El modelo de IA corre en tu propia GPU; la base de datos vive en tu disco.

---

## 📋 Tabla de contenidos

- [¿Qué hace?](#-qué-hace)
- [Cómo funciona](#-cómo-funciona)
- [Requisitos](#-requisitos)
- [Instalación](#-instalación)
- [Uso](#-uso)
- [Configuración](#-configuración)
- [Estructura del proyecto](#-estructura-del-proyecto)
- [Cómo se conecta con llama.cpp](#-cómo-se-conecta-con-llamacpp)
- [Solución de problemas](#-solución-de-problemas)
- [Desarrollo](#-desarrollo)

---

## 🎯 ¿Qué hace?

MisGastos convierte fotos de tickets en contabilidad familiar estructurada:

1. **Usuario** hace una foto al ticket desde su tablet/móvil
2. La **app web** recibe la imagen y la envía al **modelo de IA local** (Qwen3.5-9B)
3. El modelo extrae automáticamente: fecha, comercio, NIF, items individuales, total y método de pago
4. El usuario **revisa los campos** (con campos dudosos resaltados en rojo) y corrige lo que haga falta
5. Al confirmar, el gasto se guarda en **SQLite** con su categoría asignada automáticamente
6. El **dashboard** muestra estadísticas: gasto del mes, comparativa con el mes anterior, distribución por categoría, últimos gastos

### Características clave

- ✅ **100% local** — sin nube, sin APIs externas, sin telemetría
- ✅ **Cero tolerancia a fallos** — todos los tickets pasan por revisión humana antes de guardarse; los campos dudosos se marcan en rojo
- ✅ **Cola de revisión** — tickets con baja confianza del OCR van a una cola separada
- ✅ **Clasificación automática** en cascada: reglas por comercio → heurística por items → fallback
- ✅ **Doble verificación** para tickets > 50€ (el modelo lee dos veces y compara)
- ✅ **Auto-aprendizaje** — el sistema guarda las correcciones del usuario para mejorar el prompt futuro
- ✅ **Import/export Excel** — exporta a formato compatible con Excel tradicional
- ✅ **Deduplicación de tickets** por imagen + fecha + total + comercio

---

## 🏗 Cómo funciona

**Sí, está pensado para ejecutarse en un único PC** (el servidor doméstico con GPU). La app no es un servicio cloud ni se despliega en Docker en otro sitio. Funciona así:

```
┌────────────────────────────────────────────────────────────┐
│  PC servidor (Arch Linux + RTX 3090)                       │
│                                                            │
│  ┌────────────────┐    HTTP    ┌──────────────────────┐   │
│  │  llama.cpp     │◄──────────►│  MisGastos (Flask)   │   │
│  │  (puerto 8005) │            │  (puerto 5000)       │   │
│  │                │            │                      │   │
│  │  Modelo:       │            │  - Backend Python    │   │
│  │  Qwen3.5-9B    │            │  - SQLite (gastos.db)│   │
│  │  (~6GB VRAM)   │            │  - Jinja2 + HTMX     │   │
│  └────────────────┘            │  - Imágenes en disco │   │
│                                 └──────────┬───────────┘   │
│                                            │               │
└────────────────────────────────────────────┼───────────────┘
                                             │
                                  WiFi doméstica (LAN)
                                             │
                              ┌──────────────┴──────────────┐
                              │  Tablet/móvil del usuario    │
                              │  http://IP-DEL-SERVIDOR:5000  │
                              └─────────────────────────────┘
```

### Componentes

1. **llama.cpp (servicio independiente, puerto 8005)** — Sirve el modelo Qwen3.5-9B con API compatible OpenAI. Corre como servicio systemd.
2. **MisGastos (Flask, puerto 5000)** — Backend web que recibe imágenes, las envía a llama.cpp, procesa el JSON resultante, lo guarda en SQLite y sirve la UI.
3. **SQLite (`data/gastos.db`)** — Base de datos local con transacciones, items, comercios, categorías, scans y correcciones.
4. **UI (Jinja2 + HTMX + Tailwind CDN)** — Interfaz web responsive accesible desde cualquier dispositivo en la LAN.

### Flujo de una transacción

```
Foto del ticket → Preprocesamiento (redimensionar a 1024px)
                → llama.cpp (Qwen3.5-9B) lee el ticket
                → Devuelve JSON con datos + confidence por campo
                → Sanity checks (suma items ≈ total, fecha plausible)
                → Si confidence < 0.7 → Cola de revisión
                → Si confidence ≥ 0.7 → Pantalla de edición
                → Usuario revisa y confirma
                → Guardado en SQLite + items + categorización automática
```

---

## 💻 Requisitos

### Hardware

| Componente | Mínimo | Recomendado |
|---|---|---|
| GPU NVIDIA | 8GB VRAM (Qwen3.5-3B Q4) | 12GB+ VRAM (Qwen3.5-9B Q4) |
| RAM | 16GB | 32GB |
| Disco | 10GB (modelo + DB + imágenes) | 50GB (varios años de uso) |
| CPU | 4 núcleos | 8+ núcleos |

**Probado en:** NVIDIA RTX 3090 (24GB VRAM), Arch Linux, 32GB RAM.

### Software

- **Python 3.12+**
- **llama.cpp** compilado con soporte CUDA (`llama-server` binario)
- **Linux** — distribuciones soportadas
- **systemd** (para gestión del servicio)
- **NVIDIA drivers + CUDA toolkit** (para GPU)

### Distribuciones soportadas

| Distro | Instalar llama.cpp | Instalar NVIDIA | Instalar Python |
|---|---|---|---|
| **Arch Linux** | `yay -S llama.cpp-cuda` | `sudo pacman -S nvidia nvidia-utils` | `sudo pacman -S python` |
| **Ubuntu 22.04+** | Ver [llama.cpp README](https://github.com/ggerganov/llama.cpp) | `sudo apt install nvidia-driver-550` | `sudo apt install python3 python3-venv python3-pip` |
| **Debian 12+** | Ver [llama.cpp README](https://github.com/ggerganov/llama.cpp) | `sudo apt install nvidia-driver` | `sudo apt install python3 python3-venv python3-pip` |
| **Fedora 39+** | `dnf install llama.cpp-cuda` (COPR) | `sudo dnf install akmod-nvidia` | `sudo dnf install python3` |

### Modelos de IA

Descargar los archivos GGUF de [Hugging Face](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) y colocarlos en `~/.cache/llama.cpp/models/`:

- `Qwen3.5-9B-Q4_K_M.gguf` (~5.5 GB) — modelo principal
- `mmproj-BF16.gguf` (~50 MB) — proyector de visión

> **Alternativa más ligera:** Qwen3.5-3B (~3GB Q4) para GPUs con menos VRAM. Editar `systemd/llama-cpp-server-misgastos.service` con los nombres correctos.

---

## 🚀 Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/carlosjarenom/MisGastos.git
cd MisGastos
```

### 2. Crear entorno virtual e instalar dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Descargar el modelo

Se requiere un token gratuito de HuggingFace (el modelo está gated). Crealo en https://huggingface.co/settings/tokens:

```bash
# Instalar huggingface_hub si no lo tienes
pip install huggingface_hub

# Autenticarse (solo la primera vez)
hf auth login
# Introduce tu token cuando se te pida

# Descargar el modelo (~5.5GB)
mkdir -p ~/.cache/llama.cpp/models/
hf download unsloth/Qwen3.5-9B-GGUF \
    --include "Qwen3.5-9B-Q4_K_M.gguf" \
    --include "mmproj-BF16.gguf" \
    --local-dir ~/.cache/llama.cpp/models/
```

> **Nota:** `wget`/`curl` directo ya no funciona porque HuggingFace requiere autentificación.

### 4. Instalar y compilar llama.cpp

```bash
# En Arch Linux
yay -S llama.cpp-cuda

# O compilar desde fuente
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make GGML_CUDA=1
sudo cp build/bin/llama-server /usr/local/bin/
```

Verificar que funciona:

```bash
llama-server --version
```

### 5. Instalar el servicio systemd

El archivo `systemd/llama-cpp-server-misgastos.service` ya tiene las rutas correctas con `~` (tu home). Copialo al directorio de systemd de usuario:

```bash
cp systemd/llama-cpp-server-misgastos.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable llama-cpp-server-misgastos
systemctl --user start llama-cpp-server-misgastos
```

**Para eliminar el servicio** (desinstalar):

```bash
systemctl --user stop llama-cpp-server-misgastos
systemctl --user disable llama-cpp-server-misgastos
rm ~/.config/systemd/user/llama-cpp-server-misgastos.service
systemctl --user daemon-reload
```

**Para probar manualmente** (sin systemd, en la terminal):

```bash
llama-server \
  -m ~/.cache/llama.cpp/models/Qwen3.5-9B-Q4_K_M.gguf \
  --mmproj ~/.cache/llama.cpp/models/mmproj-BF16.gguf \
  --port 8005 \
  --ctx-size 16384 \
  --gpu-layers 99 \
  --threads 16 \
  --flash-attn on
```

### 6. Hacer ejecutables los scripts

```bash
chmod +x scripts/*.sh
```

### 7. Verificar la IP del servidor

Para acceder desde otro dispositivo necesitas la IP del servidor en tu red LAN. Para verla:

```bash
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 8. Probar la instalación

```bash
# Arrancar todo
./scripts/start-misgastos.sh

# Deberías ver:
# 🟢 Modelo cargado
# 🚀 MisGastos corriendo en http://0.0.0.0:5000
```

Abrir desde el navegador del servidor: `http://localhost:5000`
Desde la tablet/móvil en la misma WiFi: `http://IP-DEL-SERVIDOR:5000`

---

## 📱 Uso

### Arrancar la app

```bash
./scripts/start-misgastos.sh
```

Esto:
1. Arranca el servicio de llama.cpp con Qwen3.5-9B (puerto 8005)
2. Espera a que el modelo se cargue (~20 segundos)
3. Arranca Flask (puerto 5000)

### Parar la app

```bash
./scripts/stop-misgastos.sh
```

Detiene Flask y el servicio de llama.cpp.

### Acceder

Desde cualquier dispositivo en la misma red WiFi:

```
http://IP-DEL-SERVIDOR:5000
```

### Flujo típico de uso

1. **Usuario** abre la app en su tablet → ve pantalla de "Nuevo ticket"
2. Hace foto al ticket (o arrastra una imagen existente)
3. El sistema lee el ticket con IA (~3-5 segundos)
4. Aparece un formulario con los campos extraídos:
   - Campos seguros → fondo verde
   - Campos dudosos → fondo rojo (revisar)
5. El usuario corrige lo que haga falta (incluyendo items individuales editables)
6. Pulsa "✅ Guardar"
7. Si el sistema no está seguro (confidence < 70%) → va a cola de revisión
8. Dashboard muestra el gasto añadido + estadísticas actualizadas

### Endpoints disponibles

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Dashboard con resumen del mes |
| GET | `/scan` | Página de subir ticket |
| POST | `/scan/upload` | Procesa imagen con OCR |
| POST | `/scan/save` | Guarda ticket confirmado |
| GET | `/scan/review-queue` | Cola de tickets pendientes de revisión |
| GET | `/scan/<id>/edit` | Editar ticket desde la cola |
| POST | `/scan/<id>/discard` | Descartar ticket pendiente |
| GET | `/scan/image/<filename>` | Sirve imagen de un ticket |
| GET | `/expenses` | Historial con filtros (mes, categoría) |
| GET | `/expense/<id>` | Detalle de un gasto |
| GET/POST | `/expense/<id>/edit` | Editar gasto guardado |
| POST | `/expense/<id>/delete` | Borrar gasto |
| GET/POST | `/import-excel` | Importar Excel histórico *(en mantenimiento)* |
| GET | `/export-excel` | Exportar mes a Excel |
| GET | `/health` | Health check (DB, VLM, disco) |

---

## ⚙️ Configuración

Todas las opciones viven en `config.py`:

```python
# Llama.cpp (OCR VLM)
LLAMA_ENDPOINT = "http://localhost:8005/v1"
LLAMA_MODEL = "qwen3.5-9b"
LLAMA_TEMPERATURE = 0.1         # determinismo
LLAMA_MAX_TOKENS = 1024
DOUBLE_CHECK_THRESHOLD = 50.0   # € — tickets >50€ se leen dos veces

# Flask
FLASK_HOST = "0.0.0.0"          # escuchar en todas las interfaces
FLASK_PORT = 5000

# Rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "gastos.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

# Imagen
MAX_IMAGE_DIM = 1024            # redimensionar a max 1024px lado largo

# Categorías (9 + Mixto)
CATEGORIES = [
    (1, "Comida", None, "#10b981"),
    (2, "Farmacia y Salud", None, "#3b82f6"),
    (3, "Limpieza y Hogar", None, "#f59e0b"),
    (4, "Transporte", None, "#8b5cf6"),
    (5, "Cuidado personal", None, "#ec4899"),
    (6, "Educación", None, "#06b6d4"),
    (7, "Ocio", None, "#ef4444"),
    (8, "Servicios", None, "#6b7280"),  # luz, agua, internet, teléfono, seguros
    (9, "Otros", None, "#9ca3af"),
    (10, "Mixto", None, "#fbbf24"),
]
```

### Modo debug

Por defecto `debug=False`. Para desarrollo:

```bash
FLASK_DEBUG=1 python3 app.py
```

### Cambiar de GPU

Si tienes menos VRAM, usa Qwen3.5-3B editando el archivo `.service`:

```ini
-m ~/.cache/llama.cpp/models/Qwen_Qwen3.5-3B-Q4_K_M.gguf
--mmproj ~/.cache/llama.cpp/models/mmproj-Qwen_Qwen3.5-3B-f16.gguf
```

Y en `config.py`:

```python
LLAMA_MODEL = "qwen3.5-3b"
```

---

## 📁 Estructura del proyecto

```
MisGastos/
├── app.py                      # Flask app + endpoints
├── config.py                   # Configuración central
├── requirements.txt            # Dependencias Python
│
├── services/                   # Lógica de negocio
│   ├── ocr.py                  # Backend VLM (Qwen3.5-9B)
│   ├── image_processor.py      # Preprocesamiento (redimensionar, EXIF)
│   ├── classifier.py           # Clasificación en cascada
│   ├── llama_client.py         # Cliente HTTP para llama.cpp
│   └── excel.py                # Import/Export Excel
│
├── models/
│   └── schema.py               # SQLite schema + migraciones
│
├── templates/                  # Jinja2 + HTMX
│   ├── base.html
│   ├── scan/
│   │   ├── upload.html         # Drag & drop + cámara
│   │   ├── edit.html           # Form editable con items
│   │   └── review_queue.html   # Cola de revisión
│   ├── expenses/
│   │   ├── list.html           # Historial con filtros
│   │   ├── detail.html         # Detalle con items
│   │   └── edit.html           # Editar gasto guardado
│   ├── stats/
│   │   └── dashboard.html      # Resumen + presupuestos
│   ├── import_excel.html       # Importación Excel
│   └── partial.html            # Layout para HTMX fragments
│
├── scripts/                    # Arranque/parada
│   ├── start-misgastos.sh
│   └── stop-misgastos.sh
│
├── systemd/
│   └── llama-cpp-server-misgastos.service  # Servicio systemd
│
├── information of project/     # Documentación técnica
│   └── PLAN-TECNICO-GUA-GLM.md
│
└── data/                       # Generado en runtime (no commitear)
    ├── gastos.db               # SQLite
    └── uploads/                # Imágenes temporales
```

---

## 🧠 Cómo se conecta con llama.cpp

**llama.cpp no viene incluido en este repositorio.** Es un proyecto separado que sirve modelos GGUF con una API compatible OpenAI. MisGastos se conecta a él por HTTP.

### Arquitectura

```
MisGastos (Flask, Python)  ──── HTTP POST /v1/chat/completions ────►  llama-server (C++)
   │                                                                       │
   │  services/llama_client.py                                          Modelo GGUF cargado
   │  usa librería `openai` de Python                                  en VRAM GPU
   │  con base_url=http://localhost:8005/v1
   │                                                                       │
   │  Recibe: texto JSON con datos del ticket                          Devuelve:
   │  + confidence por campo                                           tokens generados
```

### ¿Por qué separado?

1. **llama.cpp es un binario C++ pesado** — no se integra bien en un proyecto Python
2. **Permite reutilizar el modelo** — otros proyectos pueden compartir el mismo servicio llama.cpp
3. **Aísla fallos** — si el modelo crashea, Flask sigue corriendo
4. **Configuración óptima** — parámetros de GPU, threads, ctx-size se ajustan independientemente

### ¿Cómo se arranca llama.cpp?

El servicio systemd (`~/.config/systemd/user/llama-cpp-server-misgastos.service`) lanza:

```bash
llama-server \
  -m ~/.cache/llama.cpp/models/Qwen3.5-9B-Q4_K_M.gguf \
  --mmproj ~/.cache/llama.cpp/models/mmproj-BF16.gguf \
  --port 8005 \
  --host 0.0.0.0 \
  --ctx-size 16384 \
  --gpu-layers 99 \
  --threads 16 \
  --batch-size 512 \
  --ubatch-size 512 \
  --flash-attn on
```

**Parámetros clave:**

| Parámetro | Valor | Razón |
|---|---|---|
| `-m` | Qwen3.5-9B Q4_K_M | Modelo multimodal para OCR de tickets |
| `--mmproj` | mmproj-BF16 | Proyector de visión (necesario para VLM) |
| `--port` | 8005 | Puerto del servidor llama.cpp |
| `--ctx-size` | 16384 | Suficiente para imagen + prompt + JSON output |
| `--gpu-layers` | 99 | Todo el modelo en VRAM (ajustar si tu GPU tiene menos) |
| `--threads` | 16 | CPU threads para preprocesamiento |
| `--flash-attn on` | on | Acelera atención, especialmente con imágenes grandes |
| `--batch-size` | 512 | Optimizado para un ticket por request |

### Cambiar de modelo

1. Descarga el nuevo GGUF + mmproj a `~/.cache/llama.cpp/models/`
2. Edita las rutas en `systemd/llama-cpp-server-misgastos.service`
3. Edita `LLAMA_MODEL` en `config.py`
4. Recarga: `systemctl --user daemon-reload && systemctl --user restart llama-cpp-server-misgastos`

### Verificar que llama.cpp está corriendo

```bash
curl http://localhost:8005/v1/models
# Debería devolver JSON con el modelo cargado
```

O desde la app: visitar `http://IP-DEL-SERVIDOR:5000/health` — debe mostrar `"vlm": true`.

---

## 🛠 Solución de problemas

### "vlm: false" en /health

El servicio llama.cpp no está corriendo o no responde.

```bash
# Verificar estado
systemctl --user status llama-cpp-server-misgastos

# Ver logs
journalctl --user -u llama-cpp-server-misgastos -f

# Reiniciar
systemctl --user restart llama-cpp-server-misgastos
```

Causas comunes:
- Modelo no descargado o ruta incorrecta en `.service`
- VRAM insuficiente (bajar a Qwen3.5-3B)
- CUDA no disponible (verificar con `nvidia-smi`)

### "No se pudo procesar la imagen"

La imagen subida no es válida. Asegúrate de subir JPG/PNG/WEBP reales, no PDFs u otros formatos.

### La app no carga desde la tablet

1. Verificar que estás en la misma WiFi
2. Verificar la IP del servidor: `ip addr show`
3. Verificar que el firewall permite el puerto 5000:
   ```bash
   sudo firewall-cmd --add-port=5000/tcp --permanent
   sudo firewall-cmd --reload
   # O en ufw:
   sudo ufw allow 5000/tcp
   ```
4. Probar desde el propio servidor: `curl http://localhost:5000/health`

### Error 500 al borrar un gasto

No debería pasar (arreglado en v5+), pero si ocurre:
- Verifica que usas el último commit (`git pull`)
- Mira logs con `journalctl --user -u llama-cpp-server-misgastos`

### El modelo tarda mucho

- Imágenes muy grandes (>5MB): la app ya las redimensiona a 1024px, pero puedes reducir `MAX_IMAGE_DIM` en `config.py`
- Mucha carga en GPU: verifica con `nvidia-smi` que no hay otros procesos
- Probar `--flash-attn on` (ya activado por defecto)

### Resetear la base de datos

⚠️ **Borra todos los datos.** Solo para desarrollo.

```bash
rm data/gastos.db
python3 -c "from app import init_db; init_db()"
```

---

## 👨‍💻 Desarrollo

### Estructura de cambios

- **Backend:** todo en `app.py` (endpoints), `services/` (lógica), `models/schema.py` (DB)
- **Frontend:** Jinja2 templates en `templates/`, sin build step (Tailwind CDN)
- **Sin framework JS complejo** — HTMX para interactividad, JS vanilla mínimo

### Tests

No hay test suite formal todavía. Para verificar manualmente:

```bash
# Sin VLM (modo mock para desarrollo)
python3 -c "
import services.ocr as ocr_mod
from services.ocr import OCRResult
ocr_mod.extract_ticket = lambda p: OCRResult(...)
from app import app
app.run(debug=True)
"
```

### Contribuir

1. Fork del repo
2. Rama: `git checkout -b feature/nombre`
3. Commit: `git commit -m "feat: descripción"`
4. Push: `git push origin feature/nombre`
5. Pull Request

### Convención de commits

- `feat:` nueva funcionalidad
- `fix:` corrección de bug
- `refactor:` reestructuración
- `docs:` documentación
- `chore:` tareas de mantenimiento

---

## 📜 Licencia

Proyecto personal para uso familiar. Sin licencia open-source explícita por ahora.

## 👥 Créditos

- **Diseño y arquitectura:** Carlos + Jarvis (Qwen3.6-27B) + GLM 5.2 (reviews de código)
- **Modelo:** Qwen3.5-9B por Alibaba
- **Inferencia:** llama.cpp por Georgi Gerganov
- **Frontend:** HTMX + Tailwind CSS

---

*Documentación generada julio 2026. Última actualización: post-review v7 (44 bugs corregidos en 7 rounds de inspección).*
