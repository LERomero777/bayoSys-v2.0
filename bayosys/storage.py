"""
storage.py — bayoSys v2.0
Lee y escribe archivos JSON. No sabe nada de curses ni de aritmética.

Compat total con JSON v1 — campos nuevos (Optional) se rellenan con
defaults si no están presentes en el archivo.
"""

import json
import os
from dataclasses import asdict
from datetime import date
from typing import List

from bayosys.models import Batch, Proveedor

# ── RUTAS ────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.join(os.path.expanduser("~"), "bayosys", "data")
PROV_FILE     = os.path.join(BASE_DIR, "proveedores.json")
REGISTROS_DIR = os.path.join(BASE_DIR, "registros")


def _asegurar_dirs():
    """Crea los directorios si no existen."""
    os.makedirs(REGISTROS_DIR, exist_ok=True)


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
    Si no existe el archivo lo crea con defaults.
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


# ── COMPAT v1 → v2 ───────────────────────────────────────────────────────────

# Campos introducidos en v2 con sus defaults.
# Permite cargar JSON v1 sin KeyError.
_DEFAULTS_V2 = {
    "t_inicio":          None,
    "t_fin":             None,
    "notas":             "",
    "proveedor":         "nd",
    "costo_kg":          0.0,
    "kg_merma_cruda":    None,
    "composicion":       "nd",
    "kg_descarte_rojo":  None,
    "kg_fondeo":         None,
    "ml_leche":          None,
    "kg_gas":            None,
    "sal_agregada":      False,
    "leche_agregada":    False,
    "temp_max":          None,
    "humo_detectado":    False,
    "fuente_temp":       "manual",
    "espuma_intensidad": "nd",
    "t_flotacion":       None,
    "t_espuma":          None,
    "t_130":             None,
}


def _batch_from_dict(d: dict) -> Batch:
    """
    Construye un Batch desde un dict JSON (v1 o v2).
    Los campos nuevos de v2 ausentes en v1 se rellenan con _DEFAULTS_V2.
    """
    return Batch(**{**_DEFAULTS_V2, **d})


# ── REGISTROS DE BATCH ───────────────────────────────────────────────────────

def _ruta_dia(fecha: str) -> str:
    """Devuelve la ruta del archivo JSON para una fecha YYYY-MM-DD."""
    return os.path.join(REGISTROS_DIR, f"{fecha}.json")


def cargar_batches(fecha: str) -> List[Batch]:
    """
    Carga todos los batches de un día.
    Si no existe el archivo devuelve lista vacía.
    Compatible con JSON generados por v1.
    """
    ruta = _ruta_dia(fecha)
    if not os.path.exists(ruta):
        return []
    try:
        with open(ruta) as f:
            data = json.load(f)
        return [_batch_from_dict(b) for b in data.get("batches", [])]
    except Exception:
        return []


def guardar_batch(batch: Batch):
    """
    Agrega un batch al archivo del día correspondiente.
    Si el archivo no existe lo crea.
    Si ya existe un batch con el mismo id lo reemplaza (upsert).
    """
    _asegurar_dirs()
    ruta    = _ruta_dia(batch.fecha)
    batches = cargar_batches(batch.fecha)

    batches = [b for b in batches if b.id != batch.id]
    batches.append(batch)
    batches.sort(key=lambda b: b.id)

    with open(ruta, "w") as f:
        json.dump({
            "fecha":   batch.fecha,
            "batches": [asdict(b) for b in batches],
        }, f, indent=2, ensure_ascii=False)


def eliminar_batch(fecha: str, batch_id: int):
    """Elimina un batch por id del archivo del día."""
    batches = cargar_batches(fecha)
    batches = [b for b in batches if b.id != batch_id]
    ruta = _ruta_dia(fecha)
    with open(ruta, "w") as f:
        json.dump({
            "fecha":   fecha,
            "batches": [asdict(b) for b in batches],
        }, f, indent=2, ensure_ascii=False)


# ── UTILIDADES ───────────────────────────────────────────────────────────────

def fecha_hoy() -> str:
    """Devuelve la fecha de hoy en formato YYYY-MM-DD."""
    return date.today().isoformat()


def fechas_con_registro() -> List[str]:
    """Lista todas las fechas que tienen archivo de registro, ordenadas."""
    _asegurar_dirs()
    archivos = sorted(os.listdir(REGISTROS_DIR))
    return [f.replace(".json", "") for f in archivos if f.endswith(".json")]


def siguiente_batch_id(fecha: str) -> int:
    """Devuelve el próximo id de batch para un día dado (max + 1)."""
    batches = cargar_batches(fecha)
    if not batches:
        return 1
    return max(b.id for b in batches) + 1