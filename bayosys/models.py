
"""
models.py - bayoSys v2.0
Estructuras de datos. No sabe nada de JSON, curses ni aritmetica.

v2.0: campos de telemetria, materia prima extendida, insumos, sensor.
Backward compat total con JSON v1 - campos nuevos son Optional con default.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, List

# ── CONSTANTES FÍSICAS ───────────────────────────────────────────────────────
# Medidas con bascula. No modificar sin remedir.

KG_GRASA_POR_CUBETA = 26.0 # 26 kg de grasa ~ 1 cubeta de 19 litros.
LT_POR_CUBETA = 19.0 # Litros por cubeta estandar.
DENSIDAD_MANTECA = 0.8752 # kg/litro, densidad de la manteca a 20°C. Fuente: https://www.engineeringtoolbox.com/fats-oils-densities-d_1698.html
REND_MANT_KG = round(LT_POR_CUBETA * DENSIDAD_MANTECA / KG_GRASA_POR_CUBETA, 6)

# ── UMBRALES DEL PROCESO  ────────────────────────────────────────────────────────
TEMP_HUMO = 140.0 # Temperatura del humo, en °C. Limite irreversible.
REND_UMBRAL = 0.21 # >21% SOBRE rend_proceso, batch ok.

# ── PROVEEDOR ───────────────────────────────────────────────────────

@dataclass
class Proveedor:
    clave: str # "JC" | "El17" | "Bona" | "Mixto"
    nombre: str # "Carnes JC"
    costo_kg: float # $/kg - puede cambiar.

# ── BATCH ───────────────────────────────────────────────────────

@dataclass
class Batch:
    # Identificación
    id: int # 1-N dentro del dia.
    fecha: str # "YYYY-MM-DD" 
    operador: str # Nombre del chicharronero.  
    t_inicio: Optional[str] = None # "HH:MM:SS"
    t_fin: Optional[str] = None # "HH:MM:SS" 
    notas: str = "" # Campo libre para anotaciones.

    #Proveedor
    proveedor: str = "nd"
    costo_kg: float = 0.0 # $/kg ese dia.

    # ── Mediciones base (v1.0)  ────────────────────────────
    kg_grasa: float = 0.0 # kg de grasa antes de echar al cazo.
    kg_chi: float = 0.0 # kg de chicharron resultantes.

    # ── materia prima extendida (v2.0) ────────────────────────────
    kg_merma_cruda:    Optional[float] = None  # hueso+cartílago pesado ANTES del cazo
    composicion:       str             = "nd"  # "limpia"|"carnuda"|"rojiza"|"muy_roja"
    kg_descarte_rojo:  Optional[float] = None  # detectado en despacho, no en proceso

    # ── insumos (v2.0) ────────────────────────────────────────────
    kg_fondeo:         Optional[float] = None  # manteca inicial del batch anterior
    ml_leche:          Optional[float] = None  # estandarizar el tanteo actual
    kg_gas:            Optional[float] = None  # consumo gas LP

    # ── checklist etapa 1 (v2.0) ──────────────────────────────────
    sal_agregada:      bool            = False
    leche_agregada:    bool            = False

    # ── telemetría térmica (v2.0) ─────────────────────────────────
    temp_max:          Optional[float] = None  # °C máxima del batch
    humo_detectado:    bool            = False
    fuente_temp:       str             = "manual"  # "manual"|"esp32"

    # ── calidad etapa 2 (v2.0) ────────────────────────────────────
    espuma_intensidad: str             = "nd"  # "minima"|"moderada"|"intensa"|"nd"

    # ── telemetría temporal extendida (v2.0) ──────────────────────
    t_flotacion:       Optional[str]   = None  # chicharrón flota → fin E1
    t_espuma:          Optional[str]   = None  # aparece espuma → fin E2
    t_130:             Optional[str]   = None  # referencia térmica

    def __post_init__(self):
        # Campos sin default que pueden llegar vacios.
        if not self.fecha:
            self.fecha = date.today().isoformat()

        # Rangos fisicos basicos.
        if self.kg_grasa < 0.0:
            raise ValueError(f"kg_grasa negativo: {self.kg_grasa}")
        if self.kg_chi < 0.0:
            raise ValueError(f"kg_chi negativo: {self.kg_chi}")
        if self.kg_chi > self.kg_grasa and self.kg_grasa > 0.0:
            raise ValueError(f"kg_chi ({self.kg_chi}) > kg_grasa ({self.kg_grasa}) - imposible"
            )
        if self.temp_max is not None and self.temp_max >= TEMP_HUMO:
            self.humo_detectado = True

        # Normalizar strings.
        self.composicion = self.composicion.lower()
        self.espuma_intensidad = self.espuma_intensidad.lower()
        self.fuente_temp = self.fuente_temp.lower()
         
    

    # ── propiedades derivadas ─────────────────────────────────────

    @property
    def kg_grasa_cazo(self) -> float:
        """Grasa neta que entró al cazo — base para rend_proceso."""
        return self.kg_grasa - (self.kg_merma_cruda or 0.0)

    @property
    def etapa1_completa(self) -> bool:
        """Checklist mínimo de Etapa 1."""
        return self.sal_agregada and self.leche_agregada

    @property
    def tiene_telemetria(self) -> bool:
        """True si hay al menos un timestamp registrado."""
        return any([self.t_inicio, self.t_flotacion, self.t_espuma, self.t_fin])

    @property
    def humo_riesgo(self) -> bool:
        """Zona de riesgo antes del límite irreversible."""
        if self.humo_detectado:
            return True
        if self.temp_max is not None and self.temp_max >= 135.0:
            return True
        return False

    