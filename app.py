"""
MisGastos — Aplicación principal Flask
Contabilización de gastos familiares
"""
import calendar
import json
import os
import sqlite3
import uuid
from datetime import date, datetime

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, send_file, url_for)
from werkzeug.utils import secure_filename

from config import (CATEGORIES, DB_PATH, FLASK_HOST, FLASK_PORT, UPLOAD_DIR)
from models.schema import get_db, init_db
from services.classifier import unified_classify
from services.excel import export_excel, export_month_excel, import_excel
from services.image_processor import enhance_image, rotate_image
from services.ocr import extract_ticket, _clean_json_response

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# ============================================================
# HELPERS
# ============================================================

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def is_deep_analysis_enabled() -> bool:
    return get_setting('deep_analysis', 'false') == 'true'

def is_category_analysis_enabled() -> bool:
    return get_setting('category_analysis', 'false') == 'true'

def is_thinking_enabled() -> bool:
    return get_setting('enable_thinking', 'true') == 'true'

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ============================================================
# JINJA FILTERS
# ============================================================

MESES_ES = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic'
]

@app.template_filter('format_date_es')
def format_date_es(value):
    if not value: return ''
    if isinstance(value, datetime):
        return f"{value.day} {MESES_ES[value.month - 1]} {value.year}"
    if isinstance(value, str):
        value = value.split(' ')[0].split('T')[0]
    try:
        d = datetime.strptime(value, '%Y-%m-%d')
        return f"{d.day} {MESES_ES[d.month - 1]} {d.year}"
    except: return value

@app.context_processor
def inject_globals():
    return {'abs': abs}

# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
def dashboard():
    conn = get_db()
    c = conn.cursor()
    now = date.today()
    last_day = calendar.monthrange(now.year, now.month)[1]
    m_start, m_end = f"{now.year}-{now.month:02d}-01", f"{now.year}-{now.month:02d}-{last_day:02d}"

    c.execute("SELECT SUM(total) FROM transactions WHERE kind='expense' AND date >= ? AND date <= ?", (m_start, m_end))
    total_mes = c.fetchone()[0] or 0

    if now.month > 1: p_start, p_end = f"{now.year}-{now.month-1:02d}-01", f"{now.year}-{now.month:02d}-01"
    else: p_start, p_end = f"{now.year-1}-12-01", f"{now.year}-01-01"

    c.execute("SELECT SUM(total) FROM transactions WHERE kind='expense' AND date >= ? AND date < ?", (p_start, p_end))
    total_anterior = c.fetchone()[0] or 0

    c.execute("""
        SELECT c.name, c.color, SUM(t.total) as total
        FROM transactions t JOIN categories c ON t.category_id = c.id
        WHERE t.kind='expense' AND t.date >= ? AND t.date <= ?
        GROUP BY t.category_id ORDER BY total DESC
    """, (m_start, m_end))
    por_categoria = c.fetchall()

    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0

    c.execute("SELECT b.limit_val, b.category_id, c.name, c.color FROM budgets b JOIN categories c ON b.category_id = c.id WHERE b.month_col = ?", (m_start[:7],))
    b_rows = c.fetchall()
    budgets = []
    for b in b_rows:
        c.execute("SELECT COALESCE(SUM(total), 0) FROM transactions WHERE kind='expense' AND category_id = ? AND date >= ? AND date <= ?", (b['category_id'], m_start, m_end))
        spent = c.fetchone()[0]
        if b['limit_val'] > 0:
            pct = (spent / b['limit_val']) * 100
            budgets.append({'name': b['name'], 'color': b['color'], 'limit': b['limit_val'], 'spent': spent, 'pct': pct, 'over': pct > 100})

    c.execute("SELECT COUNT(*) FROM transactions WHERE kind='expense' AND date >= ? AND date <= ?", (m_start, m_end))
    num_tickets = c.fetchone()[0] or 0

    c.execute("""
        SELECT t.*, c.name as category_name, m.name as merchant_name
        FROM transactions t LEFT JOIN categories c ON t.category_id = c.id LEFT JOIN merchants m ON t.merchant_id = m.id
        WHERE t.kind='expense' ORDER BY t.date DESC, t.id DESC LIMIT 5
    """)
    ultimos = c.fetchall()
    conn.close()
    return render_template("stats/dashboard.html", total_mes=total_mes, por_categoria=por_categoria, total_anterior=total_anterior, review_queue_count=review_count, budgets=budgets, ultimos=ultimos, num_tickets=num_tickets)

# ============================================================
# SCAN / OCR
# ============================================================

@app.route("/scan")
def scan():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.close()
    return render_template("scan/upload.html", review_queue_count=review_count)

@app.route("/scan/upload", methods=["POST"])
def scan_upload():
    if "image" not in request.files: return "No image", 400
    f = request.files["image"]
    if f.filename == "" or not allowed_file(f.filename): return "Invalid file", 400

    filename = f"{uuid.uuid4()}{os.path.splitext(f.filename)[1]}"
    path = os.path.join(UPLOAD_DIR, filename)
    f.save(path)

    try:
        res = extract_ticket(path, deep_analysis=is_deep_analysis_enabled(), enable_thinking=is_thinking_enabled())
    except Exception as e:
        if os.path.exists(path): os.remove(path)
        return render_template("scan/upload.html", error=str(e), review_queue_count=0)

    conn = get_db()
    auto_cat = unified_classify(comercio=res.comercio, items=res.items, vlm_suggestion=res.categoria_sugerida if is_category_analysis_enabled() else None, db_conn=conn)

    c = conn.cursor()
    status = "pending" if res.overall_confidence < 0.7 else "reviewed"
    c.execute("INSERT INTO scans (model, raw_output, confidence, duration_ms, status, image_path) VALUES (?,?,?,?,?,?)",
              ("qwen3.5-9b", res.raw_output, res.overall_confidence, res.duration_ms, status, filename))
    scan_id = c.lastrowid
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    review_count = c.fetchone()[0] or 0
    conn.commit()
    conn.close()

    base = "partial.html" if request.headers.get("HX-Request") else "base.html"
    all_cats = [{'id': c[0], 'name': c[1], 'parent_id': c[2], 'color': c[3]} for c in CATEGORIES]
    return render_template("scan/edit.html", base_template=base, scan_id=scan_id, image_filename=filename, fecha=res.fecha, comercio=res.comercio, card_last4=res.card_last4, items=res.items, total=res.total, metodo_pago=res.metodo_pago, overall_confidence=res.overall_confidence, field_confidence=res.field_confidence, auto_category=auto_cat, all_categories=all_cats, error=res.error, review_queue_count=review_count)

@app.route("/scan/save", methods=["POST"])
def scan_save():
    try:
        total = float(request.form.get("total", 0))
        cat_id = int(request.form.get("category", 9))
    except: return "Invalid data", 400

    scan_id, date_val, merchant = request.form.get("scan_id"), request.form.get("date"), request.form.get("merchant", "").strip()
    if not date_val or total <= 0: return "Invalid date/total", 400

    conn = get_db()
    c = conn.cursor()
    original_values, ocr_conf = {}, None
    if scan_id:
        c.execute("SELECT raw_output, confidence FROM scans WHERE id = ?", (scan_id,))
        row = c.fetchone()
        if row:
            ocr_conf = row['confidence']
            try: original_values = json.loads(_clean_json_response(row['raw_output']))
            except: pass

    m_id = None
    if merchant:
        c.execute("SELECT id FROM merchants WHERE name = ?", (merchant,))
        mrow = c.fetchone()
        if mrow: m_id = mrow['id']
        else:
            c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?,?)", (merchant, cat_id))
            m_id = c.lastrowid

    c.execute("""
        INSERT INTO transactions (kind, date, merchant_id, total, payment_method, category_id, source, ocr_confidence, card_last4, vehicle)
        VALUES ('expense',?,?,?,?,?,'ocr',?,?,?)
    """, (date_val, m_id, total, request.form.get("payment_method"), cat_id, ocr_conf, request.form.get("card_last4"), request.form.get("vehicle")))
    txn_id = c.lastrowid

    if is_deep_analysis_enabled():
        descs, prices, qtys = request.form.getlist("item_desc[]"), request.form.getlist("item_price[]"), request.form.getlist("item_qty[]")
        for i in range(len(descs)):
            try:
                d, p, q = descs[i].strip(), float(prices[i]), float(qtys[i])
                if d and p > 0:
                    c.execute("INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id) VALUES (?,?,?,?,?)", (txn_id, d, q, p, cat_id))
                    c.execute("INSERT INTO products (name, unit_price, date, transaction_id, merchant_id) VALUES (?,?,?,?,?)", (d, p, date_val, txn_id, m_id))
            except: continue

    if scan_id: c.execute("UPDATE scans SET transaction_id = ?, status = 'saved' WHERE id = ?", (txn_id, scan_id))
    _log_corrections(c, txn_id, original_values, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for("scan"))

# ============================================================
# GESTIÓN DE GASTOS
# ============================================================

@app.route("/expenses")
def expenses():
    conn = get_db()
    c = conn.cursor()
    year, month = request.args.get("year"), request.args.get("month")
    meses_es_dict = {'01': 'Enero', '02': 'Febrero', '03': 'Marzo', '04': 'Abril', '05': 'Mayo', '06': 'Junio', '07': 'Julio', '08': 'Agosto', '09': 'Septiembre', '10': 'Octubre', '11': 'Noviembre', '12': 'Diciembre'}

    if year and month:
        c.execute("""
            SELECT t.*, cat.name as category_name, cat.color as category_color, m.name as merchant_name, s.image_path as scan_image
            FROM transactions t LEFT JOIN categories cat ON t.category_id = cat.id LEFT JOIN merchants m ON t.merchant_id = m.id LEFT JOIN scans s ON s.transaction_id = t.id AND s.status = 'saved'
            WHERE t.kind='expense' AND t.date LIKE ? ORDER BY t.date DESC, t.id DESC
        """, (f"{year}-{month}-%",))
        list_data = c.fetchall()
        total = sum(e['total'] for e in list_data)
        c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
        rev = c.fetchone()[0] or 0
        conn.close()
        return render_template("expenses/list.html", view="month", year=year, month=month, month_name=meses_es_dict.get(month), expenses_list=list_data, total_mes=total, review_queue_count=rev)

    elif year:
        c.execute("SELECT DISTINCT substr(date, 1, 7) as ym FROM transactions WHERE kind='expense' AND date LIKE ? ORDER BY ym DESC", (f"{year}-%",))
        months = []
        for r in c.fetchall():
            m_code = r['ym'][5:7]
            c.execute("SELECT COUNT(*), SUM(total) FROM transactions WHERE kind='expense' AND date LIKE ?", (f"{r['ym']}-%",))
            stats = c.fetchone()
            months.append({'month': m_code, 'count': stats[0], 'total': stats[1] or 0})
        c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
        rev = c.fetchone()[0] or 0
        conn.close()
        return render_template("expenses/list.html", view="months", year=year, months=months, meses_es=meses_es_dict, review_queue_count=rev)

    c.execute("SELECT DISTINCT substr(date, 1, 4) as y FROM transactions WHERE kind='expense' ORDER BY y DESC")
    years = []
    for r in c.fetchall():
        c.execute("SELECT COUNT(*), SUM(total) FROM transactions WHERE kind='expense' AND date LIKE ?", (f"{r['y']}-%",))
        stats = c.fetchone()
        years.append({'year': r['y'], 'count': stats[0], 'total': stats[1] or 0})
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("expenses/list.html", view="years", years=years, review_queue_count=rev)

@app.route("/expense/<int:txn_id>")
def expense_detail(txn_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT t.*, c.name as category_name, m.name as merchant_name FROM transactions t LEFT JOIN categories c ON t.category_id = c.id LEFT JOIN merchants m ON t.merchant_id = m.id WHERE t.id = ?", (txn_id,))
    txn = c.fetchone()
    if not txn: return "Not found", 404
    c.execute("SELECT * FROM transaction_items WHERE transaction_id = ?", (txn_id,))
    items = c.fetchall()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("expenses/detail.html", txn=txn, items=items, review_queue_count=rev)

@app.route("/expense/<int:txn_id>/edit", methods=["GET", "POST"])
def edit_expense(txn_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT t.*, m.name as merchant_name FROM transactions t LEFT JOIN merchants m ON t.merchant_id = m.id WHERE t.id = ?", (txn_id,))
    txn = c.fetchone()
    if not txn: return "Not found", 404

    if request.method == "POST":
        d, t, cat, merch = request.form.get("date"), float(request.form.get("total")), int(request.form.get("category")), request.form.get("merchant", "").strip()
        pm, card = request.form.get("payment_method") or None, request.form.get("card_last4") or None

        m_id = None
        if merch:
            c.execute("SELECT id FROM merchants WHERE name = ?", (merch,))
            mrow = c.fetchone()
            if mrow: m_id = mrow['id']
            else:
                c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?,?)", (merch, cat))
                m_id = c.lastrowid

        c.execute("UPDATE transactions SET date=?, total=?, category_id=?, merchant_id=?, payment_method=?, card_last4=?, manual_edited=1 WHERE id=?", (d, t, cat, m_id, pm, card, txn_id))
        conn.commit()
        conn.close()
        return redirect(url_for("expense_detail", txn_id=txn_id))

    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    cats = c.fetchall()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("expenses/edit.html", txn=txn, categories=cats, review_queue_count=rev)

@app.route("/expense/<int:txn_id>/delete", methods=["POST"])
def delete_expense(txn_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT image_path FROM scans WHERE transaction_id = ?", (txn_id,))
    row = c.fetchone()
    c.execute("UPDATE scans SET transaction_id = NULL, status = 'discarded' WHERE transaction_id = ?", (txn_id,))
    c.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    conn.commit()
    conn.close()
    if row and row['image_path']:
        base = os.path.join(UPLOAD_DIR, os.path.splitext(row['image_path'])[0])
        for s in ['', '_processed', '_current', '_enhanced', '_rot90', '_rot180', '_rot270']:
            p = base + s + '.jpg' if s else os.path.join(UPLOAD_DIR, row['image_path'])
            if os.path.exists(p): os.remove(p)
    return redirect(url_for("expenses"))

@app.route("/expense/new", methods=["GET", "POST"])
def new_expense_manual():
    if request.method == "POST":
        d, t, cat, merch = request.form.get("date"), float(request.form.get("total", 0)), int(request.form.get("category", 9)), request.form.get("merchant", "").strip()
        pm, desc, kind = request.form.get("payment_method"), request.form.get("description", "").strip(), request.form.get("kind", "expense")
        card, veh = request.form.get("card_last4", "").strip() or None, request.form.get("vehicle", "").strip() or None

        conn = get_db()
        c = conn.cursor()
        m_id = None
        if merch:
            c.execute("SELECT id FROM merchants WHERE name = ?", (merch,))
            mrow = c.fetchone()
            if mrow: m_id = mrow['id']
            else:
                c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?,?)", (merch, cat))
                m_id = c.lastrowid

        c.execute("INSERT INTO transactions (kind, date, description, merchant_id, total, payment_method, category_id, source, card_last4, vehicle) VALUES (?,?,?,?,?,?,?, 'manual',?,?)",
                  (kind, d, desc or None, m_id, t, pm or None, cat, card, veh))
        txn_id = c.lastrowid

        if is_deep_analysis_enabled():
            descs, prices, qtys = request.form.getlist("item_desc[]"), request.form.getlist("item_price[]"), request.form.getlist("item_qty[]")
            for i in range(len(descs)):
                try:
                    de, p, q = descs[i].strip(), float(prices[i]), float(qtys[i])
                    if de and p > 0:
                        c.execute("INSERT INTO transaction_items (transaction_id, description, quantity, unit_price, category_id) VALUES (?,?,?,?,?)", (txn_id, de, q, p, cat))
                        c.execute("INSERT INTO products (name, unit_price, date, transaction_id, merchant_id) VALUES (?,?,?,?,?)", (de, p, d, txn_id, m_id))
                except: continue
        conn.commit()
        conn.close()
        return redirect(url_for("dashboard"))

    all_cats = [{'id': c[0], 'name': c[1], 'parent_id': c[2], 'color': c[3]} for c in CATEGORIES]
    return render_template("expenses/new.html", all_categories=all_cats, review_queue_count=0)

# ============================================================
# REVISIÓN / IMÁGENES
# ============================================================

@app.route("/scan/review-queue")
def review_queue():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scans WHERE status = 'pending' ORDER BY confidence ASC")
    raw = c.fetchall()
    scans = []
    for s in raw:
        d = dict(s)
        try:
            out = json.loads(_clean_json_response(d['raw_output']))
            d['parsed_merchant'], d['parsed_confidence'] = out.get('comercio'), out.get('overall_confidence', 0)
        except: d['parsed_merchant'], d['parsed_confidence'] = None, None
        scans.append(d)
    conn.close()
    return render_template("scan/review_queue.html", scans=scans, review_queue_count=len(scans))

@app.route("/scan/<int:scan_id>/discard", methods=["POST"])
def discard_scan(scan_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET status = 'discarded' WHERE id = ?", (scan_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("review_queue"))

@app.route("/scan/<int:scan_id>/edit")
def edit_pending_scan(scan_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scans WHERE id = ? AND status IN ('pending', 'reviewed')", (scan_id,))
    scan = c.fetchone()
    if not scan: return "Not found", 404
    try: data = json.loads(_clean_json_response(scan['raw_output']))
    except: data = {}
    auto_cat = unified_classify(comercio=data.get("comercio"), items=data.get("items"), vlm_suggestion=data.get("categoria_sugerida") if is_category_analysis_enabled() else None, db_conn=conn)
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    all_cats = [{'id': c[0], 'name': c[1], 'parent_id': c[2], 'color': c[3]} for c in CATEGORIES]
    return render_template("scan/edit.html", base_template="base.html", scan_id=scan_id, image_filename=scan['image_path'], fecha=data.get("fecha"), comercio=data.get("comercio"), card_last4=data.get("card_last4"), items=data.get("items", []), total=data.get("total"), metodo_pago=data.get("metodo_pago"), overall_confidence=scan['confidence'] or 0.0, field_confidence=data.get("field_confidence", {}), auto_category=auto_cat, all_categories=all_cats, error=None, review_queue_count=rev)

@app.route("/scan/image/<filename>")
def scan_image(filename):
    safe = secure_filename(filename)
    base = os.path.splitext(safe)[0]
    for s in ['_current.jpg', '_processed.jpg', '']:
        p = base + s if s else safe
        if os.path.exists(os.path.join(UPLOAD_DIR, p)): return send_from_directory(UPLOAD_DIR, p)
    return "Not found", 404

@app.route("/scan/<int:scan_id>/rotate/<int:deg>", methods=["POST"])
def rotate_route(scan_id, deg):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT image_path FROM scans WHERE id=?", (scan_id,))
    img = c.fetchone()['image_path']
    new_path = rotate_image(os.path.join(UPLOAD_DIR, img), deg)
    c.execute("UPDATE scans SET image_path=? WHERE id=?", (os.path.basename(new_path), scan_id))
    conn.commit()
    conn.close()
    res = extract_ticket(new_path, deep_analysis=is_deep_analysis_enabled())
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET raw_output=?, confidence=?, status='reviewed' WHERE id=?", (res.raw_output, res.overall_confidence, scan_id))
    conn.commit()
    conn.close()
    return redirect(url_for("edit_pending_scan", scan_id=scan_id))

@app.route("/scan/<int:scan_id>/enhance", methods=["POST"])
def enhance_route(scan_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT image_path FROM scans WHERE id=?", (scan_id,))
    img = c.fetchone()['image_path']
    new_path = enhance_image(os.path.join(UPLOAD_DIR, img))
    c.execute("UPDATE scans SET image_path=? WHERE id=?", (os.path.basename(new_path), scan_id))
    conn.commit()
    conn.close()
    res = extract_ticket(new_path, deep_analysis=is_deep_analysis_enabled())
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scans SET raw_output=?, confidence=?, status='reviewed' WHERE id=?", (res.raw_output, res.overall_confidence, scan_id))
    conn.commit()
    conn.close()
    return redirect(url_for("edit_pending_scan", scan_id=scan_id))

# ============================================================
# SETTINGS / ADMIN
# ============================================================

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        set_setting('deep_analysis', 'true' if request.form.get("deep_analysis") == "on" else 'false')
        set_setting('category_analysis', 'true' if request.form.get("category_analysis") == "on" else 'false')
        set_setting('enable_thinking', 'true' if request.form.get("enable_thinking") == "on" else 'false')
        set_setting('theme', request.form.get("theme", "light"))
        return redirect(url_for("settings"))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("settings.html", deep_enabled=is_deep_analysis_enabled(), cat_analysis_enabled=is_category_analysis_enabled(), thinking_enabled=is_thinking_enabled(), current_theme=get_setting('theme', 'light'), review_queue_count=rev)

@app.route("/settings/merchants", methods=["GET", "POST"])
def manage_merchants():
    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        m_id, name, cat = request.form.get("merchant_id"), request.form.get("name", "").strip(), request.form.get("category_id", 9)
        if m_id: c.execute("UPDATE merchants SET name=?, default_category_id=? WHERE id=?", (name, cat, m_id))
        else:
            try: c.execute("INSERT INTO merchants (name, default_category_id) VALUES (?, ?)", (name, cat))
            except sqlite3.IntegrityError: pass
        conn.commit()
        return redirect(url_for("manage_merchants"))
    c.execute("SELECT m.id, m.name, m.default_category_id, c.name as cat_name FROM merchants m LEFT JOIN categories c ON m.default_category_id = c.id ORDER BY m.name")
    merchants = c.fetchall()
    c.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name")
    cats = c.fetchall()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("settings/merchants.html", merchants=merchants, categories=cats, review_queue_count=rev)

@app.route("/import-excel", methods=["GET", "POST"])
def import_excel_route():
    result = None
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        if f.filename:
            tmp = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.xlsx")
            f.save(tmp)
            result = import_excel(tmp)
            os.remove(tmp)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scans WHERE status = 'pending'")
    rev = c.fetchone()[0] or 0
    conn.close()
    return render_template("import_excel.html", result=result, review_queue_count=rev)

@app.route("/export-excel")
def export_excel_route():
    m, y = request.args.get("month"), request.args.get("year")
    if y and m:
        path = export_month_excel(year=y, month=m)
        name = f"gastos_{y}_{m}.xlsx"
    else:
        path = export_excel(month=m, year=y)
        name = os.path.basename(path)
    return send_file(path, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=name)

@app.route("/health")
def health():
    import shutil, requests
    db_ok = False
    try:
        conn = get_db(); conn.execute("SELECT 1").fetchone(); conn.close(); db_ok = True
    except: pass
    vlm_ok = False
    try:
        from config import LLAMA_ENDPOINT
        r = requests.get(f"{LLAMA_ENDPOINT}/models", timeout=2)
        vlm_ok = (r.status_code == 200)
    except: pass
    disk = shutil.disk_usage(os.path.dirname(DB_PATH))
    return jsonify({"status": "ok" if db_ok and vlm_ok else "degraded", "db": db_ok, "vlm": vlm_ok, "disk_free_gb": round(disk.free / (1024**3), 1), "disk_pct": round((disk.used/disk.total)*100, 1)})

def _log_corrections(c, txn_id, original_values, data):
    field_map = {"date": "fecha", "merchant": "comercio", "total": "total", "payment_method": "metodo_pago", "card_last4": "card_last4"}
    for db_f, ocr_k in field_map.items():
        orig = str(original_values.get(ocr_k) or '').strip()
        curr = str(data.get(db_f) or '').strip()
        if orig != curr:
            c.execute("INSERT INTO corrections (transaction_id, field, original_value, corrected_value) VALUES (?, ?, ?, ?)", (txn_id, ocr_k, orig or None, curr or None))

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    init_db()
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=(os.environ.get("FLASK_DEBUG")=="1"))
