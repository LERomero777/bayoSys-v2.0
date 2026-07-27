"""
test_cierre_manteca.py — bayoSys · Productos El Bayo

El cierre ya no re-deriva la manteca de la producción: la lee del POS, que es
donde entra por el pool y sale por los tickets. Estas pruebas cubren que lo
que reporta el cierre coincide con lo que realmente pasó en el POS, y que el
stock ya no se cuenta dos veces.

Corre con: python3 -m unittest tests/test_cierre_manteca.py -v
(desde la raíz del repo)
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bayosys"))

import pos_db
from models import LT_POR_CUBETA, Config


class CierreTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bayosys_test_")
        self._base_orig = pos_db.BASE_DIR
        self._db_orig   = pos_db.POS_DB
        pos_db.BASE_DIR = self.tmp
        pos_db.POS_DB   = os.path.join(self.tmp, "pos.db")
        pos_db.init_db()
        self.fecha = "2026-07-27"

    def tearDown(self):
        pos_db.BASE_DIR = self._base_orig
        pos_db.POS_DB   = self._db_orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _vender(self, sku, descripcion, cantidad, precio):
        total = cantidad * precio
        tid = pos_db.crear_ticket(
            batch_id = "20260727-1",
            operador = "op",
            items    = [dict(sku=sku, descripcion=descripcion, cantidad=cantidad,
                             precio_unit=precio, subtotal=total)],
            pagos    = {"efectivo": total},
            cambio   = 0.0,
        )
        # crear_ticket sella la fecha con datetime.now(); alinear para el test
        with pos_db.conectar() as conn:
            conn.execute("UPDATE tickets SET fecha = ? WHERE id = ?", (self.fecha, tid))
            conn.execute("UPDATE movimientos_inv SET fecha = ? WHERE referencia = ?",
                         (self.fecha, f"ticket#{tid}"))
        return tid


class TestLecturaDelPOS(CierreTestCase):

    def test_ventas_de_cubeta_conservan_su_precio_negociado(self):
        """El precio de cubeta se negocia venta por venta — no se promedia."""
        from cierre import leer_manteca_del_pos

        pos_db.cargar_produccion_batch("20260727-1", 10.0, 3 * LT_POR_CUBETA)
        self._vender("CUB", "Cubeta 19lt", 1, 500.0)
        self._vender("CUB", "Cubeta 19lt", 1, 450.0)   # cliente frecuente

        mant = leer_manteca_del_pos(self.fecha)
        precios = sorted(v.precio for v in mant["ventas_cubeta"])

        self.assertEqual(len(mant["ventas_cubeta"]), 2)
        self.assertEqual(precios, [450.0, 500.0])
        self.assertEqual(mant["stock_cubetas"], 1)

    def test_litreada_se_agrupa_por_ticket(self):
        from cierre import leer_manteca_del_pos

        pos_db.cargar_produccion_batch("20260727-1", 10.0, 2 * LT_POR_CUBETA)
        pos_db.abrir_cubeta(env_1lt=10, env_05lt=8)
        self._vender("M1LT", "Manteca 1lt", 3, 35.0)
        self._vender("M05",  "Manteca 500ml", 2, 20.0)

        mant = leer_manteca_del_pos(self.fecha)

        self.assertEqual(len(mant["ventas_litreada"]), 2)   # dos tickets
        self.assertEqual(sum(v.env_1lt  for v in mant["ventas_litreada"]), 3)
        self.assertEqual(sum(v.env_05lt for v in mant["ventas_litreada"]), 2)

    def test_stock_litreada_incluye_granel_y_envases(self):
        """
        stock_litreada_lt = todo lo que NO es cubeta sellada,
        o sea el granel del pool más los envases ya llenados.
        """
        from cierre import leer_manteca_del_pos

        pos_db.cargar_produccion_batch("20260727-1", 10.0, LT_POR_CUBETA + 5.0)
        pos_db.litrear_de_pool(env_1lt=4, env_05lt=0)   # 5.0 → 4 envases, 1.0 lt granel

        mant = leer_manteca_del_pos(self.fecha)

        self.assertAlmostEqual(mant["pool_lt"], 1.0, places=3)
        self.assertEqual(mant["env_1lt_stock"], 4)
        self.assertAlmostEqual(mant["stock_litreada_lt"], 5.0, places=3)
        self.assertEqual(mant["stock_cubetas"], 1)

    def test_los_litros_no_se_cuentan_dos_veces(self):
        """
        El bug de fondo: los mismos litros aparecían como stock de litreada
        Y como cubetas. Ahora cada litro está en exactamente un lado.
        """
        from cierre import leer_manteca_del_pos

        lt_producidos = 3 * LT_POR_CUBETA + 7.0
        pos_db.cargar_produccion_batch("20260727-1", 10.0, lt_producidos)
        pos_db.abrir_cubeta(env_1lt=12, env_05lt=6)
        self._vender("CUB",  "Cubeta 19lt", 1, 500.0)
        self._vender("M1LT", "Manteca 1lt", 5, 35.0)

        mant = leer_manteca_del_pos(self.fecha)

        vendido = (sum(v.cantidad for v in mant["ventas_cubeta"]) * LT_POR_CUBETA +
                   sum(v.lt_total for v in mant["ventas_litreada"]))
        en_stock = mant["stock_cubetas"] * LT_POR_CUBETA + mant["stock_litreada_lt"]

        self.assertAlmostEqual(vendido + en_stock, lt_producidos, places=3)

    def test_ticket_anulado_no_cuenta_como_venta(self):
        from cierre import leer_manteca_del_pos

        pos_db.cargar_produccion_batch("20260727-1", 10.0, 3 * LT_POR_CUBETA)
        tid = self._vender("CUB", "Cubeta 19lt", 1, 500.0)
        pos_db.anular_ticket(tid)

        mant = leer_manteca_del_pos(self.fecha)
        self.assertEqual(mant["ventas_cubeta"], [])

    def test_dia_sin_ventas_de_manteca(self):
        from cierre import leer_manteca_del_pos

        pos_db.cargar_produccion_batch("20260727-1", 10.0, 10.0)
        mant = leer_manteca_del_pos(self.fecha)

        self.assertEqual(mant["ventas_cubeta"], [])
        self.assertEqual(mant["ventas_litreada"], [])
        self.assertEqual(mant["stock_cubetas"], 0)
        self.assertAlmostEqual(mant["stock_litreada_lt"], 10.0, places=3)


class TestPreciosCongelados(CierreTestCase):

    def test_el_ingreso_usa_el_precio_de_la_venta_no_el_de_hoy(self):
        """Subir el precio mañana no debe reescribir el ingreso de ayer."""
        from cierre import leer_manteca_del_pos
        from calcular import calcular_ing_manteca_real
        from models import CierreDia

        pos_db.cargar_produccion_batch("20260727-1", 10.0, 2 * LT_POR_CUBETA)
        pos_db.abrir_cubeta(env_1lt=10, env_05lt=0)
        self._vender("M1LT", "Manteca 1lt", 4, 35.0)     # se vendió a 35

        mant   = leer_manteca_del_pos(self.fecha)
        cierre = CierreDia(
            fecha=self.fecha, chi_pub_kg=0.0, chi_may_kg=0.0,
            ventas_litreada=mant["ventas_litreada"],
            stock_litreada_lt=mant["stock_litreada_lt"],
            ventas_cubeta=mant["ventas_cubeta"],
            stock_cubetas=mant["stock_cubetas"],
        )

        cfg_manana = Config(precio_mant_lt1=50.0)        # mañana sube a 50
        r = calcular_ing_manteca_real(cierre, cfg_manana)

        self.assertAlmostEqual(r["ing_litreada_1lt"], 140.0, places=2)   # 4 × 35

    def test_cierre_viejo_sin_precio_cae_a_config(self):
        """Los cierres guardados antes del cambio no traen precio."""
        from calcular import calcular_ing_manteca_real
        from models import CierreDia, VentaLitreada

        cierre = CierreDia(
            fecha="2026-06-01", chi_pub_kg=0.0, chi_may_kg=0.0,
            ventas_litreada=[VentaLitreada(env_1lt=4, env_05lt=2, lt_total=5.0)],
            stock_litreada_lt=0.0, ventas_cubeta=[], stock_cubetas=0.0,
        )
        r = calcular_ing_manteca_real(cierre, Config())

        self.assertAlmostEqual(r["ing_litreada_1lt"],  4 * 35.0, places=2)
        self.assertAlmostEqual(r["ing_litreada_05lt"], 2 * 20.0, places=2)


if __name__ == "__main__":
    unittest.main()
