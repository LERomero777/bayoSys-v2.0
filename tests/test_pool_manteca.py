"""
test_pool_manteca.py — bayoSys · Productos El Bayo

Prueba el pool de manteca a granel: la producción entra en litros, el
inventario del POS solo recibe cubetas COMPLETAS y la fracción queda flotando
para el día siguiente.

Corre con: python3 -m unittest tests/test_pool_manteca.py -v
(desde la raíz del repo)
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bayosys"))

import pos_db
from models import LT_POR_CUBETA, Batch, Config


class PoolTestCase(unittest.TestCase):
    """Base: cada test corre contra una pos.db limpia en un tmpdir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bayosys_test_")
        self._base_orig = pos_db.BASE_DIR
        self._db_orig   = pos_db.POS_DB
        pos_db.BASE_DIR = self.tmp
        pos_db.POS_DB   = os.path.join(self.tmp, "pos.db")
        pos_db.init_db()

    def tearDown(self):
        pos_db.BASE_DIR = self._base_orig
        pos_db.POS_DB   = self._db_orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stock(self, sku):
        return pos_db.get_sku(sku)["stock"]


class TestEmisionDeCubetas(PoolTestCase):

    def test_produccion_parcial_no_emite_cubeta(self):
        """15 lt no alcanzan para una cubeta de 19 — todo queda flotando."""
        r = pos_db.cargar_produccion_batch("20260727-1", kg_chi=8.0, lt_manteca=15.0)

        self.assertEqual(r["cubetas_emitidas"], 0)
        self.assertAlmostEqual(r["pool_despues"], 15.0, places=3)
        self.assertEqual(self.stock("CUB"), 0)
        self.assertAlmostEqual(self.stock("CHI"), 8.0, places=3)

    def test_emite_solo_la_parte_entera(self):
        """25.65 lt = 1.35 cubetas → sube 1, deja 0.35 de cubeta flotando."""
        lt = 1.35 * LT_POR_CUBETA
        r  = pos_db.cargar_produccion_batch("20260727-1", kg_chi=8.0, lt_manteca=lt)

        self.assertEqual(r["cubetas_emitidas"], 1)
        self.assertEqual(self.stock("CUB"), 1)
        self.assertAlmostEqual(r["pool_despues"] / LT_POR_CUBETA, 0.35, places=6)

    def test_el_remanente_se_acumula_entre_batches(self):
        """
        La dinámica real del día: cada batch parte de lo que dejó el anterior.
        1.35 cub → sube 1, quedan 0.35
        0.35 + 1.20 = 1.55 cub → sube 1, quedan 0.55
        """
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 1.35 * LT_POR_CUBETA)
        r2 = pos_db.cargar_produccion_batch("20260727-2", 8.0, 1.20 * LT_POR_CUBETA)

        self.assertEqual(r2["cubetas_emitidas"], 1)
        self.assertEqual(self.stock("CUB"), 2)
        self.assertAlmostEqual(r2["pool_despues"] / LT_POR_CUBETA, 0.55, places=6)

    def test_remanente_acumulado_emite_cubeta_sin_batch_grande(self):
        """Tres batches chicos que solos no dan cubeta, juntos sí."""
        for i in range(3):
            pos_db.cargar_produccion_batch(f"20260727-{i+1}", 5.0, 7.0)

        # 21 lt en total → 1 cubeta + 2 lt
        self.assertEqual(self.stock("CUB"), 1)
        self.assertAlmostEqual(pos_db.get_pool_manteca(), 2.0, places=3)

    def test_cubeta_exacta_no_se_pierde_por_redondeo(self):
        """19.0 lt clavados deben emitir 1 cubeta y dejar el pool en 0."""
        r = pos_db.cargar_produccion_batch("20260727-1", 8.0, LT_POR_CUBETA)

        self.assertEqual(r["cubetas_emitidas"], 1)
        self.assertAlmostEqual(r["pool_despues"], 0.0, places=6)

    def test_multiples_cubetas_de_un_solo_batch(self):
        r = pos_db.cargar_produccion_batch("20260727-1", 20.0, 3.5 * LT_POR_CUBETA)

        self.assertEqual(r["cubetas_emitidas"], 3)
        self.assertAlmostEqual(r["pool_despues"] / LT_POR_CUBETA, 0.5, places=6)

    def test_la_carga_es_idempotente(self):
        """Reintentar el mismo batch no duplica inventario."""
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 40.0)
        r2 = pos_db.cargar_produccion_batch("20260727-1", 8.0, 40.0)

        self.assertTrue(r2["ya_cargado"])
        self.assertEqual(self.stock("CUB"), 2)
        self.assertAlmostEqual(self.stock("CHI"), 8.0, places=3)

    def test_no_se_pierden_litros_en_el_camino(self):
        """
        Invariante del sistema: los litros producidos siguen enteros,
        repartidos entre cubetas selladas y pool a granel.
        """
        lotes = [21.9, 26.3, 18.7, 30.1]
        for i, lt in enumerate(lotes):
            pos_db.cargar_produccion_batch(f"20260727-{i+1}", 5.0, lt)

        en_cubetas = self.stock("CUB") * LT_POR_CUBETA
        self.assertAlmostEqual(en_cubetas + pos_db.get_pool_manteca(),
                               sum(lotes), places=3)


class TestAperturaYLitreado(PoolTestCase):

    def test_abrir_cubeta_devuelve_el_sobrante_al_pool(self):
        """
        Abrir una cubeta y llenar 10 envases de 1lt dejaba 9 litros
        evaporados. Ahora se quedan a granel.
        """
        pos_db.cargar_produccion_batch("20260727-1", 8.0, LT_POR_CUBETA)
        r = pos_db.abrir_cubeta(env_1lt=10, env_05lt=0)

        self.assertEqual(self.stock("CUB"), 0)
        self.assertEqual(self.stock("M1LT"), 10)
        self.assertAlmostEqual(r["pool_despues"], LT_POR_CUBETA - 10, places=3)

    def test_abrir_cubeta_conserva_los_litros_totales(self):
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 2 * LT_POR_CUBETA + 5.0)
        pos_db.abrir_cubeta(env_1lt=12, env_05lt=4)

        en_cubetas = self.stock("CUB") * LT_POR_CUBETA
        en_envases = self.stock("M1LT") * 1.0 + self.stock("M05") * 0.5
        total = en_cubetas + en_envases + pos_db.get_pool_manteca()

        self.assertAlmostEqual(total, 2 * LT_POR_CUBETA + 5.0, places=3)

    def test_litrear_de_pool_no_consume_cubeta_sellada(self):
        """El remanente flotante se puede envasar sin romper una cubeta."""
        pos_db.cargar_produccion_batch("20260727-1", 8.0, LT_POR_CUBETA + 6.0)
        self.assertEqual(self.stock("CUB"), 1)

        r = pos_db.litrear_de_pool(env_1lt=4, env_05lt=2)

        self.assertEqual(self.stock("CUB"), 1)          # intacta
        self.assertEqual(self.stock("M1LT"), 4)
        self.assertEqual(self.stock("M05"), 2)
        self.assertAlmostEqual(r["pool_despues"], 1.0, places=3)

    def test_litrear_mas_de_lo_que_hay_falla(self):
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 3.0)
        with self.assertRaises(ValueError):
            pos_db.litrear_de_pool(env_1lt=10, env_05lt=0)

    def test_abrir_cubeta_no_puede_envasar_mas_de_lo_disponible(self):
        pos_db.cargar_produccion_batch("20260727-1", 8.0, LT_POR_CUBETA)
        with self.assertRaises(ValueError):
            pos_db.abrir_cubeta(env_1lt=25, env_05lt=0)


class TestLedger(PoolTestCase):

    def test_el_saldo_sale_del_ultimo_renglon(self):
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 10.0)
        pos_db.cargar_produccion_batch("20260727-2", 8.0, 10.0)

        hist = pos_db.get_pool_historial(limit=5)
        self.assertEqual(hist[0]["lt_saldo"], pos_db.get_pool_manteca())
        self.assertEqual(hist[0]["cubetas_emitidas"], 1)

    def test_ajuste_por_conteo_fisico(self):
        pos_db.cargar_produccion_batch("20260727-1", 8.0, 10.0)
        r = pos_db.ajustar_pool_manteca(8.5, nota="merma en trasvase")

        self.assertAlmostEqual(r["delta"], -1.5, places=3)
        self.assertAlmostEqual(pos_db.get_pool_manteca(), 8.5, places=3)

    def test_ajuste_negativo_rechazado(self):
        with self.assertRaises(ValueError):
            pos_db.ajustar_pool_manteca(-1.0)


class TestIntegracionBatch(PoolTestCase):

    def test_cargar_produccion_desde_un_batch_real(self):
        """
        Un batch de 30 kg de grasa rinde ~21.9 lt = 1.15 cubetas.
        Sube 1 cubeta y deja ~2.9 lt flotando.
        """
        from pos import cargar_produccion

        batch = Batch(
            id="20260727-1", fecha="2026-07-27", hora="08:00",
            proveedor="JC", costo_kg=25.0, temp_entrada="fria",
            composicion="mixto", operador="op",
            kg_grasa=30.0, kg_chi=8.0,
        )
        res = cargar_produccion(batch, Config())

        self.assertEqual(res["cubetas_emitidas"], 1)
        self.assertEqual(self.stock("CUB"), 1)
        self.assertAlmostEqual(self.stock("CHI"), 8.0, places=3)
        self.assertGreater(res["pool_despues"], 0)
        self.assertLess(res["pool_despues"], LT_POR_CUBETA)


if __name__ == "__main__":
    unittest.main()
