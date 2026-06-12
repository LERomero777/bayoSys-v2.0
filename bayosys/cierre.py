"""
cierre.py — bayoSys · Productos El Bayo
Registro de ventas reales al cierre del día.

CAMBIOS junio 2026:
  - INIX eliminado — reemplazado por litreada en presentaciones 1lt y 0.5lt
  - Stock litreada y stock cubetas son inventarios INDEPENDIENTES
  - La captura de litreada acepta envases; el sistema calcula litros y valida
    contra lt_mant_dia disponible
  - calcular_ing_manteca_real() centraliza el cálculo de ingreso real manteca
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import List

from config import fecha_hoy, BASE_DIR
from calcular import calcular_dia, calcular_ing_manteca_real
from config import cargar_batches, cargar_config
from models import (
    CierreDia, VentaCubeta, VentaLitreada,
    LT_POR_ENV_1LT, LT_POR_ENV_05LT
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


def cargar_stock_ayer(fecha: str) -> tuple[float, float]:
    """
    Devuelve (stock_cubetas, stock_litreada_lt) del día anterior con cierre.
    Retorna (0.0, 0.0) si no hay cierre previo.
    """
    from config import fechas_con_registro
    fechas = fechas_con_registro()
    if fecha in fechas:
        idx = fechas.index(fecha)
        for f in reversed(fechas[:idx]):
            c = cargar_cierre(f)
            if c:
                return c.stock_cubetas, c.stock_litreada_lt
    return 0.0, 0.0


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _sep(char="─", ancho=54):
    print(char * ancho)

def _titulo(texto):
    print()
    _sep("═")
    print(f"  {texto}")
    _sep("═")

def _pedir_float(prompt, minimo=0.0, maximo=9999.0) -> float:
    while True:
        try:
            val = float(input(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            print(f"  ! valor fuera de rango ({minimo}–{maximo})")
        except ValueError:
            print("  ! ingresa un número válido")

def _pedir_int(prompt, minimo=0, maximo=9999) -> int:
    while True:
        try:
            val = int(input(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            print(f"  ! valor fuera de rango ({minimo}–{maximo})")
        except ValueError:
            print("  ! ingresa un número entero")

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
    print(f"  cubetas pot: {rd.cubetas_dia:.2f} cub  (si toda fuera a cubeta)")

    # stocks de ayer
    stock_cub_ayer, stock_lit_ayer = cargar_stock_ayer(fecha)
    cub_disponibles = stock_cub_ayer + rd.cubetas_dia
    lit_disponibles = stock_lit_ayer + rd.lt_mant_dia

    print(f"\n  stock ayer  cubetas  : {stock_cub_ayer:.1f}  →  disponible: {cub_disponibles:.2f}")
    print(f"  stock ayer  litreada : {stock_lit_ayer:.2f} lt  →  disponible: {lit_disponibles:.2f} lt")

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

    # ── manteca litreada ─────────────────────────────────────────────
    print(f"\n  MANTECA LITREADA  ({lit_disponibles:.2f} lt disponibles)")
    print(f"  precio 1lt:  ${cfg.precio_mant_lt1:.0f}   precio 500ml: ${cfg.precio_mant_lt05:.0f}")
    _sep()

    ventas_litreada   = []
    lt_litreada_usado = 0.0

    while True:
        lt_restantes = lit_disponibles - lt_litreada_usado
        print(f"  litros litreada asignados: {lt_litreada_usado:.2f} / {lit_disponibles:.2f} lt")
        if not _confirmar("  ¿registrar venta de litreada?"):
            break

        env_1lt  = _pedir_int(f"  envases de 1lt   vendidos (disp: {lt_restantes:.2f} lt)", 0, int(lt_restantes / LT_POR_ENV_1LT))
        env_05lt = _pedir_int(f"  envases de 500ml vendidos", 0, int((lt_restantes - env_1lt * LT_POR_ENV_1LT) / LT_POR_ENV_05LT))

        lt_tx = env_1lt * LT_POR_ENV_1LT + env_05lt * LT_POR_ENV_05LT

        if lt_litreada_usado + lt_tx > lit_disponibles + 0.001:
            print(f"  ! excede litros disponibles ({lt_restantes:.2f} lt). ajusta.")
            continue

        if env_1lt == 0 and env_05lt == 0:
            print("  ! ningún envase ingresado")
            continue

        ing_tx = env_1lt * cfg.precio_mant_lt1 + env_05lt * cfg.precio_mant_lt05
        ventas_litreada.append(VentaLitreada(env_1lt=env_1lt, env_05lt=env_05lt, lt_total=round(lt_tx, 3)))
        lt_litreada_usado += lt_tx
        print(f"  → {env_1lt} × 1lt  +  {env_05lt} × 500ml  =  {lt_tx:.2f} lt  =  ${ing_tx:,.0f}")

    stock_litreada_final = lit_disponibles - lt_litreada_usado
    print(f"  stock litreada al cierre: {stock_litreada_final:.2f} lt")

    # ── cubetas ──────────────────────────────────────────────────────
    # Las cubetas disponibles = stock_ayer + cubetas_dia - lt_litreada_usado convertido
    # NOTA: lt_litreada_usado sale del mismo pool lt_mant_dia
    #       hay que restar esos litros antes de convertir a cubetas
    lt_para_cubetas   = rd.lt_mant_dia - lt_litreada_usado
    cub_de_hoy        = max(0.0, lt_para_cubetas / 19.0)
    cub_disponibles   = stock_cub_ayer + cub_de_hoy

    print(f"\n  CUBETAS  ({cub_disponibles:.2f} disponibles después de litreada)")
    _sep()

    ventas_cubeta      = []
    total_cub_vendidas = 0.0

    while True:
        print(f"  cubetas vendidas hasta ahora: {total_cub_vendidas:.1f}")
        if not _confirmar("  ¿agregar venta de cubetas?"):
            break
        cantidad = _pedir_float("  cantidad de cubetas", 0.5, cub_disponibles - total_cub_vendidas)
        precio   = _pedir_float("  precio por cubeta ($)", 200, 900)
        ventas_cubeta.append(VentaCubeta(cantidad=cantidad, precio=precio))
        total_cub_vendidas += cantidad
        print(f"  → {cantidad:.1f} cub × ${precio:.0f} = ${cantidad * precio:,.0f}")

    stock_cubetas_final = cub_disponibles - total_cub_vendidas
    print(f"  stock cubetas al cierre: {stock_cubetas_final:.2f} cub")

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
    )

    # ── resumen ───────────────────────────────────────────────────────
    ing_chi   = chi_pub_kg * cfg.precio_chi_pub + chi_may_kg * cfg.precio_chi_may
    mant_real = calcular_ing_manteca_real(cierre, cfg)
    ing_total = ing_chi + mant_real["ing_mant_real"]
    utilidad  = ing_total - rd.c_total_dia

    _titulo("RESUMEN DEL CIERRE")
    print(f"  chi público : {chi_pub_kg:.2f} kg × ${cfg.precio_chi_pub:.0f} = ${chi_pub_kg * cfg.precio_chi_pub:,.2f}")
    print(f"  chi mayoreo : {chi_may_kg:.2f} kg × ${cfg.precio_chi_may:.0f} = ${chi_may_kg * cfg.precio_chi_may:,.2f}")
    print(f"  litreada 1lt: {mant_real['env_1lt_total']} env × ${cfg.precio_mant_lt1:.0f} = ${mant_real['ing_litreada_1lt']:,.2f}")
    print(f"  litreada ½lt: {mant_real['env_05lt_total']} env × ${cfg.precio_mant_lt05:.0f} = ${mant_real['ing_litreada_05lt']:,.2f}")
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
