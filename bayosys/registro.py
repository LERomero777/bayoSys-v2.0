"""
registro.py — bayoSys · Productos El Bayo
Captura interactiva de un batch al terminar el fondeo.
Sin curses — usa pedir() simple para máxima compatibilidad.
"""

from datetime import datetime
from config import (
    cargar_config, cargar_proveedores,
    guardar_batch, cargar_batches,
    fecha_hoy, siguiente_batch_id
)
from calcular import calcular_batch
from estilos import (
    titulo_txt, sep_txt, ok_txt, alerta_txt, aviso_txt,
    opcion_txt, dato_txt, c, imprimir, pedir,
)
from models import Batch


# ── CANCELACIÓN ───────────────────────────────────────────────────────────────
# Enter vacío en cualquier paso del registro = cancelar todo el flujo.
# Mismo patrón que el wizard de administración en main.py.

class _Cancelado(Exception):
    """Señal interna — el operador abortó el registro a la mitad."""
    pass

# ── HELPERS DE INPUT ─────────────────────────────────────────────────────────

def _limpiar():
    imprimir("\n" * 2)


def _titulo(texto):
    imprimir()
    imprimir(titulo_txt(texto, 54))

def _pedir_float(prompt, minimo=0.0, maximo=999.0) -> float:
    while True:
        try:
            val = float(pedir(f"  {prompt}: ").strip())
            if minimo <= val <= maximo:
                return val
            imprimir(alerta_txt(f"valor fuera de rango ({minimo}–{maximo}), intenta de nuevo"))
        except ValueError:
            imprimir(alerta_txt("ingresa un número válido"))


def _pedir_opcion(prompt, opciones: list) -> str:
    """Muestra opciones numeradas y devuelve la seleccionada."""
    for i, op in enumerate(opciones, 1):
        imprimir(f"  [{i}] {op}")
    while True:
        try:
            idx = int(pedir(f"  {prompt}: ").strip())
            if 1 <= idx <= len(opciones):
                return opciones[idx - 1]
            imprimir(alerta_txt(f"elige entre 1 y {len(opciones)}"))
        except ValueError:
            imprimir(alerta_txt("ingresa el número de la opción"))

def _confirmar(prompt) -> bool:
    resp = pedir(f"  {prompt} [s/n]: ").strip().lower()
    return resp in ("s", "si", "sí", "y", "yes", "a huevo")

def _pedir_float_c(prompt, minimo=0.0, maximo=999.0) -> float:
    """Como _pedir_float pero Enter vacío cancela el registro completo."""
    while True:
        raw = pedir(f"  {prompt}  [Enter=cancelar]: ").strip()
        if raw == "":
            raise _Cancelado()
        try:
            val = float(raw)
            if minimo <= val <= maximo:
                return val
            imprimir(alerta_txt(f"valor fuera de rango ({minimo}–{maximo}), intenta de nuevo"))
        except ValueError:
            imprimir(alerta_txt("ingresa un número válido"))

def _pedir_float_opcional(prompt, minimo=0.0, maximo=999.0) -> float:
    """Como _pedir_float pero Enter vacío devuelve 0.0 (omitir → se deriva), sin cancelar el registro."""
    while True:
        raw = pedir(f"  {prompt}  [Enter = usar derivado]: ").strip()
        if raw == "":
            return 0.0
        try:
            val = float(raw)
            if minimo <= val <= maximo:
                return val
            imprimir(alerta_txt(f"valor fuera del límite físico posible (máx {maximo:.3f} kg), intenta de nuevo"))
        except ValueError:
            imprimir(alerta_txt("ingresa un número válido"))

def _pedir_opcion_c(prompt, opciones: list) -> str:
    """Como _pedir_opcion pero '0' o Enter vacío cancela el registro completo."""
    for i, op in enumerate(opciones, 1):
        imprimir(f"  [{i}] {op}")
    imprimir(opcion_txt("0", "cancelar registro"))
    while True:
        raw = pedir(f"  {prompt}: ").strip()
        if raw == "" or raw == "0":
            raise _Cancelado()
        try:
            idx = int(raw)
            if 1 <= idx <= len(opciones):
                return opciones[idx - 1]
            imprimir(alerta_txt(f"elige entre 0 y {len(opciones)}"))
        except ValueError:
            imprimir(alerta_txt("ingresa el número de la opción"))

# ── REGISTRO DE BATCH ─────────────────────────────────────────────────────────

def registrar_batch():
    """Flujo completo de captura de un batch. Cancelable con Enter en cualquier paso."""

    cfg       = cargar_config()
    provs     = cargar_proveedores()
    fecha     = fecha_hoy()
    batch_id  = siguiente_batch_id(fecha)
    hora      = datetime.now().strftime("%H:%M")

    _titulo(f"registro de batch {batch_id}  |  {hora}")
    imprimir("  [Enter en cualquier paso = cancelar el registro]")

    try:
        # ── proveedor ────────────────────────────────────────────────────
        imprimir("\n  PROVEEDOR")
        claves  = list(provs.keys()) + ["mixto"]
        nombres = [provs[k].nombre if k in provs else "Mixto" for k in claves]
        for i, (cl, nm) in enumerate(zip(claves, nombres), 1):
            costo = f"  ${provs[cl].costo_kg}/kg" if cl in provs else ""
            imprimir(f"  [{i}] {nm}{costo}")
        imprimir(opcion_txt("0", "cancelar registro"))

        while True:
            raw = pedir("  proveedor: ").strip()
            if raw == "" or raw == "0":
                raise _Cancelado()
            try:
                idx = int(raw)
                if 1 <= idx <= len(claves):
                    proveedor = claves[idx - 1]
                    break
                imprimir(alerta_txt(f"elige entre 0 y {len(claves)}"))
            except ValueError:
                imprimir(alerta_txt("ingresa el número"))

        # costo del proveedor — puede haber cambiado hoy
        if proveedor in provs:
            costo_default = provs[proveedor].costo_kg
            imprimir(f"\n  costo registrado: ${costo_default}/kg")
            if _confirmar("  ¿cambió el precio hoy?"):
                costo_kg = _pedir_float_c("  nuevo costo $/kg", 10.0, 100.0)
                provs[proveedor].costo_kg = costo_kg
                from config import guardar_proveedores
                guardar_proveedores(provs)
                imprimir(ok_txt(f"precio actualizado a ${costo_kg}/kg"))
            else:
                costo_kg = costo_default
        else:
            costo_kg = _pedir_float_c("  costo $/kg de la grasa", 10.0, 100.0)

        # ── condiciones ──────────────────────────────────────────────────
        imprimir("\n  CONDICIONES")
        imprimir("  temperatura de entrada de la grasa:")
        temp_entrada = _pedir_opcion_c("  temp", ["congelada", "fria", "ambiente"])

        imprimir("  composición del lote:")
        composicion = _pedir_opcion_c("  composición", ["tejido", "grasa", "mixto"])

        operador = pedir("  operador (Enter = yo): ").strip() or "yo"

        # ── tiempos de coccion ────────────────────────────────────────
        imprimir("\n  TIEMPOS DE COCCIÒN")
        hora_inicio = pedir("  hora inicio cocciòn (HH:MM, Enter = omitir): ").strip()
        hora_fin    = pedir("  hora fin cocciòn (HH:MM, Enter = omitir): ").strip()
        # ── mediciones de báscula ────────────────────────────────────────
        imprimir("\n  MEDICIONES DE BÁSCULA")
        kg_grasa = _pedir_float_c("  kg grasa entrada (báscula ANTES)", 1.0, 200.0)
        kg_chi   = _pedir_float_c("  kg chicharrón salida (báscula DESPUÉS)", 0.1, 100.0)
        # ── manteca  ───────────────────────────────────────────────────── 
        imprimir("\n  MANTECA")
        maximo_mant = round(kg_grasa - kg_chi, 3)
        kg_mant_real = _pedir_float_opcional( "  kg manteca real pesada", 0.0, maximo_mant)
        # ── observaciones ────────────────────────────────────────────────
        obs = pedir("\n  observaciones (Enter para omitir): ").strip()

    except _Cancelado:
        imprimir("\n  ✗ registro cancelado — nada se guardó\n")
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
        hora_inicio  = hora_inicio,
        hora_fin     = hora_fin,
        kg_mant_real = kg_mant_real,
    )

    # ── vista previa con cálculos ────────────────────────────────────
    batches_dia = cargar_batches(fecha)
    n_batches   = len(batches_dia) + 1   # incluye el actual

    r = calcular_batch(batch, cfg, n_batches)

    _titulo(f"RESUMEN BATCH #{batch_id}")
    imprimir(f"  grasa entrada : {kg_grasa} kg")
    imprimir(f"  chicharrón    : {kg_chi} kg  ({r.rend_chi_pct:.1f}%)")
    imprimir(f"  manteca       : {r.kg_mant:.3f} kg  /  {r.lt_mant:.2f} lt  ({r.rend_mant_pct:.1f}%)")
    imprimir(f"  merma         : {r.merma_kg:.3f} kg  ({r.merma_pct:.1f}%)")
    imprimir(f"  ─────────────────────────────")
    imprimir(f"  costo batch   : ${r.c_batch:.2f}")
    imprimir(f"  costo/kg chi  : ${r.c_chi_unit:.2f}/kg")
    imprimir(f"  costo real/kg : ${r.costo_real_kg_chi:.2f}/kg  (métrica proveedor)")

    # acumulado del día
    if batches_dia:
        from calcular import calcular_dia
        todos = batches_dia + [batch]
        rd = calcular_dia(todos, cfg)
        imprimir(f"\n  ACUMULADO DÍA ({len(todos)} batches)")
        imprimir(f"  chicharrón total : {rd.kg_chi_dia} kg")
        imprimir(f"  manteca total    : {rd.kg_mant_dia:.3f} kg  /  {rd.lt_mant_dia:.2f} lt")
        imprimir(f"  utilidad día     : ${rd.utilidad:.2f}")

    # ── confirmar y guardar ──────────────────────────────────────────
    imprimir()
    if _confirmar("  ¿guardar este batch?"):
        guardar_batch(batch)
        imprimir("\n" + ok_txt(f"batch #{batch_id} guardado") + "\n")
        return batch
    else:
        imprimir("\n  ✗ batch descartado\n")
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
            imprimir(f"  batches registrados : {len(batches)}")
            imprimir(f"  chicharrón total    : {rd.kg_chi_dia} kg")
            imprimir(f"  manteca total       : {rd.kg_mant_dia:.3f} kg")
            imprimir(f"  utilidad día        : ${rd.utilidad:.2f}")
            imprimir()
            for b in batches:
                imprimir(f"  #{b.id}  {b.hora}  {b.proveedor}  "
                      f"{b.kg_grasa}kg→{b.kg_chi}kg chi  [{b.temp_entrada}]")
        else:
            imprimir("  sin batches registrados hoy")

        imprimir()
        imprimir(opcion_txt("1", "registrar batch"))
        imprimir(opcion_txt("2", "volver al menú principal"))

        op = pedir("  opción: ").strip()
        if op == "1":
            registrar_batch()
        elif op == "2":
            break
        else:
            imprimir(alerta_txt("opción inválida"))


if __name__ == "__main__":
    menu_registro()
