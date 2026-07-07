#!/bin/bash
# install.sh — Instalación automatizada de MisGastos
#
# Uso:
#   ./scripts/install.sh                # instalación básica
#   ./scripts/install.sh --with-model   # también descarga el modelo (5.5GB)
#   ./scripts/install.sh --help         # ayuda
#
# El script:
#   1. Verifica prerequisitos (Python 3.12+, NVIDIA, llama.cpp, wget)
#   2. Crea entorno virtual Python e instala dependencias
#   3. Descarga el modelo Qwen3.5-9B (opcional, con --with-model)
#   4. Hace scripts ejecutables
#   5. Instala el servicio systemd (sustituye rutas automáticamente)
#   6. Inicializa la base de datos SQLite
#   7. Verifica que todo carga correctamente y muestra URLs
#
# No usar set -e: queremos manejar errores con mensajes claros.

# ============================================================
# CONFIGURACIÓN
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"
MODEL_DIR="$HOME/.cache/llama.cpp/models"
MODEL_NAME="Qwen3.5-9B-Q4_K_M.gguf"
MMPROJ_NAME="mmproj-BF16.gguf"
MODEL_SIZE_GB="5.5"
SERVICE_NAME="llama-cpp-server-misgastos.service"
SERVICE_SRC="$PROJECT_DIR/systemd/$SERVICE_NAME"
SERVICE_DST="$HOME/.config/systemd/user/$SERVICE_NAME"

# Colores (solo si el terminal los soporta)
if [[ -t 1 ]] && [[ -z "$NO_COLOR" ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' NC=''
fi

# Estado global
INSTALL_OK=true
NEED_MODEL=false
DISTRO="unknown"
DISTRO_ID=""
DISTRO_VERSION=""

# ============================================================
# FUNCIONES
# ============================================================

print_header() {
    echo
    echo -e "${BLUE}${BOLD}💸 MisGastos — Instalador${NC}"
    echo -e "${BLUE}========================${NC}"
    echo
}

print_step() {
    echo -e "\n${BOLD}▶ $1${NC}"
}

print_ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

print_warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

print_err() {
    echo -e "  ${RED}✗${NC} $1" >&2
}

print_info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

check_command() {
    command -v "$1" &> /dev/null
}

check_prereq() {
    local cmd="$1"
    local name="$2"
    local hint="${3:-}"
    
    if check_command "$cmd"; then
        print_ok "$name encontrado"
        return 0
    else
        print_err "$name NO encontrado"
        if [[ -n "$hint" ]]; then
            print_info "Instalar con: $hint"
        fi
        return 1
    fi
}

confirm() {
    local prompt="$1"
    local default="${2:-y}"
    local answer
    
    if [[ "$default" == "y" ]]; then
        read -p "$prompt [Y/n] " answer
        answer=${answer:-y}
    else
        read -p "$prompt [y/N] " answer
        answer=${answer:-n}
    fi
    
    [[ "$answer" =~ ^[Yy]$ ]]
}

die() {
    print_err "$1"
    echo
    echo -e "${RED}Instalación abortada.${NC}"
    exit 1
}

print_help() {
    cat <<EOF
MisGastos — Instalador automatizado

USO:
    ./scripts/install.sh [opciones]

OPCIONES:
    --with-model     Descarga también el modelo Qwen3.5-9B (~5.5GB)
    --skip-systemd   No instalar el servicio systemd (uso manual)
    --help, -h       Muestra esta ayuda

EJEMPLOS:
    ./scripts/install.sh
        Instalación básica: venv + dependencias + scripts + servicio systemd

    ./scripts/install.sh --with-model
        Igual que arriba + descarga del modelo desde HuggingFace

SIN ESTE SCRIPT (instalación manual):
    Consulta el README.md, sección "Instalación avanzada"

REQUISITOS PREVIOS:
    - Python 3.12+
    - GPU NVIDIA con drivers + CUDA
    - llama-server (llama.cpp) en PATH
      Instalar con: yay -S llama.cpp-cuda  (Arch Linux)
    - Token de HuggingFace (gratis): https://huggingface.co/settings/tokens
      (huggingface_hub se instala automáticamente con --with-model)
EOF
}

# ============================================================
# DETECCIÓN DE DISTRIBUCIÓN
# ============================================================

detect_distro() {
    if [[ -f /etc/os-release ]]; then
        DISTRO_ID=$(. /etc/os-release && echo "$ID")
        DISTRO_VERSION=$(. /etc/os-release && echo "$VERSION_ID")
    fi

    case "$DISTRO_ID" in
        arch)
            DISTRO="arch"
            PKG_MGR="pacman"
            ;;
        ubuntu|pop)
            DISTRO="debian"
            PKG_MGR="apt"
            ;;
        debian)
            DISTRO="debian"
            PKG_MGR="apt"
            ;;
        fedora)
            DISTRO="fedora"
            PKG_MGR="dnf"
            ;;
        *)
            DISTRO="unknown"
            PKG_MGR="unknown"
            ;;
    esac
}

get_install_hint() {
    local pkg="$1"
    local hint="$2"

    case "$DISTRO" in
        arch)
            case "$pkg" in
                python|python3) echo "sudo pacman -S python" ;;
                nvidia) echo "sudo pacman -S nvidia nvidia-utils" ;;
                llama.cpp) echo "yay -S llama.cpp-cuda" ;;
                *) echo "$hint" ;;
            esac
            ;;
        debian)
            case "$pkg" in
                python|python3) echo "sudo apt install python3 python3-venv python3-pip" ;;
                nvidia) echo "sudo apt install nvidia-driver-550" ;;
                llama.cpp) echo "Ver https://github.com/ggerganov/llama.cpp" ;;
                *) echo "$hint" ;;
            esac
            ;;
        fedora)
            case "$pkg" in
                python|python3) echo "sudo dnf install python3" ;;
                nvidia) echo "sudo dnf install akmod-nvidia" ;;
                llama.cpp) echo "Ver https://github.com/ggerganov/llama.cpp" ;;
                *) echo "$hint" ;;
            esac
            ;;
        *)
            echo "$hint"
            ;;
    esac
}
}

# ============================================================
# PARSE ARGS
# ============================================================

WITH_MODEL=false
SKIP_SYSTEMD=false

for arg in "$@"; do
    case "$arg" in
        --with-model) WITH_MODEL=true ;;
        --skip-systemd) SKIP_SYSTEMD=true ;;
        --help|-h) print_help; exit 0 ;;
        *) print_err "Opción desconocida: $arg"; echo; print_help; exit 1 ;;
    esac
done

# ============================================================
# 0. VALIDACIÓN INICIAL
# ============================================================

print_header

# Detectar distribución
detect_distro
print_info "Distribución detectada: ${DISTRO_ID} ${DISTRO_VERSION} (${DISTRO})"

# Verificar que el script se ejecuta desde el proyecto correcto
if [[ ! -f "$PROJECT_DIR/app.py" ]]; then
    die "No se encontró app.py en $PROJECT_DIR. ¿Ejecutaste el script desde fuera del proyecto?"
fi

# Avisar si se ejecuta como root
if [[ $EUID -eq 0 ]]; then
    print_warn "Estás ejecutando como root. No es recomendable."
    if ! confirm "¿Continuar como root?" "n"; then
        exit 1
    fi
fi

# ============================================================
# 1. PREREQUISITOS
# ============================================================

print_step "1/7 — Verificando prerequisitos"

PREREQ_OK=true

# Python 3
if ! check_prereq python3 "Python 3" ""; then
    PREREQ_OK=false
else
    # Versión
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [[ -z "$PY_MAJOR" || -z "$PY_MINOR" ]] || [[ $PY_MAJOR -lt 3 ]] || [[ $PY_MAJOR -eq 3 && $PY_MINOR -lt 12 ]]; then
        print_err "Python 3.12+ requerido (tienes $PY_VERSION)"
        PREREQ_OK=false
    else
        print_ok "Python $PY_VERSION (>= 3.12)"
    fi
fi

# pip (directo o vía python3 -m pip)
PIP_CMD=""
if check_command pip; then
    PIP_CMD="pip"
    print_ok "pip encontrado"
elif python3 -m pip --version &> /dev/null; then
    PIP_CMD="python3 -m pip"
    print_ok "pip encontrado (vía python3 -m pip)"
else
    print_err "pip NO encontrado"
    print_info "Instalar con: sudo pacman -S python-pip  (Arch Linux)"
    PREREQ_OK=false
fi

# venv module
if ! python3 -c "import venv" 2>/dev/null; then
    print_err "Módulo venv de Python no disponible"
    print_info "Instalar con: sudo pacman -S python-virtualenv  (Arch Linux)"
    PREREQ_OK=false
else
    print_ok "Módulo venv disponible"
fi

# GPU NVIDIA
if check_command nvidia-smi; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | tr -d '\n')
    VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' \n')
    if [[ -z "$GPU_NAME" || -z "$VRAM_MB" ]]; then
        print_warn "nvidia-smi no devolvió datos de GPU (¿drivers mal instalados?)"
    elif [[ ! "$VRAM_MB" =~ ^[0-9]+$ ]]; then
        print_warn "No se pudo leer VRAM: '$VRAM_MB'"
    elif [[ $VRAM_MB -lt 8000 ]]; then
        print_warn "GPU con ${VRAM_MB}MB VRAM — puede que necesites Qwen3.5-3B en vez de 9B"
        print_info "GPU: $GPU_NAME"
    else
        print_ok "GPU: $GPU_NAME (${VRAM_MB}MB VRAM)"
    fi
else
    print_err "NVIDIA drivers NO encontrados (falta nvidia-smi)"
    print_info "Instalar drivers NVIDIA: sudo pacman -S nvidia nvidia-utils"
    PREREQ_OK=false
fi

# llama.cpp
if ! check_command llama-server; then
    print_err "llama.cpp NO encontrado (falta llama-server en PATH)"
    print_info "En Arch Linux: yay -S llama.cpp-cuda"
    print_info "Compilar desde fuente: https://github.com/ggerganov/llama.cpp"
    PREREQ_OK=false
else
    print_ok "llama.cpp encontrado"
fi

# huggingface-cli (solo necesario si se va a descargar el modelo)
if [[ "$WITH_MODEL" == "true" ]]; then
    # Verificar token de HuggingFace
    if [[ -z "${HF_TOKEN}" ]] && [[ ! -f "$HOME/.cache/huggingface/token" ]]; then
        print_warn "No se encontró token de HuggingFace"
        print_info "Para --with-model necesitas un token (gratis)"
        print_info "  1. Crea uno en https://huggingface.co/settings/tokens"
        print_info "  2. Luego ejecuta: hf auth login"
        echo
        if confirm "¿Tienes tu token de HuggingFace listo para introducir ahora?"; then
            print_info "Usa: huggingface-cli login"
                    if ! check_command hf; then
                print_info "Instalando huggingface_hub (incluye CLI hf)..."
                pip install -q huggingface_hub
            fi
            hf auth login
        fi
    fi
    
    if ! check_command hf; then
        print_warn "CLI hf no instalado — se instalará en el paso del modelo"
    else
        print_ok "CLI hf encontrado"
    fi
fi

# systemctl --user
if [[ "$SKIP_SYSTEMD" == "false" ]]; then
    if ! systemctl --user >/dev/null 2>&1; then
        print_warn "systemctl --user no disponible — no se instalará el servicio systemd"
        SKIP_SYSTEMD=true
    else
        print_ok "systemd disponible"
    fi
fi

if [[ "$PREREQ_OK" == "false" ]]; then
    echo
    print_err "Faltan prerequisitos. Instálalos y vuelve a ejecutar este script."
    exit 1
fi

print_ok "Todos los prerequisitos están listos"

# ============================================================
# 2. ENTORNO VIRTUAL PYTHON
# ============================================================

print_step "2/7 — Creando entorno virtual Python"

# Recrear venv si ya existe
if [[ -d "$VENV_DIR" ]]; then
    print_warn "El entorno virtual ya existe en $VENV_DIR"
    if ! confirm "¿Recrearlo? (se perderán paquetes instalados manualmente)" "n"; then
        print_info "Manteniendo entorno virtual existente"
    else
        rm -rf "$VENV_DIR"
        print_ok "Entorno virtual anterior eliminado"
    fi
fi

# Crear venv
if [[ ! -d "$VENV_DIR" ]]; then
    if ! python3 -m venv "$VENV_DIR"; then
        die "No se pudo crear el entorno virtual en $VENV_DIR"
    fi
    print_ok "Entorno virtual creado en $VENV_DIR"
fi

# Activar venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
print_ok "Entorno virtual activado"

# Actualizar pip
print_info "Actualizando pip, wheel, setuptools..."
if ! python3 -m pip install --upgrade pip wheel setuptools --quiet; then
    print_warn "No se pudo actualizar pip (continuando de todas formas)"
fi

# Instalar dependencias
print_info "Instalando dependencias (esto puede tardar 1-2 minutos)..."
if ! python3 -m pip install -r "$PROJECT_DIR/requirements.txt"; then
    die "No se pudieron instalar las dependencias. Revisa requirements.txt y tu conexión a internet."
fi
print_ok "Dependencias instaladas"

# ============================================================
# 3. MODELO (OPCIONAL)
# ============================================================

print_step "3/7 — Verificando modelo Qwen3.5-9B"

mkdir -p "$MODEL_DIR"

MODEL_PATH="$MODEL_DIR/$MODEL_NAME"
MMPROJ_PATH="$MODEL_DIR/$MMPROJ_NAME"

NEED_MODEL=false
if [[ -f "$MODEL_PATH" && -f "$MMPROJ_PATH" ]]; then
    print_ok "Modelo ya existe en $MODEL_DIR"
else
    if [[ ! -f "$MODEL_PATH" ]]; then
        print_warn "Falta: $MODEL_PATH"
        NEED_MODEL=true
    fi
    if [[ ! -f "$MMPROJ_PATH" ]]; then
        print_warn "Falta: $MMPROJ_PATH"
        NEED_MODEL=true
    fi
    
    if [[ "$WITH_MODEL" == "true" ]]; then
        # Instalar huggingface_hub si no está
        if ! check_command hf; then
            print_info "Instalando huggingface_hub..."
            if ! pip install -q huggingface_hub; then
                die "No se pudo instalar huggingface_hub. Verifica tu conexión a internet."
            fi
        fi
        
        # Verificar token de HuggingFace
        if [[ -z "${HF_TOKEN}" ]] && [[ ! -f "$HOME/.cache/huggingface/token" ]]; then
            die "No se encontró token de HuggingFace. Ejecuta 'hf auth login' primero."
        fi
        
        # Verificar espacio en disco
        AVAILABLE_GB=$(df -BG "$MODEL_DIR" | awk 'NR==2 {gsub("G","",$4); print $4}')
        if [[ "$AVAILABLE_GB" =~ ^[0-9]+$ ]] && [[ $AVAILABLE_GB -lt 7 ]]; then
            die "Espacio insuficiente en disco: ${AVAILABLE_GB}GB disponibles, se necesitan ~7GB"
        fi
        
        if confirm "Se descargarán ~${MODEL_SIZE_GB}GB. ¿Continuar?"; then
            print_info "Descargando modelo con huggingface-cli (puede tardar varios minutos)..."
            echo
            
            # Descargar solo los archivos necesarios
            if ! hf download unsloth/Qwen3.5-9B-GGUF \
                --include "$MODEL_NAME" \
                --include "$MMPROJ_NAME" \
                --local-dir "$MODEL_DIR"; then
                print_err "Error descargando el modelo"
                NEED_MODEL=true
            else
                print_ok "Descarga completada"
                # Resetear NEED_MODEL si ambos archivos existen
                if [[ -f "$MODEL_PATH" && -f "$MMPROJ_PATH" ]]; then
                    NEED_MODEL=false
                    print_ok "Modelo principal descargado ($MODEL_NAME)"
                    print_ok "Proyector de visión descargado ($MMPROJ_NAME)"
                else
                    print_warn "Descarga completada pero faltan archivos. Revisa $MODEL_DIR"
                    NEED_MODEL=true
                fi
            fi
        else
            print_warn "Descarga cancelada — deberás descargar el modelo manualmente"
        fi
    else
        print_info "Descarga del modelo omitida (usa --with-model para descargar)"
        print_info "Descarga manual: hf download unsloth/Qwen3.5-9B-GGUF --include '*.gguf' --local-dir $MODEL_DIR"
    fi
fi

if [[ "$NEED_MODEL" == "true" ]]; then
    print_warn "El modelo NO está completo. La app fallará al arrancar."
    print_info "Descárgalo manualmente o vuelve a ejecutar con --with-model"
fi

# ============================================================
# 4. SCRIPTS EJECUTABLES
# ============================================================

print_step "4/7 — Haciendo scripts ejecutables"

if chmod +x "$PROJECT_DIR/scripts/"*.sh 2>/dev/null; then
    print_ok "Scripts en scripts/ son ejecutables"
else
    print_warn "No se pudieron hacer ejecutables los scripts (¿permisos?)"
fi

# ============================================================
# 5. SERVICIO SYSTEMD
# ============================================================

if [[ "$SKIP_SYSTEMD" == "true" ]]; then
    print_step "5/7 — Servicio systemd (omitido)"
    print_info "Saltado por --skip-systemd o porque systemctl --user no está disponible"
else
    print_step "5/7 — Instalando servicio systemd"
    
    # Verificar que el archivo fuente existe
    if [[ ! -f "$SERVICE_SRC" ]]; then
        print_warn "No se encontró $SERVICE_SRC — saltando instalación del servicio"
    else
        mkdir -p "$(dirname "$SERVICE_DST")"
        
        # Generar archivo de servicio con rutas correctas
        TMP_SERVICE=$(mktemp) || { print_warn "No se pudo crear archivo temporal"; SKIP_SYSTEMD=true; }
        
        if [[ "$SKIP_SYSTEMD" == "false" ]]; then
            # %h se expande automáticamente en systemd, pero sustituir /home/carlos/ si existe
            sed "s|/home/carlos/|$HOME/|g" "$SERVICE_SRC" > "$TMP_SERVICE"
            
            # Reemplazar ruta del binario llama-server si está en otro sitio
            LLAMA_SERVER_PATH=$(command -v llama-server 2>/dev/null || echo "$HOME/.local/bin/llama-server")
            sed -i "s|/home/carlos/.local/bin/llama-server|$LLAMA_SERVER_PATH|g" "$TMP_SERVICE"
            
            # Verificar que las rutas del modelo existen (si el modelo está descargado)
            if [[ ! -f "$MODEL_PATH" ]]; then
                print_warn "El modelo no está en $MODEL_PATH — el servicio fallará al arrancar"
                print_info "Descarga el modelo antes de arrancar el servicio"
            fi
            
            # Copiar
            if cp "$TMP_SERVICE" "$SERVICE_DST"; then
                print_ok "Servicio instalado en $SERVICE_DST"
                rm -f "$TMP_SERVICE"
                
                systemctl --user daemon-reload
                print_ok "systemd recargado"
                
                print_info "Servicio: $SERVICE_NAME"
                print_info "Comandos útiles:"
                echo "    systemctl --user start $SERVICE_NAME    # arrancar"
                echo "    systemctl --user stop $SERVICE_NAME     # parar"
                echo "    systemctl --user status $SERVICE_NAME   # estado"
                echo "    journalctl --user -u $SERVICE_NAME -f   # logs"
            else
                print_warn "No se pudo copiar el servicio a $SERVICE_DST"
                rm -f "$TMP_SERVICE"
            fi
        fi
    fi
fi

# ============================================================
# 6. INICIALIZAR BASE DE DATOS
# ============================================================

print_step "6/7 — Inicializando base de datos"

# Crear directorios necesarios
mkdir -p "$PROJECT_DIR/data/uploads"

cd "$PROJECT_DIR" || die "No se pudo cambiar a $PROJECT_DIR"

if python3 -c "from app import init_db; init_db()" 2>&1 | tail -5; then
    if [[ -f "$PROJECT_DIR/data/gastos.db" ]]; then
        print_ok "Base de datos inicializada en data/gastos.db"
    else
        print_warn "init_db() ejecutado pero no se creó gastos.db"
    fi
else
    print_warn "No se pudo inicializar la base de datos (puede que falten dependencias)"
fi

# ============================================================
# 7. TEST FINAL
# ============================================================

print_step "7/7 — Test final"

# Detectar IP del servidor (sin grep -P para mayor compatibilidad)
SERVER_IP=$(ip -4 addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | cut -d/ -f1 | head -1)
if [[ -z "$SERVER_IP" ]]; then
    SERVER_IP="localhost"
    print_warn "No se pudo detectar la IP del servidor en la LAN"
fi

# Verificar que se puede importar la app (sin arrancar Flask)
if python3 -c "from app import app" 2>&1; then
    print_ok "Módulos Python cargan correctamente"
else
    print_warn "No se pudo importar la app (puede ser por dependencias faltantes)"
    print_info "Revisa que el entorno virtual esté activo: source .venv/bin/activate"
fi

print_info "Para arrancar MisGastos:"
echo "    cd $PROJECT_DIR"
echo "    ./scripts/start-misgastos.sh"
echo
print_info "URLs de acceso:"
echo "    Local:        http://localhost:5000"
echo "    LAN (tablet): http://$SERVER_IP:5000"
echo
print_info "Para parar:"
echo "    ./scripts/stop-misgastos.sh"

# ============================================================
# RESUMEN FINAL
# ============================================================

echo
echo -e "${GREEN}${BOLD}✅ Instalación completada${NC}"
echo
echo "Resumen:"
echo "  • Proyecto:        $PROJECT_DIR"
echo "  • Entorno virtual: $VENV_DIR"
if [[ -f "$MODEL_PATH" && -f "$MMPROJ_PATH" ]]; then
    echo "  • Modelo:          $MODEL_PATH"
else
    echo -e "  • Modelo:          ${RED}PENDIENTE DE DESCARGA${NC}"
fi
[[ "$SKIP_SYSTEMD" == "false" ]] && [[ -f "$SERVICE_DST" ]] && echo "  • Servicio:        $SERVICE_DST"
echo "  • Base de datos:   $PROJECT_DIR/data/gastos.db"
echo "  • IP del servidor: $SERVER_IP"
echo
echo -e "${BOLD}Siguiente paso:${NC} ./scripts/start-misgastos.sh"
echo

if [[ "$NEED_MODEL" == "true" ]]; then
    echo -e "${YELLOW}${BOLD}⚠ Acción requerida:${NC}"
    echo "  Falta descargar el modelo. Opción:"
    echo "    ./scripts/install.sh --with-model"
    echo "  O descarga manual desde:"
    echo "    https://huggingface.co/unsloth/Qwen3.5-9B-GGUF"
    echo
fi

exit 0
