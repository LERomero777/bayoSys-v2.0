"""
models.py — bayoSys · Productos El Bayo
Estructuras de datos. No sabe nada de JSON, curses ni aritmética.

MEDICIONES DE CAMPO (báscula digital):
  kg_grasa  — ANTES de echar al cazo
  kg_chi    — DESPUÉS de sacar el chicharrón
  Todo lo demás se calcula en calcular.py

MANTECA — modelo revisado (junio 2026):
  - Se eliminaron KG_POR_INIX y LT_POR_INIX (envase INIX descontinuado)
  - La manteca se vende por litro envasado: presentaciones 1lt y 0.5lt
  - Los precios son por presentación, no por litro abstracto
  - El mix litreada/cubeta NO se proyecta — se captura en cierre real
  - Stock litreada y stock cubetas son inventarios independientes
"""

from dataclasses import dataclass
from typing import List


# ── CONSTANTES FÍSICAS ───────────────────────────────────────────────────────
# Medidas con báscula tarada. No cambiar sin remedir.

KG_GRASA_POR_CUBETA = 26.0      # relación de campo: 26 kg grasa → 1 cubeta
LT_POR_CUBETA       = 19.0      # litros por cubeta estándar
DENSIDAD_MANTECA    = 0.8752    # kg/lt — medido: 414g neto / 473ml referencia

# Presentaciones litreada — volumen real del envase
LT_POR_ENV_1LT      = 1.000     # litros por envase de 1 litro
LT_POR_ENV_05LT     = 0.500     # litros por envase de 500ml

# constante derivada — kg de manteca por kg de grasa
# = (1/26) × 19 × 0.8752 = 0.6396
REND_MANT_KG = round(LT_POR_CUBETA * DENSIDAD_MANTECA / KG_GRASA_POR_CUBETA, 6)


# ── PROVEEDOR ────────────────────────────────────────────────────────────────

@dataclass
class Proveedor:
    clave:    str
    nombre:   str
    costo_kg: float


# ── BATCH ────────────────────────────────────────────────────────────────────

@dataclass
class Batch:
    id:    int
    fecha: str
    hora:  str
    proveedor: str
    costo_kg:  float
    temp_entrada: str   # "congelada" | "fria" | "ambiente"
    composicion:  str   # "tejido" | "grasa" | "mixto"
    operador:     str
    kg_grasa: float     # ANTES de echar al cazo
    kg_chi:   float     # DESPUÉS de sacar el chicharrón
    observaciones: str = ""


# ── RESULTADO DE BATCH ───────────────────────────────────────────────────────

@dataclass
class ResultadoBatch:
    batch_id: int
    kg_mant:  float
    lt_mant:  float
    cubetas:  float
    merma_kg: float
    rend_chi_pct:  float
    rend_mant_pct: float
    merma_pct:     float
    c_grasa:           float
    c_batch:           float
    c_chi_unit:        float   # aproximado sin fijos del día
    c_mant_unit:       float   # aproximado sin fijos del día
    costo_real_kg_chi: float   # c_grasa / kg_chi — métrica de proveedor


# ── RESULTADO DE DÍA ─────────────────────────────────────────────────────────

@dataclass
class ResultadoDia:
    fecha:     str
    n_batches: int

    # producción
    kg_grasa_dia: float
    kg_chi_dia:   float
    kg_mant_dia:  float
    lt_mant_dia:  float
    cubetas_dia:  float     # cubetas potenciales de toda la manteca
    merma_kg_dia: float

    # rendimientos
    rend_chi_pct:  float
    rend_mant_pct: float
    merma_pct:     float

    # costos
    c_grasa_dia:  float
    c_total_dia:  float
    c_chi_unit:   float
    c_mant_unit:  float

    # canales chicharrón (proyección por mix)
    chi_pub_kg:  float
    chi_may_kg:  float
    ing_chi_pub: float
    ing_chi_may: float
    ing_chi:     float

    # manteca — proyección simple: toda a cubeta (peor caso / referencia)
    # el desglose real litreada/cubeta vive en CierreDia
    ing_mant:     float

    # resultado proyectado
    ing_total: float
    utilidad:  float

    # métricas
    margen_chi_pub_pct:  float
    margen_chi_may_pct:  float
    margen_mant_cub_pct: float
    precio_min_chi:      float
    precio_justo_chi:    float
    precio_prem_chi:     float
    util_mensual:        float


# ── VENTA LITREADA ───────────────────────────────────────────────────────────

@dataclass
class VentaLitreada:
    """
    Una transacción de venta de manteca litreada.
    lt_total se valida en cierre contra litros disponibles del día.
    """
    env_1lt:  int     # envases de 1 litro vendidos
    env_05lt: int     # envases de 500ml vendidos
    lt_total: float   # env_1lt×1.0 + env_05lt×0.5  (calculado y validado)


# ── CIERRE DEL DÍA ───────────────────────────────────────────────────────────

@dataclass
class VentaCubeta:
    cantidad: float
    precio:   float


@dataclass
class CierreDia:
    fecha: str

    # chicharrón
    chi_pub_kg: float
    chi_may_kg: float

    # manteca litreada — inventario independiente
    ventas_litreada:   List[VentaLitreada]
    stock_litreada_lt: float    # litros en stock al cierre del día

    # manteca cubetas — inventario independiente
    ventas_cubeta: List[VentaCubeta]
    stock_cubetas: float        # cubetas en bodega al cierre

    observaciones: str = ""


# ── CONFIG ───────────────────────────────────────────────────────────────────

@dataclass
class Config:
    # distribución de costo
    alpha: float = 0.70
    beta:  float = 0.30

    # costos laborales
    destajo_kg:      float = 1.50
    diario_empleado: float = 350.0

    # costos operativos fijos
    leche_dia:       float = 80.0
    costo_env_1lt:   float = 3.50    # costo envase 1lt vacío
    costo_env_05lt:  float = 2.00    # costo envase 500ml vacío

    # gas — capturado en cierre, NO aquí

    # precios — chicharrón
    precio_chi_pub:  float = 230.0
    precio_chi_may:  float = 180.0

    # precios — manteca
    precio_mant_cub:  float = 500.0  # $/cubeta 19lt
    precio_mant_lt1:  float = 35.0   # $/envase 1lt
    precio_mant_lt05: float = 20.0   # $/envase 500ml

    # mix chicharrón para proyección en analisis/simulador
    mix_chi_pub_pct: float = 30.0
    # mix manteca — eliminado, se captura en cierre real

    def __post_init__(self):
        self.beta = round(1.0 - self.alpha, 10)
