"""
reportes.py — bayoSys · Productos El Bayo

Exportador a Excel. Toma los datos que ya existen y los escribe en una
libreta .xlsx con una pestaña por fuente.

Capa de presentación tabular — el mismo papel que pos_tui.py cumple para
la presentación visual: consume datos ya listos y no decide nada. Aquí no
hay SQL propio ni reglas de negocio; todo entra por los getters de rango
de pos_db.py y config.py. Si un número está mal, está mal en la fuente,
no aquí.

Dos formas de dispararlo:
  exportar_rango(desde, hasta)  → manual, rango elegido por el operador
  exportar_dia(fecha)           → un solo día
  exportar_dia_seguro(fecha)    → igual pero NUNCA lanza excepción; es la
                                  que usan los ganchos automáticos del
                                  corte de caja

openpyxl se importa protegido a propósito. Es la única dependencia externa
del proyecto, y si en una máquina falta, un import suelto arriba de este
archivo tumbaría main.py entero — el POS no abriría. Así, sin openpyxl
todo lo demás sigue funcionando y solo falla la exportación, con un
mensaje que dice cómo instalarlo.
"""

import os
from dataclasses import asdict
from datetime import date, datetime

from config import BASE_DIR, cargar_batches_rango
from pos_db import (
    get_tickets_rango,
    get_pedidos_rango,
    get_gastos_rango,
    get_cortes_rango,
    get_movimientos_rango,
    get_pool_historial_rango,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False


# ── RUTAS ────────────────────────────────────────────────────────────────────

# Junto a registros/ y pos.db, no en la carpeta del código: son archivos
# generados de negocio, no fuente. BASE_DIR ya está en .gitignore vía data/.
EXPORTES_DIR = os.path.join(BASE_DIR, "exportes")


class ErrorReporte(Exception):
    """Falla al generar el reporte. La captura quien dispara la exportación."""


# ── FORMATOS DE CELDA ────────────────────────────────────────────────────────

FMT_TEXTO  = "@"
FMT_DINERO = '$#,##0.00'
FMT_PESO   = '#,##0.000'      # kg y litros — el negocio pesa con decimales
FMT_ENTERO = '#,##0'

# Banderas 0/1 de SQLite. Se escriben como texto 'sí'/'no': el reporte lo
# lee una persona, no el sistema, y un 0 suelto en una columna 'Entregado'
# obliga a recordar la convención.
FMT_SINO   = "@sino"

_HDR_FILL   = "1F2A37"
_HDR_FONT   = "FFFFFF"
_TOT_FILL   = "E8EDF2"
_BORDE_SUAVE = "C8D0D8"


# ── DEFINICIÓN DE COLUMNAS ───────────────────────────────────────────────────
#
# (encabezado, clave del dato, formato, suma)
#
# 'suma' marca si la columna se totaliza al pie. No es cosmético: hay
# columnas que NO se pueden sumar sin decir una mentira.
#   - total_ticket y los pagos vienen de la cabecera del ticket y se repiten
#     en cada línea del mismo ticket — sumarlos cuenta la venta varias veces.
#     Lo que se suma en Ventas es 'subtotal', que sí es por línea.
#   - lt_saldo del pool es un saldo corrido, no un movimiento. Lo que se
#     suma ahí es lt_delta.

COLS_VENTAS = [
    ("Fecha",         "fecha",         FMT_TEXTO,  False),
    ("Hora",          "hora",          FMT_TEXTO,  False),
    ("Ticket",        "ticket_id",     FMT_ENTERO, False),
    ("Batch",         "batch_id",      FMT_TEXTO,  False),
    ("Operador",      "operador",      FMT_TEXTO,  False),
    ("SKU",           "sku",           FMT_TEXTO,  False),
    ("Descripción",   "descripcion",   FMT_TEXTO,  False),
    ("Cantidad",      "cantidad",      FMT_PESO,   False),
    ("Precio unit.",  "precio_unit",   FMT_DINERO, False),
    ("Subtotal",      "subtotal",      FMT_DINERO, True),
    ("Total ticket",  "total_ticket",  FMT_DINERO, False),
    ("Efectivo",      "pago_efectivo", FMT_DINERO, False),
    ("Transfer",      "pago_transfer", FMT_DINERO, False),
    ("Tarjeta",       "pago_tarjeta",  FMT_DINERO, False),
    ("Cambio",        "cambio",        FMT_DINERO, False),
]

COLS_PEDIDOS = [
    ("Fecha pedido",   "fecha_pedido",      FMT_TEXTO,  False),
    ("Fecha entrega",  "fecha_entrega",     FMT_TEXTO,  False),
    ("Cliente",        "cliente_clave",     FMT_TEXTO,  False),
    ("Nombre",         "cliente_nombre",    FMT_TEXTO,  False),
    ("Kg",             "kg",                FMT_PESO,   True),
    ("Precio kg",      "precio_kg_pactado", FMT_DINERO, False),
    ("Prioridad",      "prioridad",         FMT_TEXTO,  False),
    ("Entregado",      "entregado",         FMT_SINO,   False),
    ("Nota",           "nota",              FMT_TEXTO,  False),
]

COLS_GASTOS = [
    ("Fecha",       "fecha",       FMT_TEXTO,  False),
    ("Hora",        "hora",        FMT_TEXTO,  False),
    ("Batch",       "batch_id",    FMT_TEXTO,  False),
    ("Tipo",        "tipo",        FMT_TEXTO,  False),
    ("Descripción", "descripcion", FMT_TEXTO,  False),
    ("Monto",       "monto",       FMT_DINERO, True),
]

COLS_CORTES = [
    # 'Día operación' y 'Guardado' pueden no coincidir: un corte hecho
    # pasada la medianoche, o uno atrasado que rescató el guardian, se
    # guarda con fecha posterior al día que cierra. Van las dos columnas
    # para que un corte fechado fuera del rango pedido se explique solo
    # en vez de parecer un error del reporte.
    ("Día operación", "dia_operacion",  FMT_TEXTO,  False),
    ("Guardado",      "fecha",          FMT_TEXTO,  False),
    ("Hora",         "hora",            FMT_TEXTO,  False),
    ("Batch",        "batch_id",        FMT_TEXTO,  False),
    ("Efectivo",     "ventas_efectivo", FMT_DINERO, True),
    ("Transfer",     "ventas_transfer", FMT_DINERO, True),
    ("Tarjeta",      "ventas_tarjeta",  FMT_DINERO, True),
    ("Total ventas", "total_ventas",    FMT_DINERO, True),
    ("Gastos",       "total_gastos",    FMT_DINERO, True),
    ("Neto",         "neto",            FMT_DINERO, True),
    ("Fondo caja",   "fondo_caja",      FMT_DINERO, False),
    ("A entregar",   "a_entregar",      FMT_DINERO, True),
    ("Nota",         "nota",            FMT_TEXTO,  False),
]

COLS_MOVIMIENTOS = [
    ("Fecha",      "fecha",      FMT_TEXTO, False),
    ("Hora",       "hora",       FMT_TEXTO, False),
    ("SKU",        "sku",        FMT_TEXTO, False),
    ("Tipo",       "tipo",       FMT_TEXTO, False),
    ("Cantidad",   "cantidad",   FMT_PESO,  True),
    ("Referencia", "referencia", FMT_TEXTO, False),
    ("Nota",       "nota",       FMT_TEXTO, False),
]

COLS_POOL = [
    ("Fecha",            "fecha",            FMT_TEXTO,  False),
    ("Hora",             "hora",             FMT_TEXTO,  False),
    ("Tipo",             "tipo",             FMT_TEXTO,  False),
    ("Litros mov.",      "lt_delta",         FMT_PESO,   True),
    ("Cubetas emitidas", "cubetas_emitidas", FMT_PESO,   True),
    ("Saldo pool (lt)",  "lt_saldo",         FMT_PESO,   False),
    ("Referencia",       "referencia",       FMT_TEXTO,  False),
    ("Nota",             "nota",             FMT_TEXTO,  False),
]

COLS_PRODUCCION = [
    ("Fecha",         "fecha",          FMT_TEXTO,  False),
    ("Batch",         "id",             FMT_ENTERO, False),
    ("Hora",          "hora",           FMT_TEXTO,  False),
    ("Inicio",        "hora_inicio",    FMT_TEXTO,  False),
    ("Fin",           "hora_fin",       FMT_TEXTO,  False),
    ("Proveedor",     "proveedor",      FMT_TEXTO,  False),
    ("Costo kg",      "costo_kg",       FMT_DINERO, False),
    ("Temp. entrada", "temp_entrada",   FMT_TEXTO,  False),
    ("Composición",   "composicion",    FMT_TEXTO,  False),
    ("Operador",      "operador",       FMT_TEXTO,  False),
    ("Kg grasa",      "kg_grasa",       FMT_PESO,   True),
    ("Kg chicharrón", "kg_chi",         FMT_PESO,   True),
    ("Kg manteca",    "kg_mant_real",   FMT_PESO,   True),
    ("Observaciones", "observaciones",  FMT_TEXTO,  False),
]


# ── NORMALIZACIÓN DE FILAS ───────────────────────────────────────────────────

def _a_dict(fila) -> dict:
    """
    Deja cualquier fila como dict plano.

    Las fuentes no devuelven lo mismo: pos_db.py da sqlite3.Row y config.py
    da dataclasses Batch. En vez de que el escritor sepa de los dos tipos,
    se aplanan aquí y de ahí para abajo todo es un dict.
    """
    if isinstance(fila, dict):
        return fila
    if hasattr(fila, "keys"):            # sqlite3.Row
        return {k: fila[k] for k in fila.keys()}
    return asdict(fila)                  # dataclass (Batch)


def _limpiar(valor, formato):
    """Deja el valor listo para la celda, respetando el tipo que Excel espera."""
    if valor is None:
        return ""
    if formato == FMT_SINO:
        return "sí" if valor else "no"
    if formato == FMT_TEXTO:
        return str(valor)
    return valor


def _fmt_excel(formato: str) -> str:
    """FMT_SINO es marca interna, no un formato que Excel entienda: la celda
    ya lleva 'sí'/'no', así que para Excel es texto."""
    return FMT_TEXTO if formato == FMT_SINO else formato


# ── ESCRITURA DE UNA PESTAÑA ─────────────────────────────────────────────────

def _escribir_hoja(wb, titulo: str, columnas: list, filas: list):
    """
    Escribe una pestaña completa: encabezado, datos y fila de totales.

    Una pestaña sin datos igual se crea, con sus encabezados y una nota. Un
    reporte al que le falta la pestaña 'Gastos' se lee como que algo falló;
    uno que dice "sin registros en el periodo" contesta la pregunta.
    """
    ws = wb.create_sheet(titulo)

    borde = Border(bottom=Side(style="thin", color=_BORDE_SUAVE))
    for col, (encabezado, _, _, _) in enumerate(columnas, start=1):
        celda = ws.cell(row=1, column=col, value=encabezado)
        celda.font      = Font(bold=True, color=_HDR_FONT)
        celda.fill      = PatternFill("solid", fgColor=_HDR_FILL)
        celda.alignment = Alignment(horizontal="center", vertical="center")
        celda.border    = borde

    for i, fila in enumerate(filas, start=2):
        datos = _a_dict(fila)
        for col, (_, clave, formato, _) in enumerate(columnas, start=1):
            celda = ws.cell(row=i, column=col,
                            value=_limpiar(datos.get(clave), formato))
            celda.number_format = _fmt_excel(formato)

    fila_fin = len(filas) + 1

    if filas:
        # Totales como fórmula SUM y no como número precalculado: si el
        # operador filtra o edita el reporte, el pie sigue cuadrando solo.
        fila_tot = fila_fin + 1
        etiqueta = ws.cell(row=fila_tot, column=1, value="TOTAL")
        etiqueta.font = Font(bold=True)
        etiqueta.fill = PatternFill("solid", fgColor=_TOT_FILL)
        for col, (_, _, formato, suma) in enumerate(columnas, start=1):
            celda = ws.cell(row=fila_tot, column=col)
            celda.fill = PatternFill("solid", fgColor=_TOT_FILL)
            celda.font = Font(bold=True)
            if suma:
                letra = get_column_letter(col)
                celda.value         = f"=SUM({letra}2:{letra}{fila_fin})"
                celda.number_format = _fmt_excel(formato)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columnas))}{fila_fin}"
    else:
        nota = ws.cell(row=2, column=1, value="— sin registros en el periodo —")
        nota.font = Font(italic=True, color="808080")

    ws.freeze_panes = "A2"

    # Ancho por contenido, con tope: una nota larga no debe empujar la hoja
    # a un ancho imposible de leer.
    for col, (encabezado, clave, _, _) in enumerate(columnas, start=1):
        largo = len(str(encabezado))
        for fila in filas[:200]:            # muestra: no recorre 10k filas
            valor = _a_dict(fila).get(clave)
            if valor is not None:
                largo = max(largo, len(str(valor)))
        ws.column_dimensions[get_column_letter(col)].width = min(max(largo + 3, 9), 42)

    return ws


def _hoja_portada(wb, desde: str, hasta: str, conteos: dict):
    """
    Primera pestaña: qué rango se exportó, cuándo, y cuántas filas trae cada
    pestaña. Sirve para saber de un vistazo si un reporte vacío está vacío
    porque no hubo movimiento o porque se pidió el rango equivocado.
    """
    ws = wb.create_sheet("Resumen", 0)

    titulo = ws.cell(row=1, column=1, value="bayoSys — Productos El Bayo")
    titulo.font = Font(bold=True, size=15)
    sub = ws.cell(row=2, column=1, value="Exportación de operación")
    sub.font = Font(size=11, color="606060")

    filas = [
        ("Desde",             desde),
        ("Hasta",             hasta),
        ("Generado",          datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    r = 4
    for etiqueta, valor in filas:
        c = ws.cell(row=r, column=1, value=etiqueta)
        c.font = Font(bold=True)
        ws.cell(row=r, column=2, value=valor)
        r += 1

    r += 1
    enc = ws.cell(row=r, column=1, value="Pestaña")
    enc.font = Font(bold=True, color=_HDR_FONT)
    enc.fill = PatternFill("solid", fgColor=_HDR_FILL)
    enc2 = ws.cell(row=r, column=2, value="Registros")
    enc2.font = Font(bold=True, color=_HDR_FONT)
    enc2.fill = PatternFill("solid", fgColor=_HDR_FILL)
    r += 1
    for nombre, n in conteos.items():
        ws.cell(row=r, column=1, value=nombre)
        ws.cell(row=r, column=2, value=n).number_format = FMT_ENTERO
        r += 1

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 24
    return ws


# ── VALIDACIÓN ───────────────────────────────────────────────────────────────

def _validar_fecha(valor: str, etiqueta: str) -> str:
    """Acepta solo AAAA-MM-DD. Las comparaciones de rango en SQL dependen de
    ese formato: como texto ISO, 'menor que' coincide con 'antes que'."""
    try:
        return date.fromisoformat(str(valor).strip()).isoformat()
    except (ValueError, TypeError):
        raise ErrorReporte(f"{etiqueta} inválida: '{valor}' — usa AAAA-MM-DD")


def _compacta(fecha: str) -> str:
    """'2026-08-14' → '20260814', para el nombre del archivo."""
    return fecha.replace("-", "")


# ── EXPORTACIÓN ──────────────────────────────────────────────────────────────

def exportar_rango(desde: str, hasta: str, ruta: str = None) -> str:
    """
    Escribe la libreta con todo lo que ocurrió entre dos fechas, inclusive.

    Devuelve la ruta del archivo generado.
    Lanza ErrorReporte si las fechas no sirven o si falta openpyxl.
    """
    if not OPENPYXL_OK:
        raise ErrorReporte(
            "falta openpyxl — instálalo con:\n"
            "      pip install openpyxl --break-system-packages"
        )

    desde = _validar_fecha(desde, "fecha inicial")
    hasta = _validar_fecha(hasta, "fecha final")
    if desde > hasta:
        raise ErrorReporte(
            f"el rango está al revés: {desde} es posterior a {hasta}"
        )

    # Una pestaña por fuente. Cada getter ya viene filtrado por rango; aquí
    # no se filtra ni se recalcula nada.
    hojas = [
        ("Ventas",       COLS_VENTAS,      get_tickets_rango(desde, hasta)),
        ("Pedidos",      COLS_PEDIDOS,     get_pedidos_rango(desde, hasta)),
        ("Gastos",       COLS_GASTOS,      get_gastos_rango(desde, hasta)),
        ("Cortes",       COLS_CORTES,      get_cortes_rango(desde, hasta)),
        ("Movimientos",  COLS_MOVIMIENTOS, get_movimientos_rango(desde, hasta)),
        ("Pool Manteca", COLS_POOL,        get_pool_historial_rango(desde, hasta)),
        ("Producción",   COLS_PRODUCCION,  cargar_batches_rango(desde, hasta)),
    ]

    wb = Workbook()
    wb.remove(wb.active)                 # openpyxl abre con una hoja vacía

    conteos = {}
    for titulo, columnas, filas in hojas:
        _escribir_hoja(wb, titulo, columnas, filas)
        conteos[titulo] = len(filas)

    _hoja_portada(wb, desde, hasta, conteos)
    wb.active = 0

    if ruta is None:
        if desde == hasta:
            nombre = f"dia_{_compacta(desde)}.xlsx"
        else:
            nombre = f"rango_{_compacta(desde)}_a_{_compacta(hasta)}.xlsx"
        ruta = os.path.join(EXPORTES_DIR, nombre)

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    try:
        wb.save(ruta)
    except OSError as e:
        # El caso real: el archivo está abierto en Excel/LibreOffice y el
        # sistema no deja sobrescribirlo.
        raise ErrorReporte(f"no se pudo escribir '{ruta}': {e}")

    return ruta


def exportar_dia(fecha: str = None) -> str:
    """Exporta un solo día. Sin argumento, hoy."""
    if fecha is None:
        fecha = date.today().isoformat()
    return exportar_rango(fecha, fecha)


def exportar_dia_seguro(fecha: str = None) -> str | None:
    """
    Exporta un día sin poder tumbar a quien la llama. Nunca lanza.

    Es la que usan los ganchos automáticos del corte de caja. La prioridad
    ahí es el corte, no el Excel: el corte ya quedó guardado en la DB, y un
    Excel que no se generó se vuelve a sacar a mano con el modo manual. Al
    revés no — un corte perdido o un cierre a medias por una excepción del
    exportador no se recupera.

    Devuelve la ruta si salió, None si falló. Quien llame decide qué avisar.
    """
    try:
        return exportar_dia(fecha)
    except Exception:
        return None
