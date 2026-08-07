"""
tui.py — bayoSys · Productos El Bayo
Simulador de escenarios con sliders. Modo análisis interactivo.

CAMBIOS junio 2026:
  - Eliminado slider precio_mant_inix
  - Agregados sliders precio_mant_lt1 y precio_mant_lt05
  - simular() ya no calcula canal INIX — usa litreada genérica
    con mix separado litreada/cubeta (solo para simulación, no afecta cierre)
  - Corrección: mano_obra_dia → diario_empleado (nombre correcto en Config)
"""

import curses
from dataclasses import dataclass
from typing import List

from config import cargar_config, guardar_config
from models import LT_POR_CUBETA, LT_POR_ENV_1LT, LT_POR_ENV_05LT
from estilos import (
    init_colors, sadd as safe_add, hline, encabezado, pie,
    abrir_lienzo, verificar_tamano, set_titulo_terminal, CAJA,
    TEXTO, ACENTO, OK, AVISO, ALERTA, TITULO, DATO, TAB, BOTON, CHROME,
    color_margen as margen_color, color_utilidad as util_color,
)


# ── HELPERS DE DIBUJO ────────────────────────────────────────────────────────
# La paleta y safe_add viven en estilos.py, compartidos con pos_tui.py.
# Antes este archivo tenía su propio init_colors() con 8 pares donde
# pos_tui tenía 9, y el par 7 significaba cosas distintas en cada uno.

# Anchos de la columna de sliders. Se fijan aquí en vez de derivarse del
# largo de cada etiqueta: si el ancho de barra depende de la etiqueta, cada
# barra termina en una columna distinta y los valores dejan de alinearse.
ANCHO_ETIQUETA = 22
ANCHO_VALOR    = 10
ANCHO_SLIDERS  = 52
X_RESULTADOS   = ANCHO_SLIDERS + 2


def draw_slider(win, y, x, width, val, min_v, max_v, label, fmt, unit, selected):
    bar_w   = max(8, width - ANCHO_ETIQUETA - ANCHO_VALOR - 3)
    pct     = (val - min_v) / (max_v - min_v) if max_v > min_v else 0
    fill    = max(0, min(bar_w, int(pct * bar_w)))
    val_str = fmt.format(val) + unit
    attr_l  = ACENTO() | curses.A_BOLD if selected else TEXTO()
    attr_b  = ACENTO() | curses.A_BOLD if selected else ACENTO()

    x_bar = x + ANCHO_ETIQUETA
    x_val = x_bar + bar_w + 3

    # el tramo vacío va en CHROME: la barra debe leerse por contraste de
    # brillo, no por dos colores compitiendo
    safe_add(win, y, x,                f"{label:<{ANCHO_ETIQUETA}}", attr_l)
    safe_add(win, y, x_bar,            CAJA["bar_i"],                CHROME())
    safe_add(win, y, x_bar + 1,        CAJA["lleno"] * fill,         attr_b)
    safe_add(win, y, x_bar + 1 + fill, CAJA["vacio"] * (bar_w - fill), CHROME())
    safe_add(win, y, x_bar + 1 + bar_w, CAJA["bar_d"],               CHROME())
    safe_add(win, y, x_val,            f"{val_str:>{ANCHO_VALOR}}",
             DATO() | (curses.A_BOLD if selected else 0))

ANCHO_KV      = 18   # ancho de la etiqueta en el panel de resultados
ANCHO_COLUMNA = 42   # ancho de una columna de resultados


def draw_kv(win, y, x, label, value, color=None):
    """Par etiqueta/valor. La etiqueta va apagada y el valor destacado —
    en una pantalla llena de números, el ojo debe caer en las cifras."""
    if color is None: color = DATO()
    safe_add(win, y, x,            f"{label:<{ANCHO_KV}}", CHROME())
    safe_add(win, y, x + ANCHO_KV, str(value),             color)


# ── DEFINICIÓN DE SLIDERS ────────────────────────────────────────────────────

@dataclass
class Slider:
    label:   str
    key:     str
    min_val: float
    max_val: float
    step:    float
    fmt:     str  = "{:.0f}"
    unit:    str  = ""

TABS = [
    ("Producción", [
        Slider("kg grasa / día",       "kg_grasa_sim",    40,  200, 5,   "{:.0f}", " kg"),
        Slider("costo grasa",          "costo_grasa_sim", 18,   42, 1,   "{:.0f}", " $/kg"),
        Slider("rendimiento chi%",     "rend_chi_sim",    18,   28, 0.5, "{:.1f}", "%"),
        Slider("empleado / día",       "diario_empleado", 200, 1500, 50, "{:.0f}", " $"),
        Slider("gas / día",            "gas_dia",         100,  800, 25, "{:.0f}", " $"),
    ]),
    ("Precios", [
        Slider("chi público $/kg",     "precio_chi_pub",   150, 500, 5,  "{:.0f}", " $"),
        Slider("chi mayoreo $/kg",     "precio_chi_may",   100, 350, 5,  "{:.0f}", " $"),
        Slider("manteca cubeta $",     "precio_mant_cub",  300, 800, 10, "{:.0f}", " $"),
        Slider("litreada 1lt $",       "precio_mant_lt1",   20,  80,  1, "{:.0f}", " $"),
        Slider("litreada 500ml $",     "precio_mant_lt05",  10,  50,  1, "{:.0f}", " $"),
    ]),
    ("Mix canales", [
        Slider("chi → público %",      "mix_chi_pub_pct",      0, 100, 5, "{:.0f}", "%"),
        Slider("mant → litreada %",    "mix_mant_litreada_pct", 0, 100, 5, "{:.0f}", "%"),
        Slider("litreada → 1lt %",     "mix_litreada_1lt_pct",  0, 100, 5, "{:.0f}", "%"),
        Slider("alpha (% costo→chi)",  "alpha",                40,  90, 5, "{:.0f}", "%"),
    ]),
]


# ── CÁLCULO PARA SIMULADOR ───────────────────────────────────────────────────

def simular(cfg, extras: dict) -> dict:
    """
    Simulación rápida con parámetros de los sliders.
    El mix litreada/cubeta aquí es solo para análisis hipotético.
    En producción real ese mix viene del cierre, no de Config.
    """
    from models import REND_MANT_KG, DENSIDAD_MANTECA, KG_GRASA_POR_CUBETA

    kg       = extras.get("kg_grasa_sim",        120.0)
    costo    = extras.get("costo_grasa_sim",       25.0)
    rend     = extras.get("rend_chi_sim",          22.0) / 100
    empleado = extras.get("diario_empleado",       cfg.diario_empleado)
    gas      = extras.get("gas_dia",              200.0)
    p_cpub   = extras.get("precio_chi_pub",        cfg.precio_chi_pub)
    p_cmay   = extras.get("precio_chi_may",        cfg.precio_chi_may)
    p_mcub   = extras.get("precio_mant_cub",       cfg.precio_mant_cub)
    p_lt1    = extras.get("precio_mant_lt1",       cfg.precio_mant_lt1)
    p_lt05   = extras.get("precio_mant_lt05",      cfg.precio_mant_lt05)
    mix_cp   = extras.get("mix_chi_pub_pct",       cfg.mix_chi_pub_pct) / 100
    mix_lit  = extras.get("mix_mant_litreada_pct", 30.0) / 100
    mix_1lt  = extras.get("mix_litreada_1lt_pct",  60.0) / 100
    alpha    = extras.get("alpha",                 cfg.alpha * 100) / 100
    beta     = 1.0 - alpha

    kg_chi  = kg * rend
    kg_mant = kg * REND_MANT_KG
    lt_mant = kg_mant / DENSIDAD_MANTECA
    merma   = kg - kg_chi - kg_mant

    c_grasa   = kg * costo
    c_destajo = kg * cfg.destajo_kg
    c_fijos   = empleado + cfg.leche_dia + gas
    c_total   = c_grasa + c_destajo + c_fijos

    c_chi_u = (c_total * alpha) / kg_chi  if kg_chi  > 0 else 0
    c_mnt_u = (c_total * beta)  / kg_mant if kg_mant > 0 else 0

    # chicharrón
    chi_pub = kg_chi * mix_cp
    chi_may = kg_chi * (1 - mix_cp)
    ing_cp  = chi_pub * p_cpub
    ing_cm  = chi_may * p_cmay
    ing_chi = ing_cp + ing_cm

    # manteca — litreada y cubeta
    lt_litreada = lt_mant * mix_lit
    lt_cubeta   = lt_mant * (1 - mix_lit)

    lt_1lt   = lt_litreada * mix_1lt
    lt_05lt  = lt_litreada * (1 - mix_1lt)
    env_1lt  = lt_1lt  / LT_POR_ENV_1LT
    env_05lt = lt_05lt / LT_POR_ENV_05LT

    cubetas  = lt_cubeta / LT_POR_CUBETA
    ing_lit  = env_1lt * p_lt1 + env_05lt * p_lt05
    ing_cub  = cubetas * p_mcub
    ing_mnt  = ing_lit + ing_cub

    ing_tot = ing_chi + ing_mnt
    util    = ing_tot - c_total

    mg_cp  = (p_cpub - c_chi_u) / p_cpub * 100 if p_cpub > 0 else 0
    mg_cm  = (p_cmay - c_chi_u) / p_cmay * 100 if p_cmay > 0 else 0
    p_ml   = p_mcub / LT_POR_CUBETA
    p_l1   = p_lt1  / LT_POR_ENV_1LT
    p_l05  = p_lt05 / LT_POR_ENV_05LT
    mg_ml  = (p_ml  - c_mnt_u) / p_ml  * 100 if p_ml  > 0 else 0
    mg_l1  = (p_l1  - c_mnt_u) / p_l1  * 100 if p_l1  > 0 else 0
    mg_l05 = (p_l05 - c_mnt_u) / p_l05 * 100 if p_l05 > 0 else 0

    return dict(
        kg_chi=kg_chi, kg_mant=kg_mant, lt_mant=lt_mant,
        merma_kg=merma,
        rend_chi=rend * 100, rend_mant=kg_mant / kg * 100,
        merma_pct=merma / kg * 100,
        c_grasa=c_grasa, c_total=c_total,
        c_chi_u=c_chi_u, c_mnt_u=c_mnt_u,
        ing_chi=ing_chi, ing_lit=ing_lit, ing_cub=ing_cub,
        ing_mnt=ing_mnt, ing_tot=ing_tot,
        util=util, util_mes=util * 25,
        mg_cp=mg_cp, mg_cm=mg_cm, mg_ml=mg_ml, mg_l1=mg_l1, mg_l05=mg_l05,
        env_1lt=env_1lt, env_05lt=env_05lt, cubetas=cubetas,
        precio_min=c_chi_u,
        precio_justo=c_chi_u / 0.65 if c_chi_u > 0 else 0,
        precio_prem=c_chi_u  / 0.45 if c_chi_u > 0 else 0,
    )


# ── PANEL DE RESULTADOS ───────────────────────────────────────────────────────

def draw_results(win, r, columnas):
    """
    Vuelca el panel de resultados sobre una o más columnas.

    `columnas` es una lista de (x, y_inicial, y_maxima). Cuando una columna
    se llena, sigue en la siguiente. Con el lienzo de alto fijo los ~31
    renglones de resultados no caben en una sola columna, y sin este salto
    las últimas secciones (utilidad y precios recomendados — justo las que
    se consultan) quedaban cortadas fuera de la pantalla.
    """
    col = 0
    rx, y, y_max = columnas[0]
    activa = True     # False cuando la sección en curso no cupo en ningún lado

    def hay_lugar(n=1):
        return y + n <= y_max

    def siguiente_columna() -> bool:
        nonlocal col, rx, y, y_max
        if col + 1 >= len(columnas):
            return False
        col += 1
        rx, y, y_max = columnas[col]
        return True

    def kv(label, val, color=None):
        nonlocal y
        # si el encabezado de la sección no cupo, sus renglones tampoco se
        # dibujan: media sección suelta sin título se lee como un error
        if not activa:
            return
        if not hay_lugar() and not siguiente_columna():
            return
        draw_kv(win, y, rx, label, val, color)
        y += 1

    def seccion(titulo):
        """Un título nunca debe quedar solo al final de una columna: si no
        caben al menos tres renglones, la sección entera salta."""
        nonlocal y, activa
        if y != columnas[col][1]:
            y += 1
        if not hay_lugar(3) and not siguiente_columna():
            activa = False
            return
        activa = True
        safe_add(win, y, rx, titulo, TITULO()); y += 1
        hline(win, y, rx, ANCHO_COLUMNA); y += 1

    seccion("PRODUCCIÓN")
    kv("chicharrón",  f"{r['kg_chi']:.1f} kg  ({r['rend_chi']:.1f}%)")
    kv("manteca",     f"{r['kg_mant']:.1f}kg / {r['lt_mant']:.1f}lt ({r['rend_mant']:.1f}%)")
    kv("merma",       f"{r['merma_kg']:.1f} kg  ({r['merma_pct']:.1f}%)")
    seccion("COSTOS")
    kv("costo grasa",   f"${r['c_grasa']:,.0f}")
    kv("costo total",   f"${r['c_total']:,.0f}")
    kv("costo/kg chi",  f"${r['c_chi_u']:.2f}/kg")
    kv("costo/kg mant", f"${r['c_mnt_u']:.2f}/kg")
    seccion("MÁRGENES")
    kv("chi público",    f"{r['mg_cp']:.1f}%",  margen_color(r['mg_cp']))
    kv("chi mayoreo",    f"{r['mg_cm']:.1f}%",  margen_color(r['mg_cm']))
    kv("mant cubeta",    f"{r['mg_ml']:.1f}%",  margen_color(r['mg_ml']))
    kv("mant 1lt",       f"{r['mg_l1']:.1f}%",  margen_color(r['mg_l1']))
    kv("mant 500ml",     f"{r['mg_l05']:.1f}%", margen_color(r['mg_l05']))
    seccion("RESULTADO")
    kv("ingreso chi",    f"${r['ing_chi']:,.0f}")
    kv("ingreso litreada", f"${r['ing_lit']:,.0f} ({r['env_1lt']:.0f}×1lt {r['env_05lt']:.0f}×½lt)")
    kv("ingreso cubeta", f"${r['ing_cub']:,.0f}  ({r['cubetas']:.1f} cub)")
    kv("ingreso total",  f"${r['ing_tot']:,.0f}")
    kv("UTILIDAD DÍA",   f"${r['util']:,.0f}",  util_color(r['util']))
    kv("UTIL MES ×25",   f"${r['util_mes']:,.0f}",
       util_color(r['util']) | curses.A_BOLD)
    seccion("PRECIOS REC. CHICHARRÓN")
    kv("mínimo",  f"${r['precio_min']:.2f}/kg",   AVISO())
    kv("justo",   f"${r['precio_justo']:.2f}/kg",  OK())
    kv("premium", f"${r['precio_prem']:.2f}/kg",
       OK() | curses.A_BOLD)


# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────

def main(pantalla):
    """
    'pantalla' es la terminal real; 'stdscr' es el lienzo de tamaño fijo
    centrado en ella — mismo esquema que pos_tui.py, para que las dos
    pantallas de curses del sistema midan lo mismo al abrir.
    """
    curses.curs_set(0)
    init_colors()
    pantalla.keypad(True)
    if not verificar_tamano(pantalla):
        return

    dims_term = pantalla.getmaxyx()
    stdscr    = abrir_lienzo(pantalla)
    stdscr.keypad(True)

    cfg     = cargar_config()
    tab_idx = 0
    sl_idx  = 0
    extras  = {
        "kg_grasa_sim":        120.0,
        "costo_grasa_sim":      25.0,
        "rend_chi_sim":         22.0,
        "diario_empleado":      cfg.diario_empleado,
        "gas_dia":             200.0,
        "precio_chi_pub":       cfg.precio_chi_pub,
        "precio_chi_may":       cfg.precio_chi_may,
        "precio_mant_cub":      cfg.precio_mant_cub,
        "precio_mant_lt1":      cfg.precio_mant_lt1,
        "precio_mant_lt05":     cfg.precio_mant_lt05,
        "mix_chi_pub_pct":      cfg.mix_chi_pub_pct,
        "mix_mant_litreada_pct": 30.0,
        "mix_litreada_1lt_pct":  60.0,
        "alpha":                cfg.alpha * 100,
    }

    while True:
        # si se redimensiona la terminal, el lienzo se recentra
        if pantalla.getmaxyx() != dims_term:
            dims_term = pantalla.getmaxyx()
            stdscr    = abrir_lienzo(pantalla)
            stdscr.keypad(True)

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        r_cab = simular(cfg, extras)
        encabezado(stdscr, "simulador de escenarios",
                   f"utilidad/día  ${r_cab['util']:,.0f}",
                   util_color(r_cab["util"]) | curses.A_BOLD)

        # tabs — la activa en video inverso, las demás apagadas
        tx = 2
        for i, (name, _) in enumerate(TABS):
            label = f" {name} "
            safe_add(stdscr, 2, tx, label,
                     TAB() if i == tab_idx else CHROME())
            tx += len(label) + 1
        hline(stdscr, 3, 0, w)

        sliders  = TABS[tab_idx][1]
        n        = len(sliders)
        sl_idx   = max(0, min(sl_idx, n - 1))
        sl_width = min(ANCHO_SLIDERS, w - 4)

        for i, sl in enumerate(sliders):
            val = extras.get(sl.key, sl.min_val)
            draw_slider(stdscr, 5 + i, 1, sl_width,
                        val, sl.min_val, sl.max_val,
                        sl.label, sl.fmt, sl.unit,
                        selected=(i == sl_idx))

        # dos columnas: primero la derecha (completa, de arriba a abajo) y
        # después el hueco que dejan los sliders abajo a la izquierda
        y_libre_izq = 5 + n + 1
        columnas = [(X_RESULTADOS, 5, h - 3)]
        if y_libre_izq < h - 5:
            columnas.append((2, y_libre_izq, h - 3))
        if X_RESULTADOS + 30 < w:
            draw_results(stdscr, r_cab, columnas)

        r = r_cab
        status = (f"  util/día: ${r['util']:,.0f}  |  "
                  f"ing: ${r['ing_tot']:,.0f}  |  "
                  f"chi: {r['kg_chi']:.1f}kg  "
                  f"mant: {r['kg_mant']:.1f}kg  |  "
                  f"costo: ${r['c_total']:,.0f}")
        # pie de teclas y, en el mismo renglón, la utilidad del día: es el
        # dato que decide todo, así que va aparte y con su propio semáforo
        # en vez de diluirse entre los demás números de la barra
        pie(stdscr, ("Tab", "pestaña"), ("↑↓", "slider"), ("←→", "ajustar"),
            ("Shift+←→", "paso x5"), ("s", "guardar"), ("q", "salir"))

        safe_add(stdscr, h - 1, 0, " " * (w - 1), TAB())
        safe_add(stdscr, h - 1, 0, status[:w - 1], TAB())

        stdscr.refresh()

        key = stdscr.getch()
        sl  = sliders[sl_idx]
        val = extras.get(sl.key, sl.min_val)

        if key in (ord('q'), ord('Q')):
            break
        elif key in (ord('s'), ord('S')):
            cfg.precio_chi_pub    = extras["precio_chi_pub"]
            cfg.precio_chi_may    = extras["precio_chi_may"]
            cfg.precio_mant_cub   = extras["precio_mant_cub"]
            cfg.precio_mant_lt1   = extras["precio_mant_lt1"]
            cfg.precio_mant_lt05  = extras["precio_mant_lt05"]
            cfg.mix_chi_pub_pct   = extras["mix_chi_pub_pct"]
            cfg.diario_empleado   = extras["diario_empleado"]
            cfg.alpha             = extras["alpha"] / 100
            guardar_config(cfg)
            safe_add(stdscr, h - 1, 1, " config guardada ", BOTON())
            stdscr.refresh()
            curses.napms(800)
        elif key == ord('\t'):
            tab_idx = (tab_idx + 1) % len(TABS)
            sl_idx  = 0
        elif key == curses.KEY_UP:
            sl_idx = (sl_idx - 1) % n
        elif key == curses.KEY_DOWN:
            sl_idx = (sl_idx + 1) % n
        elif key == curses.KEY_RIGHT:
            extras[sl.key] = min(sl.max_val, val + sl.step)
        elif key == curses.KEY_LEFT:
            extras[sl.key] = max(sl.min_val, val - sl.step)
        elif key == curses.KEY_SRIGHT:
            extras[sl.key] = min(sl.max_val, val + sl.step * 5)
        elif key == curses.KEY_SLEFT:
            extras[sl.key] = max(sl.min_val, val - sl.step * 5)


def iniciar_tui():
    set_titulo_terminal("bayoSys · Simulador de escenarios")
    try:
        curses.wrapper(main)
    finally:
        set_titulo_terminal()


if __name__ == "__main__":
    iniciar_tui()
