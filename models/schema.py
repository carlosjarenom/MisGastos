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
    """Inicializar la base de datos con el schema v5."""
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

    # Valores por defecto de los ajustes
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('deep_analysis', 'false')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('theme', 'light')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('category_analysis', 'false')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('enable_thinking', 'true')")

    # Migración: añadir columna image_path a scans si no existe
    c.execute("PRAGMA table_info(scans)")
    columns = [row[1] for row in c.fetchall()]
    if 'image_path' not in columns:
        c.execute("ALTER TABLE scans ADD COLUMN image_path TEXT")

    # Migración: añadir columna card_last4 a transactions si no existe
    c.execute("PRAGMA table_info(transactions)")
    columns = [row[1] for row in c.fetchall()]
    if 'card_last4' not in columns:
        c.execute("ALTER TABLE transactions ADD COLUMN card_last4 TEXT")

    # Migración: añadir columna vehicle a transactions si no existe
    c.execute("PRAGMA table_info(transactions)")
    columns = [row[1] for row in c.fetchall()]
    if 'vehicle' not in columns:
        c.execute("ALTER TABLE transactions ADD COLUMN vehicle TEXT")

    # Insertar categorías canónicas si no existen
    for cat in CATEGORIES + TRANSPORT_SUBCATEGORIES:
        c.execute(
            "INSERT OR IGNORE INTO categories (id, name, parent_id, color) VALUES (?, ?, ?, ?)",
            cat
        )

    # FIX 25A: Limpiar categorías viejas y asegurar las nuevas según config.py
    # Eliminar categorías que ya no existen
    valid_ids = [cat[0] for cat in CATEGORIES + TRANSPORT_SUBCATEGORIES]
    valid_ids_str = ",".join(map(str, valid_ids))

    # Fallback category ID (usually "Otros", the last of canonical categories if not found)
    otros_cat_id = next((cat[0] for cat in CATEGORIES if cat[1] == "Otros"), 6)

    c.execute("DELETE FROM categories WHERE id NOT IN (" + valid_ids_str + ")")
    # Actualizar transacciones que apuntan a categorías viejas
    c.execute("UPDATE transactions SET category_id = ? WHERE category_id NOT IN (" + valid_ids_str + ")", (otros_cat_id,))
    # Actualizar items de transacciones
    c.execute("UPDATE transaction_items SET category_id = ? WHERE category_id NOT IN (" + valid_ids_str + ")", (otros_cat_id,))

    # FIX 18: Migrar quantity de INTEGER a REAL en transaction_items
    # SQLite no soporta ALTER COLUMN, así que recrear la tabla
    c.execute("PRAGMA table_info(transaction_items)")
    columns = [row[1] for row in c.fetchall()]
    if 'quantity' in columns:
        # Verificar si quantity es INTEGER (necesita migración)
        c.execute("SELECT typeof(quantity) FROM transaction_items LIMIT 1")
        type_row = c.fetchone()
        if type_row and type_row[0] == 'integer':
            # Recrear tabla con quantity REAL
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
