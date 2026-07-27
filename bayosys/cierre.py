"""
cierre.py — bayoSys · Productos El Bayo
Registro de ventas reales al cierre del día.

CAMBIOS junio 2026:
  - INIX eliminado — reemplazado por litreada en presentaciones 1lt y 0.5lt
  - calcular_ing_manteca_real() centraliza el cálculo de ingreso real manteca

CAMBIO julio 2026 — la manteca ya no se captura aquí:
  Toda la manteca se vende por el POS, así que el cierre la LEE de ahí en vez
  de preguntarla. La producción entra al POS por el pool de granel (solo
  cubetas completas al stock) y las ventas salen por los tickets.

  Antes el cierre re-derivaba el inventario —stock_ayer + litros_del_día/19
  menos lo vendido— y arrastraba el resultado al día siguiente. Eso contaba
  los mismos litros dos veces, una como stock de litreada y otra como
  cubetas, y el error se componía día tras día.
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import List

from config import fecha_hoy, BASE_DIR
from calcular import calcular_dia, calcular_ing_manteca_real, calcular_ing_chi_real
from config import cargar_batches, cargar_config
from models import (
    CierreDia, VentaCubeta, VentaLitreada,
    LT_POR_ENV_1LT, LT_POR_ENV_05LT, LT_POR_CUBETA
)


# ── PERSISTENCIA ─────────────────────────────────────────────────────────────

def _ruta_cierre(fecha: str) -> str:
    return os.path.join(BASE_DIR, "registros", f"{fecha}_cierre.json")


def guardar_cierre(cierre: CierreDia):
    data = asdict(cierre)
    with open(_ruta_cierre(cierre.fecha), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cargar_cierre(fecha: str) -> CierreDia | None:
    ruta = _ruta_cierre(fecha)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta) as f:
            data = json.load(f)
        data["ventas_litreada"] = [VentaLitreada(**v) for v in data["ventas_litreada"]]
        data["ventas_cubeta"]   = [VentaCubeta(**v)   for v in data["ventas_cubeta"]]
        return CierreDia(**data)
    except Exception:
        return None


# ── MANTECA: LA VERDAD VIVE EN EL POS ────────────────────────────────────────
# La manteca no se vuelve a capturar aquí. La producción entra al POS por el
# pool de granel (solo cubetas completas al stock) y las ventas salen por los
# tickets. El cierre únicamente lee ese estado y lo deja asentado en el
# histórico del día.
#
# Antes se re-derivaba: stock_ayer + lt_del_día/19 - vendidas. Eso contaba los
# mismos litros dos veces —una como stock de litreada y otra como cubetas— y
# como el resultado se arrastraba al día siguiente, el error se componía.

def leer_manteca_del_pos(fecha: str) -> dict:
    """
    Reconstruye las ventas de manteca del día y el stock final desde el POS.
    Cada venta conserva el precio con el que se cobró, no el precio de hoy.
    """
    from pos_db import get_items_vendidos_dia, get_sku, get_pool_manteca

    ventas_cubeta = [
        VentaCubeta(cantidad=r["cantidad"], precio=r["precio_unit"])
        for r in get_items_vendidos_dia("CUB", fecha)
    ]

    # una VentaLitreada por ticket — un mismo ticket puede llevar 1lt y ½lt
    por_ticket: dict = {}
    for sku, campo, campo_precio in (("M1LT", "env_1lt",  "precio_1lt"),
                                     ("M05",  "env_05lt", "precio_05lt")):
        for r in get_items_vendidos_dia(sku, fecha):
            t = por_ticket.setdefault(r["ticket_id"], dict(
                env_1lt=0, env_05lt=0, precio_1lt=0.0, precio_05lt=0.0))
            t[campo]        += int(r["cantidad"])
            t[campo_precio]  = r["precio_unit"]

    ventas_litreada = [
        VentaLitreada(
            env_1lt     = v["env_1lt"],
            env_05lt    = v["env_05lt"],
            lt_total    = round(v["env_1lt"] * LT_POR_ENV_1LT +
                                v["env_05lt"] * LT_POR_ENV_05LT, 3),
            precio_1lt  = v["precio_1lt"],
            precio_05lt = v["precio_05lt"],
        )
        for _, v in sorted(por_ticket.items())
    ]

    def _stock(sku: str) -> float:
        row = get_sku(sku)
        return row["stock"] if row else 0.0

    pool_lt   = get_pool_manteca()
    env_1lt   = _stock("M1LT")
    env_05lt  = _stock("M05")

    return dict(
        ventas_cubeta   = ventas_cubeta,
        ventas_litreada = ventas_litreada,
        stock_cubetas   = _stock("CUB"),
        pool_lt         = pool_lt,
        env_1lt_stock   = env_1lt,
        env_05lt_stock  = env_05lt,
        # todo lo que no es cubeta sellada, expresado en litros
        stock_litreada_lt = round(
            pool_lt + env_1lt * LT_POR_ENV_1LT + env_05lt * LT_POR_ENV_05LT, 3
        ),
    )


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _sep(char="─", ancho=54):
    print(char * ancho)

def _titulo(texto):
    print()
    _sep("═")
    print(f"  {texto}")
    _sep("═")

def _pedir_float(prompt, minimo=0.0, maximo=9999.0) -> float:
    # si el rango viene vacío no hay valor que aceptar: preguntar sería un
    # bucle sin salida, así que se devuelve el mínimo y se sigue
    if maximo < minimo:
        return minimo
    while True:
        try:
            val = float(input(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            print(f"  ! valor fuera de rango ({minimo}–{maximo})")
        except ValueError:
            print("  ! ingresa un número válido")

def _confirmar(prompt) -> bool:
    return input(f"  {prompt} [s/n]: ").strip().lower() in ("s", "si", "sí", "y")


# ── FLUJO DE CIERRE ───────────────────────────────────────────────────────────

def registrar_cierre():
    cfg   = cargar_config()
    fecha = fecha_hoy()

    batches = cargar_batches(fecha)
    if not batches:
        print(f"\n  ! no hay batches registrados para {fecha}")
        return None

    # costo de gas del día
    print(f"\n  GAS DEL DÍA")
    _sep()
    c_gas_dia = _pedir_float("costo gas hoy (ticket gasera $)", 0, 2000)

    rd = calcular_dia(batches, cfg, c_gas_dia)

    _titulo(f"CIERRE DEL DÍA — {fecha}")

    print(f"  producción del día:")
    print(f"  chicharrón : {rd.kg_chi_dia:.2f} kg")
    print(f"  manteca    : {rd.kg_mant_dia:.2f} kg  /  {rd.lt_mant_dia:.2f} lt")
    print(f"  equivale a : {rd.cubetas_dia:.2f} cub  (referencia — el inventario real está abajo)")

    # ── chicharrón ───────────────────────────────────────────────────
    print(f"\n  CHICHARRÓN  ({rd.kg_chi_dia:.2f} kg producidos)")
    _sep()
    chi_pub_kg = _pedir_float(
        f"kg vendidos a público × ${cfg.precio_chi_pub:.0f}",
        0, rd.kg_chi_dia
    )
    chi_may_kg = _pedir_float(
        f"kg vendidos a mayoreo × ${cfg.precio_chi_may:.0f}",
        0, rd.kg_chi_dia - chi_pub_kg
    )
    chi_total = chi_pub_kg + chi_may_kg
    if chi_total < rd.kg_chi_dia:
        diff = rd.kg_chi_dia - chi_total
        print(f"  nota: {diff:.2f} kg sin asignar — se agregan a mayoreo")
        chi_may_kg += diff

    # ── manteca — se lee del POS, no se captura ──────────────────────
    mant = leer_manteca_del_pos(fecha)
    ventas_litreada     = mant["ventas_litreada"]
    ventas_cubeta       = mant["ventas_cubeta"]
    stock_litreada_final = mant["stock_litreada_lt"]
    stock_cubetas_final  = mant["stock_cubetas"]

    env_1lt_vend  = sum(v.env_1lt  for v in ventas_litreada)
    env_05lt_vend = sum(v.env_05lt for v in ventas_litreada)
    cub_vendidas  = sum(v.cantidad for v in ventas_cubeta)

    print(f"\n  MANTECA  (registrada en el POS — no se captura aquí)")
    _sep()
    print(f"  litreada vendida : {env_1lt_vend} × 1lt   {env_05lt_vend} × 500ml"
          f"   ({len(ventas_litreada)} tickets)")
    print(f"  cubetas vendidas : {cub_vendidas:.1f}   ({len(ventas_cubeta)} ventas)")
    print(f"  ─────────────────────────────")
    print(f"  en bodega        : {stock_cubetas_final:.0f} cubetas selladas")
    print(f"  envasado         : {mant['env_1lt_stock']:.0f} × 1lt   "
          f"{mant['env_05lt_stock']:.0f} × 500ml")
    print(f"  a granel         : {mant['pool_lt']:.2f} lt  "
          f"(faltan {max(0.0, LT_POR_CUBETA - mant['pool_lt']):.2f} lt para cubeta)")

    # cuadre contra lo que salió de producción hoy
    lt_en_pos = (stock_cubetas_final * LT_POR_CUBETA + stock_litreada_final +
                 cub_vendidas * LT_POR_CUBETA +
                 sum(v.lt_total for v in ventas_litreada))
    print(f"\n  producción del día : {rd.lt_mant_dia:.2f} lt")
    print(f"  litros en el POS   : {lt_en_pos:.2f} lt  (bodega + envasado + granel + vendido)")
    print(f"  nota: la diferencia es el arrastre de días anteriores")

    # ── observaciones ─────────────────────────────────────────────────
    obs = input("\n  observaciones (Enter para omitir): ").strip()

    # ── construir cierre ──────────────────────────────────────────────
    cierre = CierreDia(
        fecha             = fecha,
        chi_pub_kg        = chi_pub_kg,
        chi_may_kg        = chi_may_kg,
        ventas_litreada   = ventas_litreada,
        stock_litreada_lt = round(stock_litreada_final, 3),
        ventas_cubeta     = ventas_cubeta,
        stock_cubetas     = round(stock_cubetas_final, 2),
        observaciones     = obs,
        c_gas_dia         = c_gas_dia,
    )

    # ── resumen ───────────────────────────────────────────────────────
    chi_real  = calcular_ing_chi_real(cierre, cfg)
    ing_chi   = chi_real["ing_chi_real"]
    mant_real = calcular_ing_manteca_real(cierre, cfg)
    ing_total = ing_chi + mant_real["ing_mant_real"]
    utilidad  = ing_total - rd.c_total_dia

    _titulo("RESUMEN DEL CIERRE")
    print(f"  chi público : {chi_pub_kg:.2f} kg × ${cfg.precio_chi_pub:.0f} = ${chi_pub_kg * cfg.precio_chi_pub:,.2f}")
    print(f"  chi mayoreo : {chi_may_kg:.2f} kg × ${cfg.precio_chi_may:.0f} = ${chi_may_kg * cfg.precio_chi_may:,.2f}")
    # los precios salen de cada venta, no de Config — pueden variar en el día
    print(f"  litreada 1lt: {mant_real['env_1lt_total']} env = ${mant_real['ing_litreada_1lt']:,.2f}")
    print(f"  litreada ½lt: {mant_real['env_05lt_total']} env = ${mant_real['ing_litreada_05lt']:,.2f}")
    for v in ventas_cubeta:
        print(f"  cubeta      : {v.cantidad:.1f} × ${v.precio:.0f} = ${v.cantidad * v.precio:,.2f}")
    _sep("─", 44)
    print(f"  INGRESO REAL:   ${ing_total:,.2f}")
    print(f"  COSTO DÍA:      ${rd.c_total_dia:,.2f}  (incl. gas ${c_gas_dia:.0f})")
    signo = "✓" if utilidad > 0 else "!!"
    print(f"  UTILIDAD REAL:  ${utilidad:,.2f}  {signo}")
    print(f"  stock litreada: {stock_litreada_final:.2f} lt")
    print(f"  stock cubetas:  {stock_cubetas_final:.2f} cub")

    print()
    if _confirmar("  ¿guardar cierre?"):
        guardar_cierre(cierre)
        print(f"\n  ✓ cierre guardado\n")
        return cierre
    else:
        print("\n  ✗ cierre descartado\n")
        return None


# ── MENÚ ─────────────────────────────────────────────────────────────────────

def menu_cierre():
    while True:
        fecha  = fecha_hoy()
        cierre = cargar_cierre(fecha)

        _titulo(f"CIERRE DEL DÍA — {fecha}")

        if cierre:
            cfg       = cargar_config()
            mant_real = calcular_ing_manteca_real(cierre, cfg)
            print(f"  cierre registrado")
            print(f"  chi público:  {cierre.chi_pub_kg:.2f} kg")
            print(f"  chi mayoreo:  {cierre.chi_may_kg:.2f} kg")
            print(f"  litreada:     {mant_real['env_1lt_total']}×1lt  {mant_real['env_05lt_total']}×½lt  ({mant_real['lt_litreada_total']:.2f} lt)  = ${mant_real['ing_litreada']:,.0f}")
            print(f"  cubetas:      {mant_real['cubetas_vendidas']:.1f} cub  = ${mant_real['ing_cubetas']:,.0f}")
            print(f"  stock lit:    {cierre.stock_litreada_lt:.2f} lt")
            print(f"  stock cub:    {cierre.stock_cubetas:.2f} cub")
            print()
            print("  [1] rehacer cierre")
            print("  [2] volver")
        else:
            print("  sin cierre registrado hoy")
            print()
            print("  [1] registrar cierre")
            print("  [2] volver")

        op = input("\n  opción: ").strip()
        if op == "1":
            registrar_cierre()
        elif op == "2":
            break


if __name__ == "__main__":
    menu_cierre()
