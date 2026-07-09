"""
services/excel.py — Import/Export de Excel
"""
import os
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from config import BASE_DIR
from models.schema import get_db

def import_excel(filepath: str) -> dict:
    """Importar Excel (En mantenimiento)."""
    return {
        "imported": 0,
        "skipped": 0,
        "errors": ["Importación en mantenimiento."]
    }

def export_excel(month: str = None, year: str = None) -> str:
    """Exportar datos a Excel."""
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
    ws.append(["Fecha", "Descripción", "Importe", "Método", "Categoría"])

    for row in rows:
        ws.append([row[0], row[1] or "", f"{row[2]:.2f}", row[3] or "", row[4] or ""])

    filename = f"misgastos_{year}_{month}.xlsx" if year and month else "misgastos_export.xlsx"
    path = os.path.join(BASE_DIR, "data", filename)
    wb.save(path)
    conn.close()
    return path

def export_month_excel(year: str, month: str) -> str:
    """Exportar un mes concreto a Excel elegante."""
    meses_es = {'01': 'Enero', '02': 'Febrero', '03': 'Marzo', '04': 'Abril', '05': 'Mayo', '06': 'Junio', '07': 'Julio', '08': 'Agosto', '09': 'Septiembre', '10': 'Octubre', '11': 'Noviembre', '12': 'Diciembre'}
    month_name = meses_es.get(month, month)

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT t.date, t.description, m.name as merchant, t.total, t.payment_method,
            cat.name as category, t.card_last4, t.vehicle
        FROM transactions t
        LEFT JOIN merchants m ON t.merchant_id = m.id
        LEFT JOIN categories cat ON t.category_id = cat.id
        WHERE t.kind='expense' AND t.date LIKE ?
        ORDER BY t.date
    """, (f"{year}-{month}-%",))
    rows = c.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = f"{month_name} {year}"

    # Estilos y renderizado (simplificado para brevedad pero funcional)
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1F2937', end_color='1F2937', fill_type='solid')

    ws.merge_cells('A1:H1')
    ws['A1'] = f"Gastos \u2014 {month_name} {year}"
    ws['A1'].font = Font(size=16, bold=True)

    headers = ['Fecha', 'Comercio', 'Descripción', 'Categoría', 'Total', 'Método', 'Tarjeta', 'Coche']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill

    for row_idx, row in enumerate(rows, 5):
        ws.cell(row=row_idx, column=1, value=row['date'])
        ws.cell(row=row_idx, column=2, value=row['merchant'] or '')
        ws.cell(row=row_idx, column=3, value=row['description'] or '')
        ws.cell(row=row_idx, column=4, value=row['category'] or '')
        ws.cell(row=row_idx, column=5, value=float(row['total']) if row['total'] else 0).number_format = '#,##0.00\\ "€"'
        ws.cell(row=row_idx, column=6, value=row['payment_method'] or '')
        ws.cell(row=row_idx, column=7, value=f"****{row['card_last4']}" if row['card_last4'] else '')
        ws.cell(row=row_idx, column=8, value=row['vehicle'] or '')

    col_widths = [12, 25, 30, 15, 12, 15, 12, 15]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    filename = f"gastos_{year}_{month}.xlsx"
    path = os.path.join(BASE_DIR, "data", filename)
    wb.save(path)
    return path
