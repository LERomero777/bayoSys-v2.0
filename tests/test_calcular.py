"""
test_calcular.py — bayoSys · Productos El Bayo
Pruebas de bayosys/calcular.py — "el corazón del negocio".

Corre con: python3 -m unittest tests/test_calcular.py -v
(desde la raíz del repo)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bayosys"))

from calcular import (
    calcular_batch, calcular_dia,
    calcular_ing_manteca_real, calcular_ing_chi_real,
    comparar_proveedores,
)
from models import (
    Batch, Config, CierreDia, VentaLitreada, VentaCubeta,
)


def _batch(**over):
    base = dict(
        id=1, fecha="2026-07-27", hora="08:00", proveedor="JC",
        costo_kg=25.0, temp_entrada="fria", composicion="mixto",
        operador="op", kg_grasa=100.0, kg_chi=25.0,
    )
    base.update(over)
    return Batch(**base)


class TestCalcularBatch(unittest.TestCase):
    def test_batch_normal(self):
        r = calcular_batch(_batch(), Config(), 1)
        self.assertEqual(r.rend_chi_pct, 25.0)
        self.assertGreater(r.kg_mant, 0)
        self.assertGreater(r.c_batch, 0)

    def test_batch_kg_chi_cero(self):
        # sin chicharrón todavía (recién echado al cazo) — no debe tronar
        r = calcular_batch(_batch(kg_chi=0.0), Config(), 1)
        self.assertEqual(r.c_chi_unit, 0.0)
        self.assertEqual(r.costo_real_kg_chi, 0.0)

    def test_batch_kg_grasa_cero_truena(self):
        # comportamiento actual: no hay guard contra kg_grasa == 0 (división
        # por cero). Documentamos el comportamiento actual — si algún día se
        # agrega un guard, este test debe actualizarse deliberadamente.
        with self.assertRaises(ZeroDivisionError):
            calcular_batch(_batch(kg_grasa=0.0), Config(), 1)


class TestCalcularDia(unittest.TestCase):
    def test_dia_sin_batches_truena(self):
        with self.assertRaises(ValueError):
            calcular_dia([], Config())

    def test_dia_gas_cero_vs_real(self):
        batches = [_batch()]
        cfg = Config()
        sin_gas = calcular_dia(batches, cfg, 0.0)
        con_gas = calcular_dia(batches, cfg, 200.0)
        self.assertAlmostEqual(con_gas.c_total_dia - sin_gas.c_total_dia, 200.0, places=2)

    def test_dia_precio_chi_pub_cero_no_truena(self):
        cfg = Config(precio_chi_pub=0.0)
        r = calcular_dia([_batch()], cfg)
        self.assertEqual(r.margen_chi_pub_pct, 0.0)

    def test_dia_precio_chi_may_cero_no_truena(self):
        cfg = Config(precio_chi_may=0.0)
        r = calcular_dia([_batch()], cfg)
        self.assertEqual(r.margen_chi_may_pct, 0.0)

    def test_dia_precios_recomendados_usan_config(self):
        cfg = Config(margen_justo_pct=50.0, margen_premium_pct=75.0, dias_laborales_mes=20)
        r = calcular_dia([_batch()], cfg)
        self.assertAlmostEqual(r.precio_justo_chi, r.c_chi_unit / 0.5, places=2)
        self.assertAlmostEqual(r.precio_prem_chi,  r.c_chi_unit / 0.25, places=2)
        self.assertAlmostEqual(r.util_mensual, r.utilidad * 20, delta=0.5)


class TestIngresoReal(unittest.TestCase):
    def _cierre(self, **over):
        base = dict(
            fecha="2026-07-27",
            chi_pub_kg=10.0, chi_may_kg=5.0,
            ventas_litreada=[VentaLitreada(env_1lt=3, env_05lt=2, lt_total=4.0)],
            stock_litreada_lt=1.0,
            ventas_cubeta=[VentaCubeta(cantidad=2.0, precio=500.0)],
            stock_cubetas=0.5,
        )
        base.update(over)
        return CierreDia(**base)

    def test_ing_manteca_real(self):
        cfg = Config()
        cierre = self._cierre()
        mr = calcular_ing_manteca_real(cierre, cfg)
        self.assertEqual(mr["env_1lt_total"], 3)
        self.assertEqual(mr["env_05lt_total"], 2)
        esperado = 3 * cfg.precio_mant_lt1 + 2 * cfg.precio_mant_lt05 + 2.0 * 500.0
        self.assertAlmostEqual(mr["ing_mant_real"], esperado, places=2)

    def test_ing_chi_real(self):
        cfg = Config()
        cierre = self._cierre()
        cr = calcular_ing_chi_real(cierre, cfg)
        esperado = 10.0 * cfg.precio_chi_pub + 5.0 * cfg.precio_chi_may
        self.assertAlmostEqual(cr["ing_chi_real"], esperado, places=2)

    def test_c_gas_dia_persiste_en_cierre(self):
        cierre = self._cierre(c_gas_dia=150.0)
        self.assertEqual(cierre.c_gas_dia, 150.0)
        # cierre sin especificar c_gas_dia — default no rompe cierres viejos
        cierre_viejo = self._cierre()
        self.assertEqual(cierre_viejo.c_gas_dia, 0.0)


class TestCompararProveedores(unittest.TestCase):
    def test_agrupa_por_proveedor(self):
        batches = [
            _batch(id=1, proveedor="JC",   kg_grasa=100.0, kg_chi=25.0, costo_kg=25.0),
            _batch(id=2, proveedor="JC",   kg_grasa=50.0,  kg_chi=12.0, costo_kg=25.0),
            _batch(id=3, proveedor="El17", kg_grasa=80.0,  kg_chi=22.0, costo_kg=25.0),
        ]
        comp = comparar_proveedores(batches)
        self.assertEqual(set(comp.keys()), {"JC", "El17"})
        self.assertEqual(comp["JC"]["batches"], 2)
        self.assertAlmostEqual(comp["JC"]["kg_grasa_total"], 150.0, places=2)


if __name__ == "__main__":
    unittest.main()
