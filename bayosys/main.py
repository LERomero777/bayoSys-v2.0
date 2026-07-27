#!/usr/bin/env python3
"""
main.py — bayoSys · Productos El Bayo
Punto de entrada único. Navega todos los módulos desde aquí.
Uso: python3 main.py

CAMBIOS:
  - [7] Administración — alta/edición/baja de productos del catálogo POS
    sin tocar código. El catálogo vive 100% en pos_db.py (SQLite).
  - Cancelación explícita en cada paso del wizard admin (Enter o 0 = salir)
  - El registro de batches, POS y guardian NO se modifican.
"""

import os
from config import (
    cargar_config, guardar_config,
    cargar_batches, cargar_proveedores, guardar_proveedores,
    fecha_hoy
)
from calcular import calcular_dia
from registro import menu_registro, registrar_batch
from cierre import menu_cierre, cargar_cierre
from analisis import menu_analisis
from tui import iniciar_tui
from pos_db import init_db, calcular_corte, guardar_corte
from guardian import verificar_integridad
from nuke import factory_reset

# ── HELPERS GENERALES ─────────────────────────────────────────────────────────

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


# ── HELPERS CANCELABLES — solo para wizards del menú admin ───────────────────
# Todo paso del wizard de catálogo puede abortarse con Enter o '0'.
# Se usa una excepción interna para desenrollar el flujo limpio hasta
# el punto de entrada de la operación, sin dejar la conexión SQLite a medias.

class _Cancelado(Exception):
    """Señal interna — el usuario abortó el wizard a la mitad."""
    pass


def _pedir_texto_c(prompt, min_len=1, max_len=30) -> str:
    while True:
        val = input(f"  {prompt}  [Enter=cancelar]: ").strip()
        if val == "":
            raise _Cancelado()
        if min_len <= len(val) <= max_len:
            return val
        print(f"  ! largo válido: {min_len}–{max_len} caracteres")


def _pedir_opcion_c(prompt, opciones: list) -> str:
    for i, op in enumerate(opciones, 1):
        print(f"  [{i}] {op}")
    print(f"  [0] cancelar")
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


def _pedir_float_c(prompt, minimo, maximo) -> float:
    while True:
        raw = input(f"  {prompt}  [Enter=cancelar]: ").strip()
        if raw == "":
            raise _Cancelado()
        try:
            val = float(raw)
            if minimo <= val <= maximo:
                return val
            print(f"  ! rango válido: {minimo}–{maximo}")
        except ValueError:
            print("  ! ingresa un número")


def _pedir_sku_c(prompt) -> str:
    """SKU en mayúsculas, cancelable con Enter."""
    val = input(f"  {prompt}  [Enter=cancelar]: ").strip().upper()
    if val == "":
        raise _Cancelado()
    return val


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
        from calcular import calcular_ing_manteca_real, calcular_ing_chi_real
        ing_chi   = calcular_ing_chi_real(cierre, cfg)["ing_chi_real"]
        mr        = calcular_ing_manteca_real(cierre, cfg)
        ing_total = ing_chi + mr["ing_mant_real"]
        utilidad  = ing_total - rd.c_total_dia
        estado += (f"  util:${utilidad:,.0f}"
                   f"  lit:{cierre.stock_litreada_lt:.1f}lt"
                   f"  cub:{cierre.stock_cubetas:.1f}  [cerrado]")
    else:
        estado += f"  costo:${rd.c_total_dia:,.0f}  [sin cierre]"

    return estado


# ── BATCH ACTIVO ──────────────────────────────────────────────────────────────

def _ruta_batch_activo() -> str:
    base = os.path.join(os.path.expanduser("~"), "bayosys", "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "batch_activo.txt")

def get_batch_activo() -> str | None:
    """Retorna el batch_id activo del POS, o None si no hay ninguno."""
    ruta = _ruta_batch_activo()
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta) as f:
            return f.read().strip()
    except Exception:
        return None

def set_batch_activo(batch_id: str | None):
    ruta = _ruta_batch_activo()
    if batch_id is None:
        if os.path.exists(ruta):
            os.remove(ruta)
    else:
        with open(ruta, "w") as f:
            f.write(str(batch_id))

def hay_corte_pendiente(batch_id: str) -> bool:
    from pos_db import tiene_corte_guardado
    corte = calcular_corte(batch_id=batch_id)
    if corte["n_tickets"] == 0:
        return False
    return not tiene_corte_guardado(batch_id)

def hacer_corte_entre_batches(batch_id: str):
    print()
    _sep("─")
    print(f"  CORTE DE CAJA — batch #{batch_id}")
    _sep("─")
    corte = calcular_corte(batch_id=batch_id)
    print(f"  tickets del turno : {corte['n_tickets']}")
    print(f"  ventas efectivo   : ${corte['ventas_efectivo']:,.2f}")
    print(f"  ventas transfer   : ${corte['ventas_transfer']:,.2f}")
    print(f"  ventas tarjeta    : ${corte['ventas_tarjeta']:,.2f}")
    print(f"  ─────────────────────────────────────")
    print(f"  TOTAL VENTAS      : ${corte['total_ventas']:,.2f}")
    print(f"  gastos del turno  : ${corte['total_gastos']:,.2f}")
    print(f"  ─────────────────────────────────────")
    print(f"  NETO              : ${corte['neto']:,.2f}")
    print(f"  fondo de caja     : ${corte['fondo_caja']:,.2f}")
    print(f"  A ENTREGAR        : ${corte['a_entregar']:,.2f}")
    print()
    if _confirmar("  ¿confirmar corte?"):
        corte_id = guardar_corte(batch_id, corte)
        print(f"\n  ✓ corte #{corte_id} guardado\n")
        return True
    else:
        print("\n  ! corte no confirmado — las ventas siguen en el sistema\n")
        return False


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


# ── MENÚ DE ADMINISTRACIÓN — CATÁLOGO DE PRODUCTOS ───────────────────────────
# Todo el catálogo del POS vive en pos_db.py (SQLite).
# Este menú es la única puerta para tocarlo sin editar código.
# Cada paso es cancelable con Enter o 0 — nunca se queda atrapado a la mitad.

def _mostrar_catalogo():
    from pos_db import get_inventario
    inv = get_inventario()
    print()
    print(f"  {'SKU':<8} {'Descripción':<20} {'Tipo':<10} {'Origen':<12} "
          f"{'Menú':>5} {'Orden':>6} {'Stock':>8} {'Precio':>9}  Estado")
    _sep("─", 78)
    for row in inv:
        estado = "activo" if row["activo"] else "INACTIVO"
        print(f"  {row['sku']:<8} {row['descripcion']:<20} "
              f"{row['tipo_venta']:<10} {row['origen']:<12} "
              f"{'sí' if row['es_menu'] else 'no':>5} "
              f"{row['orden_menu']:>6} "
              f"{row['stock']:>8.2f} "
              f"${row['precio_venta']:>8.2f}  {estado}")
    print()


def _alta_producto():
    """
    Wizard de alta — cada paso cancelable con Enter.
    Si se cancela a la mitad, no se escribe nada en la DB.
    """
    from pos_db import agregar_sku_catalogo, get_sku

    print()
    _sep("─")
    print("  ALTA DE PRODUCTO NUEVO   [Enter en cualquier paso = cancelar]")
    _sep("─")

    try:
        sku = _pedir_sku_c("SKU (código corto, ej: TORT)")

        if get_sku(sku):
            print(f"  ! el SKU '{sku}' ya existe — usa [2] editar en su lugar")
            if not _confirmar("  ¿deseas sobreescribirlo de todos modos?"):
                print("\n  cancelado — nada se modificó\n")
                return

        descripcion = _pedir_texto_c("descripción (ej: Tortillas)", 1, 30)
        unidad      = _pedir_opcion_c("unidad de venta", ["pza", "kg"])

        print("\n  tipo de venta:")
        print("    normal   → cantidad entera, precio fijo (ej: chorizo)")
        print("    peso     → cantidad decimal en kg (ej: chicharrón)")
        print("    variable → precio se negocia cada vez (ej: cubeta)")
        print("    libre    → descripción y precio libres")
        tipo_venta = _pedir_opcion_c("tipo de venta",
                                      ["normal", "peso", "variable", "libre"])

        precio = _pedir_float_c(f"precio de venta $/{unidad}", 0, 9999)

        print("\n  origen del stock:")
        print("    manual     → se carga a mano desde inventario o admin")
        print("    produccion → se carga automático desde registro de batch")
        print("    apertura   → se carga al abrir cubeta")
        origen = _pedir_opcion_c("origen del stock",
                                  ["manual", "produccion", "apertura"])

        orden = int(_pedir_float_c("posición en menú POS (1-9, 9=al final)", 1, 9))

    except _Cancelado:
        print("\n  ✗ alta cancelada — nada se guardó\n")
        return

    print()
    _sep("─")
    print(f"  SKU:         {sku}")
    print(f"  descripción: {descripcion}")
    print(f"  unidad:      {unidad}")
    print(f"  tipo_venta:  {tipo_venta}")
    print(f"  precio:      ${precio:.2f}")
    print(f"  origen:      {origen}")
    print(f"  orden menú:  [{orden}]")
    _sep("─")

    if not _confirmar("  ¿guardar este producto?"):
        print("\n  ✗ alta cancelada — nada se guardó\n")
        return

    creado = agregar_sku_catalogo(
        sku=sku, descripcion=descripcion, unidad=unidad,
        precio=precio, tipo_venta=tipo_venta, origen=origen,
        es_menu=1, orden_menu=orden
    )

    if creado:
        print(f"\n  ✓ producto '{sku}' creado — aparecerá en el POS en tecla [{orden}]")
    else:
        print(f"\n  ✓ producto '{sku}' actualizado")


def _editar_producto():
    from pos_db import get_sku, editar_sku

    print()
    try:
        sku = _pedir_sku_c("SKU a editar")
    except _Cancelado:
        print("\n  cancelado\n")
        return

    row = get_sku(sku)
    if not row:
        print(f"  ! SKU '{sku}' no existe")
        return

    print(f"\n  editando: {row['descripcion']}  (${row['precio_venta']:.2f})")
    print("  Enter para dejar sin cambios en cada campo — Enter en todos = cancelar\n")

    cambios = {}

    nueva_desc = input(f"  descripción [{row['descripcion']}]: ").strip()
    if nueva_desc:
        cambios["descripcion"] = nueva_desc

    nuevo_precio = input(f"  precio [{row['precio_venta']:.2f}]: ").strip()
    if nuevo_precio:
        try:
            cambios["precio_venta"] = float(nuevo_precio)
        except ValueError:
            print("  ! precio inválido, se ignora")

    nuevo_orden = input(f"  orden menú [{row['orden_menu']}]: ").strip()
    if nuevo_orden:
        try:
            cambios["orden_menu"] = int(nuevo_orden)
        except ValueError:
            print("  ! orden inválido, se ignora")

    if not cambios:
        print("\n  sin cambios — cancelado")
        return

    print()
    for k, v in cambios.items():
        print(f"  {k}: → {v}")
    if not _confirmar("  ¿aplicar estos cambios?"):
        print("\n  ✗ cancelado — nada se modificó\n")
        return

    editar_sku(sku, **cambios)
    print(f"\n  ✓ '{sku}' actualizado: {list(cambios.keys())}")


def _baja_producto():
    from pos_db import get_sku, desactivar_sku

    print()
    try:
        sku = _pedir_sku_c("SKU a desactivar")
    except _Cancelado:
        print("\n  cancelado\n")
        return

    row = get_sku(sku)
    if not row:
        print(f"  ! SKU '{sku}' no existe")
        return

    print(f"\n  producto: {row['descripcion']}  stock actual: {row['stock']}")
    if not _confirmar(f"  ¿desactivar '{sku}'? (deja de aparecer en POS, historial se conserva)"):
        print("\n  cancelado\n")
        return

    try:
        desactivar_sku(sku)
        print(f"\n  ✓ '{sku}' desactivado — ya no aparece en el POS")
    except ValueError as e:
        print(f"\n  ! {e}")


def _reactivar_producto():
    from pos_db import get_sku, reactivar_sku

    print()
    try:
        sku = _pedir_sku_c("SKU a reactivar")
    except _Cancelado:
        print("\n  cancelado\n")
        return

    row = get_sku(sku)
    if not row:
        print(f"  ! SKU '{sku}' no existe")
        return

    if not _confirmar(f"  ¿reactivar '{row['descripcion']}'?"):
        print("\n  cancelado\n")
        return

    reactivar_sku(sku)
    print(f"\n  ✓ '{sku}' reactivado — vuelve a aparecer en el POS")


def menu_administracion():
    while True:
        _limpiar()
        _sep()
        print("  ADMINISTRACIÓN — Catálogo de productos POS")
        _sep()
        _mostrar_catalogo()
        _sep("─")
        print("  [1] alta de producto nuevo")
        print("  [2] editar producto")
        print("  [3] desactivar producto")
        print("  [4] reactivar producto")
        print("  [5] volver")
        print()

        op = input("  opción: ").strip()

        if op == "1":
            _alta_producto()
            input("\n  Enter para continuar...")
        elif op == "2":
            _editar_producto()
            input("\n  Enter para continuar...")
        elif op == "3":
            _baja_producto()
            input("\n  Enter para continuar...")
        elif op == "4":
            _reactivar_producto()
            input("\n  Enter para continuar...")
        elif op == "5":
            break
        else:
            print("  ! opción inválida")
            input("  Enter para continuar...")


# ── FLUJO REGISTRO BATCH CON CORTE ───────────────────────────────────────────

def flujo_registrar_batch():
    """
    Registra un batch nuevo.
    Si hay un batch activo con ventas sin corte, solicita el corte primero.
    Al guardar el batch, pregunta si abrir el POS para ese batch.
    """
    batch_activo = get_batch_activo()

    if batch_activo and hay_corte_pendiente(batch_activo):
        print()
        _sep("─")
        print(f"  ! hay ventas sin corte del batch #{batch_activo}")
        print(f"    debes hacer el corte antes de registrar un nuevo batch")
        _sep("─")
        hacer_corte_entre_batches(batch_activo)
        set_batch_activo(None)

    batch = registrar_batch()

    if batch is None:
        return

    try:
        from pos import cargar_produccion
        res = cargar_produccion(batch, cargar_config())
        print(f"  ✓ inventario POS: {res['mensaje']}")
        if res["cubetas_emitidas"] > 0:
            print(f"    {res['pool_antes']:.2f}lt acumulados + {res['lt_ingresados']:.2f}lt "
                  f"del batch → {res['cubetas_emitidas']:.0f} cubeta"
                  f"{'s' if res['cubetas_emitidas'] != 1 else ''} completa"
                  f"{'s' if res['cubetas_emitidas'] != 1 else ''} a bodega")
        else:
            print(f"    {res['pool_despues']:.2f}lt a granel — aún no completa una cubeta")
    except Exception as e:
        print(f"  ! error cargando producción al POS: {e}")

    print()
    if _confirmar(f"  ¿abrir el POS para el batch #{batch.id}?"):
        set_batch_activo(batch.id)
        _abrir_pos(batch.id)
    else:
        print(f"  POS no abierto — puedes abrirlo desde [2] del menú principal\n")


# ── ABRIR POS ─────────────────────────────────────────────────────────────────

def _abrir_pos(batch_id: str):
    """Abre el POS curses para el batch indicado."""
    try:
        from pos_tui import iniciar_pos_tui
        set_batch_activo(batch_id)
        iniciar_pos_tui(batch_id=batch_id, operador="Luis")
    except Exception as e:
        print(f"\n  ! error al abrir el POS: {e}\n")
        input("  Enter para continuar...")


# ── MENÚ PRINCIPAL ───────────────────────────────────────────────────────────

def main():
    init_db()
    verificar_integridad()   # ← audita días anteriores antes del menú

    while True:

        _limpiar()
        fecha        = fecha_hoy()
        estado       = _estado_hoy()
        batch_activo = get_batch_activo()

        _sep()
        print("  bayoSys · Productos El Bayo")
        _sep("─")
        print(f"  {fecha}  |  {estado}")
        if batch_activo:
            print(f"  POS activo: batch #{batch_activo}")
        _sep()
        print()
        print("  [1] registrar batch")
        print("  [2] POS — punto de venta")
        print("  [3] cierre del día")
        print("  [4] análisis y reportes")
        print("  [5] simulador de escenarios")
        print("  [6] configuración")
        print("  [7] administración — catálogo POS")
        print("  [8] factory reset")
        print()
        print("  [0] salir")
        print()

        op = input("  opción: ").strip()

        if op == "1":
            _limpiar()
            flujo_registrar_batch()

        elif op == "2":
            _limpiar()
            batch_activo = get_batch_activo()
            if batch_activo:
                _abrir_pos(batch_activo)
            else:
                fecha   = fecha_hoy()
                batches = cargar_batches(fecha)
                if not batches:
                    print("\n  ! no hay batches registrados hoy")
                    print("  registra un batch primero\n")
                    input("  Enter para continuar...")
                else:
                    cfg = cargar_config()
                    from calcular import calcular_batch
                    print("\n  batches de hoy:")
                    for i, b in enumerate(batches, 1):
                        r = calcular_batch(b, cfg, len(batches))
                        print(f"  [{i}] {b.id}  {b.hora}  {b.proveedor}")
                        print(f"       chi:{b.kg_chi}kg  "
                              f"mant:{r.kg_mant:.2f}kg/{r.lt_mant:.2f}lt  "
                              f"rend:{r.rend_chi_pct:.1f}%")
                    print()
                    try:
                        idx = int(input("  ¿qué batch? [número]: ").strip())
                        if 1 <= idx <= len(batches):
                            _abrir_pos(batches[idx - 1].id)
                        else:
                            print("  ! opción fuera de rango")
                            input("  Enter para continuar...")
                    except ValueError:
                        print("  ! ingresa un número")
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

        elif op == "7":
            menu_administracion()

        elif op == "8":
            factory_reset()

        elif op == "0":
            print("\n  hasta luego\n")
            break

        else:
            print("  ! opción inválida")
            input("  Enter para continuar...")


if __name__ == "__main__":
    main()
