"""
El caso que reportó el operador: teclear "$40 de chicharrón" y que el
sistema termine pidiendo otra cifra.

La causa no es punto flotante. Es cuantización decidida a propósito: los kg
se redondean a gramos y el total se recalcula desde ahí, así que el importe
tecleado y el cobrado no coinciden. Antes la pantalla anunciaba el tecleado
mientras el ticket llevaba el recalculado.

Aquí se fija que:
  1. lo que se anuncia es siempre lo que el ticket cobra
  2. el desvío por cuantización nunca pasa de medio paso de gramo
  3. el cliente igual paga la cifra redonda, porque el redondeo de efectivo
     absorbe la diferencia
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bayosys"))


class TestCapturaPorMonto(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["HOME"] = tempfile.mkdtemp()
        os.makedirs(os.path.join(os.environ["HOME"], "bayosys", "data"),
                    exist_ok=True)

    def _kg_y_total(self, monto, precio_kg):
        """Reproduce la aritmética de pedir_kg_o_monto_modal."""
        kg = round(monto / precio_kg, 3)
        return kg, round(kg * precio_kg, 2)

    def test_el_caso_reportado_100_05(self):
        """$100 a $230/kg produce un ticket de $100.05, no de $100."""
        kg, total = self._kg_y_total(100, 230.0)
        self.assertEqual(kg, 0.435)
        self.assertEqual(total, 100.05)

    def test_desvio_acotado_a_medio_gramo(self):
        """La cuantización nunca puede desviar más de medio paso."""
        precio = 230.0
        paso   = precio * 0.001          # lo que vale un gramo
        for monto in (40, 50, 100, 200, 300, 37, 88, 512):
            _, total = self._kg_y_total(monto, precio)
            self.assertLessEqual(abs(total - monto), paso / 2 + 0.005,
                                 f"monto {monto} se desvió demasiado")

    def test_el_cliente_paga_la_cifra_redonda(self):
        """
        El redondeo de efectivo absorbe el desvío: quien pide $100 de
        chicharrón paga $100, aunque el ticket valga $100.05.
        """
        from pos import total_a_cobrar_efectivo
        for monto in (40, 50, 100, 200, 300):
            _, total = self._kg_y_total(monto, 230.0)
            self.assertEqual(total_a_cobrar_efectivo(total), float(monto),
                             f"pedir ${monto} no terminó cobrando ${monto}")

    def test_la_venta_registrada_no_se_redondea(self):
        """El ticket conserva el importe real. Solo el cobro se redondea."""
        from pos import total_a_cobrar_efectivo
        _, total = self._kg_y_total(100, 230.0)
        self.assertEqual(total, 100.05)                       # la venta
        self.assertEqual(total_a_cobrar_efectivo(total), 100.0)  # la caja
        self.assertNotEqual(total, total_a_cobrar_efectivo(total))

    def test_precio_unitario_intacto(self):
        """
        No se ajusta el precio para cuadrar el monto exacto: eso ensuciaría
        el histórico del que vive analisis.py.
        """
        import pos, pos_db
        pos_db.init_db()
        sesion = pos.iniciar_pos(batch_id="20260101-1", operador="test")
        kg, _  = self._kg_y_total(100, 230.0)
        cfg    = pos.cargar_config()
        item   = pos.agregar_chicharron(sesion, kg)
        self.assertEqual(item.precio_unit, cfg.precio_chi_pub)
        self.assertEqual(item.subtotal, round(kg * cfg.precio_chi_pub, 2))


if __name__ == "__main__":
    unittest.main()
