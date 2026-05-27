from typing import List
from dataclasses import dataclass

from bayosys.models import (
    Batch,
    KG_GRASA_POR_CUBETA,
    LT_POR_CUBETA,
    DENSIDAD_MANTECA,
    REND_MANT_KG,
    REND_UMBRAL,
)


@dataclass
class ResultadoBatch:
    """Resultados derivados de un Batch. Nunca se persiste en JSON."""

    batch_id: int
    kg_grasa_cazo: float    # heredado de Batch.kg_grasa_cazo
    kg_mant: float          # kg_grasa × REND_MANT_KG
    lt_mant: float          # kg_grasa × LT_POR_CUBETA / KG_GRASA_POR_CUBETA
    cubetas: float          # lt_mant / LT_POR_CUBETA  →  kg_grasa / KG_GRASA_POR_CUBETA
    rend_proceso: float     # kg_chi / kg_grasa_cazo × 100  — evalúa cazo y operador
    rend_economico: float   # kg_chi / kg_grasa       × 100  — evalúa negocio y proveedor
    alerta_humo: bool       # True si temp_cazo >= TEMP_HUMO
    alerta_rend: bool       # True si rend_proceso < 20.0


def calcular_batch(batch: Batch) -> ResultadoBatch:
    """
    Calcula métricas derivadas de un Batch ya cargado.

    Args:
        batch: instancia de Batch con etapa 1 completa (kg_grasa > 0).

    Returns:
        ResultadoBatch con todos los campos calculados.

    Notas:
        - Si kg_grasa_cazo == 0 (batch incompleto), rend_proceso queda en 0.0.
        - Si kg_grasa == 0, rend_economico queda en 0.0.
        - cubetas es equivalente algebraico de lt_mant / LT_POR_CUBETA.
    """
    kg_grasa_cazo = batch.kg_grasa_cazo  # propiedad de Batch

    kg_mant = batch.kg_grasa * REND_MANT_KG
    lt_mant = batch.kg_grasa * LT_POR_CUBETA / KG_GRASA_POR_CUBETA
    cubetas = batch.kg_grasa / KG_GRASA_POR_CUBETA  # == lt_mant / LT_POR_CUBETA

    rend_proceso = (
        (batch.kg_chi / kg_grasa_cazo * 100) if kg_grasa_cazo > 0 else 0.0
    )
    rend_economico = (
        (batch.kg_chi / batch.kg_grasa * 100) if batch.kg_grasa > 0 else 0.0
    )

    return ResultadoBatch(
        batch_id=batch.id,
        kg_grasa_cazo=kg_grasa_cazo,
        kg_mant=kg_mant,
        lt_mant=lt_mant,
        cubetas=cubetas,
        rend_proceso=rend_proceso,
        rend_economico=rend_economico,
        alerta_humo=batch.humo_riesgo,
        alerta_rend=rend_proceso < 20.0,
    )