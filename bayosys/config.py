"""
config.py — bayoSys · Productos El Bayo
Lee y escribe archivos JSON. No sabe nada de curses ni de aritmética.
"""

import json
import os
from dataclasses import asdict
from datetime import date
from typing import List, Optional

from models import Batch, Config, Proveedor

# ── RUTAS ────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.join(os.path.expanduser("~"), "bayosys", "data")
CONFIG_FILE   = os.path.join(BASE_DIR, "config.json")
PROV_FILE     = os.path.join(BASE_DIR, "proveedores.json")
REGISTROS_DIR = os.path.join(BASE_DIR, "registros")


def _asegurar_dirs():
    """Crea los directorios si no existen."""
    os.makedirs(REGISTROS_DIR, exist_ok=True)


# ── COBRO EN EFECTIVO ────────────────────────────────────────────────────────

# Múltiplo al que se redondea el EFECTIVO a cobrar. 0.50 = medio peso,
# 1.00 = peso cerrado. Ponerlo en 0 apaga el redondeo y vuelve a cobrar
# al centavo exacto.
#
# Existe por una razón física, no contable: en Hermosillo la moneda de 5
# centavos ya no circula y la de 10 casi no aparece en caja, así que un
# total de 50.10 no es cobrable — ni el cliente tiene con qué pagarlo ni
# el operador con qué dar cambio.
#
# Se redondea el COBRO, nunca la venta registrada: el ticket sigue
# valiendo 50.10 y la diferencia se guarda aparte en
# tickets.diferencia_redondeo. Si se redondeara la venta, el histórico de
# precios se degradaría y analisis.py empezaría a producir márgenes falsos.
#
# Solo aplica a efectivo: la terminal (Mercado Pago / Clip) cobra al
# centavo exacto porque no tiene problema físico de cambio.
REDONDEO_EFECTIVO = 0.50


# ── CONFIG ───────────────────────────────────────────────────────────────────

def cargar_config() -> Config:
    """Carga config.json. Si no existe devuelve defaults."""
    _asegurar_dirs()
    if not os.path.exists(CONFIG_FILE):
        return Config()
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return Config(**data)
    except Exception:
        return Config()


def guardar_config(cfg: Config):
    """Guarda Config en config.json."""
    _asegurar_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(asdict(cfg), f, indent=2, ensure_ascii=False)


# ── PROVEEDORES ──────────────────────────────────────────────────────────────

PROVEEDORES_DEFAULT = {
    "JC":   {"clave": "JC",   "nombre": "Carnes JC",    "costo_kg": 25.0},
    "El17": {"clave": "El17", "nombre": "Rancho El 17", "costo_kg": 25.0},
    "Bona": {"clave": "Bona", "nombre": "Bona Prime",   "costo_kg": 23.0},
}


def cargar_proveedores() -> dict:
    """
    Carga proveedores.json.
    Devuelve dict { clave: Proveedor }.
    """
    _asegurar_dirs()
    if not os.path.exists(PROV_FILE):
        _guardar_proveedores_raw(PROVEEDORES_DEFAULT)
        return {k: Proveedor(**v) for k, v in PROVEEDORES_DEFAULT.items()}
    try:
        with open(PROV_FILE) as f:
            data = json.load(f)
        return {k: Proveedor(**v) for k, v in data.items()}
    except Exception:
        return {k: Proveedor(**v) for k, v in PROVEEDORES_DEFAULT.items()}


def guardar_proveedores(proveedores: dict):
    """Guarda dict { clave: Proveedor } en proveedores.json."""
    raw = {k: asdict(v) for k, v in proveedores.items()}
    _guardar_proveedores_raw(raw)


def _guardar_proveedores_raw(raw: dict):
    _asegurar_dirs()
    with open(PROV_FILE, "w") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)


# ── REGISTROS DE BATCH ───────────────────────────────────────────────────────

def _ruta_dia(fecha: str) -> str:
    """Devuelve la ruta del archivo JSON para una fecha YYYY-MM-DD."""
    return os.path.join(REGISTROS_DIR, f"{fecha}.json")


def cargar_batches(fecha: str) -> List[Batch]:
    """
    Carga todos los batches de un día.
    Si no existe el archivo devuelve lista vacía.
    """
    ruta = _ruta_dia(fecha)
    if not os.path.exists(ruta):
        return []
    try:
        with open(ruta) as f:
            data = json.load(f)
        return [Batch(**b) for b in data.get("batches", [])]
    except Exception:
        return []


def guardar_batch(batch: Batch):
    """
    Agrega un batch al archivo del día correspondiente.
    Si el archivo no existe lo crea.
    Si ya existe un batch con el mismo id lo reemplaza.
    """
    _asegurar_dirs()
    ruta  = _ruta_dia(batch.fecha)
    batches = cargar_batches(batch.fecha)

    # reemplazar si ya existe ese id
    batches = [b for b in batches if b.id != batch.id]
    batches.append(batch)
    batches.sort(key=lambda b: b.id)

    with open(ruta, "w") as f:
        json.dump({
            "fecha":   batch.fecha,
            "batches": [asdict(b) for b in batches]
        }, f, indent=2, ensure_ascii=False)


def eliminar_batch(fecha: str, batch_id: int):
    """Elimina un batch por id del archivo del día."""
    batches = cargar_batches(fecha)
    batches = [b for b in batches if b.id != batch_id]
    ruta = _ruta_dia(fecha)
    with open(ruta, "w") as f:
        json.dump({
            "fecha":   fecha,
            "batches": [asdict(b) for b in batches]
        }, f, indent=2, ensure_ascii=False)


def fecha_hoy() -> str:
    """Devuelve la fecha de hoy en formato YYYY-MM-DD."""
    return date.today().isoformat()


def fechas_con_registro() -> List[str]:
    """Lista todas las fechas que tienen archivo de registro."""
    _asegurar_dirs()
    archivos = sorted(os.listdir(REGISTROS_DIR))
    return [f.replace(".json", "") for f in archivos if f.endswith(".json")]


def siguiente_batch_id(fecha: str) -> str:
    """
    Devuelve el próximo id de batch en formato AAAAMMDD-N.
    Ejemplo: '20261219-1', '20261219-2', ...
    Único globalmente — no colisiona entre días.
    """
    batches = cargar_batches(fecha)
    fecha_compact = fecha.replace("-", "")   # '2026-12-19' → '20261219'
    if not batches:
        return f"{fecha_compact}-1"
    # extraer el N del último batch del día
    ns = []
    for b in batches:
        try:
            n = int(b.id.split("-")[-1])
            ns.append(n)
        except (ValueError, AttributeError):
            pass
    siguiente_n = max(ns) + 1 if ns else 1
    return f"{fecha_compact}-{siguiente_n}"
