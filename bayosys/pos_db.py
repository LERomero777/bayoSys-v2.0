"""
pos_db.py — bayoSys · Productos El Bayo
Capa de datos SQLite para el POS.
No sabe nada de curses ni de lógica de negocio.
Solo crea tablas, inserta y consulta.

Base de datos: ~/bayosys/data/pos.db
"""

import sqlite3
import os
from datetime import date, datetime
from typing import Optional

BASE_DIR = os.path.join(os.path.expanduser("~"), "bayosys", "data")
POS_DB   = os.path.join(BASE_DIR, "pos.db")


# ── CONEXIÓN ─────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    os.makedirs(BASE_DIR, exist_ok=True)
    conn = sqlite3.connect(POS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ── INIT — CREAR TABLAS ──────────────────────────────────────────────────────

SCHEMA = """
-- Inventario de productos
CREATE TABLE IF NOT EXISTS inventario (
    sku         TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL,
    stock       REAL NOT NULL DEFAULT 0,
    unidad      TEXT NOT NULL DEFAULT 'pza',  -- 'pza' | 'kg'
    precio_venta REAL NOT NULL DEFAULT 0,
    es_precio_variable INTEGER NOT NULL DEFAULT 0,  -- 1 = cubeta, artículo libre
    activo      INTEGER NOT NULL DEFAULT 1
);

-- Movimientos de inventario (entradas y salidas)
CREATE TABLE IF NOT EXISTS movimientos_inv (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT NOT NULL,
    hora        TEXT NOT NULL,
    sku         TEXT NOT NULL,
    tipo        TEXT NOT NULL,  -- 'venta' | 'carga' | 'apertura_cubeta' | 'ajuste'
    cantidad    REAL NOT NULL,  -- positivo=entrada, negativo=salida
    referencia  TEXT,           -- ticket_id o 'manual'
    nota        TEXT,
    FOREIGN KEY (sku) REFERENCES inventario(sku)
);

-- Tickets de venta
CREATE TABLE IF NOT EXISTS tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL,
    hora                TEXT NOT NULL,
    batch_id            INTEGER,        -- batch activo al momento de la venta
    operador            TEXT NOT NULL DEFAULT 'Luis',
    pago_efectivo       REAL NOT NULL DEFAULT 0,
    pago_transfer       REAL NOT NULL DEFAULT 0,
    pago_tarjeta        REAL NOT NULL DEFAULT 0,
    total               REAL NOT NULL DEFAULT 0,
    cambio              REAL NOT NULL DEFAULT 0,
    impreso             INTEGER NOT NULL DEFAULT 0,
    anulado             INTEGER NOT NULL DEFAULT 0
);

-- Líneas de cada ticket
CREATE TABLE IF NOT EXISTS ticket_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    sku         TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    cantidad    REAL NOT NULL,
    precio_unit REAL NOT NULL,
    subtotal    REAL NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

-- Gastos internos (gas, leche, gral)
CREATE TABLE IF NOT EXISTS gastos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT NOT NULL,
    hora        TEXT NOT NULL,
    batch_id    INTEGER,
    tipo        TEXT NOT NULL,  -- 'gas' | 'leche' | 'gral'
    descripcion TEXT,
    monto       REAL NOT NULL
);

-- Cortes de caja
CREATE TABLE IF NOT EXISTS cortes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL,
    hora                TEXT NOT NULL,
    batch_id            INTEGER,
    ventas_efectivo     REAL NOT NULL DEFAULT 0,
    ventas_transfer     REAL NOT NULL DEFAULT 0,
    ventas_tarjeta      REAL NOT NULL DEFAULT 0,
    total_ventas        REAL NOT NULL DEFAULT 0,
    total_gastos        REAL NOT NULL DEFAULT 0,
    neto                REAL NOT NULL DEFAULT 0,
    fondo_caja          REAL NOT NULL DEFAULT 250.0,
    a_entregar          REAL NOT NULL DEFAULT 0,
    nota                TEXT
);
"""

SKUs_DEFAULT = [
    # sku, descripcion, stock, unidad, precio_venta, es_precio_variable
    ("CHI",  "Chicharrón",    0,  "kg",  230.0, 0),
    ("M1LT", "Manteca 1lt",   0,  "pza",  35.0, 0),
    ("M05",  "Manteca 500ml", 0,  "pza",  20.0, 0),
    ("CHO",  "Chorizo",       0,  "pza",  30.0, 0),
    ("CUB",  "Cubeta 19lt",   0,  "pza", 500.0, 1),  # precio variable
    ("LIB",  "Artículo libre",0,  "pza",   0.0, 1),  # precio variable
]

def init_db():
    """Crea tablas e inserta SKUs default si no existen."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for sku, desc, stock, unidad, precio, var in SKUs_DEFAULT:
            conn.execute("""
                INSERT OR IGNORE INTO inventario
                (sku, descripcion, stock, unidad, precio_venta, es_precio_variable)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sku, desc, stock, unidad, precio, var))
        conn.commit()


# ── INVENTARIO ───────────────────────────────────────────────────────────────

def get_inventario() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM inventario WHERE activo = 1 ORDER BY sku"
        ).fetchall()

def get_sku(sku: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM inventario WHERE sku = ?", (sku,)
        ).fetchone()

def actualizar_stock(sku: str, delta: float,
                     tipo: str, referencia: str = "manual", nota: str = ""):
    """
    Modifica el stock de un SKU en delta (positivo=entrada, negativo=salida).
    Registra el movimiento en movimientos_inv.
    """
    ahora = datetime.now()
    fecha = ahora.strftime("%Y-%m-%d")
    hora  = ahora.strftime("%H:%M")
    with get_conn() as conn:
        conn.execute(
            "UPDATE inventario SET stock = stock + ? WHERE sku = ?",
            (delta, sku)
        )
        conn.execute("""
            INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fecha, hora, sku, tipo, delta, referencia, nota))
        conn.commit()

def actualizar_precio(sku: str, nuevo_precio: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE inventario SET precio_venta = ? WHERE sku = ?",
            (nuevo_precio, sku)
        )
        conn.commit()

def agregar_sku(sku: str, descripcion: str, unidad: str,
                precio: float, es_variable: int = 0):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO inventario
            (sku, descripcion, stock, unidad, precio_venta, es_precio_variable, activo)
            VALUES (?, ?, 0, ?, ?, ?, 1)
        """, (sku, descripcion, unidad, precio, es_variable))
        conn.commit()

def get_movimientos(sku: str = None, fecha: str = None, limit: int = 50) -> list:
    sql    = "SELECT * FROM movimientos_inv WHERE 1=1"
    params = []
    if sku:
        sql += " AND sku = ?"
        params.append(sku)
    if fecha:
        sql += " AND fecha = ?"
        params.append(fecha)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


# ── TICKETS ───────────────────────────────────────────────────────────────────

def crear_ticket(batch_id: int, operador: str,
                 items: list, pagos: dict, cambio: float) -> int:
    """
    Crea un ticket completo.

    items: lista de dicts con keys: sku, descripcion, cantidad, precio_unit, subtotal
    pagos: dict con keys opcionales: efectivo, transfer, tarjeta
    Retorna el id del ticket creado.
    """
    ahora  = datetime.now()
    fecha  = ahora.strftime("%Y-%m-%d")
    hora   = ahora.strftime("%H:%M:%S")
    total  = sum(i["subtotal"] for i in items)
    p_ef   = pagos.get("efectivo",  0.0)
    p_tr   = pagos.get("transfer",  0.0)
    p_ta   = pagos.get("tarjeta",   0.0)

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO tickets
            (fecha, hora, batch_id, operador,
             pago_efectivo, pago_transfer, pago_tarjeta,
             total, cambio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fecha, hora, batch_id, operador, p_ef, p_tr, p_ta, total, cambio))
        ticket_id = cur.lastrowid

        for item in items:
            conn.execute("""
                INSERT INTO ticket_items
                (ticket_id, sku, descripcion, cantidad, precio_unit, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticket_id, item["sku"], item["descripcion"],
                  item["cantidad"], item["precio_unit"], item["subtotal"]))

            # descontar inventario si no es CHI ni LIB
            if item["sku"] not in ("CHI", "LIB") and item["sku"] != "":
                conn.execute("""
                    UPDATE inventario SET stock = stock - ? WHERE sku = ?
                """, (item["cantidad"], item["sku"]))
                conn.execute("""
                    INSERT INTO movimientos_inv
                    (fecha, hora, sku, tipo, cantidad, referencia, nota)
                    VALUES (?, ?, ?, 'venta', ?, ?, '')
                """, (fecha, hora[:5], item["sku"],
                      -item["cantidad"], f"ticket#{ticket_id}"))

        conn.commit()
    return ticket_id

def anular_ticket(ticket_id: int):
    """Marca ticket como anulado y revierte el stock de sus items."""
    with get_conn() as conn:
        items = conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()
        conn.execute(
            "UPDATE tickets SET anulado = 1 WHERE id = ?", (ticket_id,)
        )
        ahora = datetime.now()
        for item in items:
            if item["sku"] not in ("CHI", "LIB"):
                conn.execute(
                    "UPDATE inventario SET stock = stock + ? WHERE sku = ?",
                    (item["cantidad"], item["sku"])
                )
                conn.execute("""
                    INSERT INTO movimientos_inv
                    (fecha, hora, sku, tipo, cantidad, referencia, nota)
                    VALUES (?, ?, ?, 'ajuste', ?, ?, 'anulación')
                """, (ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
                      item["sku"], item["cantidad"], f"anula#{ticket_id}"))
        conn.commit()

def get_tickets_dia(fecha: str = None) -> list:
    if fecha is None:
        fecha = date.today().isoformat()
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM tickets
            WHERE fecha = ? AND anulado = 0
            ORDER BY id
        """, (fecha,)).fetchall()

def get_ticket_items(ticket_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()

def marcar_impreso(ticket_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tickets SET impreso = 1 WHERE id = ?", (ticket_id,)
        )
        conn.commit()


# ── GASTOS ────────────────────────────────────────────────────────────────────

def registrar_gasto(batch_id: int, tipo: str,
                    monto: float, descripcion: str = "") -> int:
    ahora = datetime.now()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO gastos (fecha, hora, batch_id, tipo, descripcion, monto)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
              batch_id, tipo, descripcion, monto))
        conn.commit()
        return cur.lastrowid

def get_gastos_dia(fecha: str = None) -> list:
    if fecha is None:
        fecha = date.today().isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM gastos WHERE fecha = ? ORDER BY id",
            (fecha,)
        ).fetchall()

def get_gastos_batch(batch_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM gastos WHERE batch_id = ? ORDER BY id",
            (batch_id,)
        ).fetchall()


# ── CORTES ────────────────────────────────────────────────────────────────────

def calcular_corte(batch_id: int = None, fecha: str = None) -> dict:
    """
    Calcula los totales para un corte de caja.
    Si batch_id: filtra solo ese batch.
    Si fecha: filtra todo el día.
    Si ambos None: usa fecha de hoy.
    """
    if fecha is None:
        fecha = date.today().isoformat()

    with get_conn() as conn:
        if batch_id is not None:
            tickets = conn.execute("""
                SELECT * FROM tickets
                WHERE batch_id = ? AND anulado = 0
            """, (batch_id,)).fetchall()
            gastos = conn.execute(
                "SELECT * FROM gastos WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        else:
            tickets = conn.execute("""
                SELECT * FROM tickets
                WHERE fecha = ? AND anulado = 0
            """, (fecha,)).fetchall()
            gastos = conn.execute(
                "SELECT * FROM gastos WHERE fecha = ?", (fecha,)
            ).fetchall()

    v_ef  = sum(t["pago_efectivo"] for t in tickets)
    v_tr  = sum(t["pago_transfer"] for t in tickets)
    v_ta  = sum(t["pago_tarjeta"]  for t in tickets)
    total = v_ef + v_tr + v_ta
    g_tot = sum(g["monto"] for g in gastos)
    neto  = total - g_tot
    fondo = 250.0
    a_ent = max(0.0, v_ef - g_tot - fondo)   # solo efectivo se entrega físico

    return dict(
        n_tickets        = len(tickets),
        ventas_efectivo  = round(v_ef,  2),
        ventas_transfer  = round(v_tr,  2),
        ventas_tarjeta   = round(v_ta,  2),
        total_ventas     = round(total, 2),
        total_gastos     = round(g_tot, 2),
        neto             = round(neto,  2),
        fondo_caja       = fondo,
        a_entregar       = round(a_ent, 2),
        gastos_detalle   = gastos,
        tickets          = tickets,
    )

def guardar_corte(batch_id: int, corte: dict, nota: str = "") -> int:
    ahora = datetime.now()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO cortes
            (fecha, hora, batch_id,
             ventas_efectivo, ventas_transfer, ventas_tarjeta,
             total_ventas, total_gastos, neto, fondo_caja, a_entregar, nota)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
            batch_id,
            corte["ventas_efectivo"], corte["ventas_transfer"], corte["ventas_tarjeta"],
            corte["total_ventas"], corte["total_gastos"],
            corte["neto"], corte["fondo_caja"], corte["a_entregar"],
            nota
        ))
        conn.commit()
        return cur.lastrowid

def get_cortes(fecha: str = None) -> list:
    if fecha is None:
        fecha = date.today().isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cortes WHERE fecha = ? ORDER BY id",
            (fecha,)
        ).fetchall()


# ── RESUMEN VENTAS DÍA (para cierre.py) ──────────────────────────────────────

def resumen_ventas_dia(fecha: str = None) -> dict:
    """
    Agrega ventas del día por SKU.
    Usado por cierre.py y analisis.py.
    """
    if fecha is None:
        fecha = date.today().isoformat()

    with get_conn() as conn:
        items = conn.execute("""
            SELECT ti.sku, ti.descripcion,
                   SUM(ti.cantidad) as total_cant,
                   SUM(ti.subtotal) as total_ing
            FROM ticket_items ti
            JOIN tickets t ON ti.ticket_id = t.id
            WHERE t.fecha = ? AND t.anulado = 0
            GROUP BY ti.sku
        """, (fecha,)).fetchall()

    return {row["sku"]: dict(row) for row in items}


# ── APERTURA DE CUBETA ────────────────────────────────────────────────────────

def abrir_cubeta(env_1lt: int, env_05lt: int, nota: str = ""):
    """
    Registra la apertura de una cubeta para litrear.
    Descuenta 1 cubeta del stock CUB.
    Carga env_1lt al stock M1LT y env_05lt al stock M05.
    """
    ahora = datetime.now()
    fecha = ahora.strftime("%Y-%m-%d")
    hora  = ahora.strftime("%H:%M")
    ref   = f"apertura_cubeta_{fecha}_{hora}"

    with get_conn() as conn:
        # descontar cubeta
        conn.execute(
            "UPDATE inventario SET stock = stock - 1 WHERE sku = 'CUB'",
        )
        conn.execute("""
            INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
            VALUES (?, ?, 'CUB', 'apertura_cubeta', -1, ?, ?)
        """, (fecha, hora, ref, nota))

        # cargar litreada 1lt
        if env_1lt > 0:
            conn.execute(
                "UPDATE inventario SET stock = stock + ? WHERE sku = 'M1LT'",
                (env_1lt,)
            )
            conn.execute("""
                INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
                VALUES (?, ?, 'M1LT', 'apertura_cubeta', ?, ?, ?)
            """, (fecha, hora, env_1lt, ref, nota))

        # cargar litreada 500ml
        if env_05lt > 0:
            conn.execute(
                "UPDATE inventario SET stock = stock + ? WHERE sku = 'M05'",
                (env_05lt,)
            )
            conn.execute("""
                INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
                VALUES (?, ?, 'M05', 'apertura_cubeta', ?, ?, ?)
            """, (fecha, hora, env_05lt, ref, nota))

        conn.commit()


# ── TICKET DE TEXTO (para imprimir) ──────────────────────────────────────────

def formatear_ticket(ticket_id: int) -> str:
    with get_conn() as conn:
        t = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        items = conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()

    if not t:
        return ""

    lineas = [
        "================================",
        "   Productos El Bayo",
        f"   {t['fecha']}  {t['hora']}",
        f"   Ticket #{t['id']:04d}",
        "--------------------------------",
    ]
    for item in items:
        cant  = f"{item['cantidad']:.3f}".rstrip("0").rstrip(".") if item["sku"] == "CHI" else f"{item['cantidad']:.0f}"
        lineas.append(f"  {item['descripcion'][:18]:<18}")
        lineas.append(f"  {cant} × ${item['precio_unit']:.2f} = ${item['subtotal']:.2f}")
    lineas += [
        "--------------------------------",
        f"  TOTAL:          ${t['total']:.2f}",
    ]
    if t["pago_efectivo"] > 0:
        lineas.append(f"  Efectivo:        ${t['pago_efectivo']:.2f}")
    if t["pago_transfer"] > 0:
        lineas.append(f"  Transferencia:   ${t['pago_transfer']:.2f}")
    if t["pago_tarjeta"] > 0:
        lineas.append(f"  Tarjeta:         ${t['pago_tarjeta']:.2f}")
    if t["cambio"] > 0:
        lineas.append(f"  CAMBIO:          ${t['cambio']:.2f}")
    lineas.append("================================")
    return "\n".join(lineas)


# ── INIT AUTOMÁTICO ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print(f"pos.db inicializada en {POS_DB}")
    inv = get_inventario()
    print(f"{len(inv)} SKUs registrados:")
    for row in inv:
        print(f"  {row['sku']:<8} {row['descripcion']:<20} ${row['precio_venta']:.2f}")
