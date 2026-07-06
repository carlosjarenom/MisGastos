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
        manual_edited INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS transaction_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        quantity INTEGER DEFAULT 1,
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
        month DATE,
        limit REAL
    );

    CREATE INDEX IF NOT EXISTS idx_trans_date ON transactions(date);
    CREATE INDEX IF NOT EXISTS idx_trans_kind_date ON transactions(kind, date);
    CREATE INDEX IF NOT EXISTS idx_trans_category ON transactions(category_id);
    CREATE INDEX IF NOT EXISTS idx_trans_merchant ON transactions(merchant_id);
    CREATE INDEX IF NOT EXISTS idx_corrections_field ON corrections(field);
    """)

    # Insertar categorías canónicas si no existen
    for cat in CATEGORIES + TRANSPORT_SUBCATEGORIES:
        c.execute(
            "INSERT OR IGNORE INTO categories (id, name, parent_id, color) VALUES (?, ?, ?, ?)",
            cat
        )

    conn.commit()
    conn.close()
