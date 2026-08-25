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
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

from models import LT_POR_CUBETA, LT_POR_ENV_1LT, LT_POR_ENV_05LT

BASE_DIR = os.path.join(os.path.expanduser("~"), "bayosys", "data")
POS_DB   = os.path.join(BASE_DIR, "pos.db")

# tolerancia de punto flotante al comparar litros — 19.0 lt exactos deben
# emitir 1 cubeta, no 0 por un error de representación en el decimoquinto decimal
EPS_LT = 1e-6


# ── CONEXIÓN ─────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    os.makedirs(BASE_DIR, exist_ok=True)
    conn = sqlite3.connect(POS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def conectar():
    """
    Como get_conn(), pero cierra la conexión al salir — get_conn() por sí
    sola nunca la cierra (el protocolo 'with conn:' de sqlite3 solo hace
    commit/rollback, no close()).
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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

-- Tabulador de clientes de mayoreo — precio pactado por cliente
CREATE TABLE IF NOT EXISTS clientes_mayoreo (
    clave       TEXT PRIMARY KEY,
    nombre      TEXT NOT NULL,
    precio_kg   REAL NOT NULL,
    activo      INTEGER NOT NULL DEFAULT 1
);

-- Pedidos de mayoreo — compromisos de entrega, NO descuentan stock.
-- Son informativos: el operador decide en el momento cómo repartir
-- el chicharrón disponible usando esto como contexto (prioridad, cliente).
CREATE TABLE IF NOT EXISTS pedidos_mayoreo (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_pedido        TEXT NOT NULL,
    fecha_entrega       TEXT NOT NULL,
    cliente_clave       TEXT NOT NULL,
    kg                  REAL NOT NULL,
    precio_kg_pactado   REAL NOT NULL,
    prioridad           TEXT NOT NULL DEFAULT 'media',
    entregado           INTEGER NOT NULL DEFAULT 0,
    nota                TEXT,
    FOREIGN KEY (cliente_clave) REFERENCES clientes_mayoreo(clave)
);

-- Pool de manteca a granel — los litros sueltos de la cubeta fraccionada.
-- Es el ÚNICO lugar donde viven los litros que todavía no son cubeta sellada
-- (CUB) ni envase litreado (M1LT / M05). Un litro está en exactamente uno de
-- los tres lados, nunca en dos — esa es la regla que evita el doble conteo.
--
-- Ledger append-only: el saldo vigente es lt_saldo del último renglón.
-- Nunca se hace UPDATE, solo INSERT, para que el histórico sea auditable.
CREATE TABLE IF NOT EXISTS manteca_pool (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha            TEXT NOT NULL,
    hora             TEXT NOT NULL,
    tipo             TEXT NOT NULL,   -- 'produccion'|'apertura'|'litreado'|'ajuste'
    lt_delta         REAL NOT NULL,   -- litros que entran (+) o salen (-) del pool
    cubetas_emitidas REAL NOT NULL DEFAULT 0,   -- cubetas selladas que generó el movimiento
    lt_saldo         REAL NOT NULL,   -- saldo del pool DESPUÉS del movimiento
    referencia       TEXT,            -- batch#AAAAMMDD-N, apertura_cubeta_..., 'manual'
    nota             TEXT
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


def _columnas(conn: sqlite3.Connection, tabla: str) -> set:
    """
    Nombres de columna de una tabla. SQLite no soporta
    ADD COLUMN IF NOT EXISTS, así que hay que preguntar antes de cada ALTER
    o la segunda corrida truena con 'duplicate column name'.
    """
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}


def _migrar_pedidos_mayoreo(conn: sqlite3.Connection):
    """
    Cierra el agujero del despacho de mayoreo.

    Hasta ahora, entregar un pedido solo prendía el flag 'entregado': apagaba
    el ticker pero no dejaba rastro de cuándo salió, cuántos kilos salieron de
    verdad ni qué ticket cobró el dinero.

    Las tres columnas van nullable a propósito. Los pedidos que ya estaban
    entregados antes de esta migración no tienen esa información y no hay de
    dónde inventarla: NULL significa "se despachó antes de que existiera el
    registro", que no es lo mismo que un 0.

    'entregado' se conserva tal cual — sigue siendo el flag que filtra el
    ticker. No se convierte en 'estado TEXT': cancelados y entregas parciales
    están fuera de alcance.

    Idempotente. Se puede correr N veces.
    """
    existentes = _columnas(conn, "pedidos_mayoreo")

    migraciones = [
        # OJO: fecha_entrega_real es el evento. La columna vieja 'fecha_entrega'
        # es la promesa que se le hizo al cliente, editable y a futuro. Para
        # atribuir una venta a un día se usa esta, nunca aquella.
        ("fecha_entrega_real", "TEXT"),
        ("kg_real",            "REAL"),
        ("ticket_id",          "INTEGER"),
    ]

    for col, definicion in migraciones:
        if col not in existentes:
            conn.execute(f"ALTER TABLE pedidos_mayoreo ADD COLUMN {col} {definicion}")

    conn.commit()


def _migrar_tickets_redondeo(conn: sqlite3.Connection):
    """
    Guarda cuánto se perdonó (o se cobró de más) al redondear el efectivo.

    Sin esta columna el conteo físico de billetes nunca cuadra contra las
    ventas del turno: cada ticket redondeado deja unos centavos que aparecen
    como faltante sin explicación. Con ~100 tickets al día son decenas de
    pesos diarios de descuadre.

    NOT NULL DEFAULT 0 y no nullable: los tickets viejos se cobraron al
    centavo exacto, así que su diferencia de redondeo es cero de verdad —
    no es un dato ausente.

    Idempotente.
    """
    if "diferencia_redondeo" not in _columnas(conn, "tickets"):
        conn.execute(
            "ALTER TABLE tickets ADD COLUMN diferencia_redondeo REAL NOT NULL DEFAULT 0"
        )
    conn.commit()


def _migrar_cortes_caja(conn: sqlite3.Connection):
    """
    Dos renglones nuevos en el corte, para que el conteo físico de billetes
    tenga contra qué cuadrar:

        cambio_devuelto     — lo que salió del cajón como cambio
        redondeo_acumulado  — lo que el redondeo del efectivo movió en el turno

    El cambio importa más que el redondeo. 'ventas_efectivo' venía sumando
    'pago_efectivo', que es lo que el cliente ENTREGÓ, no lo que se quedó en
    la caja: un ticket de 50.10 pagado con un billete de 100 sumaba 100 y
    los 49.90 de cambio nunca se restaban. Con eso, 'a_entregar' pedía
    entregar dinero que ya se había devuelto.

    Idempotente.
    """
    cols = _columnas(conn, "cortes")
    if "cambio_devuelto" not in cols:
        conn.execute(
            "ALTER TABLE cortes ADD COLUMN cambio_devuelto REAL NOT NULL DEFAULT 0"
        )
    if "redondeo_acumulado" not in cols:
        conn.execute(
            "ALTER TABLE cortes ADD COLUMN redondeo_acumulado REAL NOT NULL DEFAULT 0"
        )
    conn.commit()


# ── INIT ─────────────────────────────────────────────────────────────────────

def init_db():
    """
    Crea tablas, migra schema si es necesario e inserta SKUs default.
    Seguro de llamar múltiples veces — no destruye datos existentes.
    """
    with conectar() as conn:
        conn.executescript(SCHEMA)
        _migrar_schema(conn)
        _migrar_pedidos_mayoreo(conn)
        _migrar_tickets_redondeo(conn)
        _migrar_cortes_caja(conn)

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
    with conectar() as conn:
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
    with conectar() as conn:
        return conn.execute("""
            SELECT * FROM inventario
            WHERE activo = 1 AND es_menu = 1
            ORDER BY orden_menu
        """).fetchall()

def get_sku(sku: str) -> Optional[sqlite3.Row]:
    with conectar() as conn:
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

    with conectar() as conn:
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
    with conectar() as conn:
        conn.execute(f"UPDATE inventario SET {sets} WHERE sku = ?", vals)
        conn.commit()

def desactivar_sku(sku: str):
    """
    Baja lógica — no borra físico, preserva historial de ventas.
    El SKU desaparece del POS y del inventario pero sus tickets quedan.
    """
    if sku in ("CHI", "LIB"):
        raise ValueError(f"SKU '{sku}' no se puede desactivar — es esencial")
    with conectar() as conn:
        conn.execute(
            "UPDATE inventario SET activo = 0, es_menu = 0 WHERE sku = ?", (sku,)
        )
        conn.commit()

def reactivar_sku(sku: str):
    with conectar() as conn:
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
    with conectar() as conn:
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
    with conectar() as conn:
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
    with conectar() as conn:
        return conn.execute(sql, params).fetchall()


# ── TICKETS ───────────────────────────────────────────────────────────────────

def crear_ticket(batch_id: str, operador: str,
                 items: list, pagos: dict, cambio: float,
                 diferencia_redondeo: float = 0.0) -> int:
    """
    Crea un ticket completo y descuenta stock.

    items: lista de dicts — sku, descripcion, cantidad, precio_unit, subtotal
    pagos: dict — efectivo, transfer, tarjeta
    Retorna el id del ticket creado.

    Regla de stock: solo 'libre' no descuenta (precio/desc libre, sin SKU en inventario).
    Todos los demás SKUs descuentan, incluyendo CHI (stock real de kg).

    diferencia_redondeo: lo que el redondeo del efectivo movió respecto del
    total. El campo 'total' guarda siempre la venta completa — el redondeo
    afecta la caja, no el precio.
    """
    ahora  = datetime.now()
    fecha  = ahora.strftime("%Y-%m-%d")
    hora   = ahora.strftime("%H:%M:%S")
    total  = sum(i["subtotal"] for i in items)
    p_ef   = pagos.get("efectivo", 0.0)
    p_tr   = pagos.get("transfer", 0.0)
    p_ta   = pagos.get("tarjeta",  0.0)

    with conectar() as conn:
        cur = conn.execute("""
            INSERT INTO tickets
            (fecha, hora, batch_id, operador,
             pago_efectivo, pago_transfer, pago_tarjeta,
             total, cambio, diferencia_redondeo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fecha, hora, batch_id, operador, p_ef, p_tr, p_ta,
              total, cambio, diferencia_redondeo))
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
    with conectar() as conn:
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
    with conectar() as conn:
        return conn.execute("""
            SELECT * FROM tickets
            WHERE fecha = ? AND anulado = 0
            ORDER BY id
        """, (fecha,)).fetchall()

def get_ticket_items(ticket_id: int) -> list:
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()

def marcar_impreso(ticket_id: int):
    with conectar() as conn:
        conn.execute("UPDATE tickets SET impreso = 1 WHERE id = ?", (ticket_id,))
        conn.commit()


# ── GASTOS ────────────────────────────────────────────────────────────────────

def registrar_gasto(batch_id: str, tipo: str,
                    monto: float, descripcion: str = "") -> int:
    ahora = datetime.now()
    with conectar() as conn:
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
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM gastos WHERE fecha = ? ORDER BY id", (fecha,)
        ).fetchall()

def get_gastos_batch(batch_id: str) -> list:
    with conectar() as conn:
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

    with conectar() as conn:
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

    def _campo(fila, nombre, default=0.0):
        """Tolera DBs migradas a medias: la columna puede no estar."""
        try:
            v = fila[nombre]
        except (IndexError, KeyError):
            return default
        return default if v is None else v

    # pago_efectivo es lo que el cliente ENTREGÓ, no lo que se quedó en la
    # caja. El cambio salió del mismo cajón, así que hay que restarlo para
    # saber cuánto efectivo hay de verdad.
    ef_recibido = sum(t["pago_efectivo"] for t in tickets)
    cambio_dev  = sum(_campo(t, "cambio") for t in tickets)
    redondeo    = sum(_campo(t, "diferencia_redondeo") for t in tickets)

    efectivo_caja = round(ef_recibido - cambio_dev, 2)   # billetes reales
    v_ef          = round(efectivo_caja - redondeo, 2)   # valor de venta cobrado en efectivo
    v_tr  = sum(t["pago_transfer"] for t in tickets)
    v_ta  = sum(t["pago_tarjeta"]  for t in tickets)

    # Las ventas salen del total de cada ticket, no de la suma de pagos:
    # el ticket vale lo que vale aunque el efectivo se haya redondeado.
    total = sum(_campo(t, "total") for t in tickets)
    g_tot = sum(g["monto"] for g in gastos)
    neto  = total - g_tot
    fondo = 250.0

    # efectivo esperado = ventas en efectivo + redondeo acumulado − gastos
    esperado = round(v_ef + redondeo - g_tot, 2)
    a_ent    = max(0.0, round(esperado - fondo, 2))

    return dict(
        n_tickets          = len(tickets),
        ventas_efectivo    = round(v_ef,  2),
        ventas_transfer    = round(v_tr,  2),
        ventas_tarjeta     = round(v_ta,  2),
        cambio_devuelto    = round(cambio_dev, 2),
        redondeo_acumulado = round(redondeo,   2),
        efectivo_esperado  = esperado,
        total_ventas       = round(total, 2),
        total_gastos       = round(g_tot, 2),
        neto               = round(neto,  2),
        fondo_caja         = fondo,
        a_entregar         = a_ent,
        gastos_detalle     = gastos,
        tickets            = tickets,
    )

def guardar_corte(batch_id: str, corte: dict, nota: str = "") -> int:
    ahora = datetime.now()
    with conectar() as conn:
        cur = conn.execute("""
            INSERT INTO cortes
            (fecha, hora, batch_id,
             ventas_efectivo, ventas_transfer, ventas_tarjeta,
             cambio_devuelto, redondeo_acumulado,
             total_ventas, total_gastos, neto, fondo_caja, a_entregar, nota)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
            batch_id,
            corte["ventas_efectivo"], corte["ventas_transfer"], corte["ventas_tarjeta"],
            corte.get("cambio_devuelto", 0.0), corte.get("redondeo_acumulado", 0.0),
            corte["total_ventas"],    corte["total_gastos"],
            corte["neto"],            corte["fondo_caja"], corte["a_entregar"],
            nota
        ))
        conn.commit()
        return cur.lastrowid

def get_cortes(fecha: str = None) -> list:
    if fecha is None:
        fecha = date.today().isoformat()
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM cortes WHERE fecha = ? ORDER BY id", (fecha,)
        ).fetchall()

def tiene_corte_guardado(batch_id: str) -> bool:
    """
    Retorna True si ya existe al menos un corte para este batch_id.
    Usado por guardian.py y hay_corte_pendiente() en main.py.
    """
    with conectar() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM cortes WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return row["n"] > 0


# ── RESUMEN VENTAS DÍA ────────────────────────────────────────────────────────

def get_items_vendidos_dia(sku: str, fecha: str = None) -> list:
    """
    Líneas de venta de un SKU en el día, ticket por ticket.
    A diferencia de resumen_ventas_dia() no agrega — conserva el precio real
    de cada transacción, que en cubetas se negocia venta por venta.
    """
    if fecha is None:
        fecha = date.today().isoformat()
    with conectar() as conn:
        return conn.execute("""
            SELECT ti.ticket_id, ti.cantidad, ti.precio_unit, ti.subtotal, t.hora
            FROM ticket_items ti
            JOIN tickets t ON ti.ticket_id = t.id
            WHERE t.fecha = ? AND t.anulado = 0 AND ti.sku = ?
            ORDER BY ti.ticket_id
        """, (fecha, sku)).fetchall()


def resumen_ventas_dia(fecha: str = None) -> dict:
    """Agrega ventas del día por SKU. Usado por cierre.py y analisis.py."""
    if fecha is None:
        fecha = date.today().isoformat()
    with conectar() as conn:
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


# ── POOL DE MANTECA A GRANEL ──────────────────────────────────────────────────
# Modelo físico: la manteca vive en tres lados y en uno solo a la vez.
#
#   pool (manteca_pool)  →  litros sueltos = la cubeta fraccionada abierta
#   CUB  (inventario)    →  cubetas selladas de 19 lt
#   M1LT / M05           →  envases ya litreados, listos para vender
#
# Un batch nunca cuadra a cubeta exacta (26 kg de grasa = 1 cubeta, pero los
# batches son de 25, 30, 35, 40 kg). Entonces la producción entra al pool y el
# pool emite SOLO cubetas completas al inventario; la fracción queda flotando
# para el día siguiente. La emisión ocurre únicamente al cargar producción —
# abrir una cubeta nunca vuelve a sellar otra.

def _pool_saldo(conn: sqlite3.Connection) -> float:
    """Saldo vigente del pool = lt_saldo del último renglón del ledger."""
    row = conn.execute(
        "SELECT lt_saldo FROM manteca_pool ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return float(row["lt_saldo"]) if row else 0.0


def _pool_asentar(conn: sqlite3.Connection, tipo: str, lt_delta: float,
                  saldo_nuevo: float, referencia: str, nota: str = "",
                  cubetas_emitidas: float = 0.0):
    """Escribe un renglón del ledger. Append-only, nunca UPDATE."""
    ahora = datetime.now()
    conn.execute("""
        INSERT INTO manteca_pool
        (fecha, hora, tipo, lt_delta, cubetas_emitidas, lt_saldo, referencia, nota)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"), tipo,
          round(lt_delta, 4), cubetas_emitidas, round(saldo_nuevo, 4),
          referencia, nota))


def _mov_inv(conn: sqlite3.Connection, sku: str, tipo: str, cantidad: float,
             referencia: str, nota: str = ""):
    """Mueve stock de un SKU y deja el rastro en movimientos_inv."""
    ahora = datetime.now()
    conn.execute(
        "UPDATE inventario SET stock = stock + ? WHERE sku = ?", (cantidad, sku)
    )
    conn.execute("""
        INSERT INTO movimientos_inv (fecha, hora, sku, tipo, cantidad, referencia, nota)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
          sku, tipo, cantidad, referencia, nota))


def get_pool_manteca() -> float:
    """Litros de manteca a granel pendientes de envasar o completar cubeta."""
    with conectar() as conn:
        return _pool_saldo(conn)


def get_pool_historial(limit: int = 30) -> list:
    """Ledger del pool, más reciente primero."""
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM manteca_pool ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def cargar_produccion_batch(batch_id: str, kg_chi: float,
                            lt_manteca: float) -> dict:
    """
    Carga al POS la producción de un batch recién registrado, en UNA transacción:

      - kg_chi        → stock CHI
      - lt_manteca    → pool, que emite al stock CUB las cubetas completas
                        que alcancen y deja la fracción flotando

    Idempotente: si el batch ya se cargó, no hace nada y lo reporta. Evita que
    un reintento tras un error a medias duplique el inventario del día.
    """
    referencia = f"batch#{batch_id}"

    with conectar() as conn:
        ya = conn.execute("""
            SELECT 1 FROM movimientos_inv
            WHERE referencia = ? AND tipo = 'produccion' LIMIT 1
        """, (referencia,)).fetchone()
        if ya:
            return dict(
                ya_cargado       = True,
                kg_chi           = 0.0,
                lt_ingresados    = 0.0,
                pool_antes       = _pool_saldo(conn),
                cubetas_emitidas = 0.0,
                pool_despues     = _pool_saldo(conn),
            )

        if kg_chi > 0:
            _mov_inv(conn, "CHI", "produccion", kg_chi, referencia,
                     f"{kg_chi:.3f} kg de batch {batch_id}")

        pool_antes = _pool_saldo(conn)
        total      = pool_antes + lt_manteca

        # solo cubetas COMPLETAS pasan al inventario; el resto sigue flotando
        cubetas  = float(int((total + EPS_LT) / LT_POR_CUBETA))
        remanente = max(0.0, round(total - cubetas * LT_POR_CUBETA, 4))

        if cubetas > 0:
            _mov_inv(conn, "CUB", "produccion", cubetas, referencia,
                     f"{cubetas:.0f} cub de {total:.2f} lt acumulados")

        _pool_asentar(conn, "produccion", lt_manteca, remanente, referencia,
                      nota=f"{lt_manteca:.2f} lt de batch {batch_id}",
                      cubetas_emitidas=cubetas)
        conn.commit()

    return dict(
        ya_cargado       = False,
        kg_chi           = kg_chi,
        lt_ingresados    = round(lt_manteca, 3),
        pool_antes       = round(pool_antes, 3),
        cubetas_emitidas = cubetas,
        pool_despues     = remanente,
    )


def ajustar_pool_manteca(nuevo_saldo: float, nota: str = "") -> dict:
    """
    Fija el pool al conteo físico real (merma, derrame, recuento de bodega).
    Registra la diferencia como ajuste en el ledger.
    """
    if nuevo_saldo < 0:
        raise ValueError("el pool no puede ser negativo")
    with conectar() as conn:
        antes = _pool_saldo(conn)
        _pool_asentar(conn, "ajuste", nuevo_saldo - antes, nuevo_saldo,
                      "manual", nota or "ajuste por conteo físico")
        conn.commit()
    return dict(antes=round(antes, 3), despues=round(nuevo_saldo, 3),
                delta=round(nuevo_saldo - antes, 3))


# ── APERTURA DE CUBETA Y LITREADO ─────────────────────────────────────────────

def abrir_cubeta(env_1lt: int, env_05lt: int, nota: str = "") -> dict:
    """
    Abre una cubeta sellada para litrear: -1 CUB, +19 lt al pool, y de ahí
    salen los envases que se llenen.

    Los litros que sobran de la cubeta abierta se quedan en el pool en vez de
    desaparecer — antes, abrir una cubeta y llenar 10 envases de 1lt evaporaba
    9 litros del sistema.
    """
    lt_envasados = env_1lt * LT_POR_ENV_1LT + env_05lt * LT_POR_ENV_05LT
    ahora = datetime.now()
    ref   = f"apertura_cubeta_{ahora.strftime('%Y-%m-%d_%H:%M')}"

    with conectar() as conn:
        disponible = _pool_saldo(conn) + LT_POR_CUBETA
        if lt_envasados > disponible + EPS_LT:
            raise ValueError(
                f"no alcanzan los litros — la cubeta más el pool dan "
                f"{disponible:.2f} lt y pediste envasar {lt_envasados:.2f} lt"
            )

        _mov_inv(conn, "CUB", "apertura_cubeta", -1, ref, nota)
        saldo = round(_pool_saldo(conn) + LT_POR_CUBETA, 4)
        _pool_asentar(conn, "apertura", LT_POR_CUBETA, saldo, ref,
                      nota or "cubeta abierta para litrear",
                      cubetas_emitidas=-1)

        saldo = _pool_a_envases(conn, env_1lt, env_05lt, saldo, ref, nota)
        conn.commit()

    return dict(env_1lt=env_1lt, env_05lt=env_05lt,
                lt_envasados=round(lt_envasados, 3), pool_despues=saldo)


def litrear_de_pool(env_1lt: int, env_05lt: int, nota: str = "") -> dict:
    """
    Llena envases con la manteca de la cubeta ya abierta, sin romper una nueva.
    Es lo que le da salida al remanente que dejó la producción.
    """
    lt_envasados = env_1lt * LT_POR_ENV_1LT + env_05lt * LT_POR_ENV_05LT

    with conectar() as conn:
        saldo = _pool_saldo(conn)
        if lt_envasados > saldo + EPS_LT:
            raise ValueError(
                f"el pool solo tiene {saldo:.2f} lt y pediste envasar "
                f"{lt_envasados:.2f} lt — abre una cubeta primero"
            )
        ref   = f"litreado_{datetime.now().strftime('%Y-%m-%d_%H:%M')}"
        saldo = _pool_a_envases(conn, env_1lt, env_05lt, saldo, ref, nota)
        conn.commit()

    return dict(env_1lt=env_1lt, env_05lt=env_05lt,
                lt_envasados=round(lt_envasados, 3), pool_despues=saldo)


def _pool_a_envases(conn: sqlite3.Connection, env_1lt: int, env_05lt: int,
                    saldo: float, referencia: str, nota: str) -> float:
    """Convierte litros del pool en envases M1LT/M05. Devuelve el saldo nuevo."""
    lt_envasados = env_1lt * LT_POR_ENV_1LT + env_05lt * LT_POR_ENV_05LT
    if lt_envasados <= 0:
        return saldo

    if env_1lt > 0:
        _mov_inv(conn, "M1LT", "apertura_cubeta", env_1lt, referencia, nota)
    if env_05lt > 0:
        _mov_inv(conn, "M05", "apertura_cubeta", env_05lt, referencia, nota)

    saldo = max(0.0, round(saldo - lt_envasados, 4))
    _pool_asentar(conn, "litreado", -lt_envasados, saldo, referencia,
                  nota or f"{env_1lt}×1lt  {env_05lt}×½lt")
    return saldo


# ── FORMATO TICKET DE TEXTO ───────────────────────────────────────────────────

def formatear_ticket(ticket_id: int) -> str:
    with conectar() as conn:
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

# ── CLIENTES DE MAYOREO ────────────────────────────────────────────────────────

def agregar_cliente_mayoreo(clave: str, nombre: str, precio_kg: float) -> bool:
    """
    Da de alta un cliente de mayoreo en el tabulador.
    Si ya existe (aunque inactivo), lo reactiva y actualiza precio.
    Retorna True si fue creado, False si ya existía (actualizado).
    """
    with conectar() as conn:
        existente = conn.execute(
            "SELECT clave FROM clientes_mayoreo WHERE clave = ?", (clave,)
        ).fetchone()
        if existente:
            conn.execute("""
                UPDATE clientes_mayoreo
                SET nombre = ?, precio_kg = ?, activo = 1
                WHERE clave = ?
            """, (nombre, precio_kg, clave))
            conn.commit()
            return False
        else:
            conn.execute("""
                INSERT INTO clientes_mayoreo (clave, nombre, precio_kg, activo)
                VALUES (?, ?, ?, 1)
            """, (clave, nombre, precio_kg))
            conn.commit()
            return True

def get_clientes_mayoreo(solo_activos: bool = True) -> list:
    with conectar() as conn:
        if solo_activos:
            return conn.execute(
                "SELECT * FROM clientes_mayoreo WHERE activo = 1 ORDER BY nombre"
            ).fetchall()
        return conn.execute(
            "SELECT * FROM clientes_mayoreo ORDER BY nombre"
        ).fetchall()

def get_cliente_mayoreo(clave: str) -> Optional[sqlite3.Row]:
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM clientes_mayoreo WHERE clave = ?", (clave,)
        ).fetchone()


# ── PEDIDOS DE MAYOREO ──────────────────────────────────────────────────────────
# NO tocan CHI.stock — son informativos. El operador decide en vivo
# cómo repartir el chicharrón disponible usando esto como contexto.

_ORDEN_PRIORIDAD = {"muy_alta": 0, "alta": 1, "media": 2, "baja": 3}

def crear_pedido_mayoreo(cliente_clave: str, kg: float, fecha_entrega: str,
                         prioridad: str = "media", nota: str = "") -> int:
    if prioridad not in _ORDEN_PRIORIDAD:
        raise ValueError(f"prioridad inválida: '{prioridad}'")

    cliente = get_cliente_mayoreo(cliente_clave)
    if cliente is None:
        raise ValueError(f"cliente '{cliente_clave}' no existe en el tabulador")

    ahora = datetime.now()
    with conectar() as conn:
        cur = conn.execute("""
            INSERT INTO pedidos_mayoreo
            (fecha_pedido, fecha_entrega, cliente_clave, kg,
             precio_kg_pactado, prioridad, nota)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ahora.strftime("%Y-%m-%d"), fecha_entrega, cliente_clave, kg,
              cliente["precio_kg"], prioridad, nota))
        conn.commit()
        return cur.lastrowid

def editar_pedido_mayoreo(pedido_id: int, kg: float = None,
                          prioridad: str = None, nota: str = None):
    """Edita un pedido pendiente — ej. renegociar kg antes de entregar."""
    campos = {}
    if kg is not None:
        campos["kg"] = kg
    if prioridad is not None:
        if prioridad not in _ORDEN_PRIORIDAD:
            raise ValueError(f"prioridad inválida: '{prioridad}'")
        campos["prioridad"] = prioridad
    if nota is not None:
        campos["nota"] = nota
    if not campos:
        return

    sets = ", ".join(f"{k} = ?" for k in campos)
    vals = list(campos.values()) + [pedido_id]
    with conectar() as conn:
        conn.execute(f"UPDATE pedidos_mayoreo SET {sets} WHERE id = ?", vals)
        conn.commit()

def get_pedido_mayoreo(pedido_id: int):
    """
    Un pedido individual con el nombre del cliente resuelto.
    Retorna None si no existe. Solo lectura.
    """
    with conectar() as conn:
        return conn.execute("""
            SELECT p.*, c.nombre as cliente_nombre
            FROM pedidos_mayoreo p
            JOIN clientes_mayoreo c ON p.cliente_clave = c.clave
            WHERE p.id = ?
        """, (pedido_id,)).fetchone()

def marcar_pedido_entregado(pedido_id: int, kg_real: float,
                            ticket_id: int, fecha_entrega_real: str):
    """
    Cierra el pedido: lo saca del ticker y deja el rastro del despacho —
    kilos reales sobre la báscula, ticket que cobró y día en que salió.

    SQL puro y sin validaciones a propósito. Que el pedido exista, que siga
    pendiente y que los kilos sean plausibles se verifica en pos.py, antes
    de cobrar. Aquí ya se cobró: esto solo escribe.
    """
    with conectar() as conn:
        conn.execute("""
            UPDATE pedidos_mayoreo
            SET entregado          = 1,
                kg_real            = ?,
                ticket_id          = ?,
                fecha_entrega_real = ?
            WHERE id = ?
        """, (kg_real, ticket_id, fecha_entrega_real, pedido_id))
        conn.commit()

def get_pedidos_pendientes() -> list:
    """
    Pedidos no entregados, ordenados por prioridad (muy_alta primero)
    y luego por fecha de entrega. Fuente única para el ticker y la
    pantalla de status del menú mayoreo.
    """
    with conectar() as conn:
        rows = conn.execute("""
            SELECT p.*, c.nombre as cliente_nombre
            FROM pedidos_mayoreo p
            JOIN clientes_mayoreo c ON p.cliente_clave = c.clave
            WHERE p.entregado = 0
            ORDER BY p.fecha_entrega
        """).fetchall()
    return sorted(rows, key=lambda r: _ORDEN_PRIORIDAD.get(r["prioridad"], 9))

def get_pedidos_dia(fecha_pedido: str = None) -> list:
    """Todos los pedidos tomados en una fecha, entregados o no."""
    if fecha_pedido is None:
        fecha_pedido = date.today().isoformat()
    with conectar() as conn:
        return conn.execute("""
            SELECT p.*, c.nombre as cliente_nombre
            FROM pedidos_mayoreo p
            JOIN clientes_mayoreo c ON p.cliente_clave = c.clave
            WHERE p.fecha_pedido = ?
            ORDER BY p.id
        """, (fecha_pedido,)).fetchall()


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
