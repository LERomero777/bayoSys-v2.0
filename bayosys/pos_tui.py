"""
pos_tui.py — bayoSys · Productos El Bayo
Interfaz curses del POS. Pantalla dividida estilo htop.
Navega con números, flechas y teclas directas.

Layout:
  ┌─ PRODUCTOS ──────┬─ TICKET ACTUAL ──────────────────┐
  │ [1] Chicharrón   │  1.250 kg  Chicharrón   $287.50  │
  │ [2] Manteca 1lt  │  2 pza     Manteca 1lt   $70.00  │
  │ [3] Manteca ½lt  │  ──────────────────────────────  │
  │ [4] Chorizo      │  TOTAL               $357.50      │
  │ [5] Cubeta       │                                   │
  │ [6] + Artículo   │                                   │
  ├──────────────────┤                                   │
  │ [G] Gasto        │                                   │
  │ [A] Abrir cubeta │                                   │
  │ [I] Inventario   │                                   │
  │ [C] Corte        │                                   │
  │ [Q] Salir        │                                   │
  └──────────────────┴───────────────────────────────────┘
  [ batch#2 | tickets:3 | ventas:$1,240 | neto:$890 ]
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
from pos_db import get_ticket_items, anular_ticket
from config import fecha_hoy


# ── COLORES ──────────────────────────────────────────────────────────────────

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE,   -1)           # normal
    curses.init_pair(2, curses.COLOR_CYAN,    -1)           # cyan
    curses.init_pair(3, curses.COLOR_GREEN,   -1)           # verde
    curses.init_pair(4, curses.COLOR_YELLOW,  -1)           # amarillo
    curses.init_pair(5, curses.COLOR_RED,     -1)           # rojo
    curses.init_pair(6, curses.COLOR_BLACK,   curses.COLOR_CYAN)   # tab activo
    curses.init_pair(7, curses.COLOR_BLACK,   curses.COLOR_GREEN)  # botón ok
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)           # título
    curses.init_pair(9, curses.COLOR_BLACK,   curses.COLOR_YELLOW) # warn

C_NORM   = lambda: curses.color_pair(1)
C_CYAN   = lambda: curses.color_pair(2)
C_GREEN  = lambda: curses.color_pair(3)
C_YELLOW = lambda: curses.color_pair(4)
C_RED    = lambda: curses.color_pair(5)
C_TAB    = lambda: curses.color_pair(6) | curses.A_BOLD
C_BTN    = lambda: curses.color_pair(7) | curses.A_BOLD
C_TITLE  = lambda: curses.color_pair(8) | curses.A_BOLD
C_WARN   = lambda: curses.color_pair(9) | curses.A_BOLD


# ── HELPER DRAW ───────────────────────────────────────────────────────────────

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
    """Muestra mensaje temporal en la línea de estado."""
    if color is None:
        color = C_GREEN()
    sadd(win, h - 2, 1, " " * (win.getmaxyx()[1] - 2))
    sadd(win, h - 2, 2, f" {msg} ", color)
    win.refresh()
    curses.napms(1200)


# ── INPUT MODAL ───────────────────────────────────────────────────────────────

def pedir_input(stdscr, prompt: str, y: int, x: int, ancho: int = 20) -> str:
    """Input inline en la pantalla curses."""
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
        if key in (10, 13):           # Enter
            break
        elif key in (27,):            # Esc — cancelar
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


# ── PANEL IZQUIERDO — PRODUCTOS ───────────────────────────────────────────────

MENU_PRODUCTOS = [
    ("1", "CHI",  "Chicharrón",   False),
    ("2", "M1LT", "Manteca 1lt",  False),
    ("3", "M05",  "Manteca ½lt",  False),
    ("4", "CHO",  "Chorizo",      False),
    ("5", "CUB",  "Cubeta 19lt",  True),   # precio variable
    ("6", "LIB",  "+ Artículo",   True),   # libre
]

def draw_panel_izq(win, sesion: SesionPOS, inv_dict: dict, msg: str = ""):
    h, w = win.getmaxyx()
    win.erase()
    win.border()

    sadd(win, 0, 2, " PRODUCTOS ", C_TITLE())

    cfg_row = None
    try:
        from config import cargar_config
        cfg_row = cargar_config()
    except Exception:
        pass

    y = 2
    for tecla, sku, nombre, variable in MENU_PRODUCTOS:
        stock = inv_dict.get(sku, {}).get("stock", 0)
        if sku == "CHI":
            stock_str = "∞"
            color     = C_NORM()
        elif sku in ("LIB",):
            stock_str = ""
            color     = C_CYAN()
        elif stock <= 0:
            stock_str = "sin stock"
            color     = C_RED()
        elif stock <= 3:
            stock_str = f"{stock:.0f} pza"
            color     = C_YELLOW()
        else:
            stock_str = f"{stock:.0f} pza"
            color     = C_GREEN()

        sadd(win, y, 2, f"[{tecla}]", C_CYAN() | curses.A_BOLD)
        sadd(win, y, 6, f"{nombre:<14}", color)
        sadd(win, y, 20, stock_str, color)
        y += 1

    y += 1
    hline(win, y, 1, w - 2)
    y += 1

    sadd(win, y, 2, "[G]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Registrar gasto", C_NORM())
    y += 1
    sadd(win, y, 2, "[A]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Abrir cubeta", C_NORM())
    y += 1
    sadd(win, y, 2, "[I]", C_CYAN() | curses.A_BOLD)
    sadd(win, y, 6, "Inventario", C_NORM())
    y += 1
    sadd(win, y, 2, "[H]", C_CYAN() | curses.A_BOLD)
    sadd(win, y, 6, "Historial", C_NORM())
    y += 1

    hline(win, y, 1, w - 2)
    y += 1

    sadd(win, y, 2, "[C]", C_YELLOW() | curses.A_BOLD)
    sadd(win, y, 6, "Corte de caja", C_NORM())
    y += 1
    sadd(win, y, 2, "[Q]", C_RED() | curses.A_BOLD)
    sadd(win, y, 6, "Salir", C_NORM())

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
        cant = f"{item.cantidad:.3f}".rstrip("0").rstrip(".") if item.sku == "CHI" else f"{item.cantidad:.0f}"
        linea = f"[{idx+1}] {item.descripcion[:16]:<16} {cant:>6}  ${item.subtotal:>8,.2f}"
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
    """Pantalla modal de cobro — selecciona forma de pago."""
    h, w = stdscr.getmaxyx()
    total = sesion.ticket.total

    my = h // 2 - 6
    mx = w // 2 - 18

    # dibujar caja modal
    for i in range(14):
        sadd(stdscr, my + i, mx, " " * 36)

    sadd(stdscr, my,     mx + 1, f"┌{'─'*34}┐", C_CYAN())
    sadd(stdscr, my + 1, mx + 1, f"│ COBRAR                           │", C_CYAN())
    sadd(stdscr, my + 2, mx + 1, f"│ TOTAL: ${total:>24,.2f} │", C_GREEN() | curses.A_BOLD)
    sadd(stdscr, my + 3, mx + 1, f"├{'─'*34}┤", C_CYAN())
    sadd(stdscr, my + 4, mx + 1, f"│                                  │", C_CYAN())
    sadd(stdscr, my + 5, mx + 1, f"│                                  │", C_CYAN())
    sadd(stdscr, my + 6, mx + 1, f"│                                  │", C_CYAN())
    sadd(stdscr, my + 7, mx + 1, f"├{'─'*34}┤", C_CYAN())
    sadd(stdscr, my + 8, mx + 1, f"│                                  │", C_CYAN())
    sadd(stdscr, my + 9, mx + 1, f"└{'─'*34}┘", C_CYAN())

    pagos = {"efectivo": 0.0, "transfer": 0.0, "tarjeta": 0.0}

    ef = pedir_float_modal(stdscr, "Efectivo  $", my + 4, mx + 3)
    pagos["efectivo"] = ef
    tr = pedir_float_modal(stdscr, "Transfer  $", my + 5, mx + 3)
    pagos["transfer"] = tr
    ta = pedir_float_modal(stdscr, "Tarjeta   $", my + 6, mx + 3)
    pagos["tarjeta"] = ta

    total_pagado = ef + tr + ta
    cambio = total_pagado - total

    if total_pagado < total - 0.01:
        sadd(stdscr, my + 8, mx + 2,
             f"FALTA ${total - total_pagado:.2f}  [Enter=retry]", C_RED() | curses.A_BOLD)
        stdscr.refresh()
        stdscr.getch()
        return "retry"

    if cambio > 0:
        sadd(stdscr, my + 8, mx + 2,
             f"CAMBIO: ${cambio:.2f}  [Enter]", C_YELLOW() | curses.A_BOLD)
        stdscr.refresh()
        stdscr.getch()

    try:
        resultado = cobrar(sesion, pagos)
        ticket_id = resultado["ticket_id"]

        # ofrecer imprimir
        sadd(stdscr, my + 8, mx + 2,
             f"Ticket #{ticket_id:04d}  [P]imprimir [Enter]", C_GREEN() | curses.A_BOLD)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("p"), ord("P")):
            txt = imprimir_ticket(ticket_id)
            # guardar en archivo para impresión
            ruta = os.path.expanduser(f"~/bayosys/data/ticket_{ticket_id:04d}.txt")
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            with open(ruta, "w") as f:
                f.write(txt)
            return f"ticket #{ticket_id:04d} guardado en {ruta}"

        return f"ticket #{ticket_id:04d} cobrado OK — cambio ${resultado['cambio']:.2f}"

    except ErrorPOS as e:
        return f"ERROR: {e}"


# ── PANTALLA INVENTARIO ───────────────────────────────────────────────────────

def pantalla_inventario(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sadd(stdscr, 0, 0, " INVENTARIO ".center(w), C_TITLE())
    hline(stdscr, 1, 0, w)

    inv = get_estado_inventario()
    sadd(stdscr, 2, 2, f"{'SKU':<8} {'Descripción':<22} {'Stock':>8} {'Precio':>10}", C_CYAN())
    hline(stdscr, 3, 2, w - 4)

    for i, row in enumerate(inv):
        y = 4 + i
        stock = row["stock"]
        color = C_RED() if stock <= 0 else (C_YELLOW() if stock <= 3 else C_GREEN())
        sadd(stdscr, y, 2,
             f"{row['sku']:<8} {row['descripcion']:<22} {stock:>8.0f} ${row['precio_venta']:>9.2f}",
             color if row["sku"] not in ("CHI", "LIB") else C_NORM())

    hline(stdscr, h - 3, 0, w)
    sadd(stdscr, h - 2, 2, "[C] carga manual   [A] abrir cubeta   [Esc] volver", C_NORM())
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break
        elif key in (ord("c"), ord("C")):
            # carga manual
            skus = [r["sku"] for r in inv if r["sku"] not in ("CHI", "LIB")]
            sadd(stdscr, h - 4, 2, f"SKU ({'/'.join(skus)}): ", C_CYAN())
            stdscr.refresh()
            sku_raw = pedir_input(stdscr, f"SKU: ", h - 4, 2, 8).upper()
            if sku_raw in skus:
                cant = pedir_int_modal(stdscr, f"Cantidad +: ", h - 4, 2)
                if cant > 0:
                    try:
                        msg = carga_manual_stock(sku_raw, cant)
                        flash_msg(stdscr, h, msg)
                    except ErrorPOS as e:
                        flash_msg(stdscr, h, str(e), C_RED())
            break  # refrescar inventario
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
        y = 4 + i
        sadd(stdscr, y, 2,
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
    sadd(stdscr, 0, 0, f" CORTE DE CAJA — batch#{sesion.batch_id} ".center(w), C_TITLE())
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

    fila("tickets del turno",  f"{corte['n_tickets']}")
    y += 1
    fila("ventas efectivo",    f"${corte['ventas_efectivo']:,.2f}", C_GREEN())
    fila("ventas transferencia",f"${corte['ventas_transfer']:,.2f}", C_GREEN())
    fila("ventas tarjeta",     f"${corte['ventas_tarjeta']:,.2f}",  C_GREEN())
    hline(stdscr, y, 4, 40)
    y += 1
    fila("TOTAL VENTAS",       f"${corte['total_ventas']:,.2f}", C_GREEN() | curses.A_BOLD)
    fila("gastos del turno",   f"${corte['total_gastos']:,.2f}", C_YELLOW())
    hline(stdscr, y, 4, 40)
    y += 1
    fila("NETO",               f"${corte['neto']:,.2f}", C_CYAN() | curses.A_BOLD)
    y += 1
    fila("fondo de caja",      f"${corte['fondo_caja']:,.2f}", C_NORM())
    fila("A ENTREGAR",         f"${corte['a_entregar']:,.2f}",
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


# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────

def main(stdscr, batch_id: int, operador: str = "Luis"):
    curses.curs_set(0)
    stdscr.keypad(True)
    init_colors()

    sesion = iniciar_pos(batch_id=batch_id, operador=operador)
    msg    = f"POS iniciado — batch#{batch_id}"

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # layout: izquierda 30 cols, derecha el resto
        ancho_izq = min(32, w // 3)
        ancho_der = w - ancho_izq

        # crear subventanas
        win_izq = stdscr.derwin(h - 1, ancho_izq, 0, 0)
        win_der = stdscr.derwin(h - 1, ancho_der, 0, ancho_izq)

        # inventario para mostrar stock
        inv_raw = get_estado_inventario()
        inv_dict = {r["sku"]: {"stock": r["stock"], "precio": r["precio_venta"]}
                    for r in inv_raw}

        draw_panel_izq(win_izq, sesion, inv_dict)
        draw_panel_ticket(win_der, sesion)
        draw_statusbar(stdscr, sesion, msg)

        stdscr.refresh()
        msg = ""  # limpiar después de mostrar

        key = stdscr.getch()

        # ── PRODUCTOS ─────────────────────────────────────────────────
        if key == ord("1"):   # chicharrón
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            kg = pedir_float_modal(stdscr, "kg chicharrón: ", h // 2, w // 2 - 12)
            if kg > 0:
                try:
                    agregar_chicharron(sesion, kg)
                    msg = f"chicharrón {kg:.3f}kg agregado"
                except ErrorPOS as e:
                    msg = f"! {e}"

        elif key == ord("2"):  # manteca 1lt
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            cant = pedir_int_modal(stdscr, "piezas 1lt: ", h // 2, w // 2 - 12)
            if cant > 0:
                try:
                    agregar_producto(sesion, "M1LT", cant)
                    msg = f"{cant} × Manteca 1lt agregados"
                except ErrorPOS as e:
                    msg = f"! {e}"

        elif key == ord("3"):  # manteca ½lt
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            cant = pedir_int_modal(stdscr, "piezas ½lt: ", h // 2, w // 2 - 12)
            if cant > 0:
                try:
                    agregar_producto(sesion, "M05", cant)
                    msg = f"{cant} × Manteca ½lt agregados"
                except ErrorPOS as e:
                    msg = f"! {e}"

        elif key == ord("4"):  # chorizo
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            cant = pedir_int_modal(stdscr, "piezas chorizo: ", h // 2, w // 2 - 12)
            if cant > 0:
                try:
                    agregar_producto(sesion, "CHO", cant)
                    msg = f"{cant} × Chorizo agregados"
                except ErrorPOS as e:
                    msg = f"! {e}"

        elif key == ord("5"):  # cubeta precio variable
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            precio = pedir_float_modal(stdscr, "precio cubeta $: ", h // 2, w // 2 - 14)
            if precio > 0:
                try:
                    agregar_producto_precio_variable(sesion, "CUB", 1, precio)
                    msg = f"cubeta ${precio:.0f} agregada"
                except ErrorPOS as e:
                    msg = f"! {e}"

        elif key == ord("6"):  # artículo libre
            if sesion.ticket is None:
                sesion.nuevo_ticket()
            desc  = pedir_input(stdscr, "descripción: ", h // 2,     w // 2 - 14, 20)
            if desc:
                precio = pedir_float_modal(stdscr, "precio $: ",     h // 2 + 1, w // 2 - 14)
                cant   = pedir_int_modal(stdscr,   "cantidad: ",      h // 2 + 2, w // 2 - 14)
                if precio > 0 and cant > 0:
                    try:
                        agregar_articulo_libre(sesion, desc, cant, precio)
                        msg = f"'{desc}' agregado"
                    except ErrorPOS as e:
                        msg = f"! {e}"

        # ── QUITAR ITEM ───────────────────────────────────────────────
        elif key in (ord("x"), ord("X")):
            if sesion.ticket and not sesion.ticket.vacio():
                idx = pedir_int_modal(stdscr, "quitar ítem #: ", h // 2, w // 2 - 14)
                if 1 <= idx <= sesion.ticket.n_items:
                    sesion.ticket.quitar(idx - 1)
                    msg = f"ítem #{idx} quitado"

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
        elif key == 27:   # Esc
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
            tipo = {"49": "gas", "50": "leche", "51": "gral"}.get(str(tipo_key))
            if not tipo:
                tipo = "gral"
            monto = pedir_float_modal(stdscr, "monto $: ", h // 2, w // 2 - 14)
            desc  = pedir_input(stdscr, "descripción: ", h // 2 + 1, w // 2 - 14, 25)
            if monto > 0:
                try:
                    registrar_gasto(sesion.batch_id, tipo, monto, desc)
                    msg = f"gasto {tipo} ${monto:.0f} registrado"
                except ErrorPOS as e:
                    msg = f"! {e}"

        # ── ABRIR CUBETA ──────────────────────────────────────────────
        elif key in (ord("a"), ord("A")):
            e1 = pedir_int_modal(stdscr, "envases 1lt a cargar: ",  h // 2,     w // 2 - 16)
            e05= pedir_int_modal(stdscr, "envases ½lt a cargar: ",  h // 2 + 1, w // 2 - 16)
            if e1 > 0 or e05 > 0:
                try:
                    r = abrir_cubeta_pos(e1, e05)
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


def iniciar_pos_tui(batch_id: int, operador: str = "Luis"):
    curses.wrapper(main, batch_id, operador)


if __name__ == "__main__":
    iniciar_pos_tui(batch_id=1)
