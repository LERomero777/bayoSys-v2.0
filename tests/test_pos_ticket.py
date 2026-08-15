"""
test_pos_ticket.py — bayoSys · Productos El Bayo
Pruebas de bayosys/pos_ticket.py — diseño del ticket en papel de 58mm.

Todas corren SIN impresora conectada: el ticket se arma contra LienzoTexto,
que acepta el mismo vocabulario ESC/POS pero acumula texto en memoria. Eso
es justo lo que permite iterar el diseño sin quemar rollo.

Corre con: python3 -m unittest tests/test_pos_ticket.py -v
(desde la raíz del repo)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bayosys"))

from pos_ticket import (
    COLS, LINEAS_AVANCE_CORTE, NOTA_FISCAL,
    LienzoTexto, Ticket,
    lineas_excedidas, validar_ancho, ticket_texto,
    _nota_de_pagos, _demo_texto,
)


def _armar(**kw) -> str:
    """Ticket de muestra sobre lienzo de texto."""
    lienzo = LienzoTexto()
    with Ticket(dispositivo=lienzo) as t:
        t.encabezado(folio=kw.get("folio", "42"))
        for producto, cant, precio, unidad in kw.get("items", [
            ("Chicharrón", 1.250, 230.00, "kg"),
            ("Manteca 1lt", 2, 35.00, "pza"),
        ]):
            t.item(producto, cant, precio, unidad=unidad)
        t.total(nota=kw.get("nota", ""))
        t.codigo_barras(kw.get("folio", "42"))
        t.corte()
    return lienzo.render()


class TestReglaDeAnchura(unittest.TestCase):
    """§6.D.9 — una línea de 33 no se recorta, se envuelve y rompe todo abajo."""

    def test_ticket_completo_cabe_en_32_columnas(self):
        texto = _armar()
        self.assertEqual(lineas_excedidas(texto), [],
                         f"renglones fuera de {COLS} columnas: "
                         f"{lineas_excedidas(texto)}")

    def test_el_demo_cabe(self):
        self.assertTrue(validar_ancho(_demo_texto()))

    def test_el_validador_si_detecta_una_linea_larga(self):
        # si esto pasa, el validador no sirve de nada
        largo = "x" * (COLS + 1)
        self.assertFalse(validar_ancho(largo))
        self.assertEqual(lineas_excedidas(largo)[0][1], COLS + 1)

    def test_nombre_de_producto_larguisimo_no_desborda(self):
        texto = _armar(items=[
            ("Chicharrón de lomo especial reserva extra", 1.0, 230.0, "kg"),
        ])
        self.assertTrue(validar_ancho(texto))

    def test_bloque_de_pagos_no_desborda(self):
        # los tres métodos + cambio juntos en una línea pasaban de 32
        nota = _nota_de_pagos(
            {"efectivo": 1234.56, "transfer": 999.99, "tarjeta": 500.00},
            cambio=87.65,
        )
        self.assertTrue(validar_ancho(nota), nota)
        self.assertEqual(len(nota.split("\n")), 4)

    def test_total_de_cinco_digitos_no_desborda(self):
        texto = _armar(items=[("Chicharrón", 99.999, 230.0, "kg")])
        self.assertTrue(validar_ancho(texto))


class TestGeneracionSinImpresora(unittest.TestCase):
    """§6.D.8 — el texto del ticket debe salir sin hardware conectado."""

    def test_retorna_string_no_vacio(self):
        texto = _armar()
        self.assertIsInstance(texto, str)
        self.assertIn("PRODUCTOS EL BAYO", texto)

    def test_los_importes_llegan_ya_calculados(self):
        # 1.250 kg x 230.00 = 287.50 — el módulo acomoda, no recalcula
        self.assertIn("287.50", _armar())

    def test_el_total_aparece(self):
        self.assertIn("TOTAL", _armar())

    def test_falta_de_logo_no_rompe_el_ticket(self):
        # assets/logo_ticket.png no existe en el repo todavía
        import pos_ticket
        anterior = pos_ticket.RUTA_LOGO
        pos_ticket.RUTA_LOGO = "/ruta/que/no/existe/logo.png"
        try:
            self.assertIn("PRODUCTOS EL BAYO", _armar())
        finally:
            pos_ticket.RUTA_LOGO = anterior


class TestPieDelTicket(unittest.TestCase):

    def test_codigo_de_barras_lleva_el_numero_legible(self):
        # §6.D.5 — si el código se borra por calor, el ticket sigue usable
        texto = _armar(folio="42")
        self.assertIn("000042", texto)

    def test_nota_fiscal_presente_y_dentro_del_ancho(self):
        self.assertIn(NOTA_FISCAL, _armar())
        self.assertLessEqual(len(NOTA_FISCAL), COLS)

    def test_qr_apagado_por_default(self):
        self.assertNotIn("[QR]", _armar())


class TestAvanceDeCorte(unittest.TestCase):
    """§6.D.1 — el avance es constante nombrada, no número mágico."""

    def test_la_constante_deja_papel_suficiente(self):
        # 0 deja la última línea dentro del mecanismo y se arranca a media
        # palabra; más de 6 es el desperdicio que se venía pagando
        self.assertGreaterEqual(LINEAS_AVANCE_CORTE, 2)
        self.assertLessEqual(LINEAS_AVANCE_CORTE, 6)

    def test_el_corte_alimenta_esas_lineas(self):
        lienzo = LienzoTexto()
        with Ticket(dispositivo=lienzo) as t:
            t.texto_libre("fin")
            t.corte()
        # "fin" + las líneas en blanco del avance
        self.assertEqual(len(lienzo.lineas), 1 + LINEAS_AVANCE_CORTE)


class TestLienzoTexto(unittest.TestCase):
    """El lienzo tiene que mentir igual que el papel, o las pruebas no valen."""

    def test_centrado_contra_el_ancho_real(self):
        l = LienzoTexto()
        l.set(align="center")
        l.text("ab\n")
        self.assertEqual(l.lineas[0], " " * ((COLS - 2) // 2) + "ab")

    def test_doble_ancho_parte_las_columnas_a_la_mitad(self):
        # con doble ancho cada carácter ocupa dos celdas: solo caben 16
        l = LienzoTexto()
        l.set(align="right", double_width=True)
        l.text("$1.00\n")
        self.assertEqual(len(l.lineas[0]), COLS // 2)

    def test_acentos_sobreviven(self):
        l = LienzoTexto()
        l.text("chicharrón ñ Ñ á é í ó ú °\n")
        self.assertIn("chicharrón", l.lineas[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
