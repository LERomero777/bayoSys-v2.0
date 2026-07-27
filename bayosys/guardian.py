"""
guardian.py — bayoSys · Productos El Bayo
Auditor de integridad entre días.

Al arrancar main.py, verifica que todos los días con batches registrados
tengan AMBOS cierres completos:
  1. cierre_produccion  → JSON en registros/ (cierre.py)
  2. corte_caja         → SQLite cortes table (pos_db.py)

Si falta alguno, bloquea la operación y guía al operador a resolverlo
antes de continuar con el día actual.

Filosofía: la caja no se abre si el día anterior no está cuadrado.
"""

import os
from datetime import date, timedelta, datetime
from config import cargar_batches, fechas_con_registro, fecha_hoy, cargar_config
from cierre import cargar_cierre
from pos_db import tiene_corte_guardado, calcular_corte, guardar_corte


# ── COLORES ANSI (Dead Space — sin curses, output directo) ───────────────────

R  = "\033[91m"     # rojo   — alerta crítica
Y  = "\033[93m"     # amarillo — advertencia
C  = "\033[96m"     # cyan   — info / acción
G  = "\033[92m"     # verde  — OK
DIM = "\033[2m"     # tenue  — texto secundario
B  = "\033[1m"      # bold
RST = "\033[0m"     # reset

def _sep(char="═", ancho=54, color=C):
    print(f"{color}{char * ancho}{RST}")

def _titulo(texto, color=R):
    print()
    _sep("█", color=color)
    print(f"{color}{B}  {texto}{RST}")
    _sep("█", color=color)

def _ok(texto):
    print(f"  {G}✓{RST}  {texto}")

def _warn(texto):
    print(f"  {Y}!{RST}  {texto}")

def _err(texto):
    print(f"  {R}!!{RST} {texto}")

def _info(texto):
    print(f"  {C}·{RST}  {DIM}{texto}{RST}")


# ── DETECCIÓN DE DEUDA ────────────────────────────────────────────────────────

def _fechas_auditables() -> list[str]:
    """
    Retorna fechas con batches registrados, excluyendo hoy.
    Solo auditamos días pasados — el día actual está en curso.
    """
    hoy    = fecha_hoy()
    fechas = fechas_con_registro()
    return [f for f in fechas if f < hoy]


def dia_tiene_cierre_produccion(fecha: str) -> bool:
    """True si el día tiene cierre de producción registrado en JSON."""
    return cargar_cierre(fecha) is not None


def dia_tiene_corte_caja(fecha: str) -> bool:
    """
    True si todos los batches del día tienen al menos un corte guardado.
    Un día con 3 batches necesita que los 3 tengan corte.
    """
    batches = cargar_batches(fecha)
    if not batches:
        return True   # sin batches, no hay caja que cortar
    return all(tiene_corte_guardado(b.id) for b in batches)


def auditar_dias_pendientes() -> list[dict]:
    """
    Revisa todos los días pasados con batches.
    Retorna lista de deudas ordenadas por fecha ascendente.
    
    Cada deuda:
    {
        'fecha': 'YYYY-MM-DD',
        'n_batches': int,
        'falta_cierre_prod': bool,
        'falta_corte_caja': bool,
        'batches_sin_corte': [batch_id, ...]
    }
    """
    deudas = []
    for fecha in _fechas_auditables():
        batches = cargar_batches(fecha)
        if not batches:
            continue

        falta_prod  = not dia_tiene_cierre_produccion(fecha)
        sin_corte   = [b.id for b in batches if not tiene_corte_guardado(b.id)]
        falta_caja  = len(sin_corte) > 0

        if falta_prod or falta_caja:
            deudas.append({
                'fecha':            fecha,
                'n_batches':        len(batches),
                'falta_cierre_prod': falta_prod,
                'falta_corte_caja':  falta_caja,
                'batches_sin_corte': sin_corte,
            })

    return sorted(deudas, key=lambda d: d['fecha'])


# ── RESOLUCIÓN INTERACTIVA ────────────────────────────────────────────────────

def _resolver_corte_caja(fecha: str, batch_ids: list[int]):
    """
    Muestra y guarda el corte de caja para cada batch pendiente del día.
    Flujo no-curses — usa input() simple.
    """
    for batch_id in batch_ids:
        corte = calcular_corte(batch_id=batch_id)

        if corte["n_tickets"] == 0:
            _info(f"batch #{batch_id} — sin ventas, corte automático")
            guardar_corte(batch_id, corte, nota="auto — sin ventas")
            _ok(f"batch #{batch_id} cortado (vacío)")
            continue

        print()
        _sep("─", color=Y)
        print(f"  {Y}{B}CORTE DE CAJA — batch #{batch_id}  [{fecha}]{RST}")
        _sep("─", color=Y)
        print(f"  {DIM}tickets del turno{RST}  : {corte['n_tickets']}")
        print(f"  {G}ventas efectivo  {RST}  : ${corte['ventas_efectivo']:,.2f}")
        print(f"  {G}ventas transfer  {RST}  : ${corte['ventas_transfer']:,.2f}")
        print(f"  {G}ventas tarjeta   {RST}  : ${corte['ventas_tarjeta']:,.2f}")
        _sep("─", ancho=44, color=DIM)
        print(f"  {B}TOTAL VENTAS     {RST}  : {G}${corte['total_ventas']:,.2f}{RST}")
        print(f"  {Y}gastos del turno {RST}  : ${corte['total_gastos']:,.2f}")
        _sep("─", ancho=44, color=DIM)
        print(f"  {B}NETO             {RST}  : {C}${corte['neto']:,.2f}{RST}")
        print(f"  {DIM}fondo de caja    {RST}  : ${corte['fondo_caja']:,.2f}")
        print(f"  {B}A ENTREGAR       {RST}  : {G}${corte['a_entregar']:,.2f}{RST}")
        print()

        resp = input(f"  {C}[Enter]{RST} confirmar corte  "
                     f"{Y}[s]{RST} saltar por ahora : ").strip().lower()

        if resp in ("s", "skip"):
            _warn(f"batch #{batch_id} — corte pospuesto (se pedirá de nuevo mañana)")
        else:
            corte_id = guardar_corte(batch_id, corte,
                                      nota=f"guardian — cierre tardío {fecha}")
            _ok(f"batch #{batch_id} — corte #{corte_id} guardado")


def _resolver_cierre_produccion(fecha: str):
    """
    Delega al flujo normal de cierre.py.
    El guardian no reimplementa la lógica — solo orquesta.
    """
    from cierre import registrar_cierre as _rc
    print()
    _sep("─", color=Y)
    print(f"  {Y}{B}CIERRE DE PRODUCCIÓN — {fecha}{RST}")
    _sep("─", color=Y)
    print(f"  {DIM}Este día no tiene cierre de producción registrado.{RST}")
    print(f"  {DIM}Ingresa las ventas reales para cuadrar el día.{RST}")
    print()
    resp = input(f"  {C}[Enter]{RST} registrar cierre  "
                 f"{Y}[s]{RST} saltar por ahora : ").strip().lower()
    if resp not in ("s", "skip"):
        _rc()


def resolver_deuda_interactivo(deudas: list[dict]):
    """
    TUI bloqueante estilo Dead Space.
    Muestra cada día con deuda y guía al operador a resolverlo.
    No sale hasta que todo esté resuelto O el operador decida saltar.
    """
    os.system("clear")
    _titulo("⚠  ALERTA DE INTEGRIDAD  ⚠", color=R)
    print()
    print(f"  {R}{B}Se detectaron días sin cerrar correctamente.{RST}")
    print(f"  {DIM}El sistema requiere que todos los días anteriores{RST}")
    print(f"  {DIM}tengan corte de caja Y cierre de producción.{RST}")
    print()

    for i, deuda in enumerate(deudas, 1):
        fecha      = deuda['fecha']
        n_batches  = deuda['n_batches']
        sin_corte  = deuda['batches_sin_corte']

        _sep("─", color=R)
        print(f"  {R}{B}[{i}/{len(deudas)}]{RST}  {B}{fecha}{RST}  —  "
              f"{n_batches} batch(es)")

        if deuda['falta_corte_caja']:
            _err(f"corte de caja pendiente — "
                 f"batches sin corte: {sin_corte}")
        if deuda['falta_cierre_prod']:
            _err("cierre de producción no registrado")

        print()
        resp = input(f"  {C}[Enter]{RST} resolver ahora  "
                     f"{Y}[s]{RST} saltar  "
                     f"{R}[q]{RST} salir del sistema : ").strip().lower()

        if resp == "q":
            print(f"\n  {R}Sistema detenido — resuelve los cierres pendientes.{RST}\n")
            raise SystemExit(0)

        if resp in ("s", "skip"):
            _warn(f"{fecha} — saltado (quedará pendiente)")
            continue

        os.system("clear")
        print(f"\n  {C}{B}Resolviendo: {fecha}{RST}\n")

        # 1. Cortes de caja primero (más críticos — dinero)
        if deuda['falta_corte_caja']:
            _resolver_corte_caja(fecha, sin_corte)

        # 2. Cierre de producción
        if deuda['falta_cierre_prod']:
            _resolver_cierre_produccion(fecha)

    # verificar si quedaron deudas sin resolver
    restantes = auditar_dias_pendientes()
    if restantes:
        print()
        _warn(f"quedan {len(restantes)} día(s) con deuda — "
              f"se pedirá resolver en la próxima sesión")
    else:
        print()
        _ok("todos los días anteriores están cuadrados")

    print()
    input(f"  {C}[Enter]{RST} para continuar... ")
    os.system("clear")


# ── PUNTO DE ENTRADA — llamado desde main.py ─────────────────────────────────

def verificar_integridad():
    """
    Función principal — llamar al inicio de main() antes del menú.
    Si no hay deudas, retorna silenciosamente.
    Si hay deudas, muestra la pantalla de alerta y bloquea hasta resolver.
    """
    deudas = auditar_dias_pendientes()
    if not deudas:
        return   # día limpio — continuar sin interrumpir

    resolver_deuda_interactivo(deudas)