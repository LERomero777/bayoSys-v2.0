"""
nuke.py — bayoSys · Productos El Bayo
Factory Reset — vuelve el sistema a su estado de fábrica.

Filosofía: irreversible por diseño, pero NUNCA sin respaldo previo.
Si el respaldo falla, el reset se cancela — no hay excepciones.
"""

import os
import json
import shutil
import hashlib
from datetime import datetime

from config import BASE_DIR, REGISTROS_DIR, CONFIG_FILE, PROV_FILE
from config import PROVEEDORES_DEFAULT, guardar_config, _guardar_proveedores_raw
from models import Config
from estilos import (
    titulo_txt, sep_txt, ok_txt, alerta_txt, aviso_txt,
    opcion_txt, dato_txt, c, imprimir, pedir,
)

CREDENCIALES_ADMIN = os.path.join(BASE_DIR, "credenciales_admin.json")
BACKUPS_DIR         = os.path.join(BASE_DIR, "backups", "factory_reset")
BATCH_ACTIVO_FILE   = os.path.join(BASE_DIR, "batch_activo.txt")
POS_DB              = os.path.join(BASE_DIR, "pos.db")


# ── PIN DE ADMINISTRADOR ──────────────────────────────────────────────────────

def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def verificar_pin_admin(pin_ingresado: str) -> bool:
    """
    Compara el PIN ingresado contra el hash guardado.
    Si no existe el archivo de credenciales, no permite continuar
    (evita un factory reset "gratis" en una instalación sin PIN configurado).
    """
    if not os.path.exists(CREDENCIALES_ADMIN):
        imprimir(alerta_txt("no hay PIN de administrador configurado — reset bloqueado"))
        return False
    try:
        with open(CREDENCIALES_ADMIN) as f:
            data = json.load(f)
        return _hash_pin(pin_ingresado) == data.get("pin_hash", "")
    except Exception:
        return False


# ── RESPALDO COMPLETO ─────────────────────────────────────────────────────────

def _respaldar_todo() -> str:
    """
    Copia íntegra de todo lo que se va a borrar/reiniciar.
    Retorna la ruta del respaldo. Lanza excepción si algo falla —
    quien llama debe abortar el reset si esto no se completa.
    """
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta   = os.path.join(BACKUPS_DIR, ts)
    os.makedirs(ruta, exist_ok=True)

    manifest = {"timestamp": ts, "contenido": []}

    # registros/ (batches y cierres)
    if os.path.isdir(REGISTROS_DIR) and os.listdir(REGISTROS_DIR):
        shutil.copytree(REGISTROS_DIR, os.path.join(ruta, "registros"))
        manifest["contenido"].append("registros/")

    # pos.db
    if os.path.exists(POS_DB):
        shutil.copy2(POS_DB, os.path.join(ruta, "pos.db"))
        manifest["contenido"].append("pos.db")

    # config.json
    if os.path.exists(CONFIG_FILE):
        shutil.copy2(CONFIG_FILE, os.path.join(ruta, "config.json"))
        manifest["contenido"].append("config.json")

    # proveedores.json
    if os.path.exists(PROV_FILE):
        shutil.copy2(PROV_FILE, os.path.join(ruta, "proveedores.json"))
        manifest["contenido"].append("proveedores.json")

    # batch_activo.txt
    if os.path.exists(BATCH_ACTIVO_FILE):
        shutil.copy2(BATCH_ACTIVO_FILE, os.path.join(ruta, "batch_activo.txt"))
        manifest["contenido"].append("batch_activo.txt")

    with open(os.path.join(ruta, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return ruta


# ── RESET REAL ─────────────────────────────────────────────────────────────────

def _ejecutar_reset():
    """Borra y reconstruye todo a estado de fábrica. Se llama SOLO tras respaldo OK."""
    from pos_db import init_db

    # registros/ — batches y cierres
    if os.path.isdir(REGISTROS_DIR):
        shutil.rmtree(REGISTROS_DIR)
    os.makedirs(REGISTROS_DIR, exist_ok=True)

    # pos.db — se borra y se recrea con catálogo default
    if os.path.exists(POS_DB):
        os.remove(POS_DB)
    # WAL/SHM residuales de sqlite, si existen
    for ext in ("-wal", "-shm"):
        p = POS_DB + ext
        if os.path.exists(p):
            os.remove(p)
    init_db()   # recrea schema + SKUs_DEFAULT (CHI, M1LT, M05, CHO, CUB, LIB)

    # config.json — defaults de Config()
    guardar_config(Config())

    # proveedores.json — defaults
    _guardar_proveedores_raw(PROVEEDORES_DEFAULT)

    # batch activo — limpio
    if os.path.exists(BATCH_ACTIVO_FILE):
        os.remove(BATCH_ACTIVO_FILE)


# ── FLUJO PRINCIPAL — llamado desde main.py ──────────────────────────────────

def factory_reset():
    """
    Punto de entrada del botón ☢ FACTORY RESET.
    Doble confirmación: texto "NUKE" + PIN de administrador.
    Respaldo automático antes de cualquier borrado.
    """
    imprimir()
    imprimir("═" * 54)
    imprimir("  ☢  FACTORY RESET  ☢")
    imprimir("═" * 54)
    imprimir("  Esto borrará TODOS los batches, cierres, tickets,")
    imprimir("  inventario, configuración y catálogo POS.")
    imprimir("  El catálogo volverá a los SKUs de fábrica")
    imprimir("  (CHI, M1LT, M05, CHO, CUB, LIB) — cualquier producto")
    imprimir("  agregado manualmente se pierde y debe rehacerse.")
    imprimir()
    imprimir("  Se creará un respaldo completo antes de borrar nada.")
    imprimir()

    confirm = pedir('  escribe "NUKE" para continuar: ').strip()
    if confirm != "NUKE":
        imprimir("\n  ✗ cancelado — nada se modificó\n")
        return

    pin = pedir("  PIN de administrador: ").strip()
    if not verificar_pin_admin(pin):
        imprimir("\n  ✗ PIN incorrecto — reset bloqueado\n")
        return

    imprimir("\n  creando respaldo...")
    try:
        ruta_backup = _respaldar_todo()
    except Exception as e:
        imprimir(f"\n  !! ERROR AL RESPALDAR: {e}")
        imprimir("  !! reset CANCELADO — no se borró nada\n")
        return

    imprimir(ok_txt(f"respaldo guardado en: {ruta_backup}"))
    imprimir("\n  ejecutando factory reset...")

    try:
        _ejecutar_reset()
    except Exception as e:
        imprimir(f"\n  !! ERROR durante el reset: {e}")
        imprimir(f"  !! el respaldo sigue disponible en: {ruta_backup}\n")
        return

    imprimir("\n  ☢ purga completa — sistema en estado de fábrica ☢")
    imprimir(f"  respaldo previo disponible en: {ruta_backup}")
    imprimir()

