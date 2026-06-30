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
from registro import menu_registro, registrar_batch
from cierre import menu_cierre, cargar_cierre
from analisis import menu_analisis
from tui import iniciar_tui
from pos_db import init_db, calcular_corte, guardar_corte
from guardian import verificar_integridad

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


# ── BATCH ACTIVO ──────────────────────────────────────────────────────────────
# Persiste el batch_id activo del POS en un archivo simple
# para sobrevivir reinicios del sistema

def _ruta_batch_activo() -> str:
    import os
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
    """Guarda o limpia el batch_id activo."""
    ruta = _ruta_batch_activo()
    if batch_id is None:
        if os.path.exists(ruta):
            os.remove(ruta)
    else:
        with open(ruta, "w") as f:
            f.write(str(batch_id))

def hay_corte_pendiente(batch_id: str) -> bool:
    """
    True si el batch tiene ventas registradas Y no tiene corte guardado.
    Evita falso positivo cuando ya se cortó pero hubo más ventas después.
    """
    from pos_db import tiene_corte_guardado
    corte = calcular_corte(batch_id=batch_id)
    if corte["n_tickets"] == 0:
        return False
    return not tiene_corte_guardado(batch_id)

def hacer_corte_entre_batches(batch_id: int):
    """Muestra resumen y guarda el corte del batch anterior."""
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


# ── FLUJO REGISTRO BATCH CON CORTE ───────────────────────────────────────────

def flujo_registrar_batch():
    """
    Registra un batch nuevo.
    Si hay un batch activo con ventas sin corte, solicita el corte primero.
    Al guardar el batch, pregunta si abrir el POS para ese batch.
    """
    batch_activo = get_batch_activo()

    # si hay batch activo con ventas pendientes de corte
    if batch_activo and hay_corte_pendiente(batch_activo):
        print()
        _sep("─")
        print(f"  ! hay ventas sin corte del batch #{batch_activo}")
        print(f"    debes hacer el corte antes de registrar un nuevo batch")
        _sep("─")
        hacer_corte_entre_batches(batch_activo)
        set_batch_activo(None)

    # registrar el batch nuevo
    batch = registrar_batch()

    if batch is None:
        return  # batch descartado

    # cargar kg_chi del batch al inventario del POS
    try:
        from pos_db import actualizar_stock
        actualizar_stock(
            sku        = "CHI",
            delta      = batch.kg_chi,
            tipo       = "produccion",
            referencia = f"batch#{batch.id}",
            nota       = f"{batch.kg_chi}kg — {batch.proveedor}"
        )
        print(f"  ✓ {batch.kg_chi}kg de chicharrón cargados al inventario POS")
    except Exception as e:
        print(f"  ! error cargando stock CHI: {e}")

    # preguntar si abrir el POS para este batch
   
    print()
    if _confirmar(f"  ¿abrir el POS para el batch #{batch.id}?"):
        set_batch_activo(batch.id)
        _abrir_pos(batch.id)
    else:
        print(f"  POS no abierto — puedes abrirlo desde [2] del menú principal\n")


# ── ABRIR POS ─────────────────────────────────────────────────────────────────

def _abrir_pos(batch_id: int):
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
        fecha       = fecha_hoy()
        estado      = _estado_hoy()
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
                # no hay batch activo — preguntar cuál usar
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

        elif op == "0":
            print("\n  hasta luego\n")
            break

        else:
            print("  ! opción inválida")
            input("  Enter para continuar...")


if __name__ == "__main__":
    main()