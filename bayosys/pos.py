"""
pos.py — bayoSys · Productos El Bayo
Lógica de negocio del POS. No sabe nada de curses.
Orquesta tickets, inventario, cortes y gastos.

Flujo principal:
    1. iniciar_sesion(batch_id) — abre el POS para un batch
    2. agregar_item()           — agrega línea al ticket en curso
    3. cobrar()                 — cierra el ticket, descuenta stock
    4. registrar_gasto()        — gas, leche, gasto general
    5. hacer_corte()            — resumen del turno, entrega de caja
"""

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

from pos_db import (
    init_db, get_inventario, get_sku, actualizar_stock,
    actualizar_precio, agregar_sku, abrir_cubeta,
    crear_ticket, anular_ticket, marcar_impreso,
    get_tickets_dia, get_ticket_items,
    registrar_gasto as db_registrar_gasto,
    get_gastos_dia, get_gastos_batch,
    calcular_corte, guardar_corte,
    resumen_ventas_dia, formatear_ticket, get_movimientos,
    agregar_cliente_mayoreo, get_clientes_mayoreo, get_cliente_mayoreo,
    crear_pedido_mayoreo, editar_pedido_mayoreo, marcar_pedido_entregado,
    get_pedidos_pendientes, get_pedidos_dia
)
from config import cargar_config, fecha_hoy


# ── TICKET EN CURSO ───────────────────────────────────────────────────────────

@dataclass
class ItemTicket:
    sku:         str
    descripcion: str
    cantidad:    float
    precio_unit: float

    @property
    def subtotal(self) -> float:
        return round(self.cantidad * self.precio_unit, 2)

    def to_dict(self) -> dict:
        return dict(
            sku         = self.sku,
            descripcion = self.descripcion,
            cantidad    = self.cantidad,
            precio_unit = self.precio_unit,
            subtotal    = self.subtotal,
        )


@dataclass
class TicketActual:
    batch_id: int
    operador: str
    items:    List[ItemTicket] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(i.subtotal for i in self.items), 2)

    @property
    def n_items(self) -> int:
        return len(self.items)

    def agregar(self, item: ItemTicket):
        self.items.append(item)

    def quitar(self, idx: int):
        if 0 <= idx < len(self.items):
            self.items.pop(idx)

    def limpiar(self):
        self.items.clear()

    def vacio(self) -> bool:
        return len(self.items) == 0


# ── SESIÓN DEL POS ────────────────────────────────────────────────────────────

@dataclass
class SesionPOS:
    batch_id:  int
    operador:  str
    fecha:     str
    ticket:    Optional[TicketActual] = None
    tickets_sesion: int = 0

    def nuevo_ticket(self):
        self.ticket = TicketActual(
            batch_id = self.batch_id,
            operador = self.operador,
        )

    def cerrar_ticket(self):
        self.ticket = None
        self.tickets_sesion += 1


# ── VALIDACIONES ──────────────────────────────────────────────────────────────

class ErrorPOS(Exception):
    """Error de negocio del POS — se muestra al usuario, no es bug."""
    pass


def validar_stock(sku: str, cantidad: float):
    """Lanza ErrorPOS si no hay suficiente stock."""
    if sku in ("CHI", "LIB", "CUB"):
        return   # CHI se pesa en el momento, LIB y CUB precio variable sin stock fijo
    row = get_sku(sku)
    if row is None:
        raise ErrorPOS(f"SKU '{sku}' no existe")
    if row["stock"] < cantidad:
        raise ErrorPOS(
            f"stock insuficiente — {row['descripcion']}: "
            f"{row['stock']:.0f} disponibles, pediste {cantidad:.0f}"
        )


def validar_cubeta_stock():
    """Lanza ErrorPOS si no hay cubetas en bodega."""
    row = get_sku("CUB")
    if row and row["stock"] <= 0:
        raise ErrorPOS("sin cubetas en bodega — registra producción primero")


# ── OPERACIONES DE INVENTARIO ─────────────────────────────────────────────────

def abrir_cubeta_pos(env_1lt: int, env_05lt: int, nota: str = "") -> dict:
    """
    Abre una cubeta para litrear.
    Valida que haya cubetas en bodega.
    Retorna resumen de la operación.
    """
    validar_cubeta_stock()
    if env_1lt == 0 and env_05lt == 0:
        raise ErrorPOS("debes especificar al menos un envase")

    lt_cargados = env_1lt * 1.0 + env_05lt * 0.5
    abrir_cubeta(env_1lt, env_05lt, nota)

    return dict(
        env_1lt    = env_1lt,
        env_05lt   = env_05lt,
        lt_cargados= lt_cargados,
        mensaje    = f"cubeta abierta — {env_1lt}×1lt  {env_05lt}×½lt  ({lt_cargados:.1f}lt al inventario)",
    )


def carga_manual_stock(sku: str, cantidad: float, nota: str = ""):
    """Carga stock manualmente — para chorizo u otros productos."""
    row = get_sku(sku)
    if row is None:
        raise ErrorPOS(f"SKU '{sku}' no existe el SKU")
    actualizar_stock(sku, cantidad, tipo="carga", nota=nota)
    signo = "+" if cantidad >= 0 else "" 
    return f"{row['descripcion']}: {signo}{cantidad:.3f} unidades actualizadas/ajustadas"


def cargar_cubetas_produccion(n_cubetas: float):
    """
    Registra cubetas producidas en el día — las sube al stock CUB.
    Se llama desde el cierre de producción.
    """
    actualizar_stock("CUB", n_cubetas, tipo="carga",
                     referencia="produccion", nota="cubetas del día")


# ── FLUJO DE TICKET ───────────────────────────────────────────────────────────

def agregar_chicharron(sesion: SesionPOS, kg: float) -> ItemTicket:
    """Agrega chicharrón al ticket — precio del día desde Config."""
    if kg <= 0:
        raise ErrorPOS("kg debe ser mayor a 0")
    cfg = cargar_config()
    item = ItemTicket(
        sku         = "CHI",
        descripcion = "Chicharrón",
        cantidad    = round(kg, 3),
        precio_unit = cfg.precio_chi_pub,
    )
    sesion.ticket.agregar(item)
    return item


def agregar_producto(sesion: SesionPOS, sku: str, cantidad: float) -> ItemTicket:
    """
    Agrega un producto por SKU al ticket.
    Valida stock antes de agregar.
    Para CUB valida que haya cubetas en bodega.
    """
    if cantidad <= 0:
        raise ErrorPOS("cantidad debe ser mayor a 0")

    if sku == "CUB":
        validar_cubeta_stock()
        row = get_sku("CUB")
        item = ItemTicket(
            sku         = "CUB",
            descripcion = "Cubeta 19lt",
            cantidad    = cantidad,
            precio_unit = row["precio_venta"],  # se puede sobreescribir con precio_variable
        )
    else:
        validar_stock(sku, cantidad)
        row = get_sku(sku)
        item = ItemTicket(
            sku         = sku,
            descripcion = row["descripcion"],
            cantidad    = cantidad,
            precio_unit = row["precio_venta"],
        )

    sesion.ticket.agregar(item)
    return item


def agregar_producto_precio_variable(sesion: SesionPOS, sku: str,
                                      cantidad: float, precio: float) -> ItemTicket:
    """Para CUB (precio negociado) y LIB (artículo libre)."""
    if cantidad <= 0 or precio <= 0:
        raise ErrorPOS("cantidad y precio deben ser mayores a 0")

    if sku == "CUB":
        validar_cubeta_stock()
        descripcion = "Cubeta 19lt"
    elif sku == "LIB":
        descripcion = "Artículo"
    else:
        row = get_sku(sku)
        descripcion = row["descripcion"] if row else sku

    item = ItemTicket(
        sku         = sku,
        descripcion = descripcion,
        cantidad    = cantidad,
        precio_unit = precio,
    )
    sesion.ticket.agregar(item)
    return item


def agregar_articulo_libre(sesion: SesionPOS, descripcion: str,
                            cantidad: float, precio: float) -> ItemTicket:
    """Artículo no catalogado — descripción libre."""
    if not descripcion.strip():
        raise ErrorPOS("descripción requerida")
    item = ItemTicket(
        sku         = "LIB",
        descripcion = descripcion.strip()[:30],
        cantidad    = cantidad,
        precio_unit = precio,
    )
    sesion.ticket.agregar(item)
    return item


def cobrar(sesion: SesionPOS, pagos: dict) -> dict:
    """
    Cierra el ticket actual.
    pagos: {'efectivo': X, 'transfer': Y, 'tarjeta': Z}
    Retorna dict con ticket_id, cambio, total.
    """
    if sesion.ticket is None or sesion.ticket.vacio():
        raise ErrorPOS("ticket vacío")

    total_pagado = sum(pagos.values())
    total        = sesion.ticket.total

    if total_pagado < total - 0.01:   # tolerancia de 1 centavo
        raise ErrorPOS(
            f"pago insuficiente — total: ${total:.2f}  pagado: ${total_pagado:.2f}"
        )

    cambio    = round(total_pagado - total, 2)
    items_raw = [i.to_dict() for i in sesion.ticket.items]

    ticket_id = crear_ticket(
        batch_id = sesion.batch_id,
        operador = sesion.operador,
        items    = items_raw,
        pagos    = pagos,
        cambio   = cambio,
    )

    sesion.cerrar_ticket()

    return dict(
        ticket_id = ticket_id,
        total     = total,
        cambio    = cambio,
        pagos     = pagos,
    )


def imprimir_ticket(ticket_id: int) -> str:
    """Retorna el ticket formateado como texto plano."""
    txt = formatear_ticket(ticket_id)
    marcar_impreso(ticket_id)
    return txt


# ── GASTOS ────────────────────────────────────────────────────────────────────

def registrar_gasto(batch_id: int, tipo: str,
                    monto: float, descripcion: str = "") -> dict:
    """
    Registra un gasto interno.
    tipo: 'gas' | 'leche' | 'gral'
    """
    if monto <= 0:
        raise ErrorPOS("monto debe ser mayor a 0")
    tipos_validos = ("gas", "leche", "gral")
    if tipo not in tipos_validos:
        raise ErrorPOS(f"tipo debe ser: {', '.join(tipos_validos)}")

    gasto_id = db_registrar_gasto(batch_id, tipo, monto, descripcion)
    return dict(
        gasto_id    = gasto_id,
        tipo        = tipo,
        monto       = monto,
        descripcion = descripcion,
    )


# ── CORTE DE CAJA ─────────────────────────────────────────────────────────────

def hacer_corte(sesion: SesionPOS, nota: str = "") -> dict:
    """
    Calcula y guarda el corte de caja del turno actual.
    Retorna el dict completo del corte para mostrar en pantalla.
    """
    if sesion.ticket and not sesion.ticket.vacio():
        raise ErrorPOS("hay un ticket abierto — cobra o cancela antes de cortar")

    corte = calcular_corte(batch_id=sesion.batch_id)
    corte_id = guardar_corte(sesion.batch_id, corte, nota)
    corte["corte_id"] = corte_id
    return corte


# ── CONSULTAS ─────────────────────────────────────────────────────────────────

def get_estado_inventario() -> list:
    """Retorna lista de SKUs con stock actual."""
    return get_inventario()


def get_resumen_turno(batch_id: int) -> dict:
    """Resumen rápido del turno para la barra de estado del TUI."""
    corte = calcular_corte(batch_id=batch_id)
    return dict(
        n_tickets    = corte["n_tickets"],
        total_ventas = corte["total_ventas"],
        total_gastos = corte["total_gastos"],
        neto         = corte["neto"],
    )


def get_historial_tickets(fecha: str = None) -> list:
    """Tickets del día para mostrar en historial."""
    return get_tickets_dia(fecha or fecha_hoy())


def get_detalle_ticket(ticket_id: int) -> dict:
    """Ticket completo con sus items."""
    items = get_ticket_items(ticket_id)
    return dict(ticket_id=ticket_id, items=items)


def get_movimientos_inv(sku: str = None, limit: int = 30) -> list:
    """Historial de movimientos de inventario."""
    return get_movimientos(sku=sku, fecha=fecha_hoy(), limit=limit)


# ── CONFIGURACIÓN DE PRECIOS DESDE POS ───────────────────────────────────────

def sincronizar_precios_config():
    """
    Trae los precios de Config → pos.db.
    Se llama al iniciar sesión para mantener consistencia.
    """
    cfg = cargar_config()
    actualizar_precio("CHI",  cfg.precio_chi_pub)
    actualizar_precio("M1LT", cfg.precio_mant_lt1)
    actualizar_precio("M05",  cfg.precio_mant_lt05)
    actualizar_precio("CUB",  cfg.precio_mant_cub)

# ── MAYOREO ────────────────────────────────────────────────────────────────
# Los pedidos de mayoreo NO tocan CHI.stock — son informativos.
# El operador decide en vivo cómo repartir el chicharrón disponible.

def alta_cliente_mayoreo(clave: str, nombre: str, precio_kg: float) -> dict:
    """Da de alta o actualiza un cliente en el tabulador de mayoreo."""
    clave = clave.strip().lower().replace(" ", "_")
    if not clave:
        raise ErrorPOS("clave de cliente requerida")
    if not nombre.strip():
        raise ErrorPOS("nombre requerido")
    if precio_kg <= 0:
        raise ErrorPOS("precio debe ser mayor a 0")

    creado = agregar_cliente_mayoreo(clave, nombre.strip(), precio_kg)
    return dict(
        clave   = clave,
        nombre  = nombre.strip(),
        precio_kg = precio_kg,
        creado  = creado,
        mensaje = f"cliente '{nombre}' {'creado' if creado else 'actualizado'} — ${precio_kg:.0f}/kg"
    )


def listar_clientes_mayoreo() -> list:
    return get_clientes_mayoreo()


PRIORIDADES_MAYOREO = ("muy_alta", "alta", "media", "baja")


def capturar_pedido_mayoreo(cliente_clave: str, kg: float, fecha_entrega: str,
                             prioridad: str = "media", nota: str = "") -> dict:
    """
    Registra un pedido de mayoreo. No descuenta CHI.stock.
    Lanza ErrorPOS si el cliente no existe o los datos son inválidos.
    """
    if kg <= 0:
        raise ErrorPOS("kg debe ser mayor a 0")
    if prioridad not in PRIORIDADES_MAYOREO:
        raise ErrorPOS(f"prioridad debe ser: {', '.join(PRIORIDADES_MAYOREO)}")

    cliente = get_cliente_mayoreo(cliente_clave)
    if cliente is None:
        raise ErrorPOS(f"cliente '{cliente_clave}' no existe — dalo de alta primero")

    pedido_id = crear_pedido_mayoreo(
        cliente_clave = cliente_clave,
        kg            = kg,
        fecha_entrega = fecha_entrega,
        prioridad     = prioridad,
        nota          = nota,
    )
    total = kg * cliente["precio_kg"]
    return dict(
        pedido_id = pedido_id,
        cliente   = cliente["nombre"],
        kg        = kg,
        precio_kg = cliente["precio_kg"],
        total     = total,
        prioridad = prioridad,
        mensaje   = f"pedido #{pedido_id} — {cliente['nombre']}  {kg:.1f}kg  ${total:,.0f}  [{prioridad}]"
    )


def ajustar_pedido_mayoreo(pedido_id: int, nuevo_kg: float) -> dict:
    """Renegociar kg de un pedido ya tomado, antes de entregar."""
    if nuevo_kg <= 0:
        raise ErrorPOS("kg debe ser mayor a 0")
    editar_pedido_mayoreo(pedido_id, kg=nuevo_kg)
    return dict(pedido_id=pedido_id, nuevo_kg=nuevo_kg,
                mensaje=f"pedido #{pedido_id} ajustado a {nuevo_kg:.1f}kg")


def entregar_pedido_mayoreo(pedido_id: int) -> dict:
    marcar_pedido_entregado(pedido_id)
    return dict(pedido_id=pedido_id, mensaje=f"pedido #{pedido_id} marcado como entregado")


def get_pedidos_mayoreo_pendientes() -> list:
    """Fuente única para el ticker y la pantalla de status — ya vienen
    ordenados por prioridad desde pos_db."""
    return get_pedidos_pendientes()

# ── INIT ──────────────────────────────────────────────────────────────────────

def iniciar_pos(batch_id: int, operador: str = "Luis") -> SesionPOS:
    """
    Punto de entrada del POS.
    Inicializa DB, sincroniza precios, retorna sesión activa.
    """
    init_db()
    sincronizar_precios_config()

    sesion = SesionPOS(
        batch_id = batch_id,
        operador = operador,
        fecha    = fecha_hoy(),
    )
    sesion.nuevo_ticket()
    return sesion
