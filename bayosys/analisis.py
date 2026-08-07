"""
analisis.py — bayoSys · Productos El Bayo
Resumen del día, histórico y comparativo de proveedores.

CAMBIOS junio 2026:
  - Eliminadas referencias a INIX (LT_POR_INIX, precio_mant_inix)
  - Sección manteca muestra litreada real si existe cierre,
    o proyección a cubeta si no hay cierre aún
  - cfg.diario_empleado (nombre correcto) en lugar de cfg.mano_obra_dia
  - gas se muestra solo si hay cierre registrado (viene del ticket)
"""

from config import (
    cargar_config, cargar_batches,
    fechas_con_registro
)
from calcular import (
    calcular_dia, comparar_proveedores,
    calcular_ing_manteca_real, calcular_ing_chi_real
)
from models import DENSIDAD_MANTECA, LT_POR_CUBETA
from estilos import (
    titulo_txt, sep_txt, ok_txt, alerta_txt, aviso_txt,
    opcion_txt, dato_txt, c,
)


# ── HELPERS DE FORMATO ───────────────────────────────────────────────────────

def _sep(char=None, ancho=54, estilo="chrome"):
    print(sep_txt(char, ancho, estilo))

def _titulo(texto):
    print()
    print(titulo_txt(texto, 54))

def _subtitulo(texto):
    print()
    print(f"  {c(texto, 'acento', negrita=True)}")
    _sep()

def _fila(label, valor, extra="", ancho=28):
    extra_str = f"  {extra}" if extra else ""
    print(f"  {label:<{ancho}} {valor}{extra_str}")

def _pct_bar(pct, ancho=20):
    fill = int(pct / 100 * ancho)
    return "█" * fill + "░" * (ancho - fill) + f"  {pct:.1f}%"

def _margen_str(pct):
    if pct < 15:  return f"{pct:.1f}%  !! CRITICO"
    if pct < 30:  return f"{pct:.1f}%  !  AJUSTADO"
    if pct < 50:  return f"{pct:.1f}%  ✓  SANO"
    return             f"{pct:.1f}%  ✓  PREMIUM"


# ── RESUMEN DEL DÍA ──────────────────────────────────────────────────────────

def mostrar_dia(fecha: str):
    from cierre import cargar_cierre

    batches = cargar_batches(fecha)
    if not batches:
        print(f"\n  sin registros para {fecha}")
        return

    cfg    = cargar_config()
    cierre = cargar_cierre(fecha)

    # gas del día — solo si hay cierre
    c_gas_dia = cierre.c_gas_dia if cierre else 0.0

    r = calcular_dia(batches, cfg, c_gas_dia)

    # ingresos reales — calculados una sola vez, reusados en ambas secciones
    mr_mant = calcular_ing_manteca_real(cierre, cfg) if cierre else None
    mr_chi  = calcular_ing_chi_real(cierre, cfg)     if cierre else None

    _titulo(f"RESUMEN DÍA — {fecha}  ({r.n_batches} batches)")

    # ── producción ───────────────────────────────────────────────────
    _subtitulo("PRODUCCIÓN")
    _fila("grasa entrada",  f"{r.kg_grasa_dia:.1f} kg")
    print()
    _fila("chicharrón",     f"{r.kg_chi_dia:.2f} kg",  _pct_bar(r.rend_chi_pct))
    _fila("manteca",        f"{r.kg_mant_dia:.2f} kg  /  {r.lt_mant_dia:.2f} lt",
          _pct_bar(r.rend_mant_pct))
    _fila("merma",          f"{r.merma_kg_dia:.2f} kg", _pct_bar(r.merma_pct))
    print()
    _fila("cubetas potenc.", f"{r.cubetas_dia:.2f}  (si toda la manteca fuera a cubeta)")

    # ── batches ──────────────────────────────────────────────────────
    _subtitulo("BATCHES")
    print(f"  {'#':<4} {'hora':<6} {'proveedor':<8} {'grasa':>7} {'chi':>7} {'rend%':>7}  temp")
    _sep()
    for b in batches:
        from calcular import calcular_batch
        rb = calcular_batch(b, cfg, r.n_batches)
        print(f"  {b.id:<4} {b.hora:<6} {b.proveedor:<8} "
              f"{b.kg_grasa:>6.1f}k {b.kg_chi:>6.1f}k "
              f"{rb.rend_chi_pct:>6.1f}%  {b.temp_entrada}")

    # ── costos ───────────────────────────────────────────────────────
    _subtitulo("COSTOS")
    _fila("materia prima",    f"${r.c_grasa_dia:,.2f}")
    _fila("destajo MO",       f"${r.kg_grasa_dia * cfg.destajo_kg:,.2f}  (${cfg.destajo_kg}/kg × {r.kg_grasa_dia:.0f}kg)")
    _fila("empleado (fijo)",  f"${cfg.diario_empleado:,.2f}")
    _fila("leche",            f"${cfg.leche_dia:,.2f}")
    if cierre:
        _fila("gas",          f"${c_gas_dia:,.2f}  (del ticket)")
    else:
        _fila("gas",          "—  (capturar en cierre)")
    _sep("─", 44)
    _fila("COSTO TOTAL",      f"${r.c_total_dia:,.2f}")
    print()
    _fila("costo/kg chi",     f"${r.c_chi_unit:.2f}/kg")
    _fila("costo/kg mant",    f"${r.c_mant_unit:.2f}/kg")

    # ── canales chicharrón ───────────────────────────────────────────
    _subtitulo("CANALES — CHICHARRÓN")
    if cierre:
        _fila("público  (real)",
              f"{cierre.chi_pub_kg:.2f} kg × ${cfg.precio_chi_pub:.0f}",
              f"= ${cierre.chi_pub_kg * cfg.precio_chi_pub:,.2f}  margen {_margen_str(r.margen_chi_pub_pct)}")
        _fila("mayoreo  (real)",
              f"{cierre.chi_may_kg:.2f} kg × ${cfg.precio_chi_may:.0f}",
              f"= ${cierre.chi_may_kg * cfg.precio_chi_may:,.2f}  margen {_margen_str(r.margen_chi_may_pct)}")
    else:
        _fila("público  (proy)",
              f"{r.chi_pub_kg:.2f} kg × ${cfg.precio_chi_pub:.0f}",
              f"= ${r.ing_chi_pub:,.2f}  margen {_margen_str(r.margen_chi_pub_pct)}")
        _fila("mayoreo  (proy)",
              f"{r.chi_may_kg:.2f} kg × ${cfg.precio_chi_may:.0f}",
              f"= ${r.ing_chi_may:,.2f}  margen {_margen_str(r.margen_chi_may_pct)}")

    # ── canales manteca ──────────────────────────────────────────────
    _subtitulo("CANALES — MANTECA")
    if cierre:
        mr = mr_mant
        p_lt1_lt  = cfg.precio_mant_lt1   / 1.0    # ya es $/lt
        p_lt05_lt = cfg.precio_mant_lt05  / 0.5    # normalizado a $/lt
        p_cub_lt  = cfg.precio_mant_cub   / LT_POR_CUBETA

        _fila("litreada 1lt (real)",
              f"{mr['env_1lt_total']} env × ${cfg.precio_mant_lt1:.0f}",
              f"= ${mr['ing_litreada_1lt']:,.2f}  (${p_lt1_lt:.2f}/lt)")
        _fila("litreada ½lt (real)",
              f"{mr['env_05lt_total']} env × ${cfg.precio_mant_lt05:.0f}",
              f"= ${mr['ing_litreada_05lt']:,.2f}  (${p_lt05_lt:.2f}/lt equiv)")
        for v in cierre.ventas_cubeta:
            _fila("cubeta (real)",
                  f"{v.cantidad:.1f} × ${v.precio:.0f}",
                  f"= ${v.cantidad * v.precio:,.2f}  (${v.precio / LT_POR_CUBETA:.2f}/lt)")
        print()
        if mr["lt_litreada_total"] > 0:
            _fila("precio prom litreada", f"${mr['p_prom_lt']:.2f}/lt",
                  f"vs cubeta ${mr['p_cub_lt']:.2f}/lt  →  {mr['p_prom_lt']/mr['p_cub_lt']:.1f}× más/lt")
        _fila("stock litreada", f"{cierre.stock_litreada_lt:.2f} lt")
        _fila("stock cubetas",  f"{cierre.stock_cubetas:.2f} cub")
    else:
        p_cub_lt = cfg.precio_mant_cub / LT_POR_CUBETA
        _fila("proyección cubeta",
              f"{r.cubetas_dia:.2f} cub × ${cfg.precio_mant_cub:.0f}",
              f"= ${r.ing_mant:,.2f}  margen {_margen_str(r.margen_mant_cub_pct)}")
        print(f"\n  (sin cierre registrado — mostrar real requiere cierre del día)")
        _fila("litreada 1lt",  f"${cfg.precio_mant_lt1:.0f}/env  = ${cfg.precio_mant_lt1:.2f}/lt")
        _fila("litreada ½lt",  f"${cfg.precio_mant_lt05:.0f}/env = ${cfg.precio_mant_lt05 / 0.5:.2f}/lt equiv")
        _fila("cubeta",        f"${cfg.precio_mant_cub:.0f}/19lt = ${p_cub_lt:.2f}/lt")

    # ── resultado ────────────────────────────────────────────────────
    _subtitulo("RESULTADO")
    if cierre:
        mr = mr_mant
        ing_chi_real = mr_chi["ing_chi_real"]
        ing_total    = ing_chi_real + mr["ing_mant_real"]
        utilidad     = ing_total - r.c_total_dia
        _fila("ingreso chicharrón", f"${ing_chi_real:,.2f}", "(real)")
        _fila("ingreso manteca",    f"${mr['ing_mant_real']:,.2f}", "(real)")
        _sep("─", 44)
        _fila("INGRESO TOTAL",  f"${ing_total:,.2f}", "(real)")
    else:
        _fila("ingreso chicharrón", f"${r.ing_chi:,.2f}", "(proyectado)")
        _fila("ingreso manteca",    f"${r.ing_mant:,.2f}", "(proyectado — toda a cubeta)")
        _sep("─", 44)
        _fila("INGRESO TOTAL",  f"${r.ing_total:,.2f}", "(proyectado)")
        utilidad = r.utilidad

    _fila("COSTO TOTAL",    f"${r.c_total_dia:,.2f}")
    _sep("─", 44)
    util_str = f"${utilidad:,.2f}"
    if utilidad < 0:    util_str += "  !! PÉRDIDA"
    elif utilidad < 800: util_str += "  ! BAJO"
    else:               util_str += "  ✓"
    _fila("UTILIDAD",       util_str)
    _fila("proy. mensual",  f"${utilidad * cfg.dias_laborales_mes:,.2f}",
          f"(× {cfg.dias_laborales_mes:.0f} días)")

    # ── precios recomendados ─────────────────────────────────────────
    _subtitulo("PRECIOS RECOMENDADOS — CHICHARRÓN")
    _fila("mínimo (break-even)", f"${r.precio_min_chi:.2f}/kg")
    _fila(f"justo  ({cfg.margen_justo_pct:.0f}% margen)", f"${r.precio_justo_chi:.2f}/kg")
    _fila(f"premium ({cfg.margen_premium_pct:.0f}% margen)",f"${r.precio_prem_chi:.2f}/kg")
    actual = cfg.precio_chi_pub
    if actual < r.precio_min_chi:
        print(f"\n  !! ALERTA: precio actual ${actual} está BAJO el costo real")
    elif actual < r.precio_justo_chi:
        print("\n" + alerta_txt(f" precio actual ${actual} tiene margen ajustado"))
    else:
        print("\n" + ok_txt(f" precio actual ${actual} está en rango sano"))
    print()


# ── HISTÓRICO ────────────────────────────────────────────────────────────────

def mostrar_historico(n_dias: int = 7):
    fechas = fechas_con_registro()
    if not fechas:
        print("\n  sin registros históricos")
        return

    fechas = fechas[-n_dias:]
    cfg    = cargar_config()

    _titulo(f"HISTÓRICO — últimos {len(fechas)} días")
    print(f"  {'fecha':<12} {'bat':>3} {'grasa':>7} {'chi':>7} "
          f"{'rend%':>6} {'costo':>9} {'utilidad':>10}")
    _sep()

    for fecha in fechas:
        batches = cargar_batches(fecha)
        if not batches:
            continue
        r = calcular_dia(batches, cfg)
        print(f"  {fecha:<12} {r.n_batches:>3} "
              f"{r.kg_grasa_dia:>6.1f}k {r.kg_chi_dia:>6.1f}k "
              f"{r.rend_chi_pct:>5.1f}% "
              f"${r.c_total_dia:>8,.0f} "
              f"${r.utilidad:>9,.0f}")
    print()


# ── COMPARATIVO DE PROVEEDORES ────────────────────────────────────────────────

def mostrar_proveedores(n_dias: int = 90):
    """
    n_dias: cuántos días recientes considerar (default 90).
    Evita releer TODO el historial de archivos JSON en cada consulta —
    con meses/años de operación esa lectura crecería sin límite.
    Pasa n_dias=None para forzar el histórico completo.
    """
    todas_fechas = fechas_con_registro()
    if not todas_fechas:
        print("\n  sin registros para comparar")
        return

    fechas = todas_fechas if n_dias is None else todas_fechas[-n_dias:]

    todos_batches = []
    for fecha in fechas:
        todos_batches.extend(cargar_batches(fecha))

    if not todos_batches:
        return

    if len(fechas) < len(todas_fechas):
        print(f"\n  (mostrando últimos {len(fechas)} de {len(todas_fechas)} días con registro)")

    comp = comparar_proveedores(todos_batches)

    _titulo("COMPARATIVO DE PROVEEDORES")
    print(f"  {'proveedor':<12} {'batches':>7} {'kg grasa':>9} "
          f"{'kg chi':>8} {'rend%':>7} {'$/kg chi real':>14}")
    _sep()

    ordenado = sorted(comp.items(), key=lambda x: x[1]["costo_real_kg_chi"])
    for i, (clave, datos) in enumerate(ordenado):
        marker = "← más económico" if i == 0 else ""
        print(f"  {clave:<12} {datos['batches']:>7} "
              f"{datos['kg_grasa_total']:>8.1f}k "
              f"{datos['kg_chi_total']:>7.1f}k "
              f"{datos['rend_chi_pct']:>6.1f}% "
              f"${datos['costo_real_kg_chi']:>12.2f}  {marker}")

    print()
    print("  NOTA: costo_real_kg_chi = costo_grasa / kg_chi_obtenido")
    print("        el más barato por kg grasa ≠ el más barato por kg chicharrón")
    print()


# ── MENÚ ─────────────────────────────────────────────────────────────────────

def menu_analisis():
    while True:
        print("\n")
        print(titulo_txt("ANÁLISIS — bayoSys"))
        print(opcion_txt("1", "resumen de hoy"))
        print(opcion_txt("2", "resumen de otro día"))
        print(opcion_txt("3", "histórico (últimos 7 días)"))
        print(opcion_txt("4", "comparativo de proveedores"))
        print(opcion_txt("5", "volver al menú principal"))

        op = input("\n  opción: ").strip()

        if op == "1":
            from config import fecha_hoy
            mostrar_dia(fecha_hoy())

        elif op == "2":
            fechas = fechas_con_registro()
            if not fechas:
                print("\n  sin registros disponibles")
                continue
            print("\n  fechas disponibles:")
            for i, f in enumerate(fechas[-10:], 1):
                print(f"  [{i}] {f}")
            try:
                idx = int(input("  selecciona: ").strip()) - 1
                if 0 <= idx < len(fechas[-10:]):
                    mostrar_dia(fechas[-10:][idx])
                else:
                    print(alerta_txt("opción inválida"))
            except ValueError:
                print(alerta_txt("ingresa un número"))

        elif op == "3":
            mostrar_historico(7)

        elif op == "4":
            mostrar_proveedores()

        elif op == "5":
            break

        else:
            print(alerta_txt("opción inválida"))


if __name__ == "__main__":
    menu_analisis()
