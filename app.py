"""
MisGastos — Aplicación principal Flask
Contabilización de gastos familiares para Sonia
"""
import os
import uuid
import json
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, send_file, jsonify
from werkzeug.utils import secure_filename
from models.schema import get_db, init_db
from services.ocr import extract_ticket
from services.classifier import clasificar_por_items, clasificar_por_comercio
from services.excel import import_excel, export_excel
from config import UPLOAD_DIR, FLASK_HOST, FLASK_PORT

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# ============================================================
# INICIALIZACIÓN
# ============================================================

@app.before_request
def ensure_db():
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
    month_start = f"{now.year}-{now.month:02d}-01"
    month_end = f"{now.year}-{now.month:02d}-31"

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
        SELECT c.name, c.color, SUM(t.total)
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
        SELECT b.limit_val, c.name, c.color
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
        WHERE b.month_col = ?
    """, (month_start[:7],))
    for b in c.fetchall():
        c.execute("""
            SELECT COALESCE(SUM(t.total), 0)
            FROM transactions t
            WHERE t.kind='expense' AND t.category_id = ? AND t.date >= ? AND t.date <= ?
        """, (c.execute("SELECT id FROM categories WHERE name = ?", (b['name'],)).fetchone()['id'], month_start, month_end))
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

    # Procesar OCR
    result = extract_ticket(original_path)

    # La ruta procesada es la que devuelve preprocess_image
    processed_path = original_path.rsplit(".", 1)[0] + "_processed.jpg"

    # Auto-clasificar por items
    auto_cat_id = None
    if result.items:
        auto_cat_id, _ = clasificar_por_items(result.items)
    elif result.comercio:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT default_category_id FROM merchants WHERE name LIKE ?", (f"%{result.comercio}%",))
        row = c.fetchone()
        conn.close()
        if row and row['default_category_id']:
            auto_cat_id = row['default_category_id']
    if auto_cat_id is None:
        auto_cat_id = 9  # Otros

    # Guardar en scans
    conn = get_db()
    c = conn.cursor()
    low_conf = result.overall_confidence < 0.7
    status = "pending" if low_conf else "reviewed"

    c.execute("""
        INSERT INTO scans (model, raw_output, confidence, duration_ms, status)
        VALUES (?, ?, ?, ?, ?)
    """, ("qwen3.5:9b", result.raw_output, result.overall_confidence, result.duration_ms, status))
    scan_id = c.lastrowid

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    conn.commit()
    conn.close()

    base_template = "partial.html" if "HX-Request" in request.headers or "Hx-Request" in request.headers else "base.html"

    # Todas las categorías disponibles
    all_categories = []
    from config import CATEGORIES, TRANSPORT_SUBCATEGORIES
    for cat in CATEGORIES + TRANSPORT_SUBCATEGORIES:
        all_categories.append({'id': cat[0], 'name': cat[1]})

    return render_template(
        "scan/edit.html",
        base_template=base_template,
        scan_id=scan_id,
        image_filename=original_filename,
        fecha=result.fecha,
        comercio=result.comercio,
        nif=result.nif,
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


@app.route("/scan/save", methods=["POST"])
def scan_save():
    """Guardar ticket confirmado (desde OCR o edición)."""
    data = {
        "scan_id": request.form.get("scan_id"),
        "date": request.form.get("date"),
        "merchant": request.form.get("merchant", "").strip(),
        "nif": request.form.get("nif", "").strip() or None,
        "total": float(request.form.get("total", 0)),
        "category_id": int(request.form.get("category", 0)),
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

    # Buscar o crear merchant
    c.execute("SELECT id, nif FROM merchants WHERE name = ?", (data["merchant"],))
    row = c.fetchone()
    if row:
        merchant_id = row['id']
        # Actualizar NIF si era null y ahora hay uno
        if data["nif"] and (row['nif'] is None or row['nif'] == ""):
            c.execute("UPDATE merchants SET nif = ? WHERE id = ?", (data["nif"], merchant_id))
            # Log corrección si había valor original
            orig_nif = original_values.get("nif")
            if orig_nif and orig_nif != data["nif"]:
                c.execute("""
                    INSERT INTO corrections (transaction_id, field, original_value, corrected_value)
                    VALUES (NULL, 'nif', ?, ?)
                """, (orig_nif, data["nif"]))
    else:
        c.execute(
            "INSERT INTO merchants (name, nif, default_category_id) VALUES (?, ?, ?)",
            (data["merchant"], data["nif"], data["category_id"])
        )
        merchant_id = c.lastrowid

    # Insertar transacción
    c.execute("""
        INSERT INTO transactions (kind, date, merchant_id, total, payment_method, category_id, source, ocr_confidence, field_confidence)
        VALUES ('expense', ?, ?, ?, ?, ?, 'ocr', ?, ?)
    """, (
        data["date"], merchant_id, data["total"],
        data["payment_method"] if data["payment_method"] else None,
        data["category_id"],
        ocr_confidence, field_confidence_json
    ))
    txn_id = c.lastrowid

    # Guardar items
    for i in range(len(item_descs)):
        desc = item_descs[i].strip()
        try:
            price = float(item_prices[i])
        except (ValueError, IndexError):
            price = 0
        try:
            qty = int(item_quants[i])
        except (ValueError, IndexError):
            qty = 1
        if desc and price > 0:
            c.execute("""
                INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id)
                VALUES (?, ?, ?, ?, ?)
            """, (txn_id, desc, qty, price, data["category_id"]))

    # Actualizar scan → vincular a transacción + marcar como guardado
    if data["scan_id"]:
        c.execute("""
            UPDATE scans SET transaction_id = ?, status = 'saved' WHERE id = ?
        """, (txn_id, data["scan_id"]))

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
    """Lista de gastos con filtros."""
    conn = get_db()
    c = conn.cursor()

    month = request.args.get("month", None)
    category = request.args.get("category", None)

    where = ["t.kind='expense'"]
    params = []

    if month:
        where.append("t.date LIKE ?")
        params.append(f"{month}-%")

    if category:
        where.append("t.category_id = ?")
        params.append(int(category))

    query = """
        SELECT t.*, c.name as category_name, m.name as merchant_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN merchants m ON t.merchant_id = m.id
        WHERE {}
        ORDER BY t.date DESC, t.id DESC
        LIMIT 100
    """.format(" AND ".join(where))

    c.execute(query, params)
    expenses_list = c.fetchall()

    # Categorías para filtro
    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    categories = c.fetchall()

    # Cola de revisión
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    conn.close()

    return render_template(
        "expenses/list.html",
        expenses=expenses_list,
        categories=categories,
        review_queue_count=review_count,
    )


@app.route("/expense/<int:txn_id>")
def expense_detail(txn_id):
    """Detalle de un gasto individual con sus items."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT t.*, c.name as category_name, c.color as category_color, m.name as merchant_name, m.nif as merchant_nif
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
        categories=categories,
        review_queue_count=review_count,
    )


@app.route("/expense/<int:txn_id>/edit", methods=["GET", "POST"])
def edit_expense(txn_id):
    """Editar un gasto ya guardado."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT t.*, m.name as merchant_name, m.nif as merchant_nif
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
        new_total = float(request.form.get("total", txn['total']))
        new_category = int(request.form.get("category", txn['category_id']))
        new_merchant = request.form.get("merchant", txn['merchant_name']).strip()
        new_payment = request.form.get("payment_method", txn['payment_method']) or None
        new_nif = request.form.get("nif", txn['merchant_nif'] or "")

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
        if mrow:
            if new_nif and not txn['merchant_nif']:
                c.execute("UPDATE merchants SET nif = ? WHERE id = ?", (new_nif, mrow['id']))
        else:
            # Nuevo nombre → insertar
            c.execute("INSERT INTO merchants (name, nif, default_category_id) VALUES (?, ?, ?)", (new_merchant, new_nif or None, new_category))
            new_merchant_id = c.lastrowid
            c.execute("UPDATE transactions SET merchant_id = ? WHERE id = ?", (new_merchant_id, txn_id))

        # Actualizar transacción
        c.execute("""
            UPDATE transactions SET date = ?, total = ?, category_id = ?, payment_method = ?
            WHERE id = ?
        """, (new_date, new_total, new_category, new_payment, txn_id))

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

    c.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    conn.commit()
    conn.close()

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
    scans = c.fetchall()
    conn.close()

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


# ============================================================
# IMÁGENES
# ============================================================

@app.route("/scan/image/<filename>")
def scan_image(filename):
    """Servir imagen de un scan (original o procesada)."""
    # Intentar la procesada primero, luego la original
    processed = filename.rsplit(".", 1)[0] + "_processed.jpg"
    if os.path.exists(os.path.join(UPLOAD_DIR, processed)):
        return send_from_directory(UPLOAD_DIR, processed)
    return send_from_directory(UPLOAD_DIR, filename)


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
    path = export_excel(month=month, year=year)

    return send_file(
        path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    """Health check."""
    return jsonify({"status": "ok", "db": os.path.exists(os.path.join(UPLOAD_DIR, "..", "gastos.db"))})


# ============================================================
# UTILIDADES
# ============================================================

def _log_corrections(c, txn_id, original_values, data):
    """Log cambios entre valores OCR y valores confirmados."""
    if not original_values:
        return

    field_map = {
        "date": "fecha",
        "merchant": "comercio",
        "total": "total",
        "payment_method": "metodo_pago",
        "nif": "nif",
    }

    for db_field, ocr_key in field_map.items():
        orig = original_values.get(ocr_key)
        curr = data.get(db_field)
        if orig and curr and str(orig).strip() != str(curr).strip():
            c.execute("""
                INSERT INTO corrections (transaction_id, field, original_value, corrected_value)
                VALUES (?, ?, ?, ?)
            """, (txn_id, ocr_key, str(orig), str(curr)))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    init_db()
    print(f"🚀 MisGastos corriendo en http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"📷 Abre http://{FLASK_HOST}:{FLASK_PORT}/scan para subir un ticket")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True)
