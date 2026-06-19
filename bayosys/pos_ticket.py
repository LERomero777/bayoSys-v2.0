"""
pos_ticket.py — Módulo de impresión térmica para bayoSys
Dispositivo: FMD POS58 (VID=0x0416, PID=0x5011)
Papel: 58mm | Protocolo: ESC/POS via python-escpos

CAMBIOS junio 2026:
  - item() ahora acepta cantidad + unidad ("kg" | "pza") + precio_unit
    en lugar de solo kg/precio_kg — soporta manteca, chorizo, cubeta
  - encabezado de columnas se ajusta según si el ticket es mixto
  - item_kg() se conserva como alias de compatibilidad

Uso básico:
    from pos_ticket import Ticket
    with Ticket() as t:
        t.encabezado(folio="0042")
        t.item("Chicharrón", 1.250, 230.00, unidad="kg")
        t.item("Manteca 1lt", 2, 35.00, unidad="pza")
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


class ErrorImpresora(Exception):
    """Error al conectar o imprimir — no debe tumbar el flujo del POS."""
    pass


class Ticket:
    """
    Contexto de impresión para un ticket completo.
    Soporta items mixtos: kg (chicharrón) y piezas (manteca, chorizo, cubeta).

    Ejemplo:
        with Ticket() as t:
            t.encabezado(folio="0042")
            t.item("Chicharrón", 1.250, 230.00, unidad="kg")
            t.item("Manteca 1lt", 2, 35.00, unidad="pza")
            t.separador()
            t.total()
            t.corte()
    """

    def __init__(self):
        self._p: Optional[Usb] = None
        self._items: list[dict] = []
        self._folio: str = ""

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self):
        try:
            self._p = Usb(VID, PID, timeout=USB_TIMEOUT, in_ep=0x87, out_ep=0x06)
        except Exception as e:
            raise ErrorImpresora(f"no se pudo conectar a la impresora: {e}") from e
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
        """Fila de títulos — producto en línea propia, cant/precio/total abajo."""
        self._p.set(align="left", bold=True)
        col = f"{'CANT':>6}{'P.UNIT':>9}{'TOTAL':>9}\n"
        self._p.text(col)
        self._p.set(bold=False)

    def item(self, producto: str, cantidad: float, precio_unit: float,
             unidad: str = "pza") -> "Ticket":
        """
        Agrega una línea de producto al ticket.
        El nombre va en su propia línea (evita truncar a 14 chars),
        cantidad/precio/total van en la línea siguiente, alineados a la derecha.

        Args:
            producto:    Nombre del producto
            cantidad:    Cantidad — kg (decimal) o piezas (entero)
            precio_unit: Precio por kg o por pieza, en pesos MXN
            unidad:      "kg" | "pza" — afecta solo el formato de impresión
        """
        subtotal = cantidad * precio_unit
        self._items.append(dict(
            producto=producto, cantidad=cantidad,
            precio_unit=precio_unit, subtotal=subtotal, unidad=unidad,
        ))

        if unidad == "kg":
            cant_str = f"{cantidad:.3f}".rstrip("0").rstrip(".") + "kg"
        else:
            cant_str = f"{cantidad:.0f}pz"

        linea1 = f"{producto[:COLS]}\n"
        linea2 = f"{cant_str:>6}{precio_unit:>9.2f}{subtotal:>9.2f}\n"

        self._p.set(align="left")
        self._p.text(linea1)
        self._p.text(linea2)

        return self

    def item_kg(self, producto: str, kg: float, precio_kg: float) -> "Ticket":
        """Alias de compatibilidad — equivalente a item(unidad='kg')."""
        return self.item(producto, kg, precio_kg, unidad="kg")

    def separador(self, char: str = "-") -> "Ticket":
        """Línea divisoria horizontal."""
        self._p.text(char * COLS + "\n")
        return self

    def total(self, nota: str = "") -> "Ticket":
        """Imprime el gran total y nota opcional."""
        gran_total = sum(i["subtotal"] for i in self._items)

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


# ── PUENTE CON EL POS — imprime un ticket ya cobrado en SQLite ──────────────────

def imprimir_ticket_fisico(ticket_id: int, pagos: dict, cambio: float) -> str:
    """
    Toma un ticket_id ya cobrado (existe en pos.db) e imprime el comprobante
    físico en la POS58. No falla el flujo del POS si la impresora no responde
    — el ticket ya está cobrado y guardado, esto es solo el comprobante físico.

    Lanza ErrorImpresora si algo sale mal — quien llama decide cómo avisar
    al usuario sin revertir el cobro.
    """
    from pos_db import get_ticket_items, get_conn

    with get_conn() as conn:
        t = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    items = get_ticket_items(ticket_id)

    if not t:
        raise ErrorImpresora(f"ticket #{ticket_id} no encontrado en pos.db")

    try:
        with Ticket() as tk:
            tk.encabezado(folio=str(ticket_id))
            for item in items:
                unidad = "kg" if item["sku"] == "CHI" else "pza"
                tk.item(
                    item["descripcion"], item["cantidad"],
                    item["precio_unit"], unidad=unidad,
                )

            nota_pago = []
            if pagos.get("efectivo", 0) > 0:
                nota_pago.append(f"Efectivo ${pagos['efectivo']:.2f}")
            if pagos.get("transfer", 0) > 0:
                nota_pago.append(f"Transfer ${pagos['transfer']:.2f}")
            if pagos.get("tarjeta", 0) > 0:
                nota_pago.append(f"Tarjeta ${pagos['tarjeta']:.2f}")
            nota = "  ".join(nota_pago)
            if cambio > 0:
                nota += f"  | Cambio ${cambio:.2f}"

            tk.total(nota=nota)
            tk.corte()
    except ErrorImpresora:
        raise
    except Exception as e:
        raise ErrorImpresora(f"fallo al imprimir ticket #{ticket_id}: {e}") from e

    return f"ticket #{ticket_id:04d} impreso en POS58"


# ── Demo / test desde CLI ──────────────────────────────────────────────────────

def _demo():
    """Imprime un ticket de prueba completo con items mixtos."""
    print("Enviando ticket de prueba a POS58...")
    try:
        with Ticket() as t:
            t.encabezado(folio="1")
            t.item("Chicharron",     1.250, 230.00, unidad="kg")
            t.item("Manteca 1lt",    2,      35.00, unidad="pza")
            t.item("Manteca 500ml",  1,      20.00, unidad="pza")
            t.separador()
            t.total(nota="Pago en efectivo")
            t.corte()
        print("✓ Ticket impreso correctamente.")
    except ErrorImpresora as e:
        print(f"✗ Error de impresora: {e}")
        sys.exit(1)


if __name__ == "__main__":
    _demo()
