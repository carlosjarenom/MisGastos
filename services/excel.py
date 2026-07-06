"""
services/excel.py — Import/Export de Excel
"""
import openpyxl
from models.schema import get_db


def import_excel(filepath: str) -> dict:
    """
    Importar Excel de Sonia a SQLite.
    Retorna: {"imported": N, "skipped": N, "errors": [...]}
    """
    wb = openpyxl.load_workbook(filepath, read_only=True)
    results = {"imported": 0, "skipped": 0, "errors": []}

    conn = get_db()
    c = conn.cursor()

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]

        for row in sheet.iter_rows(min_row=2, values_only=True):
            try:
                # Detectar si es fila de gasto o ingreso
                # Gastos: columnas izquierda, Ingresos: columnas derecha
                fecha = None
                descripcion = None
                total = None
                metodo = None
                kind = None

                # Intentar parsear (formato variable)
                for cell in row:
                    if cell is None:
                        continue
                    cell_str = str(cell).strip()

                    # Detectar fecha
                    if not fecha and ("20" in cell_str and "-" in cell_str):
                        fecha = cell_str[:10]
                    elif not fecha and "/" in cell_str and len(cell_str) <= 10:
                        # Formato DD/MM o DD/MM/YY
                        parts = cell_str.split("/")
                        if len(parts) == 3:
                            y = int(parts[2])
                            y = 2000 + y if y < 100 else y
                            fecha = f"{y}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"

                    # Detectar importe
                    if not total and any(c.isdigit() for c in cell_str):
                        try:
                            total = float(cell_str.replace(",", ".").replace("€", "").replace(" ", ""))
                        except ValueError:
                            pass

                    # Detectar metodo
                    if not metodo and cell_str.lower() in ["efectivo", "pass", "ing", "tarjeta"]:
                        if cell_str.lower() == "ing":
                            kind = "income"
                        else:
                            metodo = "Efectivo" if "efectivo" in cell_str.lower() else "Tarjeta"

                if not fecha or not total:
                    continue

                if kind != "income":
                    kind = "expense"
                    descripcion = str(row[1]) if row[1] else None

                # Insertar
                c.execute("""
                    INSERT INTO transactions (kind, date, description, total, payment_method, source)
                    VALUES (?, ?, ?, ?, ?, 'import_excel')
                """, (kind, fecha, descripcion, abs(total), metodo))

                results["imported"] += 1

            except Exception as e:
                results["skipped"] += 1
                results["errors"].append(str(e)[:100])

    conn.commit()
    conn.close()
    return results


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
