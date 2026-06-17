"""
pos_ticket.py — Módulo de impresión térmica para bayoSys
Dispositivo: FMD POS58 (VID=0x0416, PID=0x5011)
Papel: 58mm | Protocolo: ESC/POS via python-escpos

Uso básico:
    from pos_ticket import Ticket
    with Ticket() as t:
        t.encabezado()
        t.item("Chicharrón", 2.5, 180.00)
        t.item("Manteca de res", 1.0, 95.00)
        t.total()
        t.corte()
"""

from escpos.printer import Usb
from datetime import datetime
from typing import Optional
import sys

# ── Configuración del dispositivo ─────────────────────────────────────────────
VID         = 0x0416
PID         = 0x5011
USB_TIMEOUT = 5000   # ms

# ── Constantes de negocio ──────────────────────────────────────────────────────
NEGOCIO   = "PRODUCTOS EL BAYO"
CIUDAD    = "HERMOSILLO, SON."
DIRECCION = "Lauro Galvez #171"
COLONIA   = "Col. Palo Verde"

# Ancho útil en chars para papel 58mm a 42 cols
COLS = 32


class Ticket:
    """
    Contexto de impresión para un ticket completo.

    Ejemplo:
        with Ticket() as t:
            t.encabezado(folio="0042")
            t.item("Chicharrón", 2.5, 180.00)
            t.separador()
            t.total()
            t.corte()
    """

    def __init__(self):
        self._p: Optional[Usb] = None
        self._items: list[tuple[str, float, float]] = []
        self._folio: str = ""

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self):
        try:
            self._p = Usb(VID, PID, timeout=USB_TIMEOUT, in_ep=0x87, out_ep=0x06)
        except Exception as e:
            print(f"[pos_ticket] Error al conectar impresora: {e}", file=sys.stderr)
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._p:
            try:
                self._p.close()
            except Exception:
                pass
        return False  # no suprimir excepciones

    # ── Secciones del ticket ───────────────────────────────────────────────────

    def encabezado(self, folio: str = "") -> "Ticket":
        """Imprime el header del negocio con fecha/hora y folio."""
        self._folio = folio
        p = self._p

        p.set(align="center", bold=True, double_height=True, double_width=False)
        p.text(f"{NEGOCIO}\n")

        p.set(align="center", bold=False, double_height=False)
        p.text(f"{CIUDAD}\n")
        p.text(f"{DIRECCION}\n")
        p.text(f"{COLONIA}\n")

        self.separador("=")

        p.set(align="left", bold=False)
        ahora = datetime.now().strftime("%d/%m/%Y  %H:%M")
        p.text(f"Fecha: {ahora}\n")
        if folio:
            p.text(f"Folio: {folio.zfill(6)}\n")

        self.separador()
        self._encabezado_columnas()
        self.separador()

        return self

    def _encabezado_columnas(self) -> None:
        """Fila de títulos de columnas."""
        # PRODUCTO      CANT   PRECIO  SUBTOT
        # 58mm / 32 cols útiles
        self._p.set(align="left", bold=True)
        col = f"{'PRODUCTO':<14}{'KG':>5}{'$/KG':>7}{'TOTAL':>6}\n"
        self._p.text(col)
        self._p.set(bold=False)

    def item(self, producto: str, kg: float, precio_kg: float) -> "Ticket":
        """
        Agrega una línea de producto al ticket.

        Args:
            producto:   Nombre del producto (se trunca a 14 chars)
            kg:         Cantidad en kilogramos
            precio_kg:  Precio por kilogramo en pesos MXN
        """
        subtotal = kg * precio_kg
        self._items.append((producto, kg, precio_kg, subtotal))

        nombre = producto[:14].ljust(14)
        kg_str  = f"{kg:5.2f}"
        prc_str = f"{precio_kg:7.2f}"
        sub_str = f"{subtotal:6.2f}"

        linea = f"{nombre}{kg_str}{prc_str}{sub_str}\n"
        self._p.set(align="left")
        self._p.text(linea)

        return self

    def separador(self, char: str = "-") -> "Ticket":
        """Línea divisoria horizontal."""
        self._p.text(char * COLS + "\n")
        return self

    def total(self, nota: str = "") -> "Ticket":
        """Imprime el gran total y nota opcional."""
        gran_total = sum(i[3] for i in self._items)

        self.separador("=")
        self._p.set(align="right", bold=True, double_height=False)
        self._p.text(f"TOTAL:  ${gran_total:>8.2f}\n")
        self._p.set(bold=False)

        if nota:
            self.separador()
            self._p.set(align="left")
            self._p.text(f"{nota}\n")

        self.separador()
        self._p.set(align="center")
        self._p.text("Gracias por su compra\n")
        self._p.text("* bayoSys v2.0 *\n")
        self._p.text("\n\n")

        return self

    def corte(self, parcial: bool = True) -> "Ticket":
        """Avanza papel y corta."""
        self._p.cut(mode="PART" if parcial else "FULL")
        return self

    # ── Utilidades extra ───────────────────────────────────────────────────────

    def texto_libre(self, texto: str, align: str = "left",
                    bold: bool = False) -> "Ticket":
        """Imprime texto libre con formato opcional."""
        self._p.set(align=align, bold=bold)
        self._p.text(f"{texto}\n")
        return self

    def lote(self, numero: str, turno: str = "",
             operador: str = "") -> "Ticket":
        """Bloque de datos de lote de producción (para tickets internos)."""
        self.separador()
        self._p.set(align="left")
        self._p.text(f"Lote   : {numero}\n")
        if turno:
            self._p.text(f"Turno  : {turno}\n")
        if operador:
            self._p.text(f"Operador: {operador}\n")
        self.separador()
        return self


# ── Demo / test desde CLI ──────────────────────────────────────────────────────

def _demo():
    """Imprime un ticket de prueba completo."""
    print("Enviando ticket de prueba a POS58...")
    try:
        with Ticket() as t:
            t.encabezado(folio="1")
            t.item("Chicharron",     2.500, 180.00)
            t.item("Manteca de res", 1.000,  95.00)
            t.item("Cueritos",       0.750, 120.00)
            t.separador()
            t.total(nota="Pago en efectivo")
            t.corte()
        print("✓ Ticket impreso correctamente.")
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    _demo()
