"""
pos_tui.py — bayoSys · Productos El Bayo
Interfaz curses del POS. Pantalla dividida estilo htop.

CAMBIOS:
  - MENU_PRODUCTOS eliminado — panel se construye desde get_menu_pos() en DB
  - Handler de teclas dinámico por tipo_venta del SKU
  - Agregar producto nuevo al catálogo → aparece automáticamente en el POS
  - El resto del archivo no cambia: flujo_cobro, corte, historial, inventario

Layout:
  ┌─ PRODUCTOS ──────┬─ TICKET ACTUAL ───────────────────┐
  │ [1] Chicharrón   │  1.250 kg  Chicharrón   $287.50   │
  │ [2] Manteca 1lt  │  2 pza     Manteca 1lt   $70.00   │
  │ [3] Manteca ½lt  │  ──────────────────────────────   │
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
    cobrar, hacer_corte, registrar_gasto, abrir_cubeta_pos, litrear_pos,
    carga_manual_stock, get_estado_inventario, get_resumen_turno, get_pool_lt,
    get_historial_tickets, imprimir_ticket, ErrorPOS, SesionPOS,
    alta_cliente_mayoreo, listar_clientes_mayoreo, capturar_pedido_mayoreo,
    ajustar_pedido_mayoreo, entregar_pedido_mayoreo, get_pedidos_mayoreo_pendientes,
    PRIORIDADES_MAYOREO
)
from pos_db import get_ticket_items, anular_ticket, get_menu_pos
from config import fecha_hoy, cargar_config
from models import LT_POR_CUBETA
from estilos import (
    init_colors, sadd, hline, marco, caja, divisor, encabezado, pie,
    abrir_lienzo,
    verificar_tamano, set_titulo_terminal, CAJA, UNICODE,
    TEXTO, ACENTO, OK, AVISO, ALERTA, TITULO, DATO, TAB, BOTON, WARN, CHROME,
)

try:
    from pos_ticket import imprimir_ticket_fisico, ErrorImpresora
    IMPRESORA_DISPONIBLE = True
except ImportError:
    IMPRESORA_DISPONIBLE = False
    class ErrorImpresora(Exception):
        pass


# ── HELPERS DE DIBUJO ─────────────────────────────────────────────────────────
# La paleta, los caracteres de borde y sadd/hline viven en estilos.py —
# compartidos con tui.py para que las dos pantallas de curses no vuelvan
# a divergir. Aquí solo queda lo específico del POS.

# El lienzo es de tamaño fijo, así que el split se elige a propósito en vez
# de derivarlo del ancho de la terminal: 34 columnas alcanzan para el nombre
# de producto más largo del catálogo más la columna de stock.
ANCHO_PANEL_IZQ = 34

def flash_msg(win, h, msg, color=None):
    if color is None:
        color = OK()
    sadd(win, h - 2, 1, " " * (win.getmaxyx()[1] - 2))
    sadd(win, h - 2, 2, f" {msg} ", color)
    win.refresh()
    curses.napms(1200)


# ── INPUT MODAL ───────────────────────────────────────────────────────────────

def pedir_input(stdscr, prompt: str, y: int, x: int, ancho: int = 20) -> str:
    curses.curs_set(1)
    sadd(stdscr, y, x, prompt, ACENTO() | curses.A_BOLD)
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
                sadd(stdscr, y, cx + len(buf) - 1, chr(key), ACENTO())

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
        sadd(stdscr, y + 1, x, "  ! número inválido  ", ALERTA())
        stdscr.refresh()
        curses.napms(800)
        sadd(stdscr, y + 1, x, " " * 22)

def pedir_int_modal(stdscr, prompt: str, y: int, x: int) -> int:
    val = pedir_float_modal(stdscr, prompt, y, x)
    return int(val)


def _esperar_confirmacion(stdscr) -> bool:
    """Bloquea hasta [Enter] (True) o [Esc] (False) — ignora cualquier otra tecla."""
    while True:
        key = stdscr.getch()
        if key in (10, 13):
            return True
        if key == 27:
            return False


def pedir_kg_o_monto_modal(stdscr, nombre: str, precio_kg: float, y: int, x: int) -> tuple:
    """
    Doble entrada para productos por peso (chicharrón): kg y monto ($)
    se muestran juntos — llenar uno calcula el otro con precio_kg.
    Enter vacío (o Esc) en 'kg' pasa el turno a 'monto'; vacío en ambos cancela.
    Retorna (kg, monto); (0.0, 0.0) si se cancela.
    """
    label_kg    = f"kg {nombre}: "
    label_monto = "$ monto:    "
    ancho_label = max(len(label_kg), len(label_monto))
    cx = x + ancho_label

    sadd(stdscr, y,     x, label_kg,    ACENTO() | curses.A_BOLD)
    sadd(stdscr, y + 1, x, label_monto, ACENTO() | curses.A_BOLD)

    kg = pedir_float_modal(stdscr, "", y, cx)
    if kg > 0:
        monto = round(kg * precio_kg, 2) if precio_kg > 0 else 0.0
        sadd(stdscr, y + 1, cx, f"{monto:.2f}  (calculado)", OK())
        stdscr.refresh()
        curses.napms(700)
        return round(kg, 3), monto

    monto = pedir_float_modal(stdscr, "", y + 1, cx)
    if monto <= 0:
        return 0.0, 0.0
    if precio_kg <= 0:
        sadd(stdscr, y, x, "  ! precio no configurado  ", ALERTA())
        stdscr.refresh()
        curses.napms(1000)
        return 0.0, 0.0

    kg = round(monto / precio_kg, 3)
    sadd(stdscr, y, cx, f"{kg:.3f}  (calculado)", OK())
    stdscr.refresh()
    curses.napms(700)
    return kg, round(monto, 2)

def _modal_bloqueante(stdscr, fn, *args, **kwargs):
    """
    Ejecuta fn con timeout desactivado (getch() bloquea indefinido),
    y restaura el timeout del ticker (150ms) al salir, incluso si fn
    lanza una excepción — para que el ticker nunca quede huérfano.
    """
    stdscr.timeout(-1)
    try:
        return fn(stdscr, *args, **kwargs)
    finally:
        stdscr.timeout(150)

def pedir_cantidad_ajuste_modal(stdscr, prompt: str, y: int, x: int) -> float:
    """
    Pide una cantidad de AJUSTE de inventario con dirección explícita.
    Primero [+]/[-] para la dirección, luego la magnitud (acepta decimales).
    Devuelve el valor ya con signo aplicado. Esc en la dirección cancela (0.0).
    """
    sadd(stdscr, y, x, "  [+] sumar   [-] restar  ", ACENTO() | curses.A_BOLD)
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key == ord("+"):
            signo = 1
            break
        elif key == ord("-"):
            signo = -1
            break
        elif key == 27:
            sadd(stdscr, y, x, " " * 28)
            return 0.0
    sadd(stdscr, y, x, " " * 28)
    magnitud = pedir_float_modal(stdscr, prompt, y, x)
    return signo * magnitud


# ── MENÚ DINÁMICO — construido desde DB ───────────────────────────────────────

def _color_stock(sku: str, tipo_venta: str, stock: float):
    """Retorna (stock_str, color) según tipo de producto y nivel de stock."""
    if tipo_venta == "libre":
        # no lleva inventario: se captura descripción y precio al vender.
        # Un guion deja la columna alineada en vez de un hueco.
        return "—" if UNICODE else "-", CHROME()
    if tipo_venta == "peso":
        # chicharrón — en kg
        if stock <= 0:
            return "sin stock", ALERTA()
        elif stock <= 5:
            return f"{stock:.2f} kg", AVISO()
        else:
            return f"{stock:.2f} kg", OK()
    else:
        # piezas — normal, variable
        if stock <= 0:
            return "sin stock", ALERTA()
        elif stock <= 3:
            return f"{stock:.0f} pza", AVISO()
        else:
            return f"{stock:.0f} pza", OK()


def _tecla_a_idx(key: int) -> int:
    """Convierte tecla ASCII '1'-'9' a índice 0-8. Retorna -1 si no aplica."""
    if ord("1") <= key <= ord("9"):
        return key - ord("1")
    return -1


# ── PANEL IZQUIERDO — DINÁMICO ────────────────────────────────────────────────

def draw_panel_izq(win, sesion: SesionPOS, inv_dict: dict, menu: list):
    """
    Dibuja el panel de productos leyendo 'menu' — lista de rows de get_menu_pos().
    Cada row tiene: sku, descripcion, tipo_venta, orden_menu, precio_venta, stock.
    Las teclas 1-9 se asignan según posición en la lista (orden_menu en DB).

    Devuelve las filas donde dibujó divisiones, para que el loop principal
    pueda empalmarlas con la columna que comparte con el panel del ticket.
    """
    h, w = win.getmaxyx()
    win.erase()
    marco(win, "productos")

    # las columnas se derivan del ancho del panel en vez de estar fijas,
    # para que el stock quede alineado a la derecha sin importar el split
    x_stock      = w - 12
    ancho_nombre = x_stock - 7

    # última fila utilizable: h-1 es el borde inferior y no se toca. Sin este
    # límite, en una terminal chica el menú se dibuja encima del marco.
    ultima = h - 1

    y = 2
    for idx, row in enumerate(menu):
        if y >= ultima:
            break
        tecla     = str(idx + 1) if idx < 9 else "?"
        sku       = row["sku"]
        nombre    = row["descripcion"][:ancho_nombre]
        tipo      = row["tipo_venta"]
        stock     = inv_dict.get(sku, {}).get("stock", 0)
        stock_str, color = _color_stock(sku, tipo, stock)

        sadd(win, y, 2,       f"[{tecla}]",                  ACENTO() | curses.A_BOLD)
        sadd(win, y, 6,       f"{nombre:<{ancho_nombre}}",   TEXTO())
        sadd(win, y, x_stock, f"{stock_str:>10}",            color)
        y += 1

    # las acciones se agrupan por naturaleza: primero las que mueven
    # inventario o dinero, luego las de solo lectura, y aparte las de salida.
    # None = división entre grupos.
    acciones = [
        None,
        ("G", "Registrar gasto",                AVISO()),
        ("A", "Abrir cubeta",                   AVISO()),
        ("L", f"Litrear {get_pool_lt():.1f}lt", AVISO()),
        ("I", "Inventario",                     ACENTO()),
        ("H", "Historial",                      ACENTO()),
        ("M", "Mayoreo",                        AVISO()),
        None,
        ("C", "Corte de caja",                  AVISO()),
        ("Q", "Salir",                          ALERTA()),
    ]

    y += 1
    filas_divisor = []
    for entrada in acciones:
        if y >= ultima:
            break
        if entrada is None:
            filas_divisor.append(y)
            divisor(win, y, 0, w)
        else:
            tecla, etiqueta, color = entrada
            sadd(win, y, 2, f"[{tecla}]", color | curses.A_BOLD)
            sadd(win, y, 6, etiqueta,     TEXTO())
        y += 1

    # sin refresh() propio: las subventanas comparten el buffer de stdscr,
    # así que el loop principal pinta todo de una sola vez. Refrescar aquí
    # hacía que la columna compartida se repintara tres veces por frame
    # (borde izq → borde der → empalme) y parpadeara.
    return filas_divisor


# ── PANEL DERECHO — TICKET ────────────────────────────────────────────────────

def draw_panel_ticket(win, sesion: SesionPOS):
    h, w = win.getmaxyx()
    win.erase()
    marco(win, "ticket actual")

    if sesion.ticket is None or sesion.ticket.vacio():
        sadd(win, 2, 3, "sin carga registrada", CHROME())
        sadd(win, h - 2, 3, "en espera — selecciona un producto", CHROME())
        return

    y = 2
    for idx, item in enumerate(sesion.ticket.items):
        if y >= h - 5:
            sadd(win, y, 3, f"... +{len(sesion.ticket.items) - idx} más", AVISO())
            break
        # formato cantidad según tipo
        if item.sku == "CHI":
            cant = f"{item.cantidad:.3f}kg"
        else:
            cant = f"{item.cantidad:.0f} pza"
        # el renglón se pinta por partes para que el importe destaque
        # sobre la descripción — es el dato que el operador verifica
        sadd(win, y, 2,      f"[{idx+1}]",                  CHROME())
        sadd(win, y, 6,      f"{item.descripcion[:18]:<18}", TEXTO())
        sadd(win, y, 25,     f"{cant:>9}",                   DATO())
        sadd(win, y, w - 13, f"${item.subtotal:>10,.2f}",    TEXTO())
        y += 1

    divisor(win, h - 5, 0, w)
    sadd(win, h - 4, 3,      "TOTAL", ACENTO() | curses.A_BOLD)
    sadd(win, h - 4, w - 14, f"${sesion.ticket.total:>11,.2f}",
         OK() | curses.A_BOLD)
    sadd(win, h - 2, 3, "[X] quitar item   [P] cobrar   [Esc] limpiar", CHROME())


# ── COBRO EN EFECTIVO ─────────────────────────────────────────────────────────

# Medio centavo. Comparar efectivo contra total con `<` desnudo rechaza cobros
# legítimos cuando el total trae cola binaria; con este margen, una diferencia
# de 1e-14 nunca puede leerse como faltante. Es tolerancia de comparación, no
# de cobro: 10 centavos de menos siguen siendo insuficientes.
TOLERANCIA_CENTAVO = 0.005

# Billetes que de verdad circulan en el mostrador. Las monedas fraccionarias
# quedan fuera a propósito: en Hermosillo la de 5 centavos ya no circula y la
# de 10 casi no aparece en caja.
DENOMINACIONES = ((ord("1"), 50), (ord("2"), 100),
                  (ord("3"), 200), (ord("4"), 500))

try:
    from pos import total_a_cobrar_efectivo
except ImportError:
    # TODO(Luis) — §5.C.2. La política de redondeo va en pos.py, con la
    # constante REDONDEO_EFECTIVO en config.py. Este stub deja la pantalla
    # funcionando idéntica a hoy (cobra el total al centavo) y la conecta
    # sola en cuanto exista la función real.
    #
    #     def total_a_cobrar_efectivo(total: float) -> float:
    #         """Redondea al múltiplo configurado. NO altera el total."""
    #
    # Se redondea el COBRO, nunca la venta registrada: si se redondea la
    # venta, el histórico de precios se degrada y analisis.py empieza a
    # producir márgenes falsos.
    def total_a_cobrar_efectivo(total: float) -> float:
        return total


def _pantalla_efectivo(stdscr, total: float, my: int, mx: int) -> float:
    """
    Captura del efectivo recibido. Retorna lo que entregó el cliente,
    o 0.0 si se canceló.

    Muestra los tres números que el operador necesita —total, importe a
    cobrar y cambio— sin ajustes silenciosos: si el importe a cobrar difiere
    del total, la línea de redondeo aparece etiquetada.
    """
    a_cobrar   = round(total_a_cobrar_efectivo(total), 2)
    diferencia = round(a_cobrar - total, 2)
    recibido   = 0.0

    ANCHO = 40
    ALTO  = 17

    while True:
        for i in range(ALTO):
            sadd(stdscr, my + i, mx, " " * (ANCHO + 1))
        caja(stdscr, my, mx + 1, ALTO, ANCHO, "efectivo")

        y = my + 2
        sadd(stdscr, y, mx + 3,  "total del ticket", CHROME())
        sadd(stdscr, y, mx + 25, f"${total:>12,.2f}", TEXTO())

        # El redondeo solo se anuncia cuando existe. Una línea de "+0.00"
        # permanente sería ruido que el operador aprende a ignorar.
        if abs(diferencia) >= 0.005:
            y += 1
            etiqueta = "redondeo" if diferencia < 0 else "redondeo (+)"
            sadd(stdscr, y, mx + 3,  etiqueta, AVISO())
            sadd(stdscr, y, mx + 25, f"${diferencia:>+12,.2f}", AVISO())

        y += 1
        sadd(stdscr, y, mx + 3,  "A COBRAR", ACENTO() | curses.A_BOLD)
        sadd(stdscr, y, mx + 25, f"${a_cobrar:>12,.2f}", OK() | curses.A_BOLD)

        divisor(stdscr, my + 5, mx + 1, ANCHO, pesado=False)

        sadd(stdscr, my + 6, mx + 3, "recibido", CHROME())
        sadd(stdscr, my + 6, mx + 25, f"${recibido:>12,.2f}", DATO() | curses.A_BOLD)

        # El cambio es lo que el operador busca de un vistazo: va en su propia
        # caja y en video inverso, el tratamiento más prominente disponible.
        falta  = round(a_cobrar - recibido, 2)
        cambio = round(recibido - a_cobrar, 2)
        caja(stdscr, my + 7, mx + 1, 3, ANCHO, "cambio")
        if recibido <= 0:
            sadd(stdscr, my + 8, mx + 3, f"{'—':^{ANCHO-4}}", CHROME())
        elif falta > TOLERANCIA_CENTAVO:
            sadd(stdscr, my + 8, mx + 3,
                 f"{('FALTAN $' + format(falta, ',.2f')):^{ANCHO-4}}",
                 WARN() | curses.A_BOLD)
        else:
            sadd(stdscr, my + 8, mx + 3,
                 f"{('$' + format(cambio, ',.2f')):^{ANCHO-4}}",
                 BOTON() | curses.A_BOLD)

        fila = my + 11
        sadd(stdscr, fila, mx + 3, "[1] 50   [2] 100   [3] 200   [4] 500", ACENTO())
        sadd(stdscr, fila + 1, mx + 3, "[E] exacto   [M] otro monto   [C] limpiar",
             ACENTO())
        sadd(stdscr, fila + 3, mx + 3, "[Enter] cobrar      [Esc] cancelar", TEXTO())
        stdscr.refresh()

        key = stdscr.getch()

        if key == 27:
            return 0.0

        elif key in dict(DENOMINACIONES):
            # se acumulan: dos toques a [2] son 200
            recibido = round(recibido + dict(DENOMINACIONES)[key], 2)

        elif key in (ord("e"), ord("E")):
            recibido = a_cobrar          # pago justo, cambio cero

        elif key in (ord("c"), ord("C")):
            recibido = 0.0

        elif key in (ord("m"), ord("M")):
            libre = pedir_float_modal(stdscr, "  monto $", my + ALTO - 2, mx + 2)
            if libre > 0:
                recibido = round(recibido + libre, 2)

        elif key in (10, 13):
            if recibido <= 0:
                continue
            # Único motivo válido para rechazar: el dinero no alcanza de
            # verdad. Nunca por una diferencia en el decimocuarto decimal.
            if recibido < a_cobrar - TOLERANCIA_CENTAVO:
                sadd(stdscr, my + ALTO - 2, mx + 3,
                     f"  faltan ${a_cobrar - recibido:,.2f}  ", ALERTA() | curses.A_BOLD)
                stdscr.refresh()
                curses.napms(1200)
                continue
            return recibido


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

    ANCHO_MODAL = 38   # el modal es de ancho fijo: es una ficha de cobro,
                       # no un panel que deba estirarse con la pantalla

    def _dibujar_caja():
        for i in range(16):
            sadd(stdscr, my + i, mx, " " * (ANCHO_MODAL + 1))
        caja(stdscr, my, mx + 1, 16, ANCHO_MODAL, "cobrar")

        sadd(stdscr, my + 2, mx + 3,  "TOTAL", ACENTO() | curses.A_BOLD)
        sadd(stdscr, my + 2, mx + 23, f"${total:>13,.2f}", OK() | curses.A_BOLD)
        divisor(stdscr, my + 3, mx + 1, ANCHO_MODAL, pesado=False)

        sadd(stdscr, my + 4, mx + 3, "MÉTODO DE PAGO", CHROME())
        for i, (tecla, etiqueta) in enumerate((("1", "Efectivo"),
                                               ("2", "Transferencia"),
                                               ("3", "Tarjeta"))):
            sadd(stdscr, my + 5 + i, mx + 4, f"[{tecla}]", ACENTO() | curses.A_BOLD)
            sadd(stdscr, my + 5 + i, mx + 8, etiqueta,     TEXTO())
        sadd(stdscr, my + 8, mx + 4,  "[Esc]",    ALERTA() | curses.A_BOLD)
        sadd(stdscr, my + 8, mx + 10, "Cancelar", TEXTO())

        divisor(stdscr, my + 9, mx + 1, ANCHO_MODAL, pesado=False)
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
    if metodo1 == "efectivo":
        # El efectivo tiene su propia pantalla: es el único método con
        # problema físico de cambio. Terminal y transferencia se cobran al
        # centavo exacto y siguen por el camino de siempre.
        monto1 = _pantalla_efectivo(stdscr, total, my, mx)
    else:
        _dibujar_caja()
        monto1 = pedir_float_modal(stdscr, f"  {LABELS[metodo1]}$", my + 10, mx + 2)
    if monto1 <= 0:
        return ""

    pagos[metodo1] = monto1
    restante = round(total - monto1, 2)

    if metodo1 == "efectivo":
        _dibujar_caja()   # la pantalla de efectivo dibujó lo suyo encima

    # TODO(Luis) — §5.C.3. Cuando total_a_cobrar_efectivo() empiece a
    # redondear hacia abajo, el efectivo recibido puede ser menor al total
    # del ticket y este `restante` va a pedir un segundo método por una
    # diferencia que en realidad ya se perdonó. Falta que cobrar() (pos.py)
    # reciba el importe redondeado y guarde la diferencia en la columna
    # tickets.diferencia_redondeo — la migración ya está puesta en pos_db.py.
    # Sin ese renglón el corte no cuadra: el efectivo esperado pasa a ser
    # ventas en efectivo + redondeo acumulado − gastos.

    # paso 3: ¿cubre?
    if restante <= 0:
        cambio = abs(restante)
        if cambio > 0:
            sadd(stdscr, my + 11, mx + 2,
                 f"  CAMBIO: ${cambio:>8.2f}          ", AVISO() | curses.A_BOLD)
        else:
            sadd(stdscr, my + 11, mx + 2,
                 f"  Exacto ✓                    ", OK())
        sadd(stdscr, my + 14, mx + 2,
             "  [Enter] cobrar  [Esc] cancelar", TEXTO())
        stdscr.refresh()
        if not _esperar_confirmacion(stdscr):
            return ""
    else:
        # necesita segundo método — loopea hasta un método válido y distinto al primero
        sadd(stdscr, my + 11, mx + 2,
             f"  Falta: ${restante:>8.2f}              ", ALERTA() | curses.A_BOLD)
        while True:
            sadd(stdscr, my + 12, mx + 2,
                 "  2do método: [1]Ef [2]Tr [3]Ta [Esc]", ACENTO())
            stdscr.refresh()
            key = stdscr.getch()
            if key == 27:
                return ""
            if key in METODOS and METODOS[key] != metodo1:
                metodo2 = METODOS[key]
                break
            if key in METODOS:
                sadd(stdscr, my + 12, mx + 2,
                     "  ! ya usaste ese método — elige otro", ALERTA())
                stdscr.refresh()
                curses.napms(900)

        monto2 = pedir_float_modal(stdscr, f"  {LABELS[metodo2]}$", my + 12, mx + 2)
        pagos[metodo2] = monto2
        total_pagado   = monto1 + monto2
        if total_pagado < total - 0.01:
            sadd(stdscr, my + 14, mx + 2,
                 f"  FALTA ${total-total_pagado:.2f} — [Enter]", ALERTA() | curses.A_BOLD)
            stdscr.refresh()
            stdscr.getch()
            return "retry"
        cambio = round(total_pagado - total, 2)
        if cambio > 0:
            sadd(stdscr, my + 13, mx + 2,
                 f"  CAMBIO: ${cambio:.2f}          ", AVISO() | curses.A_BOLD)
        sadd(stdscr, my + 14, mx + 2,
             "  [Enter] cobrar  [Esc] cancelar  ", TEXTO())
        stdscr.refresh()
        if not _esperar_confirmacion(stdscr):
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
             OK() | curses.A_BOLD)
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
    encabezado(stdscr, "inventario", "escaneo de bodega")

    inv = get_estado_inventario()
    sadd(stdscr, 2, 2,
         f"{'SKU':<8} {'Descripción':<20} {'Tipo':<10} {'Stock':>9} {'Precio':>10}",
         ACENTO())
    hline(stdscr, 3, 2, w - 4)

    for i, row in enumerate(inv):
        y     = 4 + i
        stock = row["stock"]
        tipo  = row["tipo_venta"]
        stock_str, color = _color_stock(row["sku"], tipo, stock)
        sadd(stdscr, y, 2,
             f"{row['sku']:<8} {row['descripcion']:<20} {tipo:<10} "
             f"{stock_str:>9}  ${row['precio_venta']:>9.2f}",
             color if tipo != "libre" else TEXTO())

    # manteca a granel — la cubeta fraccionada que dejó la producción
    pool = get_pool_lt()
    y_pool = 4 + len(inv) + 1
    hline(stdscr, y_pool, 2, w - 4)
    sadd(stdscr, y_pool + 1, 2,
         f"{'GRANEL':<8} {'Manteca a granel':<20} {'pool':<10} "
         f"{pool:>6.2f} lt",
         OK() if pool > 0 else TEXTO())
    sadd(stdscr, y_pool + 2, 2,
         f"         faltan {max(0.0, LT_POR_CUBETA - pool):.2f} lt para completar cubeta",
         TEXTO())

    pie(stdscr, ("C", "carga manual"), ("A", "abrir cubeta"),
        ("L", "litrear granel"), ("Esc", "volver"))
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break
        elif key in (ord("c"), ord("C")):
            skus = [r["sku"] for r in inv if r["tipo_venta"] not in ("libre",)]
            sku_raw = pedir_input(stdscr, "SKU: ", h - 4, 2, 8).upper()
            if sku_raw in skus:
                delta = pedir_cantidad_ajuste_modal(stdscr, "Cantidad +: ", h - 4, 12)
                if delta != 0:
                    try:
                        msg = carga_manual_stock(sku_raw, delta)
                        flash_msg(stdscr, h, msg)
                    except ErrorPOS as e:
                        flash_msg(stdscr, h, str(e), ALERTA())
            break
        elif key in (ord("a"), ord("A"), ord("l"), ord("L")):
            # [A] rompe una cubeta sellada; [L] envasa del granel que ya está abierto
            desde_cubeta = key in (ord("a"), ord("A"))

            def _pedir_envases(stdscr):
                e1  = pedir_int_modal(stdscr, "envases 1lt: ", h - 4, 2)
                e05 = pedir_int_modal(stdscr, "envases ½lt: ", h - 4, 24)
                return e1, e05

            e1, e05 = _modal_bloqueante(stdscr, _pedir_envases)
            if e1 > 0 or e05 > 0:
                try:
                    r = (abrir_cubeta_pos(e1, e05) if desde_cubeta
                         else litrear_pos(e1, e05))
                    flash_msg(stdscr, h, r["mensaje"])
                except ErrorPOS as e:
                    flash_msg(stdscr, h, str(e), ALERTA())
            break


# ── PANTALLA HISTORIAL ────────────────────────────────────────────────────────

def pantalla_historial(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    encabezado(stdscr, "historial de tickets", "registro del turno")

    tickets = get_historial_tickets()
    sadd(stdscr, 2, 2,
         f"{'#':>4}  {'hora':<6}  {'ef':>8}  {'tr':>8}  {'ta':>8}  {'total':>9}",
         ACENTO())
    hline(stdscr, 3, 2, w - 4)

    for i, t in enumerate(tickets[-20:]):
        sadd(stdscr, 4 + i, 2,
             f"{t['id']:>4}  {t['hora'][:5]:<6}  "
             f"${t['pago_efectivo']:>7.0f}  "
             f"${t['pago_transfer']:>7.0f}  "
             f"${t['pago_tarjeta']:>7.0f}  "
             f"${t['total']:>8.2f}",
             TEXTO())

    pie(stdscr, ("Esc", "volver"))
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break

# ── PANTALLA PEDIDOS DE MAYOREO ──────────────────────────────────────────────

_COLOR_PRIORIDAD = {
    "muy_alta": lambda: ALERTA()   | curses.A_BOLD,
    "alta":     lambda: AVISO() | curses.A_BOLD,
    "media":    lambda: AVISO(),
    "baja":     lambda: ACENTO(),
}

def pantalla_status_pedidos(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    encabezado(stdscr, "pedidos de mayoreo", "pendientes")

    pedidos = get_pedidos_mayoreo_pendientes()

    if not pedidos:
        sadd(stdscr, 3, 2, "  cola despejada — sin pedidos en espera", CHROME())
    else:
        sadd(stdscr, 2, 2,
             f"{'#':>4}  {'prioridad':<9}  {'cliente':<16}  {'kg':>6}  {'precio':>8}  {'total':>9}  entrega",
             ACENTO())
        hline(stdscr, 3, 2, w - 4)

        for i, p in enumerate(pedidos[: h - 6]):
            y     = 4 + i
            color = _COLOR_PRIORIDAD.get(p["prioridad"], lambda: TEXTO())()
            total = p["kg"] * p["precio_kg_pactado"]
            sadd(stdscr, y, 2,
                 f"{p['id']:>4}  {p['prioridad']:<9}  {p['cliente_nombre'][:16]:<16}  "
                 f"{p['kg']:>5.1f}k  ${p['precio_kg_pactado']:>6.0f}  "
                 f"${total:>8.0f}  {p['fecha_entrega']}",
                 color)

    pie(stdscr, ("Esc", "volver"))
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            break

# ── FLUJOS DE CAPTURA — MAYOREO ──────────────────────────────────────────────

def flujo_alta_cliente_mayoreo(stdscr) -> str:
    """Da de alta un cliente nuevo en el tabulador de mayoreo."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sadd(stdscr, h // 2 - 4, w // 2 - 16, "NUEVO CLIENTE MAYOREO", TITULO())
    stdscr.refresh()

    nombre = pedir_input(stdscr, "nombre: ", h // 2 - 2, w // 2 - 16, 25)
    if not nombre:
        return ""
    precio = pedir_float_modal(stdscr, "precio $/kg: ", h // 2 - 1, w // 2 - 16)
    if precio <= 0:
        return ""

    try:
        r = alta_cliente_mayoreo(nombre, nombre, precio)
        return r["mensaje"]
    except ErrorPOS as e:
        return f"! {e}"


def flujo_capturar_pedido_mayoreo(stdscr) -> str:
    """Captura un pedido de mayoreo — no descuenta stock, solo informa."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    clientes = listar_clientes_mayoreo()

    if not clientes:
        sadd(stdscr, h // 2, w // 2 - 20,
             "  sin clientes en registro — da de alta primero  ", ALERTA())
        stdscr.refresh()
        stdscr.getch()
        return ""

    encabezado(stdscr, "capturar pedido", "mayoreo")
    for i, c in enumerate(clientes[:9]):
        sadd(stdscr, 3 + i, 2,
             f"[{i+1}] {c['nombre']:<20}  ${c['precio_kg']:.0f}/kg", TEXTO())
    stdscr.refresh()

    tecla_key = stdscr.getch()
    idx = _tecla_a_idx(tecla_key)
    if not (0 <= idx < len(clientes)):
        return ""
    cliente = clientes[idx]

    my = 4 + len(clientes[:9]) + 1
    kg = pedir_float_modal(stdscr, "kg del pedido: ", my, 2)
    if kg <= 0:
        return ""

    fecha_entrega = pedir_input(stdscr, "entrega (AAAA-MM-DD, Enter=mañana): ",
                                 my + 1, 2, 12)
    if not fecha_entrega:
        from datetime import date, timedelta
        fecha_entrega = (date.today() + timedelta(days=1)).isoformat()

    sadd(stdscr, my + 2, 2,
         "prioridad: [1]muy_alta [2]alta [3]media [4]baja", ACENTO())
    stdscr.refresh()
    p_key = stdscr.getch()
    prioridad = {
        ord("1"): "muy_alta", ord("2"): "alta",
        ord("3"): "media",    ord("4"): "baja",
    }.get(p_key, "media")

    try:
        r = capturar_pedido_mayoreo(cliente["clave"], kg, fecha_entrega, prioridad)
        return r["mensaje"]
    except ErrorPOS as e:
        return f"! {e}"


# ── DESPACHO DE PEDIDOS DE MAYOREO ────────────────────────────────────────────

# Tercera copia de la misma señal — main.py:62 y registro.py:25 ya la traen.
# Sigue pendiente el refactor de extraerla a un módulo compartido; repetir la
# convención existente es menos daño que inventar una cuarta aquí.
class _Cancelado(Exception):
    """Señal interna — el operador abortó el despacho a la mitad."""
    pass


# Qué tanto puede alejarse el kilaje real de lo pactado antes de pedir
# confirmación. Una diferencia grande casi siempre es un dedazo en la báscula,
# no una renegociación — pero renegociar es legítimo, así que se pregunta en
# vez de bloquear.
TOLERANCIA_KG_PEDIDO = 0.15


def _pedir_kg_reales(stdscr, kg_pedidos: float, y: int, x: int) -> float:
    """
    Kilos que de verdad salieron. Enter vacío o 0 aborta el despacho.
    Los kg pedidos quedan a la vista como referencia mientras se teclea.
    """
    sadd(stdscr, y, x, f"kg pedidos: {kg_pedidos:.1f}", DATO())
    kg_real = pedir_float_modal(stdscr, "kg reales:  ", y + 1, x)
    if kg_real <= 0:
        raise _Cancelado()
    return kg_real


def _confirmar_diferencia_kg(stdscr, kg_pedidos: float, kg_real: float,
                             y: int, x: int) -> None:
    """
    Freno de plausibilidad física. No decide nada: solo obliga a mirar el
    número cuando se sale del rango esperado.
    """
    if kg_pedidos <= 0:
        return
    desvio = abs(kg_real - kg_pedidos) / kg_pedidos
    if desvio <= TOLERANCIA_KG_PEDIDO:
        return

    signo = "más" if kg_real > kg_pedidos else "menos"
    sadd(stdscr, y, x,
         f"  ! {kg_real:.1f}kg es {desvio*100:.0f}% {signo} que lo pactado  ", WARN())
    sadd(stdscr, y + 1, x, "  ¿la báscula dice eso?  ", ALERTA())
    pie(stdscr, ("Enter", "sí, despachar"), ("Esc", "corregir"))
    stdscr.refresh()
    if not _esperar_confirmacion(stdscr):
        raise _Cancelado()


def flujo_entregar_pedido_mayoreo(stdscr) -> str:
    """
    Despacha un pedido pendiente: kilos reales sobre la báscula, cobro al
    precio pactado en el pedido y cierre de su ciclo de vida.

    El stock se descuenta aquí y no al capturar el pedido: la cantidad se
    negocia físicamente frente al cliente, y hasta este momento no hay kilos
    reales que descontar.
    """
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    pedidos = get_pedidos_mayoreo_pendientes()

    if not pedidos:
        sadd(stdscr, h // 2, w // 2 - 20,
             "  no hay pedidos pendientes de entrega  ", AVISO())
        stdscr.refresh()
        stdscr.getch()
        return ""

    encabezado(stdscr, "entregar pedido", "mayoreo")
    marcador = {"muy_alta": "●●●", "alta": "●●", "media": "●", "baja": "·"}

    visibles = pedidos[:9]
    for i, p in enumerate(visibles):
        total = p["kg"] * p["precio_kg_pactado"]
        sadd(stdscr, 3 + i, 2, f"[{i+1}]", ACENTO() | curses.A_BOLD)
        sadd(stdscr, 3 + i, 6, f"{marcador.get(p['prioridad'], ''):<3}", AVISO())
        sadd(stdscr, 3 + i, 10, f"{p['cliente_nombre']:<18}", TEXTO())
        sadd(stdscr, 3 + i, 29, f"{p['kg']:>6.1f}kg", DATO())
        sadd(stdscr, 3 + i, 39, f"${p['precio_kg_pactado']:>6.0f}/kg", DATO())
        sadd(stdscr, 3 + i, 52, f"${total:>9,.0f}", OK())
        sadd(stdscr, 3 + i, 64, f"prometido {p['fecha_entrega']}", CHROME())

    pie(stdscr, ("1-9", "elegir pedido"), ("0/Esc", "cancelar"))
    stdscr.refresh()

    try:
        idx = _tecla_a_idx(stdscr.getch())
        if not (0 <= idx < len(visibles)):
            raise _Cancelado()
        pedido = visibles[idx]

        my = 3 + len(visibles) + 1
        kg_real = _pedir_kg_reales(stdscr, pedido["kg"], my, 2)
        _confirmar_diferencia_kg(stdscr, pedido["kg"], kg_real, my + 3, 2)

        # El precio es el que se pactó en el pedido, no el del día: el
        # cliente cerró un trato y el tabulador pudo moverse desde entonces.
        total = kg_real * pedido["precio_kg_pactado"]

        stdscr.erase()
        encabezado(stdscr, "confirmar entrega", "mayoreo")
        cy = 3
        sadd(stdscr, cy,     2, f"cliente      {pedido['cliente_nombre']}", TEXTO())
        sadd(stdscr, cy + 1, 2, f"pactado      {pedido['kg']:.1f}kg "
                                f"a ${pedido['precio_kg_pactado']:,.2f}/kg", TEXTO())
        sadd(stdscr, cy + 2, 2, f"despacha     {kg_real:.1f}kg", DATO() | curses.A_BOLD)
        divisor(stdscr, cy + 3, 2, 46, pesado=False)
        sadd(stdscr, cy + 4, 2, "TOTAL A COBRAR", TITULO())
        sadd(stdscr, cy + 4, 20, f"${total:,.2f}", OK() | curses.A_BOLD)

        pie(stdscr, ("Enter", "cobrar y entregar"), ("Esc", "cancelar"))
        stdscr.refresh()
        if not _esperar_confirmacion(stdscr):
            raise _Cancelado()

    except _Cancelado:
        return ""

    # TODO(Luis) — pos.py:544. entregar_pedido_mayoreo() todavía tiene la
    # firma vieja (pedido_id) y solo prende el flag: no cobra, no descuenta
    # stock y llama a marcar_pedido_entregado() con un solo argumento, que
    # ya no existe. La reescritura es capa de lógica (§4.B.3) y va en este
    # orden estricto:
    #     1. leer el pedido y verificar que entregado == 0
    #     2. validar kg_real <= stock CHI disponible
    #     3. armar y cobrar el ticket a precio_kg_pactado
    #     4. SOLO si el cobro tuvo éxito → marcar_pedido_entregado(
    #            pedido_id, kg_real, ticket_id, fecha_entrega_real)
    # Marcar antes de cobrar produce pedidos fantasma: entregados en el
    # registro, invisibles en la caja.
    # Esta pantalla ya la llama con la firma final. Mientras no exista, el
    # TypeError se atrapa abajo y el pedido se queda pendiente — que es
    # exactamente lo que debe pasar si el cobro no ocurrió.
    try:
        r = entregar_pedido_mayoreo(pedido["id"], kg_real)
    except TypeError:
        return ("! despacho no disponible — falta reescribir "
                "entregar_pedido_mayoreo (pos.py:544)")
    except ErrorPOS as e:
        return f"! {e}"

    ticket_id = r.get("ticket_id") if isinstance(r, dict) else None
    if ticket_id:
        return (f"pedido #{pedido['id']} entregado — {kg_real:.1f}kg — "
                f"${total:,.2f} — ticket #{ticket_id}")
    return r.get("mensaje", f"pedido #{pedido['id']} entregado")


def flujo_ajustar_pedido_mayoreo(stdscr) -> str:
    """
    Renegocia los kg de un pedido antes de despacharlo. Conecta
    ajustar_pedido_mayoreo (pos.py:535), que existía sin nadie que la llamara.
    """
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    pedidos = get_pedidos_mayoreo_pendientes()

    if not pedidos:
        sadd(stdscr, h // 2, w // 2 - 20,
             "  no hay pedidos pendientes que ajustar  ", AVISO())
        stdscr.refresh()
        stdscr.getch()
        return ""

    encabezado(stdscr, "ajustar pedido", "mayoreo")
    visibles = pedidos[:9]
    for i, p in enumerate(visibles):
        sadd(stdscr, 3 + i, 2, f"[{i+1}]", ACENTO() | curses.A_BOLD)
        sadd(stdscr, 3 + i, 6, f"{p['cliente_nombre']:<18}", TEXTO())
        sadd(stdscr, 3 + i, 25, f"{p['kg']:>6.1f}kg", DATO())
        sadd(stdscr, 3 + i, 35, f"${p['precio_kg_pactado']:>6.0f}/kg", DATO())
        sadd(stdscr, 3 + i, 48, f"prometido {p['fecha_entrega']}", CHROME())

    pie(stdscr, ("1-9", "elegir pedido"), ("0/Esc", "cancelar"))
    stdscr.refresh()

    try:
        idx = _tecla_a_idx(stdscr.getch())
        if not (0 <= idx < len(visibles)):
            raise _Cancelado()
        pedido = visibles[idx]

        my = 3 + len(visibles) + 1
        sadd(stdscr, my, 2, f"kg actuales: {pedido['kg']:.1f}", DATO())
        nuevo_kg = pedir_float_modal(stdscr, "kg nuevos:   ", my + 1, 2)
        if nuevo_kg <= 0:
            raise _Cancelado()
    except _Cancelado:
        return ""

    try:
        r = ajustar_pedido_mayoreo(pedido["id"], nuevo_kg)
        return r["mensaje"]
    except ErrorPOS as e:
        return f"! {e}"


# ── PANTALLA CORTE ────────────────────────────────────────────────────────────

def pantalla_corte(stdscr, sesion: SesionPOS) -> str:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    encabezado(stdscr, "corte de caja", f"batch#{sesion.batch_id}")

    try:
        corte = hacer_corte(sesion)
    except ErrorPOS as e:
        sadd(stdscr, 3, 2, f"! {e}", ALERTA())
        sadd(stdscr, 5, 2, "[Esc] volver", TEXTO())
        stdscr.refresh()
        stdscr.getch()
        return ""

    y = 2
    def fila(label, valor, color=None):
        nonlocal y
        sadd(stdscr, y, 4, f"{label:<28}", TEXTO())
        sadd(stdscr, y, 32, valor, color or TEXTO())
        y += 1

    fila("tickets del turno",   f"{corte['n_tickets']}")
    y += 1
    fila("ventas efectivo",     f"${corte['ventas_efectivo']:,.2f}", OK())
    fila("ventas transferencia", f"${corte['ventas_transfer']:,.2f}", OK())
    fila("ventas tarjeta",      f"${corte['ventas_tarjeta']:,.2f}",  OK())
    hline(stdscr, y, 4, 40); y += 1
    fila("TOTAL VENTAS",        f"${corte['total_ventas']:,.2f}", OK() | curses.A_BOLD)
    fila("gastos del turno",    f"${corte['total_gastos']:,.2f}", AVISO())
    hline(stdscr, y, 4, 40); y += 1
    fila("NETO",                f"${corte['neto']:,.2f}", ACENTO() | curses.A_BOLD)
    y += 1
    fila("fondo de caja",       f"${corte['fondo_caja']:,.2f}", TEXTO())
    fila("A ENTREGAR",          f"${corte['a_entregar']:,.2f}",
         OK() | curses.A_BOLD if corte['a_entregar'] > 0 else TEXTO())

    pie(stdscr, ("Enter", "confirmar"), ("Esc", "cancelar"))
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (10, 13):
            return f"corte #{corte['corte_id']} guardado — entregar ${corte['a_entregar']:.2f}"
        elif key == 27:
            return ""

# ── TICKER DE PEDIDOS DE MAYOREO ──────────────────────────────────────────────

def _texto_ticker_mayoreo() -> str:
    """Arma el texto completo del ticker a partir de pedidos pendientes,
    ordenados por prioridad (ya vienen así desde get_pedidos_mayoreo_pendientes)."""
    pedidos = get_pedidos_mayoreo_pendientes()
    if not pedidos:
        # cadena vacía = no hay nada que desplazar. La cinta pone un aviso
        # fijo en vez de repetir la misma frase dando vueltas.
        return ""

    marcador = {"muy_alta": "●●●", "alta": "●●", "media": "●", "baja": "·"}
    partes = []
    for p in pedidos:
        total = p["kg"] * p["precio_kg_pactado"]
        m = marcador.get(p["prioridad"], "")
        partes.append(f"{m} {p['cliente_nombre']} — {p['kg']:.1f}kg — ${total:,.0f}")
    return "   »   ".join(partes) + "   »   "

# ── CINTA DE MAYOREO ─────────────────────────────────────────────────────────

ETIQUETA_TICKER = " MAYOREO "


def draw_ticker(stdscr, ticker_offset: int = 0):
    """
    Cinta superior con los pedidos de mayoreo pendientes, desplazándose a lo
    ancho de la pantalla.

    Antes vivía en el hueco que sobraba a la derecha de la barra de estado, y
    ese hueco se lo comían las cifras del turno: con un batch cargado el
    ticker quedaba en 6 u 8 columnas, ilegible. Acá arriba tiene el ancho
    completo y es lo primero que se ve al abrir el POS, que es lo que
    corresponde a una cola de pedidos por surtir.
    """
    w = stdscr.getmaxyx()[1]
    sadd(stdscr, 0, 0, " " * (w - 1), TAB())
    sadd(stdscr, 0, 0, ETIQUETA_TICKER, WARN())

    x = len(ETIQUETA_TICKER) + 1
    ancho = w - x - 1
    if ancho <= 0:
        return

    texto = _texto_ticker_mayoreo()
    if not texto:
        sadd(stdscr, 0, x, "cola despejada — sin pedidos pendientes", TAB())
        return
    # el texto se repite para que el scroll no muestre cortes al dar la vuelta
    doble  = texto * (ancho // len(texto) + 3)
    offset = ticker_offset % len(texto)
    sadd(stdscr, 0, x, doble[offset: offset + ancho], TAB())


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

    sadd(stdscr, h - 1, 0, " " * (w - 1), TAB())
    sadd(stdscr, h - 1, 0, status[:w - 1], TAB())

    # renglón de mensajes — propio, arriba de la barra de estado.
    # Los mensajes de error del POS vienen prefijados con "!" desde
    # _procesar_item(), así que el color se deduce de ahí.
    sadd(stdscr, h - 2, 0, " " * (w - 1))
    if msg:
        color = ALERTA() | curses.A_BOLD if msg.lstrip().startswith("!") else OK()
        sadd(stdscr, h - 2, 2, f" {msg[:w - 6]} ", color)

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
            # kg o monto $ — el que se llene calcula el otro (CHI)
            cfg = cargar_config()
            kg, monto = pedir_kg_o_monto_modal(stdscr, nombre, cfg.precio_chi_pub, h // 2, w // 2 - 14)
            if kg <= 0:
                return ""
            agregar_chicharron(sesion, kg)
            return f"{nombre} {kg:.3f}kg (${monto:,.2f}) agregado"

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

def main(pantalla, batch_id: str, operador: str = "Luis"):
    """
    'pantalla' es la terminal real; 'stdscr' de aquí en adelante es el lienzo
    de tamaño fijo centrado en ella. Todo el dibujo va contra el lienzo, así
    que el layout mide siempre lo mismo sin importar el tamaño de la ventana.
    """
    curses.curs_set(0)
    init_colors()
    pantalla.keypad(True)
    if not verificar_tamano(pantalla):
        return

    dims_term = pantalla.getmaxyx()
    stdscr    = abrir_lienzo(pantalla)
    stdscr.keypad(True)
    stdscr.timeout(150) # getch() regresa -1 si no hay tecla en 150ms - permite animar el ticker

    sesion = iniciar_pos(batch_id=batch_id, operador=operador)
    msg    = f"terminal en línea · batch#{batch_id}"
    ticker_offset = 0
    while True:
        # si el usuario redimensiona la terminal, el lienzo se recentra
        if pantalla.getmaxyx() != dims_term:
            dims_term = pantalla.getmaxyx()
            stdscr    = abrir_lienzo(pantalla)
            stdscr.keypad(True)
            stdscr.timeout(150)

        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # leer menú dinámico desde DB en cada frame
        menu = list(get_menu_pos())

        # layout vertical, de arriba a abajo:
        #   fila 0        cinta de mayoreo
        #   1 .. h-3      paneles
        #   h-2           renglón de mensajes
        #   h-1           barra de estado
        Y_PANELES    = 1
        alto_paneles = h - 3

        # los dos paneles comparten la columna del medio: el panel derecho
        # arranca una columna antes y su borde izquierdo se dibuja encima del
        # derecho del izquierdo, para que quede un solo trazo vertical
        ancho_izq = min(ANCHO_PANEL_IZQ, w // 2)
        x_union   = ancho_izq - 1
        win_izq   = stdscr.derwin(alto_paneles, ancho_izq,   Y_PANELES, 0)
        win_der   = stdscr.derwin(alto_paneles, w - x_union, Y_PANELES, x_union)

        # inventario para stock
        inv_raw  = get_estado_inventario()
        inv_dict = {r["sku"]: {"stock": r["stock"], "precio": r["precio_venta"]}
                    for r in inv_raw}

        filas_div = draw_panel_izq(win_izq, sesion, inv_dict, menu)
        draw_panel_ticket(win_der, sesion)

        # remates de la columna compartida: sin esto quedan dos esquinas
        # encimadas arriba y abajo, y las divisiones del panel izquierdo
        # terminan contra el borde del derecho en vez de empalmar con él.
        # Las filas vienen en coordenadas del panel, así que llevan el offset.
        sadd(stdscr, Y_PANELES,                    x_union, CAJA["T_ABAJO"],  ACENTO())
        sadd(stdscr, Y_PANELES + alto_paneles - 1, x_union, CAJA["T_ARRIBA"], ACENTO())
        for fila in filas_div:
            sadd(stdscr, Y_PANELES + fila, x_union, CAJA["T_IZQ"], CHROME())

        draw_ticker(stdscr, ticker_offset)
        draw_statusbar(stdscr, sesion, msg)
        ticker_offset += 1
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
                resultado = _modal_bloqueante(stdscr, flujo_cobro, sesion)
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
            sadd(stdscr, h // 2 - 3, w // 2 - 14, "REGISTRAR GASTO", TITULO())
            sadd(stdscr, h // 2 - 2, w // 2 - 14, "[1]Gas [2]Leche [3]General", ACENTO())
            stdscr.refresh()
            stdscr.timeout(-1)
            try:
                tipo_key = stdscr.getch()
            finally:
                stdscr.timeout(150)
            tipo = {ord("1"): "gas", ord("2"): "leche", ord("3"): "gral"}.get(tipo_key, "gral")
            monto = pedir_float_modal(stdscr, "monto $: ",      h // 2,     w // 2 - 14)
            desc  = pedir_input(stdscr,       "descripción: ",  h // 2 + 1, w // 2 - 14, 25)
            if monto > 0:
                try:
                    registrar_gasto(sesion.batch_id, tipo, monto, desc)
                    msg = f"gasto {tipo} ${monto:.0f} registrado"
                except ErrorPOS as e:
                    msg = f"! {e}"

        # ── ABRIR CUBETA / LITREAR GRANEL ─────────────────────────────
        # [A] rompe una cubeta sellada; [L] envasa de la que ya está abierta
        elif key in (ord("a"), ord("A"), ord("l"), ord("L")):
            desde_cubeta = key in (ord("a"), ord("A"))
            pool  = get_pool_lt()
            rotulo = "ABRIR CUBETA" if desde_cubeta else f"LITREAR GRANEL ({pool:.2f} lt)"
            sadd(stdscr, h // 2 - 1, w // 2 - 16, rotulo, TITULO())
            e1  = pedir_int_modal(stdscr, "envases 1lt a cargar: ",  h // 2,     w // 2 - 16)
            e05 = pedir_int_modal(stdscr, "envases ½lt a cargar: ",  h // 2 + 1, w // 2 - 16)
            if e1 > 0 or e05 > 0:
                try:
                    r   = (abrir_cubeta_pos(e1, e05) if desde_cubeta
                           else litrear_pos(e1, e05))
                    msg = r["mensaje"]
                except ErrorPOS as e:
                    msg = f"! {e}"

        # ── INVENTARIO ────────────────────────────────────────────────
        elif key in (ord("i"), ord("I")):
            pantalla_inventario(stdscr)

        # ── HISTORIAL ─────────────────────────────────────────────────
        elif key in (ord("h"), ord("H")):
            pantalla_historial(stdscr)

        # ── MAYOREO ───────────────────────────────────────────────────
        elif key in (ord("m"), ord("M")):
            def _submenu_mayoreo(stdscr):
                h2, w2 = stdscr.getmaxyx()
                stdscr.erase()
                sadd(stdscr, h2 // 2 - 3, w2 // 2 - 16, "MENÚ MAYOREO", TITULO())
                sadd(stdscr, h2 // 2 - 1, w2 // 2 - 16, "[1] nuevo cliente", ACENTO())
                sadd(stdscr, h2 // 2,     w2 // 2 - 16, "[2] capturar pedido", ACENTO())
                sadd(stdscr, h2 // 2 + 1, w2 // 2 - 16, "[3] status de pedidos", ACENTO())
                sadd(stdscr, h2 // 2 + 2, w2 // 2 - 16, "[4] entregar pedido", ACENTO())
                sadd(stdscr, h2 // 2 + 3, w2 // 2 - 16, "[5] ajustar pedido", ACENTO())
                sadd(stdscr, h2 // 2 + 4, w2 // 2 - 16, "[Esc] volver", TEXTO())
                stdscr.refresh()
                sub_key = stdscr.getch()
                if sub_key == ord("1"):
                    return flujo_alta_cliente_mayoreo(stdscr)
                elif sub_key == ord("2"):
                    return flujo_capturar_pedido_mayoreo(stdscr)
                elif sub_key == ord("3"):
                    pantalla_status_pedidos(stdscr)
                elif sub_key == ord("4"):
                    return flujo_entregar_pedido_mayoreo(stdscr)
                elif sub_key == ord("5"):
                    return flujo_ajustar_pedido_mayoreo(stdscr)
                return ""
            resultado = _modal_bloqueante(stdscr, _submenu_mayoreo)
            if resultado:
                msg = resultado

        # ── CORTE ─────────────────────────────────────────────────────
        elif key in (ord("c"), ord("C")):
            resultado = pantalla_corte(stdscr, sesion)
            if resultado:
                msg = resultado

        # ── SALIR ─────────────────────────────────────────────────────
        elif key in (ord("q"), ord("Q")):
            if sesion.ticket and not sesion.ticket.vacio():
                msg = "! ticket abierto — ciérralo antes de desacoplar"
            else:
                break

def iniciar_pos_tui(batch_id: str, operador: str = "Luis"):
    set_titulo_terminal(f"bayoSys · POS — batch#{batch_id}")
    try:
        curses.wrapper(main, batch_id, operador)
    finally:
        set_titulo_terminal()

if __name__ == "__main__":
    iniciar_pos_tui(batch_id="test")
