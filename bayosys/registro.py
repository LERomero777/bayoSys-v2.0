"""
registro.py — bayoSys · Productos El Bayo
Captura interactiva de un batch al terminar el fondeo.
Sin curses — usa input() simple para máxima compatibilidad.
"""

from datetime import datetime
from config import (
    cargar_config, cargar_proveedores,
    guardar_batch, cargar_batches,
    fecha_hoy, siguiente_batch_id
)
from calcular import calcular_batch
from models import Batch


# ── CANCELACIÓN ───────────────────────────────────────────────────────────────
# Enter vacío en cualquier paso del registro = cancelar todo el flujo.
# Mismo patrón que el wizard de administración en main.py.

class _Cancelado(Exception):
    """Señal interna — el operador abortó el registro a la mitad."""
    pass

# ── HELPERS DE INPUT ─────────────────────────────────────────────────────────

def _limpiar():
    print("\n" * 2)


def _titulo(texto):
    print(f"\n{'─' * 48}")
    print(f"  {texto}")
    print(f"{'─' * 48}")

def _pedir_float(prompt, minimo=0.0, maximo=999.0) -> float:
    while True:
        try:
            val = float(input(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            print(f"  ! valor fuera de rango ({minimo}–{maximo}), intenta de nuevo")
        except ValueError:
            print("  ! ingresa un número válido")


def _pedir_opcion(prompt, opciones: list) -> str:
    """Muestra opciones numeradas y devuelve la seleccionada."""
    for i, op in enumerate(opciones, 1):
        print(f"  [{i}] {op}")
    while True:
        try:
            idx = int(input(f"  {prompt}: ").strip())
            if 1 <= idx <= len(opciones):
                return opciones[idx - 1]
            print(f"  ! elige entre 1 y {len(opciones)}")
        except ValueError:
            print("  ! ingresa el número de la opción")

def _confirmar(prompt) -> bool:
    resp = input(f"  {prompt} [s/n]: ").strip().lower()
    return resp in ("s", "si", "sí", "y", "yes", "a huevo")

def _pedir_float_c(prompt, minimo=0.0, maximo=999.0) -> float:
    """Como _pedir_float pero Enter vacío cancela el registro completo."""
    while True:
        raw = input(f"  {prompt}  [Enter=cancelar]: ").strip()
        if raw == "":
            raise _Cancelado()
        try:
            val = float(raw)
            if minimo <= val <= maximo:
                return val
            print(f"  ! valor fuera de rango ({minimo}–{maximo}), intenta de nuevo")
        except ValueError:
            print("  ! ingresa un número válido")


def _pedir_opcion_c(prompt, opciones: list) -> str:
    """Como _pedir_opcion pero '0' o Enter vacío cancela el registro completo."""
    for i, op in enumerate(opciones, 1):
        print(f"  [{i}] {op}")
    print(f"  [0] cancelar registro")
    while True:
        raw = input(f"  {prompt}: ").strip()
        if raw == "" or raw == "0":
            raise _Cancelado()
        try:
            idx = int(raw)
            if 1 <= idx <= len(opciones):
                return opciones[idx - 1]
            print(f"  ! elige entre 0 y {len(opciones)}")
        except ValueError:
            print("  ! ingresa el número de la opción")

# ── REGISTRO DE BATCH ─────────────────────────────────────────────────────────

def registrar_batch():
    """Flujo completo de captura de un batch. Cancelable con Enter en cualquier paso."""

    cfg       = cargar_config()
    provs     = cargar_proveedores()
    fecha     = fecha_hoy()
    batch_id  = siguiente_batch_id(fecha)
    hora      = datetime.now().strftime("%H:%M")

    _titulo(f"REGISTRO DE BATCH — batch {batch_id}  |  {hora}")
    print("  [Enter en cualquier paso = cancelar el registro]")

    try:
        # ── proveedor ────────────────────────────────────────────────────
        print("\n  PROVEEDOR")
        claves  = list(provs.keys()) + ["mixto"]
        nombres = [provs[k].nombre if k in provs else "Mixto" for k in claves]
        for i, (cl, nm) in enumerate(zip(claves, nombres), 1):
            costo = f"  ${provs[cl].costo_kg}/kg" if cl in provs else ""
            print(f"  [{i}] {nm}{costo}")
        print(f"  [0] cancelar registro")

        while True:
            raw = input("  proveedor: ").strip()
            if raw == "" or raw == "0":
                raise _Cancelado()
            try:
                idx = int(raw)
                if 1 <= idx <= len(claves):
                    proveedor = claves[idx - 1]
                    break
                print(f"  ! elige entre 0 y {len(claves)}")
            except ValueError:
                print("  ! ingresa el número")

        # costo del proveedor — puede haber cambiado hoy
        if proveedor in provs:
            costo_default = provs[proveedor].costo_kg
            print(f"\n  costo registrado: ${costo_default}/kg")
            if _confirmar("  ¿cambió el precio hoy?"):
                costo_kg = _pedir_float_c("  nuevo costo $/kg", 10.0, 100.0)
                provs[proveedor].costo_kg = costo_kg
                from config import guardar_proveedores
                guardar_proveedores(provs)
                print(f"  ✓ precio actualizado a ${costo_kg}/kg")
            else:
                costo_kg = costo_default
        else:
            costo_kg = _pedir_float_c("  costo $/kg de la grasa", 10.0, 100.0)

        # ── condiciones ──────────────────────────────────────────────────
        print("\n  CONDICIONES")
        print("  temperatura de entrada de la grasa:")
        temp_entrada = _pedir_opcion_c("  temp", ["congelada", "fria", "ambiente"])

        print("  composición del lote:")
        composicion = _pedir_opcion_c("  composición", ["tejido", "grasa", "mixto"])

        operador = input("  operador (Enter = yo): ").strip() or "yo"

        # ── mediciones de báscula ────────────────────────────────────────
        print("\n  MEDICIONES DE BÁSCULA")
        kg_grasa = _pedir_float_c("  kg grasa entrada (báscula ANTES)", 1.0, 200.0)
        kg_chi   = _pedir_float_c("  kg chicharrón salida (báscula DESPUÉS)", 0.1, 100.0)

        # ── observaciones ────────────────────────────────────────────────
        obs = input("\n  observaciones (Enter para omitir): ").strip()

    except _Cancelado:
        print("\n  ✗ registro cancelado — nada se guardó\n")
        return None
    # ── construir batch ──────────────────────────────────────────────
    batch = Batch(
        id           = batch_id,
        fecha        = fecha,
        hora         = hora,
        proveedor    = proveedor,
        costo_kg     = costo_kg,
        temp_entrada = temp_entrada,
        composicion  = composicion,
        operador     = operador,
        kg_grasa     = kg_grasa,
        kg_chi       = kg_chi,
        observaciones= obs,
    )

    # ── vista previa con cálculos ────────────────────────────────────
    batches_dia = cargar_batches(fecha)
    n_batches   = len(batches_dia) + 1   # incluye el actual

    r = calcular_batch(batch, cfg, n_batches)

    _titulo(f"RESUMEN BATCH #{batch_id}")
    print(f"  grasa entrada : {kg_grasa} kg")
    print(f"  chicharrón    : {kg_chi} kg  ({r.rend_chi_pct:.1f}%)")
    print(f"  manteca       : {r.kg_mant:.3f} kg  /  {r.lt_mant:.2f} lt  ({r.rend_mant_pct:.1f}%)")
    print(f"  merma         : {r.merma_kg:.3f} kg  ({r.merma_pct:.1f}%)")
    print(f"  ─────────────────────────────")
    print(f"  costo batch   : ${r.c_batch:.2f}")
    print(f"  costo/kg chi  : ${r.c_chi_unit:.2f}/kg")
    print(f"  costo real/kg : ${r.costo_real_kg_chi:.2f}/kg  (métrica proveedor)")

    # acumulado del día
    if batches_dia:
        from calcular import calcular_dia
        todos = batches_dia + [batch]
        rd = calcular_dia(todos, cfg)
        print(f"\n  ACUMULADO DÍA ({len(todos)} batches)")
        print(f"  chicharrón total : {rd.kg_chi_dia} kg")
        print(f"  manteca total    : {rd.kg_mant_dia:.3f} kg  /  {rd.lt_mant_dia:.2f} lt")
        print(f"  utilidad día     : ${rd.utilidad:.2f}")

    # ── confirmar y guardar ──────────────────────────────────────────
    print()
    if _confirmar("  ¿guardar este batch?"):
        guardar_batch(batch)
        print(f"\n  ✓ batch #{batch_id} guardado\n")
        return batch
    else:
        print("\n  ✗ batch descartado\n")
        return None


# ── MENÚ DE REGISTRO DEL DÍA ─────────────────────────────────────────────────

def menu_registro():
    """Muestra los batches del día y permite agregar más."""
    while True:
        fecha   = fecha_hoy()
        batches = cargar_batches(fecha)

        _titulo(f"REGISTRO DEL DÍA — {fecha}")

        if batches:
            cfg = cargar_config()
            from calcular import calcular_dia
            rd = calcular_dia(batches, cfg)
            print(f"  batches registrados : {len(batches)}")
            print(f"  chicharrón total    : {rd.kg_chi_dia} kg")
            print(f"  manteca total       : {rd.kg_mant_dia:.3f} kg")
            print(f"  utilidad día        : ${rd.utilidad:.2f}")
            print()
            for b in batches:
                print(f"  #{b.id}  {b.hora}  {b.proveedor}  "
                      f"{b.kg_grasa}kg→{b.kg_chi}kg chi  [{b.temp_entrada}]")
        else:
            print("  sin batches registrados hoy")

        print()
        print("  [1] registrar batch")
        print("  [2] volver al menú principal")

        op = input("  opción: ").strip()
        if op == "1":
            registrar_batch()
        elif op == "2":
            break
        else:
            print("  ! opción inválida")


if __name__ == "__main__":
    menu_registro()
