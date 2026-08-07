"""
estilos.py — bayoSys · Productos El Bayo
Capa visual compartida. Paleta, bordes y helpers de dibujo.

Este módulo NO contiene lógica de negocio ni toca la base de datos.
Es la única fuente de verdad para colores, caracteres de borde y
formato de texto — tanto para los módulos de curses (pos_tui, tui)
como para los de texto plano (main, registro, cierre, analisis,
guardian, nuke).

DECISIONES DE DISEÑO
  - Cero dependencias externas. Solo stdlib: curses, os, sys, shutil,
    locale. El proyecto no tiene requirements.txt y corre en 3 máquinas
    distintas — meter rich/colorama significaría inventar un canal de
    instalación que hoy no existe.
  - Degradación automática. La paleta base son los 8 colores que
    garantiza cualquier terminal; si la terminal reporta 256 colores,
    se sustituyen por tonos más finos. Nada depende de true color.
  - Fallback ASCII. Si la terminal no maneja UTF-8 (TTY del kernel sin
    X, por ejemplo), los bordes Unicode se reemplazan por +-| en vez de
    imprimir basura.
  - Fondo por defecto. Se usa use_default_colors() para respetar el
    fondo del emulador en vez de forzar negro — así el tema no pelea
    con la transparencia ni con el esquema del usuario.

Para verificar cómo se ve en una terminal concreta:
    python3 estilos.py
"""

import os
import sys
import locale
import shutil

try:
    import curses
except ImportError:      # entornos sin curses — los módulos de texto plano igual funcionan
    curses = None


# ── DIMENSIONES DEL LIENZO ───────────────────────────────────────────────────
# El lienzo es la superficie de dibujo de tamaño fijo, centrada en la terminal.
# Las pantallas de curses dibujan contra estas medidas, no contra el tamaño
# real de la terminal — así el layout se ve igual en cualquier ventana.

ANCHO_LIENZO = 100
ALTO_LIENZO  = 30

# Mínimo por debajo del cual el layout se corta de forma visible.
MIN_ANCHO = 80
MIN_ALTO  = 24

TITULO_VENTANA = "bayoSys · Productos El Bayo"


# ── DETECCIÓN DE CAPACIDADES ─────────────────────────────────────────────────

N_COLORES = 8          # se actualiza en init_colors() con lo que reporte curses


# curses codifica lo que se dibuja usando el locale del proceso. Sin este
# setlocale, Python arranca en locale "C" y los caracteres de caja pueden
# salir mal aunque la terminal sí sea UTF-8. Se hace acá, al importar el
# módulo de estilos, porque es el que decide qué caracteres se dibujan.
try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    pass    # locale no instalado en el sistema — se sigue con el default


def _soporta_unicode() -> bool:
    """True si la terminal puede dibujar caracteres de caja Unicode."""
    enc = (locale.getpreferredencoding(False) or "").lower()
    if "utf" in enc:
        return True
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


UNICODE = _soporta_unicode()


def _ansi_activo() -> bool:
    """True si conviene emitir escapes ANSI en los módulos de texto plano."""
    if os.environ.get("NO_COLOR"):          # convención no-color.org
        return False
    if not sys.stdout.isatty():             # redirigido a archivo o pipe
        return False
    return os.environ.get("TERM", "") not in ("", "dumb")


def _ansi_256() -> bool:
    term = os.environ.get("TERM", "")
    return "256color" in term or os.environ.get("COLORTERM", "") in ("truecolor", "24bit")


# ── PALETA ───────────────────────────────────────────────────────────────────
# Índices de pares de color. Los nombres son semánticos, no cromáticos:
# lo que cambia entre 8 y 256 colores es el tono, nunca el significado.

P_TEXTO      = 1    # texto normal, datos
P_ACENTO     = 2    # estructura viva: teclas, títulos de panel, bordes activos
P_OK         = 3    # nominal — márgenes sanos, dinero a favor, confirmaciones
P_AVISO      = 4    # precaución — stock bajo, margen apretado
P_ALERTA     = 5    # crítico — errores, márgenes negativos
P_INV_ACENTO = 6    # barra de estado / pestaña activa
P_INV_OK     = 7    # botón confirmado
P_TITULO     = 8    # encabezado de sección
P_INV_AVISO  = 9    # franja de advertencia
P_CHROME     = 10   # estructura apagada: bordes secundarios, separadores
P_DATO       = 11   # valores numéricos que deben leerse rápido

# (frente, fondo) — None en el fondo = fondo por defecto de la terminal
_PALETA_8 = {
    P_TEXTO:      ("WHITE",   None),
    P_ACENTO:     ("CYAN",    None),
    P_OK:         ("GREEN",   None),
    P_AVISO:      ("YELLOW",  None),
    P_ALERTA:     ("RED",     None),
    P_INV_ACENTO: ("BLACK",   "CYAN"),
    P_INV_OK:     ("BLACK",   "GREEN"),
    P_TITULO:     ("MAGENTA", None),
    P_INV_AVISO:  ("BLACK",   "YELLOW"),
    P_CHROME:     ("WHITE",   None),   # se apaga con A_DIM, ver atributo CHROME()
    P_DATO:       ("CYAN",    None),
}

# Tonos de 256 colores. Cian holográfico + ámbar sobre grises fríos —
# la referencia es una consola de nave, no un semáforo de tres luces.
_PALETA_256 = {
    P_TEXTO:      (252, None),   # gris claro, menos agresivo que blanco puro
    P_ACENTO:     (51,  None),   # cian brillante
    P_OK:         (48,  None),   # verde
    P_AVISO:      (214, None),   # ámbar
    P_ALERTA:     (196, None),   # rojo
    P_INV_ACENTO: (16,  45),     # negro sobre cian
    P_INV_OK:     (16,  48),     # negro sobre verde
    P_TITULO:     (81,  None),   # cian claro
    P_INV_AVISO:  (16,  214),    # negro sobre ámbar
    P_CHROME:     (240, None),   # gris oscuro — estructura sin protagonismo
    P_DATO:       (45,  None),   # cian medio
}


def init_colors():
    """
    Inicializa los pares de color. Debe llamarse una sola vez, después de
    entrar a curses y antes de dibujar cualquier cosa.
    Detecta cuántos colores soporta la terminal y elige la paleta sola.
    """
    global N_COLORES
    curses.start_color()
    try:
        curses.use_default_colors()
        fondo_def = -1
    except curses.error:
        fondo_def = curses.COLOR_BLACK

    N_COLORES = getattr(curses, "COLORS", 8) or 8
    paleta = _PALETA_256 if N_COLORES >= 256 else _PALETA_8

    for idx, (fg, bg) in paleta.items():
        if isinstance(fg, str):
            fg = getattr(curses, f"COLOR_{fg}")
        if isinstance(bg, str):
            bg = getattr(curses, f"COLOR_{bg}")
        try:
            curses.init_pair(idx, fg, fondo_def if bg is None else bg)
        except curses.error:
            # terminal sin ese índice de color — se queda con el par por defecto
            pass


# ── ATRIBUTOS ────────────────────────────────────────────────────────────────
# Funciones, no constantes: el par de color no existe hasta que corre
# init_colors(), así que resolverlo en tiempo de dibujo es lo correcto.

def TEXTO():   return curses.color_pair(P_TEXTO)
def ACENTO():  return curses.color_pair(P_ACENTO)
def OK():      return curses.color_pair(P_OK)
def AVISO():   return curses.color_pair(P_AVISO)
def ALERTA():  return curses.color_pair(P_ALERTA)
def TITULO():  return curses.color_pair(P_TITULO)  | curses.A_BOLD
def DATO():    return curses.color_pair(P_DATO)
def TAB():     return curses.color_pair(P_INV_ACENTO) | curses.A_BOLD
def BOTON():   return curses.color_pair(P_INV_OK)     | curses.A_BOLD
def WARN():    return curses.color_pair(P_INV_AVISO)  | curses.A_BOLD

def CHROME():
    """
    Estructura secundaria. Con 256 colores es un gris real; con 8 colores
    no hay gris disponible, así que se simula apagando el blanco con A_DIM.
    """
    attr = curses.color_pair(P_CHROME)
    return attr if N_COLORES >= 256 else attr | curses.A_DIM


def color_margen(pct: float):
    """Verde/ámbar/rojo según qué tan sano es un margen porcentual."""
    if pct < 15:
        return ALERTA()
    if pct < 30:
        return AVISO()
    return OK()


def color_utilidad(val: float):
    """Verde/ámbar/rojo según el valor absoluto de una utilidad en pesos."""
    if val < 0:
        return ALERTA()
    if val < 1000:
        return AVISO()
    return OK()


# ── BORDES ───────────────────────────────────────────────────────────────────
# Dos juegos: trazo fino para divisiones internas, trazo grueso para el marco
# exterior de un panel. La jerarquía de grosor es lo que da la lectura de
# "consola" — todo del mismo peso se ve plano.

_CAJA_UNICODE = {
    "h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "t": "┬", "b": "┴", "l": "├", "r": "┤", "x": "┼",
    "H": "━", "V": "┃", "TL": "┏", "TR": "┓", "BL": "┗", "BR": "┛",
    "T_ABAJO": "┳", "T_ARRIBA": "┻", "T_IZQ": "┨",
    "l_pesado": "┠", "r_pesado": "┨",
    "lleno": "█", "vacio": "░", "bar_i": "[", "bar_d": "]",
    "cap_i": "╸", "cap_d": "╺",
}

_CAJA_ASCII = {
    "h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "t": "+", "b": "+", "l": "+", "r": "+", "x": "+",
    "H": "=", "V": "|", "TL": "+", "TR": "+", "BL": "+", "BR": "+",
    "T_ABAJO": "+", "T_ARRIBA": "+", "T_IZQ": "+",
    "l_pesado": "+", "r_pesado": "+",
    "lleno": "#", "vacio": ".", "bar_i": "[", "bar_d": "]",
    "cap_i": " ", "cap_d": " ",
}

CAJA = _CAJA_UNICODE if UNICODE else _CAJA_ASCII


# ── HELPERS DE DIBUJO (curses) ───────────────────────────────────────────────

def sadd(win, y, x, text, attr=0):
    """
    addstr() que nunca revienta: recorta al ancho de la ventana e ignora
    escrituras fuera de rango. Todo dibujo del proyecto pasa por aquí.
    """
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


def _addch_final(win, y, ch, attr=0):
    """
    Escribe en la última columna de la ventana.
    addstr() falla ahí porque intenta avanzar el cursor fuera de la ventana;
    insstr() no mueve el cursor, así que es la forma de cerrar el borde
    derecho sin perder la esquina.
    """
    w = win.getmaxyx()[1]
    try:
        win.insstr(y, w - 1, ch, attr)
    except curses.error:
        pass


def hline(win, y, x, ancho, char=None, attr=None):
    """Línea horizontal de trazo fino — divisiones internas de un panel."""
    if char is None:
        char = CAJA["h"]
    if attr is None:
        attr = CHROME()
    sadd(win, y, x, char * min(ancho, win.getmaxyx()[1] - x - 1), attr)


def marco(win, titulo="", attr=None, attr_titulo=None):
    """
    Marco exterior de un panel, con el título embebido en la línea superior:

        ┏━╸ PRODUCTOS ╺━━━━━━━━━━━━━━━━┓
        ┃                              ┃
        ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

    Se dibuja a mano en vez de usar win.border() porque border() no permite
    insertar el título en el trazo ni mezclar grosores.
    """
    h, w = win.getmaxyx()
    if h < 2 or w < 4:
        return
    if attr is None:
        attr = CHROME()
    if attr_titulo is None:
        attr_titulo = ACENTO() | curses.A_BOLD

    ancho_interno = w - 2

    sadd(win, 0, 0, CAJA["TL"] + CAJA["H"] * ancho_interno, attr)
    _addch_final(win, 0, CAJA["TR"], attr)
    for y in range(1, h - 1):
        sadd(win, y, 0, CAJA["V"], attr)
        _addch_final(win, y, CAJA["V"], attr)
    sadd(win, h - 1, 0, CAJA["BL"] + CAJA["H"] * ancho_interno, attr)
    _addch_final(win, h - 1, CAJA["BR"], attr)

    if titulo:
        etiqueta = f" {titulo.upper()} "
        if len(etiqueta) + 6 <= w:
            sadd(win, 0, 1, CAJA["H"] + CAJA["cap_i"], attr)
            sadd(win, 0, 3, etiqueta, attr_titulo)
            sadd(win, 0, 3 + len(etiqueta), CAJA["cap_d"], attr)


def caja(win, y, x, alto, ancho, titulo="", attr=None, attr_titulo=None):
    """
    Marco de trazo fino dibujado en coordenadas arbitrarias — para modales
    que flotan sobre una pantalla ya dibujada, donde no hay una subventana
    propia sobre la cual llamar marco().
    """
    if attr is None:
        attr = ACENTO()
    if attr_titulo is None:
        attr_titulo = ACENTO() | curses.A_BOLD

    w = win.getmaxyx()[1]
    interno = ancho - 2
    borde_der = x + ancho - 1

    def _der(fila, ch):
        # si el borde derecho cae en la última columna, addstr no alcanza
        if borde_der >= w - 1:
            _addch_final(win, fila, ch, attr)
        else:
            sadd(win, fila, borde_der, ch, attr)

    sadd(win, y, x, CAJA["tl"] + CAJA["h"] * interno, attr)
    _der(y, CAJA["tr"])
    for i in range(1, alto - 1):
        sadd(win, y + i, x,     CAJA["v"], attr)
        sadd(win, y + i, x + 1, " " * interno)
        _der(y + i, CAJA["v"])
    sadd(win, y + alto - 1, x, CAJA["bl"] + CAJA["h"] * interno, attr)
    _der(y + alto - 1, CAJA["br"])

    if titulo:
        etiqueta = f" {titulo.upper()} "
        if len(etiqueta) + 4 <= ancho:
            sadd(win, y, x + 2, etiqueta, attr_titulo)


def divisor(win, y, x, ancho, attr=None, pesado=True):
    """
    División interna de trazo fino con remates que empalman con el borde.

    `pesado` dice contra qué borde empalman los extremos: los paneles llevan
    marco grueso (┠ ┨) y los modales marco fino (├ ┤). Usar el remate
    equivocado deja una muesca visible en la unión.
    """
    if attr is None:
        attr = CHROME()
    izq = CAJA["l_pesado"] if pesado else CAJA["l"]
    der = CAJA["r_pesado"] if pesado else CAJA["r"]
    w = win.getmaxyx()[1]
    sadd(win, y, x, izq + CAJA["h"] * max(0, ancho - 2), attr)
    if x + ancho - 1 >= w - 1:
        _addch_final(win, y, der, attr)
    else:
        sadd(win, y, x + ancho - 1, der, attr)


def encabezado(win, titulo, derecha="", attr_derecha=None):
    """
    Barra de título de una pantalla completa: nombre a la izquierda, dato de
    contexto a la derecha, trazo grueso debajo. Las pantallas que ocupan
    todo el lienzo usan esto en vez de marco() — no llevan borde lateral,
    así que la jerarquía la da la barra.

    El hueco de la derecha sirve para el dato que debe estar siempre a la
    vista (el batch activo, la utilidad del día); `attr_derecha` permite
    darle color propio cuando ese dato tiene semáforo.
    """
    w = win.getmaxyx()[1]
    sadd(win, 0, 0, " " * (w - 1))
    sadd(win, 0, 2, titulo.upper(), TITULO())
    if derecha:
        sadd(win, 0, max(0, w - len(derecha) - 2), derecha,
             CHROME() if attr_derecha is None else attr_derecha)
    sadd(win, 1, 0, CAJA["H"] * (w - 1), CHROME())


def pie(win, *teclas, fila=None):
    """
    Barra inferior de teclas disponibles. Cada elemento es (tecla, descripción);
    la tecla se resalta y la descripción va apagada, para que el ojo encuentre
    la letra sin leer la frase completa.
    """
    h, w = win.getmaxyx()
    y = (h - 2) if fila is None else fila
    sadd(win, y - 1, 0, CAJA["H"] * (w - 1), CHROME())
    sadd(win, y, 0, " " * (w - 1))
    x = 2
    for tecla, desc in teclas:
        etiqueta = f"[{tecla}]"
        if x + len(etiqueta) + len(desc) + 3 >= w:
            break
        sadd(win, y, x, etiqueta, ACENTO() | curses.A_BOLD)
        x += len(etiqueta) + 1
        sadd(win, y, x, desc, CHROME())
        x += len(desc) + 3


# ── LIENZO DE TAMAÑO FIJO ────────────────────────────────────────────────────

def abrir_lienzo(pantalla, ancho=ANCHO_LIENZO, alto=ALTO_LIENZO):
    """
    Devuelve una ventana de tamaño fijo, centrada en la terminal.

    Todas las pantallas de curses dibujan contra esta ventana en vez de
    contra stdscr — como los helpers de dibujo leen getmaxyx() de la ventana
    que reciben, el layout se adapta al lienzo y no a la terminal real. El
    efecto es una consola de medidas constantes flotando en la ventana,
    en vez de un layout que se estira hasta perder proporción.

    Si la terminal es más chica que el lienzo, el lienzo se encoge a lo que
    haya disponible: curses no permite crear una ventana mayor a la pantalla.
    """
    h, w = pantalla.getmaxyx()
    alto_real  = max(1, min(alto,  h))
    ancho_real = max(1, min(ancho, w))
    y = max(0, (h - alto_real)  // 2)
    x = max(0, (w - ancho_real) // 2)

    pantalla.erase()
    pantalla.refresh()
    return curses.newwin(alto_real, ancho_real, y, x)


def verificar_tamano(pantalla) -> bool:
    """
    Avisa si la terminal es más chica que el mínimo recomendado.
    Retorna False solo si el usuario decide salir; Enter continúa de todos
    modos — es una recomendación, no un candado. Un operador con la ventana
    a medias prefiere una pantalla apretada a un programa que no abre.
    """
    h, w = pantalla.getmaxyx()
    if h >= MIN_ALTO and w >= MIN_ANCHO:
        return True

    pantalla.erase()
    lineas = [
        "TERMINAL DEMASIADO CHICA",
        "",
        f"tamaño actual      : {w} × {h}",
        f"tamaño recomendado : {MIN_ANCHO} × {MIN_ALTO}",
        "",
        "La pantalla se va a ver cortada.",
        "Agranda la ventana y vuelve a entrar.",
        "",
        "[Enter] continuar así    [Q] salir",
    ]
    for i, linea in enumerate(lineas):
        attr = AVISO() | curses.A_BOLD if i == 0 else TEXTO()
        sadd(pantalla, i + 1, 2, linea, attr)
    pantalla.refresh()

    while True:
        k = pantalla.getch()
        if k in (10, 13):
            return True
        if k in (ord("q"), ord("Q"), 27):
            return False


# ── TÍTULO DE LA VENTANA DE TERMINAL ─────────────────────────────────────────

def set_titulo_terminal(texto=TITULO_VENTANA):
    """
    Cambia el nombre que muestra la pestaña / barra de título del emulador.
    No cambia el tamaño ni el contenido: es la secuencia OSC 0. Los
    emuladores que no la reconocen la ignoran sin imprimir nada.
    """
    if not sys.stdout.isatty():
        return
    if os.environ.get("TERM", "") in ("", "dumb"):
        return
    try:
        sys.stdout.write(f"\033]0;{texto}\007")
        sys.stdout.flush()
    except Exception:
        pass


# ── COLOR EN TEXTO PLANO (ANSI) ──────────────────────────────────────────────
# Los módulos sin curses (main, registro, cierre, analisis, guardian, nuke)
# usan estos helpers. Si la terminal no soporta color, o la salida está
# redirigida a un archivo, todas las funciones devuelven el texto tal cual.

_ANSI    = _ansi_activo()
_ANSI256 = _ANSI and _ansi_256()

_RESET = "\033[0m"

# (código 8 colores, código 256 colores)
_CODIGOS = {
    "texto":  ("37", "38;5;252"),
    "acento": ("36", "38;5;51"),
    "dato":   ("36", "38;5;45"),
    "ok":     ("32", "38;5;48"),
    "aviso":  ("33", "38;5;214"),
    "alerta": ("31", "38;5;196"),
    "titulo": ("35", "38;5;81"),
    "chrome": ("2",  "38;5;240"),   # sin 256 colores se apaga con A_DIM (código 2)
}


def c(texto, estilo="texto", negrita=False) -> str:
    """
    Colorea una cadena para terminal. `estilo` es una clave de _CODIGOS —
    los mismos nombres semánticos que usa la capa de curses, para que las
    dos mitades del sistema hablen el mismo idioma visual.
    """
    if not _ANSI:
        return str(texto)
    par = _CODIGOS.get(estilo)
    if par is None:
        return str(texto)
    codigo = par[1] if _ANSI256 else par[0]
    if negrita:
        codigo = f"1;{codigo}"
    return f"\033[{codigo}m{texto}{_RESET}"


def esc(estilo="texto", negrita=False) -> str:
    """
    Devuelve solo el escape de apertura de un estilo (sin el reset), o ""
    si la terminal no soporta color. Es para código que arma sus cadenas a
    mano en vez de envolverlas con c() — así esas cadenas también degradan
    cuando la salida se redirige a un archivo.
    """
    if not _ANSI:
        return ""
    par = _CODIGOS.get(estilo)
    if par is None:
        return ""
    codigo = par[1] if _ANSI256 else par[0]
    return f"\033[{'1;' if negrita else ''}{codigo}m"


def esc_negrita() -> str:
    return "\033[1m" if _ANSI else ""


def reset() -> str:
    return _RESET if _ANSI else ""


def sep_txt(char=None, ancho=54, estilo="chrome") -> str:
    """Separador horizontal para los módulos de texto plano."""
    if char is None:
        char = CAJA["H"]
    return c(char * ancho, estilo)


def titulo_txt(texto, ancho=54) -> str:
    """
    Encabezado de sección para texto plano: el título embebido en el trazo,
    mismo motivo que marco() usa en los paneles de curses.

        ━╸ CORTE DE CAJA ╺━━━━━━━━━━━━━━━━━━━━━
    """
    etiqueta = f" {texto.upper()} "
    relleno  = max(0, ancho - len(etiqueta) - 3)
    return (c(CAJA["H"] + CAJA["cap_i"], "chrome")
            + c(etiqueta, "acento", negrita=True)
            + c(CAJA["cap_d"] + CAJA["H"] * relleno, "chrome"))


def ok_txt(texto) -> str:
    return c(f"  ✓ {texto}" if UNICODE else f"  OK {texto}", "ok")


def alerta_txt(texto) -> str:
    return c(f"  ! {texto}", "alerta", negrita=True)


def aviso_txt(texto) -> str:
    return c(f"  · {texto}" if UNICODE else f"  - {texto}", "aviso")


def opcion_txt(tecla, texto, estilo_tecla="acento") -> str:
    """Renglón de menú: la tecla resaltada, la descripción en texto normal."""
    return f"  {c(f'[{tecla}]', estilo_tecla, negrita=True)} {texto}"


def dato_txt(label, valor, ancho_label=20, estilo="dato") -> str:
    """Par etiqueta/valor alineado, con el valor resaltado."""
    return f"  {label:<{ancho_label}}: {c(valor, estilo)}"


# ── BANNER ───────────────────────────────────────────────────────────────────

_BANNER_BLOQUE = r"""
 ██████╗  █████╗ ██╗   ██╗ ██████╗ ███████╗██╗   ██╗███████╗
 ██╔══██╗██╔══██╗╚██╗ ██╔╝██╔═══██╗██╔════╝╚██╗ ██╔╝██╔════╝
 ██████╔╝███████║ ╚████╔╝ ██║   ██║███████╗ ╚████╔╝ ███████╗
 ██╔══██╗██╔══██║  ╚██╔╝  ██║   ██║╚════██║  ╚██╔╝  ╚════██║
 ██████╔╝██║  ██║   ██║   ╚██████╔╝███████║   ██║   ███████║
 ╚═════╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚══════╝   ╚═╝   ╚══════╝
""".strip("\n")


def banner(subtitulo="PRODUCTOS EL BAYO") -> str:
    """
    Identidad de arranque. Requiere 61 columnas — por debajo de eso, o sin
    UTF-8, cae a un encabezado de una línea en vez de imprimir un desastre.
    """
    ancho_term = shutil.get_terminal_size((80, 24)).columns
    if not UNICODE or ancho_term < 61:
        return c("  bayoSys · " + subtitulo, "acento", negrita=True)

    lineas = [c(l, "acento") for l in _BANNER_BLOQUE.split("\n")]
    pie    = c(f" {subtitulo.center(58)}", "chrome")
    return "\n".join(lineas + [pie])


# ── TAMAÑO EN TEXTO PLANO ────────────────────────────────────────────────────

def aviso_tamano_txt() -> str:
    """
    Advertencia de terminal chica para los módulos sin curses.
    Devuelve "" si el tamaño está bien — así el llamador solo imprime
    cuando hay algo que decir.
    """
    cols, filas = shutil.get_terminal_size((80, 24))
    if cols >= MIN_ANCHO and filas >= MIN_ALTO:
        return ""
    return alerta_txt(
        f"terminal {cols}×{filas} — bayoSys se ve mejor en "
        f"{MIN_ANCHO}×{MIN_ALTO} o más"
    )


# ── DEMO — verificación en terminal real ─────────────────────────────────────

def _demo():
    print(banner())
    print()
    print(titulo_txt("diagnóstico de terminal"))
    print(dato_txt("TERM",             os.environ.get("TERM", "(sin definir)")))
    print(dato_txt("COLORTERM",        os.environ.get("COLORTERM", "(sin definir)")))
    print(dato_txt("encoding",         locale.getpreferredencoding(False)))
    print(dato_txt("unicode",          "sí" if UNICODE else "no — fallback ASCII"))
    print(dato_txt("ANSI",             "sí" if _ANSI else "no"))
    print(dato_txt("ANSI 256 colores", "sí" if _ANSI256 else "no — paleta de 8"))
    cols, filas = shutil.get_terminal_size((80, 24))
    print(dato_txt("tamaño", f"{cols} × {filas}"))
    print()
    print(titulo_txt("paleta de texto plano"))
    for nombre in _CODIGOS:
        print(f"  {c(f'{nombre:<8}', nombre)}  {c('valor de ejemplo $1,234.56', nombre)}")
    print()
    print(ok_txt("confirmación"))
    print(aviso_txt("precaución"))
    print(alerta_txt("error"))
    print()
    print(titulo_txt("menú"))
    print(opcion_txt(1, "registrar batch"))
    print(opcion_txt("Q", "salir", "alerta"))
    print()
    aviso = aviso_tamano_txt()
    if aviso:
        print(aviso)
    print(sep_txt())

    if curses is None:
        return

    resp = input("\n  ¿probar la paleta de curses? [s/n]: ").strip().lower()
    if resp not in ("s", "si", "sí", "y"):
        return

    def _pantalla(pantalla):
        curses.curs_set(0)
        init_colors()
        lienzo = abrir_lienzo(pantalla)
        marco(lienzo, "diagnóstico curses")
        h, w = lienzo.getmaxyx()
        sadd(lienzo, 2, 3, f"colores reportados: {N_COLORES}", TEXTO())
        sadd(lienzo, 3, 3, f"lienzo: {w} × {h}  (terminal: "
                           f"{pantalla.getmaxyx()[1]} × {pantalla.getmaxyx()[0]})", TEXTO())
        divisor(lienzo, 5, 1, w - 2)
        muestras = [
            ("TEXTO",  TEXTO()),  ("ACENTO", ACENTO()), ("DATO",   DATO()),
            ("OK",     OK()),     ("AVISO",  AVISO()),  ("ALERTA", ALERTA()),
            ("TITULO", TITULO()), ("CHROME", CHROME()),
            ("TAB",    TAB()),    ("BOTON",  BOTON()),  ("WARN",   WARN()),
        ]
        for i, (nombre, attr) in enumerate(muestras):
            sadd(lienzo, 7 + i, 3, f" {nombre:<8} 1.250 kg   $287.50 ", attr)
        sadd(lienzo, h - 2, 3, "cualquier tecla para salir", CHROME())
        lienzo.refresh()
        lienzo.getch()

    curses.wrapper(_pantalla)


if __name__ == "__main__":
    _demo()
