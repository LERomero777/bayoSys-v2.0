
"""
calcular.py - bayoSys v2.0
Toda la aritmetica del negocio. No sabe nada de JSON ni de curses.
Recibe modelos, devuelve resultados.

v2.0: dos rendimientos separados (proceso vs economico),
coeficientes de costos inyectados desde config.

"""

from typing import List
from bayosys.models import (
    Batch,
    KG_GRASA_POR_CUBETA,
    LT_POR_CUBETA,
    DENSIDAD_MANTECA,
    REND_MANT_KG,
    REND_UMBRAL,
)
from dataclasses import dataclass

# ── RESULTADO DE BATCH ───────────────────────────────────────────────────────
# Nunca se guarda en JSON — se recalcula siempre desde el Batch.

@dataclass
class ResultadoBatch:
    batch_id: int
    # Produccion
    kg_mant: float # kg_grasa x REND_MANT_KG
    lt_mant: float # kg_grasa × LT_POR_CUBETA / KG_GRASA_POR_CUBETA  (ρ se cancela) 
    cubetas: float # lt_mant / LT_POR_CUBETA

    # Rendimientos
    rend_proceso:   float  # kg_chi / kg_grasa_cazo × 100  → cazo y operador
    rend_economico: float  # kg_chi / kg_grasa × 100       → negocio y proveedor

    # Costos
    