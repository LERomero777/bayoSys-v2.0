"""
calcular.py — bayoSys · Productos El Bayo
Toda la aritmética del negocio. No sabe nada de JSON ni de curses.
Recibe modelos, devuelve resultados.

CAMBIOS junio 2026:
  - Eliminado bloque INIX (inix_pzas, ing_mant_inix, margen_inix_pct)
  - ResultadoDia.ing_mant = proyección simple toda-a-cubeta
  - El desglose real litreada/cubeta lo calcula cierre.py con CierreDia
  - mix_mant_inix_pct eliminado de Config — no existe más
"""

from models import (
    Batch, Config, ResultadoBatch, ResultadoDia,
    KG_GRASA_POR_CUBETA, LT_POR_CUBETA, DENSIDAD_MANTECA,
    REND_MANT_KG
)
from typing import List
from logger import log


# ── FÓRMULAS COMPARTIDAS ─────────────────────────────────────────────────────

def _produccion_y_rendimiento(kg_grasa: float, kg_chi: float) -> tuple:
    """
    Fórmulas de producción y rendimiento compartidas entre batch y día.
    Devuelve (kg_mant, lt_mant, cubetas, merma_kg,
              rend_chi_pct, rend_mant_pct, merma_pct) sin redondear —
    cada llamador redondea según su propia precisión.
    """
    kg_mant  = kg_grasa * REND_MANT_KG
    lt_mant  = kg_mant / DENSIDAD_MANTECA
    cubetas  = kg_grasa / KG_GRASA_POR_CUBETA
    merma_kg = kg_grasa - kg_chi - kg_mant

    rend_chi_pct  = kg_chi  / kg_grasa * 100
    rend_mant_pct = kg_mant / kg_grasa * 100
    merma_pct     = 100 - rend_chi_pct - rend_mant_pct

    return kg_mant, lt_mant, cubetas, merma_kg, rend_chi_pct, rend_mant_pct, merma_pct


# ── BATCH ────────────────────────────────────────────────────────────────────

def calcular_batch(batch: Batch, cfg: Config, n_batches: int) -> ResultadoBatch:
    """
    Calcula todos los resultados de un batch individual.
    n_batches: total de batches del día (informativo, los fijos van en calcular_dia).
    """

    # producción y rendimiento
    kg_mant, lt_mant, cubetas, merma_kg, rend_chi_pct, rend_mant_pct, merma_pct = \
        _produccion_y_rendimiento(batch.kg_grasa, batch.kg_chi)

    # costos del batch — solo variables (sin fijos del día)
    c_grasa   = batch.kg_grasa * batch.costo_kg
    c_destajo = batch.kg_grasa * cfg.destajo_kg
    c_batch   = c_grasa + c_destajo

    # costo por unidad aproximado (sin fijos — útil para vista previa en registro)
    c_chi_unit  = (c_batch * cfg.alpha) / batch.kg_chi if batch.kg_chi > 0 else 0.0
    c_mant_unit = (c_batch * cfg.beta)  / kg_mant      if kg_mant      > 0 else 0.0

    # métrica de comparación entre proveedores
    costo_real_kg_chi = c_grasa / batch.kg_chi if batch.kg_chi > 0 else 0.0

    log.debug("calcular", f"batch #{batch.id}: {batch.kg_grasa:.1f}kg grasa → "
                          f"chi {round(rend_chi_pct,1)}% / mant {round(rend_mant_pct,1)}% / "
                          f"merma {round(merma_pct,1)}%")
    if merma_pct > cfg.merma_alerta_pct:
        log.warn("calcular", f"merma alta: {round(merma_pct,1)}% en batch #{batch.id}")

    return ResultadoBatch(
        batch_id          = batch.id,
        kg_mant           = round(kg_mant,  4),
        lt_mant           = round(lt_mant,  4),
        cubetas           = round(cubetas,  4),
        merma_kg          = round(merma_kg, 4),
        rend_chi_pct      = round(rend_chi_pct,  4),
        rend_mant_pct     = round(rend_mant_pct, 4),
        merma_pct         = round(merma_pct,     4),
        c_grasa           = round(c_grasa,    2),
        c_batch           = round(c_batch,    2),
        c_chi_unit        = round(c_chi_unit,  2),
        c_mant_unit       = round(c_mant_unit, 2),
        costo_real_kg_chi = round(costo_real_kg_chi, 2),
    )


# ── DÍA ──────────────────────────────────────────────────────────────────────

def calcular_dia(batches: List[Batch], cfg: Config,
                 c_gas_dia: float = 0.0) -> ResultadoDia:
    """
    Agrega todos los batches del día y calcula resultados globales.

    c_gas_dia: costo real del ticket de la gasera ese día.
               Se pasa desde cierre.py.
               Si no se pasa (análisis sin cierre) queda en 0.

    NOTA: ing_mant es proyección simple (toda la manteca a precio cubeta).
          El ingreso real de manteca (litreada + cubetas) se calcula en
          cierre.py con los datos reales de CierreDia.
    """
    if not batches:
        raise ValueError("No hay batches para calcular")

    fecha     = batches[0].fecha
    n_batches = len(batches)

    # producción total
    kg_grasa_dia = sum(b.kg_grasa for b in batches)
    kg_chi_dia   = sum(b.kg_chi   for b in batches)

    # producción y rendimiento
    kg_mant_dia, lt_mant_dia, cubetas_dia, merma_kg_dia, \
        rend_chi_pct, rend_mant_pct, merma_pct = \
        _produccion_y_rendimiento(kg_grasa_dia, kg_chi_dia)

    # costos
    c_grasa_dia = sum(b.kg_grasa * b.costo_kg for b in batches)
    c_destajo   = kg_grasa_dia * cfg.destajo_kg
    c_fijos     = cfg.diario_empleado + cfg.leche_dia + c_gas_dia
    c_total_dia = c_grasa_dia + c_destajo + c_fijos

    c_chi_unit  = (c_total_dia * cfg.alpha) / kg_chi_dia  if kg_chi_dia  > 0 else 0.0
    c_mant_unit = (c_total_dia * cfg.beta)  / kg_mant_dia if kg_mant_dia > 0 else 0.0

    # canales chicharrón (proyección por mix)
    chi_pub_kg  = kg_chi_dia * (cfg.mix_chi_pub_pct / 100)
    chi_may_kg  = kg_chi_dia - chi_pub_kg
    ing_chi_pub = chi_pub_kg * cfg.precio_chi_pub
    ing_chi_may = chi_may_kg * cfg.precio_chi_may
    ing_chi     = ing_chi_pub + ing_chi_may

    # manteca — proyección simple: toda a cubeta (referencia / peor caso)
    # El desglose real (litreada vs cubeta) lo maneja CierreDia
    cubetas_proyec = lt_mant_dia / LT_POR_CUBETA
    ing_mant       = cubetas_proyec * cfg.precio_mant_cub

    # resultado proyectado
    ing_total = ing_chi + ing_mant
    utilidad  = ing_total - c_total_dia

    # métricas
    p_mant_lt = cfg.precio_mant_cub / LT_POR_CUBETA

    margen_chi_pub_pct  = (cfg.precio_chi_pub - c_chi_unit) / cfg.precio_chi_pub * 100 if cfg.precio_chi_pub > 0 else 0.0
    margen_chi_may_pct  = (cfg.precio_chi_may - c_chi_unit) / cfg.precio_chi_may * 100 if cfg.precio_chi_may > 0 else 0.0
    margen_mant_cub_pct = (p_mant_lt - c_mant_unit) / p_mant_lt * 100 if p_mant_lt > 0 else 0.0

    precio_min_chi   = c_chi_unit
    precio_justo_chi = c_chi_unit / (1 - cfg.margen_justo_pct / 100)
    precio_prem_chi  = c_chi_unit / (1 - cfg.margen_premium_pct / 100)
    util_mensual     = utilidad * cfg.dias_laborales_mes

    log.info("calcular", f"día {fecha}: {n_batches} batches, "
                         f"{kg_grasa_dia:.1f}kg grasa, utilidad ${utilidad:,.2f}")

    return ResultadoDia(
        fecha         = fecha,
        n_batches     = n_batches,
        kg_grasa_dia  = round(kg_grasa_dia, 3),
        kg_chi_dia    = round(kg_chi_dia,   3),
        kg_mant_dia   = round(kg_mant_dia,  3),
        lt_mant_dia   = round(lt_mant_dia,  3),
        cubetas_dia   = round(cubetas_dia,  3),
        merma_kg_dia  = round(merma_kg_dia, 3),
        rend_chi_pct  = round(rend_chi_pct,  2),
        rend_mant_pct = round(rend_mant_pct, 2),
        merma_pct     = round(merma_pct,     2),
        c_grasa_dia   = round(c_grasa_dia, 2),
        c_total_dia   = round(c_total_dia, 2),
        c_chi_unit    = round(c_chi_unit,  2),
        c_mant_unit   = round(c_mant_unit, 2),
        chi_pub_kg    = round(chi_pub_kg,  3),
        chi_may_kg    = round(chi_may_kg,  3),
        ing_chi_pub   = round(ing_chi_pub, 2),
        ing_chi_may   = round(ing_chi_may, 2),
        ing_chi       = round(ing_chi,     2),
        ing_mant      = round(ing_mant,    2),
        ing_total     = round(ing_total,   2),
        utilidad      = round(utilidad,    2),
        margen_chi_pub_pct  = round(margen_chi_pub_pct,  2),
        margen_chi_may_pct  = round(margen_chi_may_pct,  2),
        margen_mant_cub_pct = round(margen_mant_cub_pct, 2),
        precio_min_chi   = round(precio_min_chi,   2),
        precio_justo_chi = round(precio_justo_chi, 2),
        precio_prem_chi  = round(precio_prem_chi,  2),
        util_mensual     = round(util_mensual,     2),
    )


# ── INGRESO REAL DE MANTECA (desde cierre) ────────────────────────────────────

def calcular_ing_manteca_real(cierre, cfg: Config) -> dict:
    """
    Calcula el ingreso real de manteca a partir de un CierreDia.
    Separado de calcular_dia porque requiere datos de ventas reales.

    Retorna dict con desglose completo para que cierre.py y analisis.py
    puedan mostrar sin duplicar lógica.
    """
    from models import LT_POR_ENV_1LT, LT_POR_ENV_05LT

    # litreada
    lt_vendidos_1lt  = sum(v.env_1lt  * LT_POR_ENV_1LT  for v in cierre.ventas_litreada)
    lt_vendidos_05lt = sum(v.env_05lt * LT_POR_ENV_05LT for v in cierre.ventas_litreada)
    env_1lt_total    = sum(v.env_1lt  for v in cierre.ventas_litreada)
    env_05lt_total   = sum(v.env_05lt for v in cierre.ventas_litreada)
    lt_litreada_total = lt_vendidos_1lt + lt_vendidos_05lt

    # el precio de la venta manda; los cierres viejos no lo traen (0.0) y caen
    # a Config — sin esto, cambiar un precio hoy repreciaba todo el histórico
    ing_litreada_1lt  = sum(v.env_1lt  * (v.precio_1lt  or cfg.precio_mant_lt1)
                            for v in cierre.ventas_litreada)
    ing_litreada_05lt = sum(v.env_05lt * (v.precio_05lt or cfg.precio_mant_lt05)
                            for v in cierre.ventas_litreada)
    ing_litreada      = ing_litreada_1lt + ing_litreada_05lt

    # cubetas
    cubetas_vendidas = sum(v.cantidad for v in cierre.ventas_cubeta)
    ing_cubetas      = sum(v.cantidad * v.precio for v in cierre.ventas_cubeta)

    # precio promedio por litro litreada (para analisis)
    p_prom_lt = ing_litreada / lt_litreada_total if lt_litreada_total > 0 else 0.0

    # precio por litro en cubeta (para comparativo)
    p_cub_lt = cfg.precio_mant_cub / LT_POR_CUBETA

    return dict(
        env_1lt_total     = env_1lt_total,
        env_05lt_total    = env_05lt_total,
        lt_litreada_total = round(lt_litreada_total, 3),
        ing_litreada_1lt  = round(ing_litreada_1lt,  2),
        ing_litreada_05lt = round(ing_litreada_05lt, 2),
        ing_litreada      = round(ing_litreada,      2),
        cubetas_vendidas  = cubetas_vendidas,
        ing_cubetas       = round(ing_cubetas,       2),
        ing_mant_real     = round(ing_litreada + ing_cubetas, 2),
        p_prom_lt         = round(p_prom_lt, 2),
        p_cub_lt          = round(p_cub_lt,  2),
    )


# ── INGRESO REAL DE CHICHARRÓN (desde cierre) ─────────────────────────────────

def calcular_ing_chi_real(cierre, cfg: Config) -> dict:
    """
    Calcula el ingreso real de chicharrón a partir de un CierreDia.
    Análogo a calcular_ing_manteca_real — evita duplicar esta fórmula
    en cierre.py, analisis.py y main.py.
    """
    ing_chi_pub  = cierre.chi_pub_kg * cfg.precio_chi_pub
    ing_chi_may  = cierre.chi_may_kg * cfg.precio_chi_may
    ing_chi_real = ing_chi_pub + ing_chi_may

    return dict(
        ing_chi_pub  = round(ing_chi_pub, 2),
        ing_chi_may  = round(ing_chi_may, 2),
        ing_chi_real = round(ing_chi_real, 2),
    )


# ── COMPARATIVO DE PROVEEDORES ────────────────────────────────────────────────

def comparar_proveedores(batches: List[Batch]) -> dict:
    """
    Agrupa batches por proveedor y calcula costo_real_kg_chi promedio.
    Devuelve dict: { clave_proveedor: { stats } }
    """
    grupos: dict = {}

    for b in batches:
        if b.proveedor not in grupos:
            grupos[b.proveedor] = {
                "batches":     0,
                "kg_grasa":    0.0,
                "kg_chi":      0.0,
                "costo_grasa": 0.0,
            }
        g = grupos[b.proveedor]
        g["batches"]     += 1
        g["kg_grasa"]    += b.kg_grasa
        g["kg_chi"]      += b.kg_chi
        g["costo_grasa"] += b.kg_grasa * b.costo_kg

    resultado = {}
    for clave, g in grupos.items():
        rend_chi   = g["kg_chi"] / g["kg_grasa"] * 100 if g["kg_grasa"] > 0 else 0
        costo_real = g["costo_grasa"] / g["kg_chi"]    if g["kg_chi"]   > 0 else 0
        resultado[clave] = {
            "batches":           g["batches"],
            "kg_grasa_total":    round(g["kg_grasa"],  2),
            "kg_chi_total":      round(g["kg_chi"],    2),
            "rend_chi_pct":      round(rend_chi,       2),
            "costo_real_kg_chi": round(costo_real,     2),
        }

    return resultado
