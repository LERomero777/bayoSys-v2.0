#!/usr/bin/env python3
"""
main.py — bayoSys · Productos El Bayo
Punto de entrada único. Navega todos los módulos desde aquí.
Uso: python3 main.py
"""

import os
from config import (
    cargar_config, guardar_config,
    cargar_batches, cargar_proveedores, guardar_proveedores,
    fecha_hoy
)
from calcular import calcular_dia
from registro import menu_registro
from cierre import menu_cierre, cargar_cierre
from analisis import menu_analisis
from tui import iniciar_tui
from pos_db import init_db


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _sep(char="═", ancho=54):
    print(char * ancho)

def _limpiar():
    os.system("clear")

def _confirmar(prompt) -> bool:
    return input(f"  {prompt} [s/n]: ").strip().lower() in ("s","si","sí","y")

def _pedir_float(prompt, minimo, maximo) -> float:
    while True:
        try:
            val = float(input(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            print(f"  ! rango válido: {minimo}–{maximo}")
        except ValueError:
            print("  ! ingresa un número")


# ── ESTADO DEL DÍA ───────────────────────────────────────────────────────────

def _estado_hoy() -> str:
    fecha   = fecha_hoy()
    batches = cargar_batches(fecha)
    cierre  = cargar_cierre(fecha)

    if not batches:
        return "sin batches hoy"

    cfg = cargar_config()
    rd  = calcular_dia(batches, cfg)
    estado = f"{len(batches)} batch(es)  chi:{rd.kg_chi_dia:.1f}kg"

    if cierre:
        from calcular import calcular_ing_manteca_real
        ing_chi   = (cierre.chi_pub_kg * cfg.precio_chi_pub +
                     cierre.chi_may_kg * cfg.precio_chi_may)
        mr        = calcular_ing_manteca_real(cierre, cfg)
        ing_total = ing_chi + mr["ing_mant_real"]
        utilidad  = ing_total - rd.c_total_dia
        estado += (f"  util:${utilidad:,.0f}"
                   f"  lit:{cierre.stock_litreada_lt:.1f}lt"
                   f"  cub:{cierre.stock_cubetas:.1f}  [cerrado]")
    else:
        estado += f"  costo:${rd.c_total_dia:,.0f}  [sin cierre]"

    return estado


# ── MENÚ DE CONFIGURACIÓN ────────────────────────────────────────────────────

def menu_config():
    while True:
        cfg = cargar_config()
        _limpiar()
        _sep()
        print("  CONFIGURACIÓN — bayoSys")
        _sep()
        print(f"  [1] precios de venta")
        print(f"       chi público:   ${cfg.precio_chi_pub:.0f}/kg")
        print(f"       chi mayoreo:   ${cfg.precio_chi_may:.0f}/kg")
        print(f"       cubeta 19lt:   ${cfg.precio_mant_cub:.0f}")
        print(f"       litreada 1lt:  ${cfg.precio_mant_lt1:.0f}/env")
        print(f"       litreada ½lt:  ${cfg.precio_mant_lt05:.0f}/env")
        print(f"  [2] costos laborales")
        print(f"       destajo:           ${cfg.destajo_kg:.2f}/kg grasa picado")
        print(f"       diario empleado:   ${cfg.diario_empleado:.0f}/día")
        print(f"  [3] costos operativos")
        print(f"       leche:             ${cfg.leche_dia:.0f}/día")
        print(f"       costo env 1lt:     ${cfg.costo_env_1lt:.2f}/pza")
        print(f"       costo env 500ml:   ${cfg.costo_env_05lt:.2f}/pza")
        print(f"       gas:               se captura en cierre del día")
        print(f"  [4] proveedores")
        print(f"  [5] distribución de costo (alpha/beta)")
        print(f"       alpha: {cfg.alpha*100:.0f}% → chicharrón")
        print(f"       beta:  {cfg.beta*100:.0f}% → manteca")
        print(f"  [6] sincronizar precios al POS")
        print(f"  [7] volver")
        print()

        op = input("  opción: ").strip()

        if op == "1":
            print()
            cfg.precio_chi_pub   = _pedir_float("chi público $/kg",   100, 500)
            cfg.precio_chi_may   = _pedir_float("chi mayoreo $/kg",   100, 400)
            cfg.precio_mant_cub  = _pedir_float("cubeta 19lt $",      200, 900)
            cfg.precio_mant_lt1  = _pedir_float("litreada 1lt $",      20,  80)
            cfg.precio_mant_lt05 = _pedir_float("litreada 500ml $",    10,  50)
            guardar_config(cfg)
            print("  ✓ precios actualizados")

        elif op == "2":
            print()
            cfg.destajo_kg      = _pedir_float("destajo $/kg grasa picado", 0.5, 10.0)
            cfg.diario_empleado = _pedir_float("diario empleado $/día", 0, 2000)
            guardar_config(cfg)
            print("  ✓ costos laborales actualizados")

        elif op == "3":
            print()
            cfg.leche_dia      = _pedir_float("leche $/día",        0, 500)
            cfg.costo_env_1lt  = _pedir_float("costo env 1lt $",    0,  20)
            cfg.costo_env_05lt = _pedir_float("costo env 500ml $",  0,  20)
            guardar_config(cfg)
            print("  ✓ costos operativos actualizados")

        elif op == "4":
            provs = cargar_proveedores()
            print()
            for clave, prov in provs.items():
                print(f"  {clave}: {prov.nombre}  ${prov.costo_kg}/kg")
            print()
            clave = input("  clave a editar (Enter para cancelar): ").strip()
            if clave in provs:
                nuevo = _pedir_float(f"nuevo costo $/kg para {clave}", 10, 100)
                provs[clave].costo_kg = nuevo
                guardar_proveedores(provs)
                print(f"  ✓ {clave} actualizado a ${nuevo}/kg")

        elif op == "5":
            print()
            alpha = _pedir_float("alpha % costo → chicharrón (40-90)", 40, 90)
            cfg.alpha = alpha / 100
            cfg.beta  = round(1.0 - cfg.alpha, 10)
            guardar_config(cfg)
            print(f"  ✓ alpha={cfg.alpha*100:.0f}%  beta={cfg.beta*100:.0f}%")

        elif op == "6":
            # sincronizar precios de Config → pos.db
            try:
                from pos_db import actualizar_precio
                actualizar_precio("CHI",  cfg.precio_chi_pub)
                actualizar_precio("M1LT", cfg.precio_mant_lt1)
                actualizar_precio("M05",  cfg.precio_mant_lt05)
                actualizar_precio("CUB",  cfg.precio_mant_cub)
                print("  ✓ precios sincronizados al POS")
            except Exception as e:
                print(f"  ! error sincronizando: {e}")

        elif op == "7":
            break


# ── MENÚ PRINCIPAL ───────────────────────────────────────────────────────────

def main():
    # inicializar SQLite del POS al arrancar
    init_db()

    while True:
        _limpiar()
        fecha  = fecha_hoy()
        estado = _estado_hoy()

        _sep()
        print("  bayoSys · Productos El Bayo")
        _sep("─")
        print(f"  {fecha}  |  {estado}")
        _sep()
        print()
        print("  [1] registrar batch")
        print("  [2] POS — punto de venta")
        print("  [3] cierre del día")
        print("  [4] análisis y reportes")
        print("  [5] simulador de escenarios")
        print("  [6] configuración")
        print()
        print("  [0] salir")
        print()

        op = input("  opción: ").strip()

        if op == "1":
            _limpiar()
            menu_registro()

        elif op == "2":
            _limpiar()
            # pos_tui se construye en siguiente iteración
            print("\n  POS en construcción — próxima sesión\n")
            input("  Enter para continuar...")

        elif op == "3":
            _limpiar()
            menu_cierre()

        elif op == "4":
            _limpiar()
            menu_analisis()

        elif op == "5":
            iniciar_tui()

        elif op == "6":
            menu_config()

        elif op == "0":
            print("\n  hasta luego\n")
            break

        else:
            print("  ! opción inválida")
            input("  Enter para continuar...")


if __name__ == "__main__":
    main()
