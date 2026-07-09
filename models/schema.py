"""
models/schema.py — Esquema de base de datos SQLite
"""
import sqlite3
import os
from config import DB_PATH, CATEGORIES, TRANSPORT_SUBCATEGORIES

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Inicializar la base de datos."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '#6366f1',
        parent_id INTEGER REFERENCES categories(id)
    );

    CREATE TABLE IF NOT EXISTS merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        nif TEXT UNIQUE,
        default_category_id INTEGER REFERENCES categories(id),
        aliases TEXT
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
        date TEXT NOT NULL,
        description TEXT,
        merchant_id INTEGER REFERENCES merchants(id),
        total REAL NOT NULL,
        payment_method TEXT,
        category_id INTEGER REFERENCES categories(id),
        source TEXT,
        raw_ocr_text TEXT,
        image_path TEXT,
        ocr_confidence REAL,
        field_confidence TEXT,
        scan_model TEXT,
        scan_duration_ms INTEGER,
        card_last4 TEXT,
        vehicle TEXT,
        manual_edited INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS transaction_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        quantity REAL DEFAULT 1.0,
        unit_price REAL NOT NULL,
        category_id INTEGER REFERENCES categories(id)
    );

    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER REFERENCES transactions(id),
        model TEXT NOT NULL,
        raw_output TEXT,
        confidence REAL,
        duration_ms INTEGER,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'reviewed', 'saved', 'discarded')),
        image_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        field TEXT NOT NULL,
        original_value TEXT,
        corrected_value TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER REFERENCES categories(id),
        month_col TEXT,
        limit_val REAL
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        unit_price REAL NOT NULL,
        date TEXT NOT NULL,
        transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
        merchant_id INTEGER REFERENCES merchants(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
    CREATE INDEX IF NOT EXISTS idx_products_date ON products(date);
    CREATE INDEX IF NOT EXISTS idx_products_merchant ON products(merchant_id);
    CREATE INDEX IF NOT EXISTS idx_trans_date ON transactions(date);
    CREATE INDEX IF NOT EXISTS idx_trans_kind_date ON transactions(kind, date);
    CREATE INDEX IF NOT EXISTS idx_trans_category ON transactions(category_id);
    CREATE INDEX IF NOT EXISTS idx_trans_merchant ON transactions(merchant_id);
    CREATE INDEX IF NOT EXISTS idx_corrections_field ON corrections(field);

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Ajustes por defecto
    for k, v in [('deep_analysis', 'false'), ('theme', 'light'), ('category_analysis', 'false'), ('enable_thinking', 'true')]:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Migraciones de columnas
    c.execute("PRAGMA table_info(scans)")
    if 'image_path' not in [r[1] for r in c.fetchall()]: c.execute("ALTER TABLE scans ADD COLUMN image_path TEXT")
    c.execute("PRAGMA table_info(transactions)")
    cols = [r[1] for r in c.fetchall()]
    if 'card_last4' not in cols: c.execute("ALTER TABLE transactions ADD COLUMN card_last4 TEXT")
    if 'vehicle' not in cols: c.execute("ALTER TABLE transactions ADD COLUMN vehicle TEXT")

    # Sincronizar categorías
    all_cats = CATEGORIES + TRANSPORT_SUBCATEGORIES
    for cat in all_cats:
        c.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, color) VALUES (?, ?, ?, ?)", cat)

    # Migrar quantity a REAL
    c.execute("PRAGMA table_info(transaction_items)")
    q_col = [r for r in c.fetchall() if r[1] == 'quantity']
    if q_col and 'INT' in q_col[0][2].upper():
        c.execute("ALTER TABLE transaction_items RENAME TO transaction_items_old")
        c.execute("""
            CREATE TABLE transaction_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                quantity REAL DEFAULT 1.0,
                unit_price REAL NOT NULL,
                category_id INTEGER REFERENCES categories(id)
            )
        """)
        c.execute("INSERT INTO transaction_items SELECT * FROM transaction_items_old")
        c.execute("DROP TABLE transaction_items_old")

    conn.commit()
    conn.close()
