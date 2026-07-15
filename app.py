"""
MisGastos — Aplicación principal Flask
Contabilización de gastos familiares
"""
import os
import sqlite3
import uuid
import json
import calendar
import shutil
import requests
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, send_file, jsonify
from werkzeug.utils import secure_filename
from models.schema import get_db, init_db
from services.ocr import extract_ticket
from services.classifier import clasificar_por_items, clasificar_por_comercio, clasificar_por_comercio_override
from services.excel import import_excel, export_excel
from services.image_processor import rotate_image, enhance_image
from config import UPLOAD_DIR, FLASK_HOST, FLASK_PORT, DB_PATH, CATEGORIES

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# ============================================================
# SETTINGS HELPERS
# ============================================================

def get_setting(key: str, default: str = "") -> str:
    """Leer un ajuste de la DB."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key: str, value: str):
    """Escribir un ajuste en la DB."""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def is_deep_analysis_enabled() -> bool:
    """¿Está activado el análisis profundo (items + products)?"""
    return get_setting('deep_analysis', 'false') == 'true'

def is_category_analysis_enabled() -> bool:
    """¿Está activado el análisis de categorías por items?"""
    return get_setting('category_analysis', 'false') == 'true'

def is_thinking_enabled() -> bool:
    """¿Está activado el razonamiento del modelo?"""
    return get_setting('enable_thinking', 'true') == 'true'


# Mapeo de nombres de categoría a IDs (para sugerencia del VLM)
CATEGORY_NAME_TO_ID = {
    "Comida": 1, "Ropa": 2, "Farmacia": 3, "Carburante": 4, "Banco": 5, "Otros": 6,
}


# ============================================================
# JINJA FILTERS
# ============================================================

MESES_ES = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic'
]


@app.template_filter('format_date_es')
def format_date_es(value):
    """Convert 'YYYY-MM-DD' → '6 jul 2026'. Acepta str o datetime.
    Maneja tanto "YYYY-MM-DD HH:MM:SS" (SQLite) como "YYYY-MM-DDTHH:MM:SS" (ISO)."""
    if not value:
        return ''
    if isinstance(value, datetime):
        return f"{value.day} {MESES_ES[value.month - 1]} {value.year}"
    if isinstance(value, str):
        # Manejar "YYYY-MM-DD HH:MM:SS" (SQLite) y "YYYY-MM-DDTHH:MM:SS" (ISO)
        value = value.split(' ')[0].split('T')[0]
    try:
        d = datetime.strptime(value, '%Y-%m-%d')
        return f"{d.day} {MESES_ES[d.month - 1]} {d.year}"
    except (ValueError, TypeError):
        return value


# ============================================================
# INICIALIZACIÓN
# ============================================================

with app.app_context():
    init_db()


# ============================================================
# RUTAS PRINCIPALES
# ============================================================

@app.route("/")
def dashboard():
    """Página principal — Dashboard con resumen del mes actual."""
    conn = get_db()
    c = conn.cursor()

    now = date.today()
    last_day = calendar.monthrange(now.year, now.month)[1]
    month_start = f"{now.year}-{now.month:02d}-01"
    month_end = f"{now.year}-{now.month:02d}-{last_day:02d}"

    # Gastos del mes
    c.execute(
        "SELECT SUM(total) FROM transactions WHERE kind='expense' AND date >= ? AND date <= ?",
        (month_start, month_end)
    )
    total_mes = c.fetchone()[0] or 0

    # Gastos mes anterior
    if now.month > 1:
        prev_start = f"{now.year}-{now.month-1:02d}-01"
        prev_end = f"{now.year}-{now.month:02d}-01"
    else:
        prev_start = f"{now.year-1}-12-01"
        prev_end = f"{now.year}-01-01"
    c.execute(
        "SELECT SUM(total) FROM transactions WHERE kind='expense' AND date >= ? AND date < ?",
        (prev_start, prev_end)
    )
    total_anterior = c.fetchone()[0] or 0

    # Gastos por categoría
    c.execute("""
        SELECT c.name, c.color, SUM(t.total) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.kind='expense' AND t.date >= ? AND t.date <= ?
        GROUP BY t.category_id
        ORDER BY SUM(t.total) DESC
    """, (month_start, month_end))
    por_categoria = c.fetchall()

    # Cola de revisión
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    # Presupuestos del mes
    budgets = []
    c.execute("""
        SELECT b.limit_val, b.category_id, c.name, c.color
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
        WHERE b.month_col = ?
    """, (month_start[:7],))
    for b in c.fetchall():
        c.execute("""
            SELECT COALESCE(SUM(t.total), 0)
            FROM transactions t
            WHERE t.kind='expense' AND t.category_id = ? AND t.date >= ? AND t.date <= ?
        """, (b['category_id'], month_start, month_end))
        spent = c.fetchone()[0]
        if b['limit_val'] > 0:
            pct = (spent / b['limit_val']) * 100
            budgets.append({
                'name': b['name'],
                'color': b['color'],
                'limit': b['limit_val'],
                'spent': spent,
                'pct': pct,
                'over': pct > 100
            })

    # Num tickets del mes
    c.execute(
        "SELECT COUNT(*) FROM transactions WHERE kind='expense' AND date >= ? AND date <= ?",
        (month_start, month_end)
    )
    num_tickets = c.fetchone()[0] or 0

    # Últimos 5 gastos
    c.execute("""
        SELECT t.*, c.name as category_name, m.name as merchant_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN merchants m ON t.merchant_id = m.id
        WHERE t.kind='expense'
        ORDER BY t.date DESC, t.id DESC
        LIMIT 5
    """)
    ultimos = c.fetchall()

    conn.close()

    return render_template(
        "stats/dashboard.html",
        total_mes=total_mes,
        por_categoria=por_categoria,
        total_anterior=total_anterior,
        review_queue_count=review_count,
        budgets=budgets,
        ultimos=ultimos,
        num_tickets=num_tickets,
    )


@app.route("/scan")
def scan():
    """Página de upload de foto."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()

    return render_template("scan/upload.html", review_queue_count=review_count)


@app.route("/scan/upload", methods=["POST"])
def scan_upload():
    """Procesar imagen subida → OCR → guardar en scans → mostrar edición."""
    if "image" not in request.files:
        return "No se recibió imagen", 400

    f = request.files["image"]
    if f.filename == "":
        return "Filename vacío", 400

    if not allowed_file(f.filename):
        return "Formato no válido", 400

    # Guardar temporalmente
    ext = os.path.splitext(f.filename)[1]
    original_filename = f"{uuid.uuid4()}{ext}"
    original_path = os.path.join(UPLOAD_DIR, original_filename)
    f.save(original_path)

    # Procesar OCR (deep_analysis según ajuste)
    try:
        deep = is_deep_analysis_enabled()
        result = extract_ticket(original_path, deep_analysis=deep, enable_thinking=is_thinking_enabled())
    except ValueError as e:
        # Imagen inválida
        os.remove(original_path)
        return render_template(
            "scan/upload.html",
            error=str(e),
            review_queue_count=0,
        )

    # Auto-clasificar — cascada v8 (FIX 25B-2)
    # Nivel 0: sugerencia del VLM (si category_analysis activado)
    # Nivel 1: override por comercio
    # Nivel 2: buscar en DB
    # Nivel 3: heurística por items (solo en modo profundo)
    # Nivel 4: fallback
    auto_cat_id = None

    if is_category_analysis_enabled() and result.categoria_sugerida:
        cat_name = result.categoria_sugerida.strip()
        auto_cat_id = CATEGORY_NAME_TO_ID.get(cat_name)

    if auto_cat_id is None and result.comercio:
        auto_cat_id = clasificar_por_comercio_override(result.comercio)

    if auto_cat_id is None and result.comercio:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?", (f"%{result.comercio}%",))
        row = c.fetchone()
        conn.close()
        if row and row['default_category_id']:
            auto_cat_id = row['default_category_id']

    if auto_cat_id is None and result.items:
        auto_cat_id, _ = clasificar_por_items(result.items)

    if auto_cat_id is None:
        auto_cat_id = 6  # Otros

    # Guardar en scans
    conn = get_db()
    c = conn.cursor()
    low_conf = result.overall_confidence < 0.7
    status = "pending" if low_conf else "reviewed"

    c.execute("""
        INSERT INTO scans (model, raw_output, confidence, duration_ms, status, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("qwen3.5-9b", result.raw_output, result.overall_confidence, result.duration_ms, status, original_filename))
    scan_id = c.lastrowid

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    conn.commit()
    conn.close()

    base_template = "partial.html" if "HX-Request" in request.headers or "Hx-Request" in request.headers else "base.html"

    # Todas las categorías disponibles
    all_categories = []
    for cat in CATEGORIES:
        all_categories.append({'id': cat[0], 'name': cat[1], 'parent_id': cat[2], 'color': cat[3]})

    return render_template(
        "scan/edit.html",
        base_template=base_template,
        scan_id=scan_id,
        image_filename=original_filename,
        fecha=result.fecha,
        comercio=result.comercio,
        card_last4=result.card_last4,
        items=result.items,
        total=result.total,
        metodo_pago=result.metodo_pago,
        overall_confidence=result.overall_confidence,
        field_confidence=result.field_confidence,
        auto_category=auto_cat_id,
        all_categories=all_categories,
        error=result.error,
        review_queue_count=review_count,
    )


@app.route("/scan/upload-batch", methods=["POST"])
def scan_upload_batch():
    """Procesar múltiples tickets a la vez, guardándolos automáticamente."""
    if "images" not in request.files:
        return "No se recibieron imágenes", 400

    files = request.files.getlist("images")
    if not files or files[0].filename == "":
        return "No se seleccionaron archivos", 400

    deep = is_deep_analysis_enabled()
    thinking = is_thinking_enabled()

    all_categories = []
    for cat in CATEGORIES:
        all_categories.append({'id': cat[0], 'name': cat[1], 'color': cat[3]})

    results = []

    for i, f in enumerate(files):
        if not f or f.filename == "":
            continue

        # Guardar imagen
        ext = os.path.splitext(f.filename)[1] or ".jpg"
        original_filename = f"{uuid.uuid4()}{ext}"
        original_path = os.path.join(UPLOAD_DIR, original_filename)
        f.save(original_path)

        # Procesar OCR
        try:
            result = extract_ticket(original_path, deep_analysis=deep, enable_thinking=thinking)
        except Exception as e:
            results.append({
                'file': f.filename,
                'status': 'error',
                'error': str(e)[:200],
            })
            continue

        # Clasificar
        auto_cat_id = None
        if result.categoria_sugerida:
            auto_cat_id = CATEGORY_NAME_TO_ID.get(result.categoria_sugerida.strip())
        if auto_cat_id is None and result.comercio:
            auto_cat_id = clasificar_por_comercio_override(result.comercio)
        if auto_cat_id is None and result.comercio:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?", (f"%{result.comercio}%",))
            row = c.fetchone()
            conn.close()
            if row and row['default_category_id']:
                auto_cat_id = row['default_category_id']
        if auto_cat_id is None and result.items:
            auto_cat_id, _ = clasificar_por_items(result.items)
        if auto_cat_id is None:
            auto_cat_id = 6  # Otros

        # Guardar en DB automáticamente (sin revisión)
        conn = get_db()
        c = conn.cursor()

        # Crear merchant
        merchant_id = None
        if result.comercio:
            c.execute("SELECT id FROM merchants WHERE name = ?", (result.comercio,))
            mrow = c.fetchone()
            if mrow:
                merchant_id = mrow['id']
            else:
                try:
                    c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?, ?)",
                              (result.comercio, auto_cat_id))
                    merchant_id = c.lastrowid
                except sqlite3.IntegrityError:
                    merchant_id = None

        # Insertar transacción
        c.execute("""
            INSERT INTO transactions (kind, date, description, merchant_id, total, payment_method, category_id, source, ocr_confidence, field_confidence, card_last4, vehicle)
            VALUES ('expense', ?, ?, ?, ?, ?, ?, 'ocr', ?, ?, ?, ?)
        """, (
            result.fecha or date.today().isoformat(),
            result.comercio or '',
            merchant_id,
            result.total or 0,
            result.metodo_pago,
            auto_cat_id,
            result.overall_confidence,
            json.dumps(result.field_confidence),
            result.card_last4,
            None,
        ))
        txn_id = c.lastrowid

        # Guardar scan
        c.execute("""
            INSERT INTO scans (model, raw_output, confidence, duration_ms, status, transaction_id, image_path)
            VALUES (?, ?, ?, ?, 'saved', ?, ?)
        """, ("qwen3.5-9b", result.raw_output, result.overall_confidence, result.duration_ms, txn_id, original_filename))

        # Guardar items si deep analysis
        if deep and result.items:
            for item in result.items:
                desc = item.get("descripcion", "").strip()
                try:
                    price = float(item.get("precio", 0))
                except (ValueError, TypeError):
                    price = 0
                try:
                    qty = float(item.get("cantidad", 1))
                except (ValueError, TypeError):
                    qty = 1.0
                if desc and price > 0:
                    c.execute("""
                        INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (txn_id, desc, qty, price, auto_cat_id))
                    c.execute("""
                        INSERT INTO products (name, unit_price, date, transaction_id, merchant_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (desc, price, result.fecha or date.today().isoformat(), txn_id, merchant_id))

        conn.commit()
        conn.close()

        # Encontrar nombre de categoría
        cat_name = next((c['name'] for c in all_categories if c['id'] == auto_cat_id), 'Otros')

        results.append({
            'file': f.filename,
            'status': 'saved',
            'comercio': result.comercio or '(desconocido)',
            'total': result.total or 0,
            'categoria': cat_name,
            'confidence': result.overall_confidence,
            'txn_id': txn_id,
        })

    return render_template("scan/batch_result.html", results=results, review_queue_count=0)


@app.route("/scan/save", methods=["POST"])
def scan_save():
    """Guardar ticket confirmado (desde OCR o edición)."""
    # Validar total y categoría antes de convertir
    try:
        total_str = request.form.get("total", "0")
        total = float(total_str) if total_str else 0.0
    except (ValueError, TypeError):
        return "Total inválido", 400
    try:
        category_id = int(request.form.get("category", 0))
    except (ValueError, TypeError):
        return "Categoría inválida", 400

    data = {
        "scan_id": request.form.get("scan_id"),
        "date": request.form.get("date"),
        "merchant": request.form.get("merchant", "").strip(),
        "card_last4": request.form.get("card_last4", "").strip() or None,
        "vehicle": request.form.get("vehicle", "").strip() or None,
        "total": total,
        "category_id": category_id,
        "payment_method": request.form.get("payment_method", ""),
    }

    # Items del formulario (si se enviaron)
    item_descs = request.form.getlist("item_desc[]")
    item_prices = request.form.getlist("item_price[]")
    item_quants = request.form.getlist("item_qty[]")

    # Validación
    if not data["date"] or data["total"] <= 0 or data["total"] > 10000:
        return "Datos inválidos", 400

    conn = get_db()
    c = conn.cursor()

    # Tracking de correcciones
    original_values = {}
    scan_row = None
    if data["scan_id"]:
        c.execute("SELECT raw_output, confidence FROM scans WHERE id = ?", (data["scan_id"],))
        scan_row = c.fetchone()
        if scan_row and scan_row['raw_output']:
            try:
                original_values = json.loads(scan_row['raw_output'])
            except (json.JSONDecodeError, TypeError):
                pass

    # Recuperar confianza del OCR para la transacción
    ocr_confidence = scan_row['confidence'] if scan_row else None
    field_confidence_json = json.dumps(original_values.get("field_confidence", {})) if original_values else None

    # Q3: Validar que la categoría existe
    c.execute("SELECT id FROM categories WHERE id = ?", (data["category_id"],))
    if not c.fetchone():
        conn.close()
        return "Categoría no existe", 400

    # Buscar o crear merchant (N4: no crear merchant fantasma si name="")
    merchant_id = None
    if data["merchant"]:
        c.execute("SELECT id FROM merchants WHERE name = ?", (data["merchant"],))
        row = c.fetchone()
        if row:
            merchant_id = row['id']
        else:
            c.execute(
                "INSERT INTO merchants (name, default_category_id) VALUES (?, ?)",
                (data["merchant"], data["category_id"])
            )
            merchant_id = c.lastrowid

    # Insertar transacción
    c.execute("""
        INSERT INTO transactions (kind, date, merchant_id, total, payment_method, category_id, source, ocr_confidence, field_confidence, card_last4, vehicle)
        VALUES ('expense', ?, ?, ?, ?, ?, 'ocr', ?, ?, ?, ?)
    """, (
        data["date"], merchant_id, data["total"],
        data["payment_method"] if data["payment_method"] else None,
        data["category_id"],
        ocr_confidence, field_confidence_json,
        data["card_last4"],
        data["vehicle"]
    ))
    txn_id = c.lastrowid

    # Solo guardar items si el análisis profundo está activado
    deep = is_deep_analysis_enabled()
    items_sum = 0.0
    if deep:
        for i in range(len(item_descs)):
            desc = item_descs[i].strip()
            try:
                price = float(item_prices[i])
            except (ValueError, IndexError):
                price = 0
            try:
                qty = float(item_quants[i])
            except (ValueError, IndexError):
                qty = 1.0
            if desc and price > 0:
                items_sum += price * qty
                c.execute("""
                    INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (txn_id, desc, qty, price, data["category_id"]))

    # Guardar items también en tabla products (historial de precios)
    if deep:
        for i in range(len(item_descs)):
            desc = item_descs[i].strip()
            try:
                price = float(item_prices[i])
            except (ValueError, IndexError):
                price = 0
            try:
                qty = float(item_quants[i])
            except (ValueError, IndexError):
                qty = 1.0
            if desc and price > 0:
                # precio del OCR = precio UNITARIO (no total)
                # Para gasolina: precio = €/litro, cantidad = litros
                unit_price = price
                c.execute("""
                    INSERT INTO products (name, unit_price, date, transaction_id, merchant_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (desc, unit_price, data["date"], txn_id, merchant_id))

    # Actualizar scan → vincular a transacción + marcar como guardado
    if data["scan_id"]:
        c.execute("""
            UPDATE scans SET transaction_id = ?, status = 'saved' WHERE id = ?
        """, (txn_id, data["scan_id"]))

    # Validar que la suma de items cuadra con el total (si hay items)
    if item_descs and items_sum > 0:
        if abs(items_sum - data["total"]) > 0.05:
            # No bloquear, pero registrar como corrección
            c.execute("""
                INSERT INTO corrections (transaction_id, field, original_value, corrected_value)
                VALUES (?, 'items_total_mismatch', ?, ?)
            """, (txn_id, str(items_sum), str(data["total"])))

    # Log correcciones de campos editados
    _log_corrections(c, txn_id, original_values, data)

    conn.commit()
    conn.close()

    return redirect(url_for("scan"))


# ============================================================
# GESTIÓN DE GASTOS
# ============================================================

@app.route("/expenses")
def expenses():
    """Explorador de archivos: año → mes → tickets."""
    conn = get_db()
    c = conn.cursor()

    year = request.args.get("year")
    month = request.args.get("month")

    meses_es = {'01': 'Enero', '02': 'Febrero', '03': 'Marzo', '04': 'Abril',
                '05': 'Mayo', '06': 'Junio', '07': 'Julio', '08': 'Agosto',
                '09': 'Septiembre', '10': 'Octubre', '11': 'Noviembre', '12': 'Diciembre'}

    if year and month:
        # Vista de tickets de un mes concreto
        month_start = f"{year}-{month}-01"
        if month == "12":
            next_month_start = f"{int(year)+1}-01-01"
        else:
            next_month_start = f"{year}-{int(month)+1:02d}-01"

        c.execute("""
            SELECT t.*, cat.name as category_name, cat.color as category_color,
                m.name as merchant_name, s.image_path as scan_image
            FROM transactions t
            LEFT JOIN categories cat ON t.category_id = cat.id
            LEFT JOIN merchants m ON t.merchant_id = m.id
            LEFT JOIN scans s ON s.transaction_id = t.id AND s.status = 'saved'
            WHERE t.kind='expense' AND t.date >= ? AND t.date < ?
            ORDER BY t.date DESC, t.id DESC
        """, (month_start, next_month_start))
        expenses_list = c.fetchall()

        total_mes = sum(e['total'] for e in expenses_list) if expenses_list else 0

        c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
        review_count = c.fetchone()[0] or 0
        conn.close()

        return render_template("expenses/list.html",
            view="month",
            year=year,
            month=month,
            month_name=meses_es.get(month, month),
            expenses_list=expenses_list,
            total_mes=total_mes,
            review_queue_count=review_count,
        )

    elif year:
        # Vista de meses de un año concreto
        c.execute("""
            SELECT DISTINCT substr(date, 1, 7) as year_month
            FROM transactions
            WHERE kind='expense' AND substr(date, 1, 4) = ?
            ORDER BY year_month DESC
        """, (year,))
        months_data = c.fetchall()

        months = []
        for md in months_data:
            m = md['year_month'][5:7]
            c.execute("""
                SELECT COUNT(*), SUM(total)
                FROM transactions
                WHERE kind='expense' AND substr(date, 1, 7) = ?
            """, (md['year_month'],))
            row = c.fetchone()
            months.append({
                'month': m,
                'count': row[0],
                'total': row[1] or 0
            })

        c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
        review_count = c.fetchone()[0] or 0
        conn.close()

        return render_template("expenses/list.html",
            view="months",
            year=year,
            months=months,
            meses_es=meses_es,
            review_queue_count=review_count,
        )

    else:
        # Vista raíz: lista de años
        c.execute("""
            SELECT DISTINCT substr(date, 1, 4) as year
            FROM transactions
            WHERE kind='expense'
            ORDER BY year DESC
        """)
        years_data = c.fetchall()

        years = []
        for yd in years_data:
            y = yd['year']
            c.execute("""
                SELECT COUNT(*), SUM(total)
                FROM transactions
                WHERE kind='expense' AND substr(date, 1, 4) = ?
            """, (y,))
            row = c.fetchone()
            years.append({
                'year': y,
                'count': row[0],
                'total': row[1] or 0
            })

        c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
        review_count = c.fetchone()[0] or 0
        conn.close()

        return render_template("expenses/list.html",
            view="years",
            years=years,
            review_queue_count=review_count,
        )



@app.route("/expense/<int:txn_id>")
def expense_detail(txn_id):
    """Detalle de un gasto individual con sus items."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT t.*, c.name as category_name, c.color as category_color, m.name as merchant_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN merchants m ON t.merchant_id = m.id
        WHERE t.id = ?
    """, (txn_id,))
    txn = c.fetchone()
    if not txn:
        return "Gasto no encontrado", 404

    # Items
    c.execute("SELECT * FROM transaction_items WHERE transaction_id = ? ORDER BY id", (txn_id,))
    items = c.fetchall()
    items_total = sum(item['unit_price'] * item['quantity'] for item in items)

    # Categorías (para edición)
    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    categories = c.fetchall()

    # Cola de revisión
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    conn.close()

    return render_template(
        "expenses/detail.html",
        txn=txn,
        items=items,
        items_total=items_total,
        categories=categories,
        review_queue_count=review_count,
    )


@app.route("/expense/<int:txn_id>/edit", methods=["GET", "POST"])
def edit_expense(txn_id):
    """Editar un gasto ya guardado."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT t.*, m.name as merchant_name
        FROM transactions t
        LEFT JOIN merchants m ON t.merchant_id = m.id
        WHERE t.id = ?
    """, (txn_id,))
    txn = c.fetchone()
    if not txn:
        return "Gasto no encontrado", 404

    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    categories = c.fetchall()

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    if request.method == "POST":
        new_date = request.form.get("date", txn['date'])
        try:
            new_total = float(request.form.get("total", txn['total']))
        except (ValueError, TypeError):
            new_total = txn['total']
        new_category = int(request.form.get("category", txn['category_id']))
        new_merchant = request.form.get("merchant", txn['merchant_name']).strip()
        new_payment = request.form.get("payment_method", txn['payment_method']) or None
        new_card_last4 = request.form.get("card_last4", txn['card_last4'] or "")

        # Q3: Validar categoría
        c.execute("SELECT id FROM categories WHERE id = ?", (new_category,))
        if not c.fetchone():
            conn.close()
            return "Categoría no existe", 400

        # Log correcciones
        if new_date != txn['date']:
            c.execute("INSERT INTO corrections (transaction_id, field, original_value, corrected_value) VALUES (?, 'date', ?, ?)", (txn_id, txn['date'], new_date))
        if abs(new_total - txn['total']) > 0.01:
            c.execute("INSERT INTO corrections (transaction_id, field, original_value, corrected_value) VALUES (?, 'total', ?, ?)", (txn_id, str(txn['total']), str(new_total)))
        if new_category != txn['category_id']:
            c.execute("INSERT INTO corrections (transaction_id, field, original_value, corrected_value) VALUES (?, 'category_id', ?, ?)", (txn_id, str(txn['category_id']), str(new_category)))

        # Actualizar merchant
        c.execute("SELECT id FROM merchants WHERE name = ?", (new_merchant,))
        mrow = c.fetchone()
        if new_merchant:
            if mrow:
                if mrow['id'] != txn['merchant_id']:
                    c.execute("UPDATE transactions SET merchant_id = ? WHERE id = ?", (mrow['id'], txn_id))
            else:
                c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?, ?)", (new_merchant, new_category))
                new_merchant_id = c.lastrowid
                c.execute("UPDATE transactions SET merchant_id = ? WHERE id = ?", (new_merchant_id, txn_id))
        else:
            c.execute("UPDATE transactions SET merchant_id = NULL WHERE id = ?", (txn_id,))

        # Actualizar transacción
        c.execute("""
            UPDATE transactions SET date = ?, total = ?, category_id = ?, payment_method = ?, card_last4 = ?
            WHERE id = ?
        """, (new_date, new_total, new_category, new_payment, new_card_last4 or None, txn_id))

        # Marcar como editado
        c.execute("UPDATE transactions SET manual_edited = 1 WHERE id = ? AND manual_edited = 0", (txn_id,))

        conn.commit()
        conn.close()
        return redirect(url_for("expense_detail", txn_id=txn_id))

    conn.close()

    return render_template(
        "expenses/edit.html",
        txn=txn,
        categories=categories,
        review_queue_count=review_count,
    )


@app.route("/expense/<int:txn_id>/delete", methods=["POST"])
def delete_expense(txn_id):
    """Borrar un gasto (usa DELETE con CASCADE para items)."""
    conn = get_db()
    c = conn.cursor()

    # Q1: Desvincular scans antes de borrar (evita FK constraint failure)
    # O8: Obtener image_path para borrar del disco
    c.execute("SELECT image_path FROM scans WHERE transaction_id = ?", (txn_id,))
    scan_row = c.fetchone()
    image_path = scan_row['image_path'] if scan_row else None

    c.execute("UPDATE scans SET transaction_id = NULL, status = 'discarded' WHERE transaction_id = ?", (txn_id,))
    c.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    conn.commit()
    conn.close()

    # O8: Borrar imagen del disco y todas sus variantes
    if image_path:
        full_path = os.path.join(UPLOAD_DIR, image_path)
        if os.path.exists(full_path):
            os.remove(full_path)

        # Borrar variantes: _processed, _current, _enhanced, y variantes de rotación
        base, _ = os.path.splitext(full_path)
        variants = ["_processed", "_current", "_enhanced", "_rot90", "_rot180", "_rot270"]
        for suffix in variants:
            variant_path = f"{base}{suffix}.jpg"
            if os.path.exists(variant_path):
                os.remove(variant_path)

    return redirect(url_for("expenses"))


# ============================================================
# COLA DE REVISIÓN
# ============================================================

@app.route("/scan/review-queue")
def review_queue():
    """Cola de revisión manual."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scans WHERE status = 'pending' ORDER BY confidence ASC")
    raw_scans = c.fetchall()
    conn.close()

    # Parsear raw_output JSON para extraer comercio y otros campos
    scans = []
    for scan in raw_scans:
        scan_dict = dict(scan)
        if scan_dict.get('raw_output'):
            try:
                data = json.loads(scan_dict['raw_output'])
                scan_dict['parsed_merchant'] = data.get('comercio')
                scan_dict['parsed_confidence'] = data.get('overall_confidence', 0)
            except (json.JSONDecodeError, TypeError):
                scan_dict['parsed_merchant'] = None
                scan_dict['parsed_confidence'] = None
        else:
            scan_dict['parsed_merchant'] = None
            scan_dict['parsed_confidence'] = None
        scans.append(scan_dict)

    return render_template("scan/review_queue.html", scans=scans, review_queue_count=len(scans))


@app.route("/scan/<int:scan_id>/discard", methods=["POST"])
def discard_scan(scan_id):
    """Descartar un scan de la cola de revisión."""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET status = 'discarded' WHERE id = ?", (scan_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("review_queue"))


@app.route("/scan/<int:scan_id>/edit")
def edit_pending_scan(scan_id):
    """Editar un scan pendiente de la cola de revisión."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scans WHERE id = ? AND status = 'pending'", (scan_id,))
    scan = c.fetchone()
    if not scan:
        return "Scan no encontrado o ya procesado", 404

    # Parsear raw_output
    try:
        data = json.loads(scan['raw_output']) if scan['raw_output'] else {}
    except json.JSONDecodeError:
        data = {}

    # Auto-clasificar — cascada v8 (FIX 25B-2)
    auto_cat_id = None
    comercio = data.get("comercio")
    items = data.get("items", [])

    if is_category_analysis_enabled() and data.get("categoria_sugerida"):
        cat_name = data["categoria_sugerida"].strip()
        auto_cat_id = CATEGORY_NAME_TO_ID.get(cat_name)

    if auto_cat_id is None and comercio:
        auto_cat_id = clasificar_por_comercio_override(comercio)

    if auto_cat_id is None and comercio:
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?", (f"%{comercio}%",))
        mrow = c.fetchone()
        if mrow and mrow['default_category_id']:
            auto_cat_id = mrow['default_category_id']

    if auto_cat_id is None and items:
        auto_cat_id, _ = clasificar_por_items(items)

    if auto_cat_id is None:
        auto_cat_id = 6  # Otros

    # Todas las categorías disponibles
    all_categories = []
    from config import CATEGORIES
    for cat in CATEGORIES:
        all_categories.append({'id': cat[0], 'name': cat[1], 'parent_id': cat[2], 'color': cat[3]})

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()

    return render_template(
        "scan/edit.html",
        base_template="base.html",
        scan_id=scan_id,
        image_filename=scan['image_path'] if scan['image_path'] else None,
        fecha=data.get("fecha"),
        comercio=data.get("comercio"),
        card_last4=data.get("card_last4"),
        items=items,
        total=data.get("total"),
        metodo_pago=data.get("metodo_pago"),
        overall_confidence=scan['confidence'] or 0.0,
        field_confidence=data.get("field_confidence", {}),
        auto_category=auto_cat_id,
        all_categories=all_categories,
        error=None,
        review_queue_count=review_count,
    )


# ============================================================
# IMÁGENES
# ============================================================

@app.route("/scan/image/<filename>")
def scan_image(filename):
    """Servir imagen de un scan (prioridad: current > processed > original)."""
    safe = secure_filename(filename)
    if not safe:
        return "Filename inválido", 400

    base, _ = os.path.splitext(safe)
    # Prioridad: _current > _processed > original
    for suffix in ('_current', '_processed'):
        candidate = f"{base}{suffix}.jpg"
        if os.path.exists(os.path.join(UPLOAD_DIR, candidate)):
            return send_from_directory(UPLOAD_DIR, candidate)
    return send_from_directory(UPLOAD_DIR, safe)


# ============================================================
# ROTACION / ENHANCE DE IMAGEN
# ============================================================

@app.route("/scan/<int:scan_id>/rotate/<int:degrees>", methods=["POST"])
def rotate_scan(scan_id, degrees):
    """Rotar la imagen de un scan y re-procesar OCR."""
    if degrees not in (90, 180, 270):
        return "Grados inválidos", 400

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT image_path FROM scans WHERE id = ?", (scan_id,))
    scan = c.fetchone()
    if not scan:
        conn.close()
        return "Scan no encontrado", 404

    image_filename = scan['image_path']
    if not image_filename:
        conn.close()
        return "Scan sin imagen", 400

    image_path = os.path.join(UPLOAD_DIR, image_filename)
    if not os.path.exists(image_path):
        conn.close()
        return "Imagen no encontrada en disco", 404

    try:
        rotated_path = rotate_image(image_path, degrees)
        rotated_filename = os.path.basename(rotated_path)
        c.execute("UPDATE scans SET image_path = ? WHERE id = ?",
                  (rotated_filename, scan_id))
        conn.commit()
    except ValueError as e:
        conn.close()
        return str(e), 400
    conn.close()

    try:
        result = extract_ticket(rotated_path, deep_analysis=is_deep_analysis_enabled(), enable_thinking=is_thinking_enabled())
    except Exception as e:
        return f"Error al re-procesar: {e}", 500

    # Auto-clasificar v8 (FIX 25B-2)
    auto_cat_id = None
    if is_category_analysis_enabled() and result.categoria_sugerida:
        cat_name = result.categoria_sugerida.strip()
        auto_cat_id = CATEGORY_NAME_TO_ID.get(cat_name)
    if auto_cat_id is None and result.comercio:
        auto_cat_id = clasificar_por_comercio_override(result.comercio)
    if auto_cat_id is None and result.comercio:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?",
                  (f"%{result.comercio}%",))
        row = c.fetchone()
        conn.close()
        if row and row['default_category_id']:
            auto_cat_id = row['default_category_id']
    if auto_cat_id is None and result.items:
        auto_cat_id, _ = clasificar_por_items(result.items)
    if auto_cat_id is None:
        auto_cat_id = 6  # Otros

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET raw_output = ?, confidence = ?, duration_ms = ? WHERE id = ?",
              (result.raw_output, result.overall_confidence, result.duration_ms, scan_id))
    status = "pending" if result.overall_confidence < 0.7 else "reviewed"
    c.execute("UPDATE scans SET status = ? WHERE id = ?", (status, scan_id))
    conn.commit()
    conn.close()

    return redirect(url_for("edit_pending_scan", scan_id=scan_id))


@app.route("/scan/<int:scan_id>/enhance", methods=["POST"])
def enhance_scan(scan_id):
    """Mejorar contraste de la imagen y re-procesar OCR."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT image_path FROM scans WHERE id = ?", (scan_id,))
    scan = c.fetchone()
    if not scan:
        conn.close()
        return "Scan no encontrado", 404

    image_filename = scan['image_path']
    if not image_filename:
        conn.close()
        return "Scan sin imagen", 400

    image_path = os.path.join(UPLOAD_DIR, image_filename)
    if not os.path.exists(image_path):
        conn.close()
        return "Imagen no encontrada en disco", 404

    try:
        enhanced_path = enhance_image(image_path)
        enhanced_filename = os.path.basename(enhanced_path)
        c.execute("UPDATE scans SET image_path = ? WHERE id = ?",
                  (enhanced_filename, scan_id))
        conn.commit()
    except ValueError as e:
        conn.close()
        return str(e), 400
    conn.close()

    try:
        result = extract_ticket(enhanced_path, deep_analysis=is_deep_analysis_enabled(), enable_thinking=is_thinking_enabled())
    except Exception as e:
        return f"Error al re-procesar: {e}", 500

    # Auto-clasificar v8 (FIX 25B-2)
    auto_cat_id = None
    if is_category_analysis_enabled() and result.categoria_sugerida:
        cat_name = result.categoria_sugerida.strip()
        auto_cat_id = CATEGORY_NAME_TO_ID.get(cat_name)
    if auto_cat_id is None and result.comercio:
        auto_cat_id = clasificar_por_comercio_override(result.comercio)
    if auto_cat_id is None and result.comercio:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?",
                  (f"%{result.comercio}%",))
        row = c.fetchone()
        conn.close()
        if row and row['default_category_id']:
            auto_cat_id = row['default_category_id']
    if auto_cat_id is None and result.items:
        auto_cat_id, _ = clasificar_por_items(result.items)
    if auto_cat_id is None:
        auto_cat_id = 6  # Otros

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET raw_output = ?, confidence = ?, duration_ms = ? WHERE id = ?",
              (result.raw_output, result.overall_confidence, result.duration_ms, scan_id))
    status = "pending" if result.overall_confidence < 0.7 else "reviewed"
    c.execute("UPDATE scans SET status = ? WHERE id = ?", (status, scan_id))
    conn.commit()
    conn.close()

    return redirect(url_for("edit_pending_scan", scan_id=scan_id))


# ============================================================
# AJUSTES
# ============================================================

@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Página de ajustes."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()

    if request.method == "POST":
        # Guardar ajustes
        deep = request.form.get("deep_analysis") == "on"
        cat_analysis = request.form.get("category_analysis") == "on"
        thinking = request.form.get("enable_thinking") == "on"
        theme = request.form.get("theme", "light")
        set_setting('deep_analysis', 'true' if deep else 'false')
        set_setting('category_analysis', 'true' if cat_analysis else 'false')
        set_setting('enable_thinking', 'true' if thinking else 'false')
        set_setting('theme', theme)
        return redirect(url_for("settings"))

    # GET: mostrar formulario
    deep_enabled = is_deep_analysis_enabled()
    cat_analysis_enabled = is_category_analysis_enabled()
    thinking_enabled = is_thinking_enabled()
    current_theme = get_setting('theme', 'light')

    return render_template(
        "settings.html",
        deep_enabled=deep_enabled,
        cat_analysis_enabled=cat_analysis_enabled,
        thinking_enabled=thinking_enabled,
        current_theme=current_theme,
        review_queue_count=review_count,
    )


# ============================================================
# GESTION DE COMERCIOS
# ============================================================

@app.route("/settings/merchants", methods=["GET", "POST"])
def manage_merchants():
    """Gestionar lista de comercios: ver, editar categoría por defecto, añadir nuevos."""
    if request.method == "POST":
        merchant_id = request.form.get("merchant_id")
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id", 9)

        conn = get_db()
        c = conn.cursor()
        if merchant_id:
            c.execute("UPDATE merchants SET name=?, default_category_id=? WHERE id=?",
                       (name, category_id, merchant_id))
        else:
            try:
                c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?, ?)",
                           (name, category_id))
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()
        return redirect(url_for("manage_merchants"))

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT m.id, m.name, m.default_category_id, c.name as cat_name
        FROM merchants m
        LEFT JOIN categories c ON m.default_category_id = c.id
        ORDER BY m.name
    """)
    merchants = c.fetchall()
    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    categories = c.fetchall()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()

    return render_template("settings/merchants.html",
                           merchants=merchants,
                           categories=categories,
                           review_queue_count=review_count)


# ============================================================
# ENTRADA MANUAL DE TICKETS
# ============================================================

@app.route("/expense/new", methods=["GET", "POST"])
def new_expense_manual():
    """Añadir un gasto manualmente (sin OCR)."""
    conn = get_db()
    c = conn.cursor()

    all_categories = []
    for cat in CATEGORIES:
        all_categories.append({'id': cat[0], 'name': cat[1],
                              'parent_id': cat[2], 'color': cat[3]})

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    if request.method == "POST":
        try:
            total = float(request.form.get("total", 0))
        except (ValueError, TypeError):
            conn.close()
            return "Total inválido", 400
        try:
            category_id = int(request.form.get("category", 0))
        except (ValueError, TypeError):
            conn.close()
            return "Categoría inválida", 400

        date_val = request.form.get("date", "")
        merchant = request.form.get("merchant", "").strip()
        card_last4 = request.form.get("card_last4", "").strip() or None
        vehicle = request.form.get("vehicle", "").strip() or None
        payment_method = request.form.get("payment_method", "")
        description = request.form.get("description", "").strip()
        kind = request.form.get("kind", "expense")

        if not date_val or total <= 0 or total > 10000:
            conn.close()
            return "Datos inválidos", 400

        c.execute("SELECT id FROM categories WHERE id = ?", (category_id,))
        if not c.fetchone():
            conn.close()
            return "Categoría no existe", 400

        merchant_id = None
        if merchant:
            c.execute("SELECT id FROM merchants WHERE name = ?", (merchant,))
            row = c.fetchone()
            if row:
                merchant_id = row['id']
            else:
                c.execute(
                    "INSERT INTO merchants (name, default_category_id) VALUES (?, ?)",
                    (merchant, category_id)
                )
                merchant_id = c.lastrowid

        c.execute("""
            INSERT INTO transactions (kind, date, description, merchant_id, total, payment_method, category_id, source, card_last4, vehicle)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)
        """, (kind, date_val, description or None, merchant_id, total,
              payment_method if payment_method else None, category_id, card_last4, vehicle))
        txn_id = c.lastrowid

        item_descs = request.form.getlist("item_desc[]")
        item_prices = request.form.getlist("item_price[]")
        item_quants = request.form.getlist("item_qty[]")
        deep = is_deep_analysis_enabled()
        if deep:
            for i in range(len(item_descs)):
                desc = item_descs[i].strip()
                try:
                    price = float(item_prices[i])
                except (ValueError, IndexError):
                    price = 0
                try:
                    qty = float(item_quants[i])
                except (ValueError, IndexError):
                    qty = 1.0
                if desc and price > 0:
                    c.execute("""
                        INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (txn_id, desc, qty, price, category_id))

        # Guardar items también en tabla products (historial de precios)
        if deep:
            for i in range(len(item_descs)):
                desc = item_descs[i].strip()
                try:
                    price = float(item_prices[i])
                except (ValueError, IndexError):
                    price = 0
                try:
                    qty = float(item_quants[i])
                except (ValueError, IndexError):
                    qty = 1.0
                if desc and price > 0:
                    # precio del form = precio UNITARIO (no total)
                    unit_price = price
                    c.execute("""
                        INSERT INTO products (name, unit_price, date, transaction_id, merchant_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (desc, unit_price, date_val, txn_id, merchant_id))

        conn.commit()
        conn.close()
        return redirect(url_for("expense_detail", txn_id=txn_id))

    conn.close()
    return render_template(
        "expenses/new.html",
        all_categories=all_categories,
        review_queue_count=review_count,
    )


# ============================================================
# IMPORT / EXPORT EXCEL
# ============================================================

@app.route("/import-excel", methods=["GET", "POST"])
def import_excel_route():
    """Página de importación de Excel."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()

    result = None
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        if f.filename:
            tmp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.xlsx")
            f.save(tmp_path)
            result = import_excel(tmp_path)
            os.remove(tmp_path)

    return render_template(
        "import_excel.html",
        result=result,
        review_queue_count=review_count,
    )


@app.route("/export-excel")
def export_excel_route():
    """Exportar a Excel."""
    month = request.args.get("month")
    year = request.args.get("year")

    if year and month:
        from services.excel import export_month_excel
        path = export_month_excel(year=year, month=month)
        filename = f"gastos_{year}_{month}.xlsx"
    else:
        path = export_excel(month=month, year=year)
        filename = os.path.basename(path)

    return send_file(
        path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    """Health check — verifica DB, VLM y disco."""

    # DB check
    db_ok = False
    try:
        check_conn = get_db()
        check_conn.execute("SELECT 1").fetchone()
        check_conn.close()
        db_ok = True
    except Exception:
        pass

    # VLM check
    vlm_ok = False
    try:
        from config import LLAMA_ENDPOINT
        r = requests.get(f"{LLAMA_ENDPOINT}/models", timeout=2)
        vlm_ok = r.status_code == 200
    except Exception:
        pass

    # Disk space
    disk = shutil.disk_usage(os.path.dirname(DB_PATH))
    disk_pct = (disk.used / disk.total) * 100

    return jsonify({
        "status": "ok" if db_ok and vlm_ok else "degraded",
        "db": db_ok,
        "vlm": vlm_ok,
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_pct": round(disk_pct, 1),
    })


# ============================================================
# UTILIDADES
# ============================================================

def _log_corrections(c, txn_id, original_values, data):
    """Log cambios entre valores OCR y valores confirmados.
    Registra también cuando OCR devolvió None y el usuario añadió info manualmente."""
    if not original_values:
        return

    field_map = {
        "date": "fecha",
        "merchant": "comercio",
        "total": "total",
        "payment_method": "metodo_pago",
        "card_last4": "card_last4",
    }

    for db_field, ocr_key in field_map.items():
        orig = original_values.get(ocr_key)
        curr = data.get(db_field)
        # Log si:
        # - OCR tenía valor y el usuario cambió
        # - OCR no tenía valor y el usuario añadió
        # - OCR tenía valor y el usuario lo borró
        if str(orig or '').strip() != str(curr or '').strip():
            c.execute("""
                INSERT INTO corrections (transaction_id, field, original_value, corrected_value)
                VALUES (?, ?, ?, ?)
            """, (
                txn_id, ocr_key,
                str(orig) if orig is not None else None,
                str(curr) if curr else None  # NULL si curr es None o '' (N5)
            ))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    init_db()
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"🚀 MisGastos corriendo en http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"📷 Abre http://{FLASK_HOST}:{FLASK_PORT}/scan para subir un ticket")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=debug)
