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
    curses.init_pair(7, curses.COLOR_CYAN,    -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)

C_NORMAL  = lambda: curses.color_pair(1)
C_CYAN    = lambda: curses.color_pair(2)
C_GREEN   = lambda: curses.color_pair(3)
C_YELLOW  = lambda: curses.color_pair(4)
C_RED     = lambda: curses.color_pair(5)
C_TAB_ON  = lambda: curses.color_pair(6) | curses.A_BOLD
C_TAB_OFF = lambda: curses.color_pair(7)
C_TITLE   = lambda: curses.color_pair(8) | curses.A_BOLD

def margen_color(pct):
    if pct < 15: return C_RED()
    if pct < 30: return C_YELLOW()
    return C_GREEN()

def util_color(val):
    if val < 0:    return C_RED()
    if val < 1000: return C_YELLOW()
    return C_GREEN()


# ── HELPERS DE DIBUJO ────────────────────────────────────────────────────────

def safe_add(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0: return
    max_len = w - x - 1
    if max_len <= 0: return
    try:
        win.addstr(y, x, str(text)[:max_len], attr)
    except curses.error:
        pass

def draw_slider(win, y, x, width, val, min_v, max_v, label, fmt, unit, selected):
    bar_w   = max(10, width - len(label) - 14)
    pct     = (val - min_v) / (max_v - min_v) if max_v > min_v else 0
    fill    = int(pct * bar_w)
    val_str = fmt.format(val) + unit
    attr_l  = C_CYAN() | curses.A_BOLD if selected else C_NORMAL()
    attr_b  = C_CYAN() | curses.A_BOLD if selected else C_CYAN()
    safe_add(win, y, x,               f"{label:<22}",           attr_l)
    safe_add(win, y, x + 22,          "[",                      C_NORMAL())
    safe_add(win, y, x + 23,          "█" * fill,               attr_b)
    safe_add(win, y, x + 23 + fill,   "░" * (bar_w - fill),     C_NORMAL())
    safe_add(win, y, x + 23 + bar_w,  "] ",                     C_NORMAL())
    safe_add(win, y, x + 25 + bar_w,  val_str,
             C_CYAN() | (curses.A_BOLD if selected else 0))

def draw_kv(win, y, x, label, value, color=None):
    if color is None: color = C_NORMAL()
    safe_add(win, y, x,      f"{label:<26}", C_NORMAL())
    safe_add(win, y, x + 26, str(value),     color)


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

def draw_results(win, r, rx, start_y):
    y = start_y

    def kv(label, val, color=None):
        nonlocal y
        draw_kv(win, y, rx, label, val, color)
        y += 1

    def sep():
        nonlocal y
        safe_add(win, y, rx, "─" * 40, C_NORMAL())
        y += 1

    safe_add(win, y, rx, "PRODUCCIÓN", C_TITLE()); y += 1
    sep()
    kv("chicharrón",  f"{r['kg_chi']:.1f} kg  ({r['rend_chi']:.1f}%)")
    kv("manteca",     f"{r['kg_mant']:.1f} kg / {r['lt_mant']:.1f} lt  ({r['rend_mant']:.1f}%)")
    kv("merma",       f"{r['merma_kg']:.1f} kg  ({r['merma_pct']:.1f}%)")
    y += 1

    safe_add(win, y, rx, "COSTOS", C_TITLE()); y += 1
    sep()
    kv("costo grasa",   f"${r['c_grasa']:,.0f}")
    kv("costo total",   f"${r['c_total']:,.0f}")
    kv("costo/kg chi",  f"${r['c_chi_u']:.2f}/kg")
    kv("costo/kg mant", f"${r['c_mnt_u']:.2f}/kg")
    y += 1

    safe_add(win, y, rx, "MÁRGENES", C_TITLE()); y += 1
    sep()
    kv("chi público",    f"{r['mg_cp']:.1f}%",  margen_color(r['mg_cp']))
    kv("chi mayoreo",    f"{r['mg_cm']:.1f}%",  margen_color(r['mg_cm']))
    kv("mant cubeta",    f"{r['mg_ml']:.1f}%",  margen_color(r['mg_ml']))
    kv("mant 1lt",       f"{r['mg_l1']:.1f}%",  margen_color(r['mg_l1']))
    kv("mant 500ml",     f"{r['mg_l05']:.1f}%", margen_color(r['mg_l05']))
    y += 1

    safe_add(win, y, rx, "RESULTADO", C_TITLE()); y += 1
    sep()
    kv("ingreso chi",    f"${r['ing_chi']:,.0f}")
    kv("ingreso litreada",f"${r['ing_lit']:,.0f}  ({r['env_1lt']:.0f}×1lt  {r['env_05lt']:.0f}×½lt)")
    kv("ingreso cubeta", f"${r['ing_cub']:,.0f}  ({r['cubetas']:.1f} cub)")
    kv("ingreso total",  f"${r['ing_tot']:,.0f}")
    kv("UTILIDAD DÍA",   f"${r['util']:,.0f}",  util_color(r['util']))
    kv("UTIL MES ×25",   f"${r['util_mes']:,.0f}",
       util_color(r['util']) | curses.A_BOLD)
    y += 1

    safe_add(win, y, rx, "PRECIOS REC. CHICHARRÓN", C_TITLE()); y += 1
    sep()
    kv("mínimo",  f"${r['precio_min']:.2f}/kg",   C_YELLOW())
    kv("justo",   f"${r['precio_justo']:.2f}/kg",  C_GREEN())
    kv("premium", f"${r['precio_prem']:.2f}/kg",
       C_GREEN() | curses.A_BOLD)


# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────

def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    init_colors()

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
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        safe_add(stdscr, 0, 0,
                 "  bayoSys · Simulador de Escenarios".center(w), C_TITLE())

        # tabs
        tx = 2
        for i, (name, _) in enumerate(TABS):
            label = f" {name} "
            safe_add(stdscr, 1, tx, label,
                     C_TAB_ON() if i == tab_idx else C_TAB_OFF())
            tx += len(label) + 1
        safe_add(stdscr, 1, tx + 2,
                 "[ Tab: tab  ↑↓: slider  ←→: ajustar  s: guardar  q: salir ]",
                 C_NORMAL())

        safe_add(stdscr, 2, 0, "─" * (w - 1), C_NORMAL())

        sliders  = TABS[tab_idx][1]
        n        = len(sliders)
        sl_idx   = max(0, min(sl_idx, n - 1))
        sl_width = min(58, w // 2 - 2)

        for i, sl in enumerate(sliders):
            val = extras.get(sl.key, sl.min_val)
            draw_slider(stdscr, 4 + i * 2, 1, sl_width,
                        val, sl.min_val, sl.max_val,
                        sl.label, sl.fmt, sl.unit,
                        selected=(i == sl_idx))

        hint_y = 4 + n * 2 + 1
        safe_add(stdscr, hint_y, 1, "Shift+←→ = paso x5", C_NORMAL())

        rx = sl_width + 4
        if rx + 30 < w:
            r = simular(cfg, extras)
            draw_results(stdscr, r, rx, 3)

        r = simular(cfg, extras)
        status = (f"  util/día: ${r['util']:,.0f}  |  "
                  f"ing: ${r['ing_tot']:,.0f}  |  "
                  f"chi: {r['kg_chi']:.1f}kg  "
                  f"mant: {r['kg_mant']:.1f}kg  |  "
                  f"costo: ${r['c_total']:,.0f}")
        safe_add(stdscr, h - 1, 0, status[:w - 1],
                 util_color(r['util']) | curses.A_BOLD)

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
            safe_add(stdscr, h - 2, 1, " config guardada ", C_GREEN())
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
    curses.wrapper(main)


if __name__ == "__main__":
    iniciar_tui()
