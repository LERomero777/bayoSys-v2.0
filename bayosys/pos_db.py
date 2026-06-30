"""
pos_db.py — bayoSys · Productos El Bayo
Capa de datos SQLite para el POS.
No sabe nada de curses ni de lógica de negocio.
Solo crea tablas, inserta y consulta.

Base de datos: ~/bayosys/data/pos.db

COLUMNAS NUEVAS (migración automática no-destructiva):
  inventario.tipo_venta   — cómo se vende el producto
  inventario.origen       — cómo entra el stock
  inventario.es_menu      — aparece en panel POS
  inventario.orden_menu   — posición tecla 1-9 en el panel
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


# ── SCHEMA BASE ───────────────────────────────────────────────────────────────

SCHEMA = """
-- Catálogo de productos — dinámico, no hardcodeado en código
CREATE TABLE IF NOT EXISTS inventario (
    sku              TEXT PRIMARY KEY,
    descripcion      TEXT NOT NULL,
    stock            REAL NOT NULL DEFAULT 0,
    unidad           TEXT NOT NULL DEFAULT 'pza',
    precio_venta     REAL NOT NULL DEFAULT 0,

    -- cómo se vende este producto en el POS
    -- 'normal'   → cantidad entera, precio fijo de DB
    -- 'peso'     → cantidad decimal en kg (ej: chicharrón)
    -- 'variable' → precio se negocia en el momento (ej: cubeta)
    -- 'libre'    → descripción y precio libres (artículo no catalogado)
    tipo_venta       TEXT NOT NULL DEFAULT 'normal',

    -- de dónde viene el stock
    -- 'produccion' → se carga automáticamente desde registro_batch
    -- 'apertura'   → se carga al abrir cubeta (M1LT, M05)
    -- 'manual'     → carga manual desde admin o POS
    origen           TEXT NOT NULL DEFAULT 'manual',

    -- control de visibilidad en panel POS
    es_menu          INTEGER NOT NULL DEFAULT 1,   -- 1=aparece, 0=solo bodega
    orden_menu       INTEGER NOT NULL DEFAULT 99,  -- posición tecla 1-9

    -- compatibilidad con versión anterior
    es_precio_variable INTEGER NOT NULL DEFAULT 0,

    activo           INTEGER NOT NULL DEFAULT 1
);

-- Movimientos de inventario — auditoría completa
CREATE TABLE IF NOT EXISTS movimientos_inv (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT NOT NULL,
    hora        TEXT NOT NULL,
    sku         TEXT NOT NULL,
    tipo        TEXT NOT NULL,  -- 'venta'|'carga'|'apertura_cubeta'|'ajuste'|'produccion'
    cantidad    REAL NOT NULL,  -- positivo=entrada, negativo=salida
    referencia  TEXT,           -- ticket_id, batch_id o 'manual'
    nota        TEXT,
    FOREIGN KEY (sku) REFERENCES inventario(sku)
);

-- Tickets de venta
CREATE TABLE IF NOT EXISTS tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL,
    hora                TEXT NOT NULL,
    batch_id            TEXT,        -- formato AAAAMMDD-N
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
    batch_id    TEXT,
    tipo        TEXT NOT NULL,  -- 'gas'|'leche'|'gral'
    descripcion TEXT,
    monto       REAL NOT NULL
);

-- Cortes de caja
CREATE TABLE IF NOT EXISTS cortes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL,
    hora                TEXT NOT NULL,
    batch_id            TEXT,
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

# SKUs del negocio — se insertan solo si no existen (INSERT OR IGNORE)
# tipo_venta y origen definen el comportamiento en el POS
SKUs_DEFAULT = [
    # sku, desc, stock, unidad, precio, tipo_venta, origen, es_menu, orden, es_precio_variable
    ("CHI",  "Chicharrón",    0, "kg",  230.0, "peso",     "produccion", 1, 1, 0),
    ("M1LT", "Manteca 1lt",   0, "pza",  35.0, "normal",   "apertura",   1, 2, 0),
    ("M05",  "Manteca 500ml", 0, "pza",  20.0, "normal",   "apertura",   1, 3, 0),
    ("CHO",  "Chorizo",       0, "pza",  30.0, "normal",   "manual",     1, 4, 0),
    ("CUB",  "Cubeta 19lt",   0, "pza", 500.0, "variable", "produccion", 1, 5, 1),
    ("LIB",  "Artículo libre",0, "pza",   0.0, "libre",    "manual",     1, 9, 1),
]


# ── MIGRACIÓN NO-DESTRUCTIVA ──────────────────────────────────────────────────

def _migrar_schema(conn: sqlite3.Connection):
    """
    Agrega columnas nuevas si no existen.
    Idempotente — se puede llamar múltiples veces sin romper nada.
    Los datos existentes nunca se tocan.
    """
    columnas_existentes = {
        row[1] for row in conn.execute("PRAGMA table_info(inventario)").fetchall()
    }

    migraciones = [
        ("tipo_venta",          "TEXT NOT NULL DEFAULT 'normal'"),
        ("origen",              "TEXT NOT NULL DEFAULT 'manual'"),
        ("es_menu",             "INTEGER NOT NULL DEFAULT 1"),
        ("orden_menu",          "INTEGER NOT NULL DEFAULT 99"),
        ("es_precio_variable",  "INTEGER NOT NULL DEFAULT 0"),
    ]

    for col, definicion in migraciones:
        if col not in columnas_existentes:
            conn.execute(f"ALTER TABLE inventario ADD COLUMN {col} {definicion}")

    # actualizar tipo_venta y origen en SKUs existentes que todavía tienen defaults
    actualizaciones = [
        ("CHI",  "peso",     "produccion"),
        ("M1LT", "normal",   "apertura"),
        ("M05",  "normal",   "apertura"),
        ("CHO",  "normal",   "manual"),
        ("CUB",  "variable", "produccion"),
        ("LIB",  "libre",    "manual"),
    ]
    for sku, tipo, origen in actualizaciones:
        conn.execute("""
            UPDATE inventario
            SET tipo_venta = ?, origen = ?
            WHERE sku = ? AND tipo_venta = 'normal' AND sku IN ('CHI','CUB','LIB')
        """, (tipo, origen, sku))

    # asignar orden_menu a los SKUs default que tengan 99
    ordenes = [("CHI",1),("M1LT",2),("M05",3),("CHO",4),("CUB",5),("LIB",9)]
    for sku, orden in ordenes:
        conn.execute("""
            UPDATE inventario SET orden_menu = ?
            WHERE sku = ? AND orden_menu = 99
        """, (orden, sku))

    conn.commit()


# ── INIT ─────────────────────────────────────────────────────────────────────

def init_db():
    """
    Crea tablas, migra schema si es necesario e inserta SKUs default.
    Seguro de llamar múltiples veces — no destruye datos existentes.
    """
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrar_schema(conn)

        for sku, desc, stock, unidad, precio, tipo, origen, es_menu, orden, var in SKUs_DEFAULT:
            conn.execute("""
                INSERT OR IGNORE INTO inventario
                (sku, descripcion, stock, unidad, precio_venta,
                 tipo_venta, origen, es_menu, orden_menu, es_precio_variable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sku, desc, stock, unidad, precio, tipo, origen, es_menu, orden, var))

        conn.commit()


# ── CATÁLOGO — LECTURA ────────────────────────────────────────────────────────

def get_inventario() -> list:
    """Todos los SKUs activos — para pantalla de inventario."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM inventario
            WHERE activo = 1
            ORDER BY orden_menu, sku
        """).fetchall()

def get_menu_pos() -> list:
    """
    Solo los SKUs que aparecen en el panel del POS, ordenados por orden_menu.
    Este es el reemplazo de MENU_PRODUCTOS hardcodeado.
    """
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM inventario
            WHERE activo = 1 AND es_menu = 1
            ORDER BY orden_menu
        """).fetchall()

def get_sku(sku: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM inventario WHERE sku = ?", (sku,)
        ).fetchone()


# ── CATÁLOGO — ESCRITURA (admin) ──────────────────────────────────────────────

def agregar_sku_catalogo(sku: str, descripcion: str, unidad: str,
                          precio: float, tipo_venta: str = "normal",
                          origen: str = "manual",
                          es_menu: int = 1, orden_menu: int = 99) -> bool:
    """
    Da de alta un producto nuevo en el catálogo.
    Si el SKU ya existe (aunque esté inactivo) lo reactiva y actualiza.
    Retorna True si fue creado, False si ya existía (actualizado).
    """
    if tipo_venta not in ("normal", "peso", "variable", "libre"):
        raise ValueError(f"tipo_venta inválido: '{tipo_venta}'")
    if unidad not in ("pza", "kg"):
        raise ValueError(f"unidad inválida: '{unidad}'")

    with get_conn() as conn:
        existente = conn.execute(
            "SELECT sku FROM inventario WHERE sku = ?", (sku,)
        ).fetchone()

        if existente:
            conn.execute("""
                UPDATE inventario
                SET descripcion=?, unidad=?, precio_venta=?,
                    tipo_venta=?, origen=?, es_menu=?, orden_menu=?,
                    es_precio_variable=?, activo=1
                WHERE sku=?
            """, (descripcion, unidad, precio, tipo_venta, origen,
                  es_menu, orden_menu,
                  1 if tipo_venta in ("variable","libre") else 0,
                  sku))
            conn.commit()
            return False  # existía, actualizado
        else:
            conn.execute("""
                INSERT INTO inventario
                (sku, descripcion, stock, unidad, precio_venta,
                 tipo_venta, origen, es_menu, orden_menu, es_precio_variable)
                VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """, (sku, descripcion, unidad, precio, tipo_venta, origen,
                  es_menu, orden_menu,
                  1 if tipo_venta in ("variable","libre") else 0))
            conn.commit()
            return True  # creado nuevo

def editar_sku(sku: str, **kwargs):
    """
    Edita campos de un SKU existente.
    Campos editables: descripcion, precio_venta, orden_menu, es_menu, activo
    Uso: editar_sku('TORT', precio_venta=18.0, orden_menu=6)
    """
    campos_permitidos = {
        "descripcion", "precio_venta", "orden_menu", "es_menu", "activo", "unidad"
    }
    campos = {k: v for k, v in kwargs.items() if k in campos_permitidos}
    if not campos:
        return

    sets  = ", ".join(f"{k} = ?" for k in campos)
    vals  = list(campos.values()) + [sku]
    with get_conn() as conn:
        conn.execute(f"UPDATE inventario SET {sets} WHERE sku = ?", vals)
        conn.commit()

def desactivar_sku(sku: str):
    """
    Baja lógica — no borra físico, preserva historial de ventas.
    El SKU desaparece del POS y del inventario pero sus tickets quedan.
    """
    if sku in ("CHI", "LIB"):
        raise ValueError(f"SKU '{sku}' no se puede desactivar — es esencial")
    with get_conn() as conn:
        conn.execute(
            "UPDATE inventario SET activo = 0, es_menu = 0 WHERE sku = ?", (sku,)
        )
        conn.commit()

def reactivar_sku(sku: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE inventario SET activo = 1, es_menu = 1 WHERE sku = ?", (sku,)
        )
        conn.commit()


# ── INVENTARIO — MOVIMIENTOS ──────────────────────────────────────────────────

def actualizar_stock(sku: str, delta: float,
                     tipo: str, referencia: str = "manual", nota: str = ""):
    """
    Modifica el stock de un SKU en delta (positivo=entrada, negativo=salida).
    Registra el movimiento en movimientos_inv para auditoría completa.
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
            INSERT INTO movimientos_inv
            (fecha, hora, sku, tipo, cantidad, referencia, nota)
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
    """Compatibilidad con código anterior — usa agregar_sku_catalogo() internamente."""
    tipo = "variable" if es_variable else "normal"
    agregar_sku_catalogo(sku, descripcion, unidad, precio, tipo_venta=tipo)

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

def crear_ticket(batch_id: str, operador: str,
                 items: list, pagos: dict, cambio: float) -> int:
    """
    Crea un ticket completo y descuenta stock.

    items: lista de dicts — sku, descripcion, cantidad, precio_unit, subtotal
    pagos: dict — efectivo, transfer, tarjeta
    Retorna el id del ticket creado.

    Regla de stock: solo 'libre' no descuenta (precio/desc libre, sin SKU en inventario).
    Todos los demás SKUs descuentan, incluyendo CHI (stock real de kg).
    """
    ahora  = datetime.now()
    fecha  = ahora.strftime("%Y-%m-%d")
    hora   = ahora.strftime("%H:%M:%S")
    total  = sum(i["subtotal"] for i in items)
    p_ef   = pagos.get("efectivo", 0.0)
    p_tr   = pagos.get("transfer", 0.0)
    p_ta   = pagos.get("tarjeta",  0.0)

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

            # descontar stock — solo LIB no descuenta
            if item["sku"] != "LIB":
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
    """Marca ticket como anulado y revierte el stock de todos sus items."""
    with get_conn() as conn:
        items = conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()
        conn.execute(
            "UPDATE tickets SET anulado = 1 WHERE id = ?", (ticket_id,)
        )
        ahora = datetime.now()
        for item in items:
            if item["sku"] != "LIB":
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
        conn.execute("UPDATE tickets SET impreso = 1 WHERE id = ?", (ticket_id,))
        conn.commit()


# ── GASTOS ────────────────────────────────────────────────────────────────────

def registrar_gasto(batch_id: str, tipo: str,
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
            "SELECT * FROM gastos WHERE fecha = ? ORDER BY id", (fecha,)
        ).fetchall()

def get_gastos_batch(batch_id: str) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM gastos WHERE batch_id = ? ORDER BY id", (batch_id,)
        ).fetchall()


# ── CORTES ────────────────────────────────────────────────────────────────────

def calcular_corte(batch_id: str = None, fecha: str = None) -> dict:
    """
    Calcula totales para un corte de caja.
    batch_id → filtra ese batch específico.
    fecha    → filtra todo el día.
    ninguno  → usa fecha de hoy.
    """
    if fecha is None:
        fecha = date.today().isoformat()

    with get_conn() as conn:
        if batch_id is not None:
            tickets = conn.execute("""
                SELECT * FROM tickets WHERE batch_id = ? AND anulado = 0
            """, (batch_id,)).fetchall()
            gastos = conn.execute(
                "SELECT * FROM gastos WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        else:
            tickets = conn.execute("""
                SELECT * FROM tickets WHERE fecha = ? AND anulado = 0
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
    a_ent = max(0.0, v_ef - g_tot - fondo)

    return dict(
        n_tickets       = len(tickets),
        ventas_efectivo = round(v_ef,  2),
        ventas_transfer = round(v_tr,  2),
        ventas_tarjeta  = round(v_ta,  2),
        total_ventas    = round(total, 2),
        total_gastos    = round(g_tot, 2),
        neto            = round(neto,  2),
        fondo_caja      = fondo,
        a_entregar      = round(a_ent, 2),
        gastos_detalle  = gastos,
        tickets         = tickets,
    )

def guardar_corte(batch_id: str, corte: dict, nota: str = "") -> int:
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
            corte["total_ventas"],    corte["total_gastos"],
            corte["neto"],            corte["fondo_caja"], corte["a_entregar"],
            nota
        ))
        conn.commit()
        return cur.lastrowid

def get_cortes(fecha: str = None) -> list:
    if fecha is None:
        fecha = date.today().isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cortes WHERE fecha = ? ORDER BY id", (fecha,)
        ).fetchall()

def tiene_corte_guardado(batch_id: str) -> bool:
    """
    Retorna True si ya existe al menos un corte para este batch_id.
    Usado por guardian.py y hay_corte_pendiente() en main.py.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM cortes WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return row["n"] > 0


# ── RESUMEN VENTAS DÍA ────────────────────────────────────────────────────────

def resumen_ventas_dia(fecha: str = None) -> dict:
    """Agrega ventas del día por SKU. Usado por cierre.py y analisis.py."""
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
    Descuenta 1 CUB → carga env_1lt a M1LT y env_05lt a M05.
    """
    ahora = datetime.now()
    fecha = ahora.strftime("%Y-%m-%d")
    hora  = ahora.strftime("%H:%M")
    ref   = f"apertura_cubeta_{fecha}_{hora}"

    with get_conn() as conn:
        conn.execute("UPDATE inventario SET stock = stock - 1 WHERE sku = 'CUB'")
        conn.execute("""
            INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
            VALUES (?, ?, 'CUB', 'apertura_cubeta', -1, ?, ?)
        """, (fecha, hora, ref, nota))

        if env_1lt > 0:
            conn.execute(
                "UPDATE inventario SET stock = stock + ? WHERE sku = 'M1LT'",
                (env_1lt,)
            )
            conn.execute("""
                INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
                VALUES (?, ?, 'M1LT', 'apertura_cubeta', ?, ?, ?)
            """, (fecha, hora, env_1lt, ref, nota))

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


# ── FORMATO TICKET DE TEXTO ───────────────────────────────────────────────────

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
        "     Productos El Bayo",
        f"   {t['fecha']}  {t['hora']}",
        f"   Ticket #{t['id']:04d}  |  {t['operador']}",
        "--------------------------------",
    ]
    for item in items:
        row = get_sku(item["sku"])
        tipo = row["tipo_venta"] if row else "normal"
        if tipo == "peso":
            cant = f"{item['cantidad']:.3f}kg".rstrip("0").rstrip(".")
        else:
            cant = f"{int(item['cantidad'])} pza"
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
    print(f"\npos.db inicializada en {POS_DB}")
    inv = get_inventario()
    print(f"\n{len(inv)} SKUs en catálogo:")
    print(f"  {'SKU':<8} {'Descripción':<20} {'Tipo':<10} {'Origen':<12} "
          f"{'Menú':>5} {'Orden':>6} {'Stock':>7} {'Precio':>9}")
    print("  " + "─" * 78)
    for row in inv:
        print(f"  {row['sku']:<8} {row['descripcion']:<20} "
              f"{row['tipo_venta']:<10} {row['origen']:<12} "
              f"{'sí' if row['es_menu'] else 'no':>5} "
              f"{row['orden_menu']:>6} "
              f"{row['stock']:>7.1f} "
              f"${row['precio_venta']:>8.2f}")
    print()