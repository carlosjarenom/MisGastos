# 💸 MisGastos

**English version** · [📖 Versión en español (default)](./README.md)

Automated family expense tracking for Sonia. A local web app that reads shopping receipts with AI (Qwen3.5-9B via llama.cpp), extracts data automatically, classifies it by category and stores it in SQLite — all without sending anything to the cloud.

> **Local privacy first.** No financial data leaves your home network. The AI model runs on your own GPU; the database lives on your disk.

---

## 📋 Table of Contents

- [What does it do?](#-what-does-it-do)
- [How it works](#-how-it-works)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Usage](#-usage)
- [Configuration](#-configuration)
- [Project structure](#-project-structure)
- [How it connects to llama.cpp](#-how-it-connects-to-llamacpp)
- [Troubleshooting](#-troubleshooting)
- [Development](#-development)

---

## 🎯 What does it do?

MisGastos turns photos of receipts into structured family accounting:

1. **Sonia** takes a photo of the receipt from her tablet/phone
2. The **web app** receives the image and sends it to the **local AI model** (Qwen3.5-9B)
3. The model automatically extracts: date, merchant, VAT ID (NIF), individual items, total and payment method
4. Sonia **reviews the fields** (with doubtful fields highlighted in red) and corrects what's needed
5. On confirmation, the expense is saved to **SQLite** with its category automatically assigned
6. The **dashboard** shows statistics: monthly spending, comparison with previous month, distribution by category, recent expenses

### Key features

- ✅ **100% local** — no cloud, no external APIs, no telemetry
- ✅ **Zero tolerance for errors** — all tickets go through human review before saving; doubtful fields are flagged in red
- ✅ **Review queue** — tickets with low OCR confidence go to a separate queue
- ✅ **Cascade auto-classification**: rules by merchant → heuristics by items → fallback
- ✅ **Double verification** for tickets > €50 (the model reads twice and compares)
- ✅ **Self-learning** — the system saves Sonia's corrections to improve the future prompt
- ✅ **Excel import/export** — exports to traditional Excel-compatible format
- ✅ **Receipt deduplication** by image + date + total + merchant

---

## 🏗 How it works

**Yes, it's designed to run on a single PC** (your home server with a GPU). The app is not a cloud service nor deployed elsewhere in Docker. It works like this:

```
┌────────────────────────────────────────────────────────────┐
│  Server PC (Arch Linux + RTX 3090)                         │
│                                                            │
│  ┌────────────────┐    HTTP    ┌──────────────────────┐   │
│  │  llama.cpp     │◄──────────►│  MisGastos (Flask)   │   │
│  │  (port 8005)   │            │  (port 5000)         │   │
│  │                │            │                      │   │
│  │  Model:        │            │  - Python backend    │   │
│  │  Qwen3.5-9B    │            │  - SQLite (gastos.db)│   │
│  │  (~6GB VRAM)   │            │  - Jinja2 + HTMX     │   │
│  └────────────────┘            │  - Images on disk    │   │
│                                 └──────────┬───────────┘   │
│                                            │               │
└────────────────────────────────────────────┼───────────────┘
                                             │
                                  Home WiFi (LAN)
                                             │
                              ┌──────────────┴──────────────┐
                              │  Sonia's tablet (living room)│
                              │  http://100.110.97.30:5000  │
                              └─────────────────────────────┘
```

### Components

1. **llama.cpp (standalone service, port 8005)** — Serves the Qwen3.5-9B model with an OpenAI-compatible API. Runs as a systemd service.
2. **MisGastos (Flask, port 5000)** — Web backend that receives images, sends them to llama.cpp, processes the resulting JSON, saves to SQLite and serves the UI.
3. **SQLite (`data/gastos.db`)** — Local database with transactions, items, merchants, categories, scans and corrections.
4. **UI (Jinja2 + HTMX + Tailwind CDN)** — Responsive web interface accessible from any LAN device.

### Transaction flow

```
Receipt photo → Preprocessing (resize to 1024px)
              → llama.cpp (Qwen3.5-9B) reads the receipt
              → Returns JSON with data + per-field confidence
              → Sanity checks (sum of items ≈ total, plausible date)
              → If confidence < 0.7 → Review queue
              → If confidence ≥ 0.7 → Edit screen
              → Sonia reviews and confirms
              → Saved to SQLite + items + auto-categorization
```

---

## 💻 Requirements

### Hardware

| Component | Minimum | Recommended |
|---|---|---|
| NVIDIA GPU | 8GB VRAM (Qwen3.5-3B Q4) | 12GB+ VRAM (Qwen3.5-9B Q4) |
| RAM | 16GB | 32GB |
| Disk | 10GB (model + DB + images) | 50GB (several years of use) |
| CPU | 4 cores | 8+ cores |

**Tested on:** NVIDIA RTX 3090 (24GB VRAM), Arch Linux, 32GB RAM.

### Software

- **Python 3.12+**
- **llama.cpp** compiled with CUDA support (`llama-server` binary)
- **Linux** (tested on Arch; should work on Debian/Ubuntu/Fedora)
- **systemd** (for service management)
- **NVIDIA drivers + CUDA toolkit** (for GPU)

### AI models

Download the GGUF files from [Hugging Face](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) and place them in `~/.cache/llama.cpp/models/`:

- `Qwen3.5-9B-Q4_K_M.gguf` (~5.5 GB) — main model
- `mmproj-BF16.gguf` (~50 MB) — vision projector

> **Lighter alternative:** Qwen3.5-3B (~3GB Q4) for GPUs with less VRAM. Edit `systemd/llama-cpp-server-misgastos.service` with the correct filenames.

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/carlosjarenom/MisGastos.git
cd MisGastos
```

### 2. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Download the model

A free HuggingFace token is required (the model is gated). Create one at https://huggingface.co/settings/tokens:

```bash
# Install huggingface_hub if not already installed
pip install huggingface_hub

# Authenticate (first time only)
hf auth login
# Enter your token when prompted

# Download the model (~5.5GB)
mkdir -p ~/.cache/llama.cpp/models/
hf download unsloth/Qwen3.5-9B-GGUF \
    --include "Qwen3.5-9B-Q4_K_M.gguf" \
    --include "mmproj-BF16.gguf" \
    --local-dir ~/.cache/llama.cpp/models/
```

> **Note:** Direct `wget`/`curl` no longer works because HuggingFace requires authentication.

### 4. Install and compile llama.cpp

```bash
# On Arch Linux
yay -S llama.cpp-cuda

# Or build from source
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make GGML_CUDA=1
sudo cp build/bin/llama-server /usr/local/bin/
```

Verify it works:

```bash
llama-server --version
```

### 5. Install the systemd service

The `systemd/llama-cpp-server-misgastos.service` file already has correct paths using `~` (your home). Copy it to your user systemd directory:

```bash
cp systemd/llama-cpp-server-misgastos.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable llama-cpp-server-misgastos
systemctl --user start llama-cpp-server-misgastos
```

**To remove the service** (uninstall):

```bash
systemctl --user stop llama-cpp-server-misgastos
systemctl --user disable llama-cpp-server-misgastos
rm ~/.config/systemd/user/llama-cpp-server-misgastos.service
systemctl --user daemon-reload
```

**To test manually** (without systemd, in the terminal):

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

### 6. Make scripts executable

```bash
chmod +x scripts/*.sh
```

### 7. Verify the server IP

Edit `README.md` and any comments where `100.110.97.30` appears to set your server's actual LAN IP. To find it:

```bash
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 8. Test the installation

```bash
# Start everything
./scripts/start-misgastos.sh

# You should see:
# 🟢 Modelo cargado
# 🚀 MisGastos corriendo en http://0.0.0.0:5000
```

Open from the server's browser: `http://localhost:5000`
From your tablet/phone on the same WiFi: `http://YOUR-SERVER-IP:5000`

---

## 📱 Usage

### Start the app

```bash
./scripts/start-misgastos.sh
```

This:
1. Starts the llama.cpp service with Qwen3.5-9B (port 8005)
2. Waits for the model to load (~20 seconds)
3. Starts Flask (port 5000)

### Stop the app

```bash
./scripts/stop-misgastos.sh
```

Stops Flask and the llama.cpp service.

### Access

From any device on the same WiFi network:

```
http://YOUR-SERVER-IP:5000
```

### Typical usage flow

1. **Sonia** opens the app on her tablet → sees "New ticket" screen
2. Takes a photo of the receipt (or drags an existing image)
3. The system reads the receipt with AI (~3-5 seconds)
4. A form appears with extracted fields:
   - Safe fields → green background
   - Doubtful fields → red background (review)
5. Sonia corrects what's needed (including individual editable items)
6. Clicks "✅ Guardar" (Save)
7. If the system isn't sure (confidence < 70%) → goes to review queue
8. Dashboard shows the new expense + updated statistics

### Available endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard with monthly summary |
| GET | `/scan` | Upload ticket page |
| POST | `/scan/upload` | Process image with OCR |
| POST | `/scan/save` | Save confirmed ticket |
| GET | `/scan/review-queue` | Queue of tickets pending review |
| GET | `/scan/<id>/edit` | Edit ticket from queue |
| POST | `/scan/<id>/discard` | Discard pending ticket |
| GET | `/scan/image/<filename>` | Serve ticket image |
| GET | `/expenses` | History with filters (month, category) |
| GET | `/expense/<id>` | Expense detail |
| GET/POST | `/expense/<id>/edit` | Edit saved expense |
| POST | `/expense/<id>/delete` | Delete expense |
| GET/POST | `/import-excel` | Import historical Excel *(under maintenance)* |
| GET | `/export-excel` | Export month to Excel |
| GET | `/health` | Health check (DB, VLM, disk) |

---

## ⚙️ Configuration

All options live in `config.py`:

```python
# Llama.cpp (OCR VLM)
LLAMA_ENDPOINT = "http://localhost:8005/v1"
LLAMA_MODEL = "qwen3.5-9b"
LLAMA_TEMPERATURE = 0.1         # determinism
LLAMA_MAX_TOKENS = 1024
DOUBLE_CHECK_THRESHOLD = 50.0   # € — tickets >€50 are read twice

# Flask
FLASK_HOST = "0.0.0.0"          # listen on all interfaces
FLASK_PORT = 5000

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "gastos.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

# Image
MAX_IMAGE_DIM = 1024            # resize to max 1024px on long side

# Categories (9 + Mixed)
CATEGORIES = [
    (1, "Comida", None, "#10b981"),
    (2, "Farmacia y Salud", None, "#3b82f6"),
    (3, "Limpieza y Hogar", None, "#f59e0b"),
    (4, "Transporte", None, "#8b5cf6"),
    (5, "Cuidado personal", None, "#ec4899"),
    (6, "Educación", None, "#06b6d4"),
    (7, "Ocio", None, "#ef4444"),
    (8, "Servicios", None, "#6b7280"),  # utilities, internet, phone, insurance
    (9, "Otros", None, "#9ca3af"),
    (10, "Mixto", None, "#fbbf24"),
]
```

### Debug mode

Default is `debug=False`. For development:

```bash
FLASK_DEBUG=1 python3 app.py
```

### Switching GPUs

If you have less VRAM, use Qwen3.5-3B by editing the `.service` file:

```ini
-m ~/.cache/llama.cpp/models/Qwen_Qwen3.5-3B-Q4_K_M.gguf
--mmproj ~/.cache/llama.cpp/models/mmproj-Qwen_Qwen3.5-3B-f16.gguf
```

And in `config.py`:

```python
LLAMA_MODEL = "qwen3.5-3b"
```

---

## 📁 Project structure

```
MisGastos/
├── app.py                      # Flask app + endpoints
├── config.py                   # Central configuration
├── requirements.txt            # Python dependencies
│
├── services/                   # Business logic
│   ├── ocr.py                  # VLM backend (Qwen3.5-9B)
│   ├── image_processor.py      # Preprocessing (resize, EXIF)
│   ├── classifier.py           # Cascade classification
│   ├── llama_client.py         # HTTP client for llama.cpp
│   └── excel.py                # Excel import/export
│
├── models/
│   └── schema.py               # SQLite schema + migrations
│
├── templates/                  # Jinja2 + HTMX
│   ├── base.html
│   ├── scan/
│   │   ├── upload.html         # Drag & drop + camera
│   │   ├── edit.html           # Editable form with items
│   │   └── review_queue.html   # Review queue
│   ├── expenses/
│   │   ├── list.html           # History with filters
│   │   ├── detail.html         # Detail with items
│   │   └── edit.html           # Edit saved expense
│   ├── stats/
│   │   └── dashboard.html      # Summary + budgets
│   ├── import_excel.html       # Excel import
│   └── partial.html            # Layout for HTMX fragments
│
├── scripts/                    # Start/stop
│   ├── start-misgastos.sh
│   └── stop-misgastos.sh
│
├── systemd/
│   └── llama-cpp-server-misgastos.service  # systemd service
│
├── information of project/     # Technical documentation
│   └── PLAN-TECNICO-GUA-GLM.md
│
└── data/                       # Generated at runtime (don't commit)
    ├── gastos.db               # SQLite
    └── uploads/                # Temp images
```

---

## 🧠 How it connects to llama.cpp

**llama.cpp is not included in this repository.** It's a separate project that serves GGUF models with an OpenAI-compatible API. MisGastos connects to it over HTTP.

### Architecture

```
MisGastos (Flask, Python)  ──── HTTP POST /v1/chat/completions ────►  llama-server (C++)
   │                                                                       │
   │  services/llama_client.py                                          GGUF model loaded
   │  uses Python `openai` library                                      in GPU VRAM
   │  with base_url=http://localhost:8005/v1
   │                                                                       │
   │  Receives: JSON text with ticket data                              Returns:
   │  + per-field confidence                                            generated tokens
```

### Why separate?

1. **llama.cpp is a heavy C++ binary** — doesn't integrate well into a Python project
2. **Allows model reuse** — other projects can share the same llama.cpp service
3. **Isolates failures** — if the model crashes, Flask keeps running
4. **Optimal configuration** — GPU parameters, threads, ctx-size are tuned independently

### How is llama.cpp started?

The systemd service (`~/.config/systemd/user/llama-cpp-server-misgastos.service`) launches:

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

**Key parameters:**

| Parameter | Value | Reason |
|---|---|---|
| `-m` | Qwen3.5-9B Q4_K_M | Multimodal model for receipt OCR |
| `--mmproj` | mmproj-BF16 | Vision projector (required for VLM) |
| `--port` | 8005 | llama.cpp server port |
| `--ctx-size` | 16384 | Enough for image + prompt + JSON output |
| `--gpu-layers` | 99 | All model layers in VRAM (adjust if your GPU has less) |
| `--threads` | 16 | CPU threads for preprocessing |
| `--flash-attn on` | on | Speeds up attention, especially with large images |
| `--batch-size` | 512 | Optimized for one ticket per request |

### Switching models

1. Download the new GGUF + mmproj to `~/.cache/llama.cpp/models/`
2. Edit the paths in `systemd/llama-cpp-server-misgastos.service`
3. Edit `LLAMA_MODEL` in `config.py`
4. Reload: `systemctl --user daemon-reload && systemctl --user restart llama-cpp-server-misgastos`

### Verify llama.cpp is running

```bash
curl http://localhost:8005/v1/models
# Should return JSON with the loaded model
```

Or from the app: visit `http://YOUR-SERVER-IP:5000/health` — should show `"vlm": true`.

---

## 🛠 Troubleshooting

### "vlm: false" in /health

The llama.cpp service isn't running or isn't responding.

```bash
# Check status
systemctl --user status llama-cpp-server-misgastos

# View logs
journalctl --user -u llama-cpp-server-misgastos -f

# Restart
systemctl --user restart llama-cpp-server-misgastos
```

Common causes:
- Model not downloaded or wrong path in `.service`
- Insufficient VRAM (downgrade to Qwen3.5-3B)
- CUDA not available (verify with `nvidia-smi`)

### "No se pudo procesar la imagen" (Could not process image)

The uploaded image is invalid. Make sure you upload real JPG/PNG/WEBP files, not PDFs or other formats.

### App doesn't load from tablet

1. Verify you're on the same WiFi
2. Check server IP: `ip addr show`
3. Verify firewall allows port 5000:
   ```bash
   sudo firewall-cmd --add-port=5000/tcp --permanent
   sudo firewall-cmd --reload
   # Or with ufw:
   sudo ufw allow 5000/tcp
   ```
4. Test from the server itself: `curl http://localhost:5000/health`

### 500 error when deleting an expense

Shouldn't happen (fixed in v5+), but if it does:
- Verify you're on the latest commit (`git pull`)
- Check logs with `journalctl --user -u llama-cpp-server-misgastos`

### Model is slow

- Very large images (>5MB): the app already resizes them to 1024px, but you can lower `MAX_IMAGE_DIM` in `config.py`
- High GPU load: verify with `nvidia-smi` that no other processes are using it
- Try `--flash-attn on` (already enabled by default)

### Reset the database

⚠️ **Deletes all data.** Development only.

```bash
rm data/gastos.db
python3 -c "from app import init_db; init_db()"
```

---

## 👨‍💻 Development

### Change structure

- **Backend:** all in `app.py` (endpoints), `services/` (logic), `models/schema.py` (DB)
- **Frontend:** Jinja2 templates in `templates/`, no build step (Tailwind CDN)
- **No complex JS framework** — HTMX for interactivity, minimal vanilla JS

### Tests

No formal test suite yet. To verify manually:

```bash
# Without VLM (mock mode for development)
python3 -c "
import services.ocr as ocr_mod
from services.ocr import OCRResult
ocr_mod.extract_ticket = lambda p: OCRResult(...)
from app import app
app.run(debug=True)
"
```

### Contributing

1. Fork the repo
2. Branch: `git checkout -b feature/name`
3. Commit: `git commit -m "feat: description"`
4. Push: `git push origin feature/name`
5. Pull Request

### Commit convention

- `feat:` new feature
- `fix:` bug fix
- `refactor:` restructuring
- `docs:` documentation
- `chore:` maintenance tasks

---

## 📜 License

Personal project for family use. No explicit open-source license for now.

## 👥 Credits

- **Design and architecture:** Carlos + Jarvis (Qwen3.6-27B) + GLM 5.2 (code reviews)
- **Model:** Qwen3.5-9B by Alibaba
- **Inference:** llama.cpp by Georgi Gerganov
- **Frontend:** HTMX + Tailwind CSS

---

*Documentation generated July 2026. Last update: post-review v7 (44 bugs fixed over 7 inspection rounds).*
