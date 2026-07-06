"""
services/excel.py — Import/Export de Excel
"""
import openpyxl
from models.schema import get_db


def import_excel(filepath: str) -> dict:
    """
    Importar Excel de Sonia a SQLite.
    Retorna: {"imported": N, "skipped": N, "errors": [...]}

    ⚠️ EN MANTENIMIENTO — El parser actual es frágil y produce datos incorrectos.
    Se está reescribiendo para soportar la estructura real del Excel de Sonia
    (gastos | ingresos, una hoja por mes, métodos de pago abreviados, etc.).
    """
    return {
        "imported": 0,
        "skipped": 0,
        "errors": ["Importación en mantenimiento. El parser se está reescribiendo para soportar el formato correcto del Excel."]
    }


def export_excel(month: str = None, year: str = None) -> str:
    """Exportar datos a Excel (formato compatible con Sonia).
    Retorna: path al archivo generado.
    """
    from config import BASE_DIR
    import os

    conn = get_db()
    c = conn.cursor()

    where = "WHERE kind='expense'"
    params = []
    if month and year:
        where += " AND date LIKE ?"
        params.append(f"{year}-{month}-%")

    c.execute(f"SELECT t.date, m.name, t.total, t.payment_method, c.name as category FROM transactions t LEFT JOIN merchants m ON t.merchant_id = m.id LEFT JOIN categories c ON t.category_id = c.id {where} ORDER BY t.date", params)
    rows = c.fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gastos"

    # Headers
    ws.append(["Fecha", "Descripción", "Importe", "Método", "Categoría"])

    for row in rows:
        ws.append([
            row[0],
            row[1] or "",
            f"{row[2]:.2f}",
            row[3] or "",
            row[4] or "",
        ])

    filename = f"misgastos_{year}_{month}.xlsx"
    path = os.path.join(BASE_DIR, "data", filename)
    wb.save(path)

    conn.close()
    return path
