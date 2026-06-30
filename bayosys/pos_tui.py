"""
pos_tui.py — bayoSys · Productos El Bayo
Interfaz curses del POS. Pantalla dividida estilo htop.

CAMBIOS:
  - MENU_PRODUCTOS eliminado — panel se construye desde get_menu_pos() en DB
  - Handler de teclas dinámico por tipo_venta del SKU
  - Agregar producto nuevo al catálogo → aparece automáticamente en el POS
  - El resto del archivo no cambia: flujo_cobro, corte, historial, inventario

Layout:
  ┌─ PRODUCTOS ──────┬─ TICKET ACTUAL ──────────────────┐
  │ [1] Chicharrón   │  1.250 kg  Chicharrón   $287.50  │
  │ [2] Manteca 1lt  │  2 pza     Manteca 1lt   $70.00  │
  │ [3] Manteca ½lt  │  ──────────────────────────────  │
  │ [4] Chorizo      │  TOTAL               $357.50      │
  │ [5] Cubeta       │                                   │
  │ [N] + Artículo   │                                   │
  ├──────────────────┤                                   │
  │ [G] Gasto        │                                   │
  │ [A] Abrir cubeta │                                   │
  │ [I] Inventario   │                                   │
  │ [C] Corte        │                                   │
  │ [Q] Salir        │                                   │
  └──────────────────┴───────────────────────────────────┘
  [ batch#20260630-1 | tickets:3 | ventas:$1,240 | neto:$890 ]
"""

import curses
import os
from pos import (
    iniciar_pos, agregar_chicharron, agregar_producto,
    agregar_producto_precio_variable, agregar_articulo_libre,
    cobrar, hacer_corte, registrar_gasto, abrir_cubeta_pos,
    carga_manual_stock, get_estado_inventario, get_resumen_turno,
    get_historial_tickets, imprimir_ticket, ErrorPOS, SesionPOS
)
from pos_db import get_ticket_items, anular_ticket, get_menu_pos
from config import fecha_hoy

try:
    from pos_ticket import imprimir_ticket_fisico, ErrorImpresora
    IMPRESORA_DISPONIBLE = True
except ImportError:
    IMPRESORA_DISPONIBLE = False
    class ErrorImpresora(Exception):
        pass


# ── COLORES ──────────────────────────────────────────────────────────────────

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE,   -1)
    curses.init_pair(2, curses.COLOR_CYAN,    -1)
    curses.init_pair(3, curses.COLOR_GREEN,   -1)
    curses.init_pair(4, curses.COLOR_YELLOW,  -1)
    curses.init_pair(5, curses.COLOR_RED,     -1)
    curses.init_pair(6, curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(7, curses.COLOR_BLACK,   curses.COLOR_GREEN)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    curses.init_pair(9, curses.COLOR_BLACK,   curses.COLOR_YELLOW)

C_NORM   = lambda: curses.color_pair(1)
C_CYAN   = lambda: curses.color_pair(2)
C_GREEN  = lambda: curses.color_pair(3)
C_YELLOW = lambda: curses.color_pair(4)
C_RED    = lambda: curses.color_pair(5)
C_TAB    = lambda: curses.color_pair(6) | curses.A_BOLD
C_BTN    = lambda: curses.color_pair(7) | curses.A_BOLD
C_TITLE  = lambda: curses.color_pair(8) | curses.A_BOLD
C_WARN   = lambda: curses.color_pair(9) | curses.A_BOLD


# ── HELPERS DE DIBUJO ─────────────────────────────────────────────────────────

def sadd(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0:
        return
    max_len = w - x - 1
    if max_len <= 0:
        return
    try:
        win.addstr(y, x, str(text)[:max_len], attr)
    except curses.error:
        pass

def hline(win, y, x, w, char="─", attr=0):
    sadd(win, y, x, char * min(w, win.getmaxyx()[1] - x - 1), attr)

def flash_msg(win, h, msg, color=None):
    if color is None:
        color = C_GREEN()
    sadd(win, h - 2, 1, " " * (win.getmaxyx()[1] - 2))
    sadd(win, h - 2, 2, f" {msg} ", color)
    win.refresh()
    curses.napms(1200)


# ── INPUT MODAL ───────────────────────────────────────────────────────────────

def pedir_input(stdscr, prompt: str, y: int, x: int, ancho: int = 20) -> str:
    curses.curs_set(1)
    sadd(stdscr, y, x, prompt, C_CYAN() | curses.A_BOLD)
    sadd(stdscr, y, x + len(prompt), " " * ancho)
    stdscr.refresh()

    buf = []
    cx  = x + len(prompt)
    while True:
        try:
            stdscr.move(y, cx + len(buf))
        except curses.error:
            pass
        key = stdscr.getch()
        if key in (10, 13):
            break
        elif key in (27,):
            curses.curs_set(0)
            return ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
                sadd(stdscr, y, cx + len(buf), " ")
        elif 32 <= key <= 126:
            if len(buf) < ancho - 1:
                buf.append(chr(key))
                sadd(stdscr, y, cx + len(buf) - 1, chr(key), C_CYAN())

    curses.curs_set(0)
    return "".join(buf).strip()


def pedir_float_modal(stdscr, prompt: str, y: int, x: int) -> float:
    while True:
        raw = pedir_input(stdscr, prompt, y, x, ancho=12)
        if not raw:
            return 0.0
        try:
            val = float(raw)
            if val >= 0:
                return val
        except ValueError:
            pass
        sadd(stdscr, y + 1, x, "  ! número inválido  ", C_RED())
        stdscr.refresh()
        curses.napms(800)
        sadd(stdscr, y + 1, x, " " * 22)


def pedir_int_modal(stdscr, prompt: str, y: int, x: int) -> int:
    val = pedir_float_modal(stdscr, prompt, y, x)
    return int(val)


# ── MENÚ DINÁMICO — construido desde DB ───────────────────────────────────────

def _color_stock(sku: str, tipo_venta: str, stock: float):
    """Retorna (stock_str, color) según tipo de producto y nivel de stock."""
    if tipo_venta == "libre":
        return "", C_CYAN()
    if tipo_venta == "peso":
        # chicharrón — en kg
        if stock <= 0:
            return "sin stock", C_RED()
        elif stock <= 5:
            return f"{stock:.2f} kg", C_YELLOW()
        else:
            return f"{stock:.2f} kg", C_GREEN()
    else:
        # piezas — normal, variable
        if stock <= 0:
            return "sin stock", C_RED()
        elif stock <= 3:
            return f"{stock:.0f} pza", C_YELLOW()
        else:
            return f"{stock:.0f} pza", C_GREEN()


def _tecla_a_idx(key: int) -> int:
    """Convierte tecla ASCII '1'-'9' a índice 0-8. Retorna -1 si no aplica."""
    if ord("1") <= key <= ord("9"):
        return key - ord("1")
    return -1


# ── PANEL IZQUIERDO — DINÁMICO ────────────────────────────────────────────────

def draw_panel_izq(win, sesion: SesionPOS, inv_dict: dict, menu: list, msg: str = ""):
    """
    Dibuja el panel de productos leyendo 'menu' — lista de rows de get_menu_pos().
    Cada row tiene: sku, descripcion, tipo_venta, orden_menu, precio_venta, stock.
    Las teclas 1-9 se asignan según posición en la lista (orden_menu en DB).
    """
    h, w = win.getmaxyx()
    win.erase()
    win.border()
    sadd(win, 0, 2, " PRODUCTOS ", C_TITLE())

    y = 2
    for idx, row in enumerate(menu):
        tecla     = str(idx + 1) if idx < 9 else "?"
        sku       = row["sku"]
        nombre    = row["descripcion"][:13]
        tipo      = row["tipo_venta"]
        stock     = inv_dict.get(sku, {}).get("stock", 0)
        stock_str, color = _color_stock(sku, tipo, stock)

        sadd(win, y, 2,  f"[{tecla}]",          C_CYAN() | curses.A_BOLD)
        sadd(win, y, 6,  f"{nombre:<13}",        color)
        sadd(win, y, 20, stock_str,              color)
        y += 1

    y += 1
    hline(win, y, 1, w - 2)
    y += 1

    sadd(win, y, 2, "[G]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Registrar gasto",  C_NORM()); y += 1
    sadd(win, y, 2, "[A]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Abrir cubeta",     C_NORM()); y += 1
    sadd(win, y, 2, "[I]", C_CYAN()   | curses.A_BOLD)
    sadd(win, y, 6, "Inventario",       C_NORM()); y += 1
    sadd(win, y, 2, "[H]", C_CYAN()   | curses.A_BOLD)
    sadd(win, y, 6, "Historial",        C_NORM()); y += 1

    hline(win, y, 1, w - 2); y += 1

    sadd(win, y, 2, "[C]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Corte de caja",   C_NORM()); y += 1
    sadd(win, y, 2, "[Q]", C_RED()    | curses.A_BOLD)
    sadd(win, y, 6, "Salir",           C_NORM())

    if msg:
        sadd(win, h - 2, 1, msg[:w - 3], C_GREEN())

    win.refresh()


# ── PANEL DERECHO — TICKET ────────────────────────────────────────────────────

def draw_panel_ticket(win, sesion: SesionPOS):
    h, w = win.getmaxyx()
    win.erase()
    win.border()
    sadd(win, 0, 2, " TICKET ACTUAL ", C_TITLE())

    if sesion.ticket is None or sesion.ticket.vacio():
        sadd(win, 2, 2, "  (vacío)", C_NORM())
        win.refresh()
        return

    y = 2
    for idx, item in enumerate(sesion.ticket.items):
        if y >= h - 5:
            sadd(win, y, 2, f"  ... +{len(sesion.ticket.items) - idx} más", C_YELLOW())
            break
        # formato cantidad según tipo
        if item.sku == "CHI":
            cant = f"{item.cantidad:.3f}kg"
        else:
            cant = f"{item.cantidad:.0f} pza"
        linea = f"[{idx+1}] {item.descripcion[:15]:<15} {cant:>8}  ${item.subtotal:>8,.2f}"
        sadd(win, y, 1, linea, C_NORM())
        y += 1

    hline(win, h - 5, 1, w - 2)
    sadd(win, h - 4, 2, "TOTAL", C_CYAN() | curses.A_BOLD)
    sadd(win, h - 4, w - 14, f"${sesion.ticket.total:>10,.2f}",
         C_GREEN() | curses.A_BOLD)
    sadd(win, h - 2, 2, "[X] quitar item   [P] cobrar   [Esc] limpiar",
         C_NORM())

    win.refresh()


# ── FLUJO COBRO ───────────────────────────────────────────────────────────────

def flujo_cobro(stdscr, sesion: SesionPOS) -> str:
    """
    Modal de cobro inteligente.
    1. Elige método principal → ingresa monto
    2. Si cubre → cobra con cambio
    3. Si no cubre → pide segundo método
    """
    h, w  = stdscr.getmaxyx()
    total = sesion.ticket.total
    my    = h // 2 - 7
    mx    = w // 2 - 18

    def _dibujar_caja():
        for i in range(16):
            sadd(stdscr, my + i, mx, " " * 38)
        sadd(stdscr, my,      mx+1, f"┌{'─'*36}┐", C_CYAN())
        sadd(stdscr, my + 1,  mx+1, f"│ COBRAR{' '*29}│", C_CYAN())
        sadd(stdscr, my + 2,  mx+1, f"│ TOTAL: ${total:>26,.2f} │",
             C_GREEN() | curses.A_BOLD)
        sadd(stdscr, my + 3,  mx+1, f"├{'─'*36}┤", C_CYAN())
        sadd(stdscr, my + 4,  mx+1, f"│ MÉTODO DE PAGO:{' '*20}│", C_CYAN())
        sadd(stdscr, my + 5,  mx+1, f"│  [1] Efectivo{' '*22}│", C_NORM())
        sadd(stdscr, my + 6,  mx+1, f"│  [2] Transferencia{' '*17}│", C_NORM())
        sadd(stdscr, my + 7,  mx+1, f"│  [3] Tarjeta{' '*23}│", C_NORM())
        sadd(stdscr, my + 8,  mx+1, f"│  [Esc] Cancelar{' '*19}│", C_NORM())
        sadd(stdscr, my + 9,  mx+1, f"├{'─'*36}┤", C_CYAN())
        for i in range(10, 15):
            sadd(stdscr, my + i, mx+1, f"│{' '*36}│", C_CYAN())
        sadd(stdscr, my + 15, mx+1, f"└{'─'*36}┘", C_CYAN())
        stdscr.refresh()

    METODOS = {ord("1"): "efectivo", ord("2"): "transfer", ord("3"): "tarjeta"}
    LABELS  = {"efectivo": "Efectivo  ", "transfer": "Transfer  ", "tarjeta": "Tarjeta   "}
    pagos   = {"efectivo": 0.0, "transfer": 0.0, "tarjeta": 0.0}

    # paso 1: elegir método principal
    _dibujar_caja()
    while True:
        key = stdscr.getch()
        if key == 27:
            return ""
        if key in METODOS:
            metodo1 = METODOS[key]
            break

    # paso 2: ingresar monto
    _dibujar_caja()
    monto1 = pedir_float_modal(stdscr, f"  {LABELS[metodo1]}$", my + 10, mx + 2)
    if monto1 <= 0:
        return ""

    pagos[metodo1] = monto1
    restante = round(total - monto1, 2)

    # paso 3: ¿cubre?
    if restante <= 0:
        cambio = abs(restante)
        if cambio > 0:
            sadd(stdscr, my + 11, mx + 2,
                 f"  CAMBIO: ${cambio:>8.2f}          ", C_YELLOW() | curses.A_BOLD)
        else:
            sadd(stdscr, my + 11, mx + 2,
                 f"  Exacto ✓                    ", C_GREEN())
        sadd(stdscr, my + 14, mx + 2,
             "  [Enter] cobrar  [Esc] cancelar", C_NORM())
        stdscr.refresh()
        key = stdscr.getch()
        if key == 27:
            return ""
    else:
        # necesita segundo método
        sadd(stdscr, my + 11, mx + 2,
             f"  Falta: ${restante:>8.2f}              ", C_RED() | curses.A_BOLD)
        sadd(stdscr, my + 12, mx + 2,
             "  2do método: [1]Ef [2]Tr [3]Ta [Esc]", C_CYAN())
        stdscr.refresh()
        key = stdscr.getch()
        if key == 27:
            return ""
        if key in METODOS:
            metodo2 = METODOS[key]
            if metodo2 == metodo1:
                metodo2 = "transfer" if metodo1 != "transfer" else "efectivo"
            monto2 = pedir_float_modal(stdscr, f"  {LABELS[metodo2]}$", my + 12, mx + 2)
            pagos[metodo2] = monto2
            total_pagado   = monto1 + monto2
            if total_pagado < total - 0.01:
                sadd(stdscr, my + 14, mx + 2,
                     f"  FALTA ${total-total_pagado:.2f} — [Enter]", C_RED() | curses.A_BOLD)
                stdscr.refresh()
                stdscr.getch()
                return "retry"
            cambio = round(total_pagado - total, 2)
            if cambio > 0:
                sadd(stdscr, my + 13, mx + 2,
                     f"  CAMBIO: ${cambio:.2f}          ", C_YELLOW() | curses.A_BOLD)
            sadd(stdscr, my + 14, mx + 2,
                 "  [Enter] cobrar  [Esc] cancelar  ", C_NORM())
            stdscr.refresh()
            key = stdscr.getch()
            if key == 27:
                return ""

    # paso 4: cobrar
    try:
        resultado = cobrar(sesion, pagos)
        ticket_id = resultado["ticket_id"]
        cambio    = resultado["cambio"]

        txt  = imprimir_ticket(ticket_id)
        ruta = os.path.expanduser(f"~/bayosys/data/ticket_{ticket_id:04d}.txt")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w") as f:
            f.write(txt)

        sadd(stdscr, my + 14, mx + 2,
             f"  Ticket #{ticket_id:04d}  [P]imprimir [Enter]",
             C_GREEN() | curses.A_BOLD)
        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord("p"), ord("P")):
            if not IMPRESORA_DISPONIBLE:
                return f"ticket #{ticket_id:04d} cobrado — sin impresora, respaldo en {ruta}"
            try:
                imprimir_ticket_fisico(ticket_id, pagos, cambio)
                return f"ticket #{ticket_id:04d} cobrado e impreso OK"
            except ErrorImpresora as e:
                return f"ticket #{ticket_id:04d} cobrado, NO imprimió ({e})"

        return f"ticket #{ticket_id:04d} cobrado OK — cambio ${cambio:.2f}"

    except ErrorPOS as e:
        return f"ERROR: {e}"


# ── PANTALLA INVENTARIO ───────────────────────────────────────────────────────

def pantalla_inventario(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sadd(stdscr, 0, 0, " INVENTARIO ".center(w), C_TITLE())
    hline(stdscr, 1, 0, w)

    inv = get_estado_inventario()
    sadd(stdscr, 2, 2,
         f"{'SKU':<8} {'Descripción':<20} {'Tipo':<10} {'Stock':>7} {'Precio':>10}",
         C_CYAN())
    hline(stdscr, 3, 2, w - 4)

    for i, row in enumerate(inv):
        y     = 4 + i
        stock = row["stock"]
        tipo  = row["tipo_venta"]
        stock_str, color = _color_stock(row["sku"], tipo, stock)
        sadd(stdscr, y, 2,
             f"{row['sku']:<8} {row['descripcion']:<20} {tipo:<10} "
             f"{stock_str:>7}  ${row['precio_venta']:>9.2f}",
             color if tipo != "libre" else C_NORM())

    hline(stdscr, h - 3, 0, w)
    sadd(stdscr, h - 2, 2,
         "[C] carga manual   [A] abrir cubeta   [Esc] volver", C_NORM())
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break
        elif key in (ord("c"), ord("C")):
            skus = [r["sku"] for r in inv if r["tipo_venta"] not in ("libre",)]
            sku_raw = pedir_input(stdscr, "SKU: ", h - 4, 2, 8).upper()
            if sku_raw in skus:
                cant = pedir_int_modal(stdscr, "Cantidad +: ", h - 4, 12)
                if cant > 0:
                    try:
                        msg = carga_manual_stock(sku_raw, cant)
                        flash_msg(stdscr, h, msg)
                    except ErrorPOS as e:
                        flash_msg(stdscr, h, str(e), C_RED())
            break
        elif key in (ord("a"), ord("A")):
            break


# ── PANTALLA HISTORIAL ────────────────────────────────────────────────────────

def pantalla_historial(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sadd(stdscr, 0, 0, " HISTORIAL DE TICKETS ".center(w), C_TITLE())
    hline(stdscr, 1, 0, w)

    tickets = get_historial_tickets()
    sadd(stdscr, 2, 2,
         f"{'#':>4}  {'hora':<6}  {'ef':>8}  {'tr':>8}  {'ta':>8}  {'total':>9}",
         C_CYAN())
    hline(stdscr, 3, 2, w - 4)

    for i, t in enumerate(tickets[-20:]):
        sadd(stdscr, 4 + i, 2,
             f"{t['id']:>4}  {t['hora'][:5]:<6}  "
             f"${t['pago_efectivo']:>7.0f}  "
             f"${t['pago_transfer']:>7.0f}  "
             f"${t['pago_tarjeta']:>7.0f}  "
             f"${t['total']:>8.2f}",
             C_NORM())

    hline(stdscr, h - 3, 0, w)
    sadd(stdscr, h - 2, 2, "[Esc] volver", C_NORM())
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break


# ── PANTALLA CORTE ────────────────────────────────────────────────────────────

def pantalla_corte(stdscr, sesion: SesionPOS) -> str:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sadd(stdscr, 0, 0,
         f" CORTE DE CAJA — batch#{sesion.batch_id} ".center(w), C_TITLE())
    hline(stdscr, 1, 0, w)

    try:
        corte = hacer_corte(sesion)
    except ErrorPOS as e:
        sadd(stdscr, 3, 2, f"! {e}", C_RED())
        sadd(stdscr, 5, 2, "[Esc] volver", C_NORM())
        stdscr.refresh()
        stdscr.getch()
        return ""

    y = 2
    def fila(label, valor, color=None):
        nonlocal y
        sadd(stdscr, y, 4, f"{label:<28}", C_NORM())
        sadd(stdscr, y, 32, valor, color or C_NORM())
        y += 1

    fila("tickets del turno",   f"{corte['n_tickets']}")
    y += 1
    fila("ventas efectivo",     f"${corte['ventas_efectivo']:,.2f}", C_GREEN())
    fila("ventas transferencia", f"${corte['ventas_transfer']:,.2f}", C_GREEN())
    fila("ventas tarjeta",      f"${corte['ventas_tarjeta']:,.2f}",  C_GREEN())
    hline(stdscr, y, 4, 40); y += 1
    fila("TOTAL VENTAS",        f"${corte['total_ventas']:,.2f}", C_GREEN() | curses.A_BOLD)
    fila("gastos del turno",    f"${corte['total_gastos']:,.2f}", C_YELLOW())
    hline(stdscr, y, 4, 40); y += 1
    fila("NETO",                f"${corte['neto']:,.2f}", C_CYAN() | curses.A_BOLD)
    y += 1
    fila("fondo de caja",       f"${corte['fondo_caja']:,.2f}", C_NORM())
    fila("A ENTREGAR",          f"${corte['a_entregar']:,.2f}",
         C_GREEN() | curses.A_BOLD if corte['a_entregar'] > 0 else C_NORM())

    hline(stdscr, h - 3, 0, w)
    sadd(stdscr, h - 2, 2, "[Enter] confirmar   [Esc] cancelar", C_NORM())
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (10, 13):
            return f"corte #{corte['corte_id']} guardado — entregar ${corte['a_entregar']:.2f}"
        elif key == 27:
            return ""


# ── BARRA DE ESTADO ───────────────────────────────────────────────────────────

def draw_statusbar(stdscr, sesion: SesionPOS, msg: str = ""):
    h, w = stdscr.getmaxyx()
    try:
        resumen = get_resumen_turno(sesion.batch_id)
        status = (f"  batch#{sesion.batch_id}"
                  f"  |  tickets:{resumen['n_tickets']}"
                  f"  |  ventas:${resumen['total_ventas']:,.0f}"
                  f"  |  gastos:${resumen['total_gastos']:,.0f}"
                  f"  |  neto:${resumen['neto']:,.0f}"
                  f"  |  {fecha_hoy()}")
    except Exception:
        status = f"  batch#{sesion.batch_id}  |  {fecha_hoy()}"

    sadd(stdscr, h - 1, 0, " " * (w - 1), C_TAB())
    sadd(stdscr, h - 1, 0, status[:w - 1], C_TAB())

    if msg:
        sadd(stdscr, h - 2, 0, " " * (w - 1))
        sadd(stdscr, h - 2, 2, f" {msg[:w-6]} ", C_GREEN())


# ── HANDLER DINÁMICO POR tipo_venta ──────────────────────────────────────────

def _procesar_item(stdscr, sesion: SesionPOS, row, h: int, w: int) -> str:
    """
    Ejecuta el flujo de venta correcto según tipo_venta del SKU.
    Retorna mensaje de resultado para la barra de estado.
    Esto reemplaza el bloque if/elif hardcodeado por SKU.
    """
    sku       = row["sku"]
    tipo      = row["tipo_venta"]
    nombre    = row["descripcion"]
    precio    = row["precio_venta"]

    if sesion.ticket is None:
        sesion.nuevo_ticket()

    try:
        if tipo == "peso":
            # cantidad decimal en kg (CHI)
            kg = pedir_float_modal(stdscr, f"kg {nombre}: ", h // 2, w // 2 - 14)
            if kg <= 0:
                return ""
            agregar_chicharron(sesion, kg)
            return f"{nombre} {kg:.3f}kg agregado"

        elif tipo == "variable":
            # precio negociado en el momento (CUB)
            precio_neg = pedir_float_modal(stdscr, f"precio {nombre} $: ", h // 2, w // 2 - 14)
            if precio_neg <= 0:
                return ""
            cant = pedir_int_modal(stdscr, "cantidad: ", h // 2 + 1, w // 2 - 14)
            if cant <= 0:
                return ""
            agregar_producto_precio_variable(sesion, sku, cant, precio_neg)
            return f"{cant} × {nombre} ${precio_neg:.0f} agregado"

        elif tipo == "libre":
            # descripción y precio libres (LIB)
            desc = pedir_input(stdscr, "descripción: ", h // 2, w // 2 - 14, 20)
            if not desc:
                return ""
            precio_lib = pedir_float_modal(stdscr, "precio $: ", h // 2 + 1, w // 2 - 14)
            cant       = pedir_int_modal(stdscr, "cantidad: ",  h // 2 + 2, w // 2 - 14)
            if precio_lib <= 0 or cant <= 0:
                return ""
            agregar_articulo_libre(sesion, desc, cant, precio_lib)
            return f"'{desc}' agregado"

        else:
            # normal — cantidad entera, precio fijo
            cant = pedir_int_modal(stdscr, f"piezas {nombre}: ", h // 2, w // 2 - 14)
            if cant <= 0:
                return ""
            agregar_producto(sesion, sku, cant)
            return f"{cant} × {nombre} agregado"

    except ErrorPOS as e:
        return f"! {e}"


# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────

def main(stdscr, batch_id: str, operador: str = "Luis"):
    curses.curs_set(0)
    stdscr.keypad(True)
    init_colors()

    sesion = iniciar_pos(batch_id=batch_id, operador=operador)
    msg    = f"POS iniciado — batch#{batch_id}"

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # leer menú dinámico desde DB en cada frame
        menu = list(get_menu_pos())

        # layout
        ancho_izq = min(32, w // 3)
        ancho_der = w - ancho_izq
        win_izq   = stdscr.derwin(h - 1, ancho_izq, 0, 0)
        win_der   = stdscr.derwin(h - 1, ancho_der, 0, ancho_izq)

        # inventario para stock
        inv_raw  = get_estado_inventario()
        inv_dict = {r["sku"]: {"stock": r["stock"], "precio": r["precio_venta"]}
                    for r in inv_raw}

        draw_panel_izq(win_izq, sesion, inv_dict, menu)
        draw_panel_ticket(win_der, sesion)
        draw_statusbar(stdscr, sesion, msg)
        stdscr.refresh()
        msg = ""

        key = stdscr.getch()

        # ── TECLAS 1-9 → productos dinámicos ─────────────────────────
        idx = _tecla_a_idx(key)
        if 0 <= idx < len(menu):
            resultado = _procesar_item(stdscr, sesion, menu[idx], h, w)
            if resultado:
                msg = resultado
            continue

        # ── QUITAR ITEM ───────────────────────────────────────────────
        if key in (ord("x"), ord("X")):
            if sesion.ticket and not sesion.ticket.vacio():
                idx_q = pedir_int_modal(stdscr, "quitar ítem #: ", h // 2, w // 2 - 14)
                if 1 <= idx_q <= sesion.ticket.n_items:
                    sesion.ticket.quitar(idx_q - 1)
                    msg = f"ítem #{idx_q} quitado"

        # ── COBRAR ────────────────────────────────────────────────────
        elif key in (ord("p"), ord("P")):
            if sesion.ticket and not sesion.ticket.vacio():
                resultado = flujo_cobro(stdscr, sesion)
                if resultado and resultado != "retry":
                    msg = resultado
                    sesion.nuevo_ticket()
            else:
                msg = "! ticket vacío"

        # ── LIMPIAR TICKET ────────────────────────────────────────────
        elif key == 27:
            if sesion.ticket and not sesion.ticket.vacio():
                sesion.ticket.limpiar()
                msg = "ticket limpiado"

        # ── GASTO ─────────────────────────────────────────────────────
        elif key in (ord("g"), ord("G")):
            stdscr.erase()
            sadd(stdscr, h // 2 - 3, w // 2 - 14, "REGISTRAR GASTO", C_TITLE())
            sadd(stdscr, h // 2 - 2, w // 2 - 14, "[1]Gas [2]Leche [3]General", C_CYAN())
            stdscr.refresh()
            tipo_key = stdscr.getch()
            tipo = {ord("1"): "gas", ord("2"): "leche", ord("3"): "gral"}.get(tipo_key, "gral")
            monto = pedir_float_modal(stdscr, "monto $: ",      h // 2,     w // 2 - 14)
            desc  = pedir_input(stdscr,       "descripción: ",  h // 2 + 1, w // 2 - 14, 25)
            if monto > 0:
                try:
                    registrar_gasto(sesion.batch_id, tipo, monto, desc)
                    msg = f"gasto {tipo} ${monto:.0f} registrado"
                except ErrorPOS as e:
                    msg = f"! {e}"

        # ── ABRIR CUBETA ──────────────────────────────────────────────
        elif key in (ord("a"), ord("A")):
            e1  = pedir_int_modal(stdscr, "envases 1lt a cargar: ",  h // 2,     w // 2 - 16)
            e05 = pedir_int_modal(stdscr, "envases ½lt a cargar: ",  h // 2 + 1, w // 2 - 16)
            if e1 > 0 or e05 > 0:
                try:
                    r   = abrir_cubeta_pos(e1, e05)
                    msg = r["mensaje"]
                except ErrorPOS as e:
                    msg = f"! {e}"

        # ── INVENTARIO ────────────────────────────────────────────────
        elif key in (ord("i"), ord("I")):
            pantalla_inventario(stdscr)

        # ── HISTORIAL ─────────────────────────────────────────────────
        elif key in (ord("h"), ord("H")):
            pantalla_historial(stdscr)

        # ── CORTE ─────────────────────────────────────────────────────
        elif key in (ord("c"), ord("C")):
            resultado = pantalla_corte(stdscr, sesion)
            if resultado:
                msg = resultado

        # ── SALIR ─────────────────────────────────────────────────────
        elif key in (ord("q"), ord("Q")):
            if sesion.ticket and not sesion.ticket.vacio():
                msg = "! cierra el ticket antes de salir"
            else:
                break


def iniciar_pos_tui(batch_id: str, operador: str = "Luis"):
    curses.wrapper(main, batch_id, operador)


if __name__ == "__main__":
    iniciar_pos_tui(batch_id="test")
