"""
pos_ticket.py — Módulo de impresión térmica para bayoSys
Dispositivo: FMD POS58 (VID=0x0416, PID=0x5011)
Papel: 58mm | Protocolo: ESC/POS via python-escpos

CAMBIOS junio 2026:
  - item() ahora acepta cantidad + unidad ("kg" | "pza") + precio_unit
    en lugar de solo kg/precio_kg — soporta manteca, chorizo, cubeta
  - encabezado de columnas se ajusta según si el ticket es mixto
  - item_kg() se conserva como alias de compatibilidad

CAMBIOS agosto 2026:
  - El ticket se arma contra un dispositivo intercambiable. Con la POS58
    conectada sale en papel; sin ella sale como string, y así se puede
    iterar el diseño sin quemar rollo en cada prueba:

        python3 -c "from pos_ticket import ticket_texto; print(ticket_texto(1))"

  - El avance de papel antes del corte dejó de estar enterrado en
    site-packages y vive en LINEAS_AVANCE_CORTE
  - Codepage explícito para que los acentos no salgan como basura
  - Código de barras del folio, para levantar el ticket con el escáner

Uso básico:
    from pos_ticket import Ticket
    with Ticket() as t:
        t.encabezado(folio="0042")
        t.item("Chicharrón", 1.250, 230.00, unidad="kg")
        t.item("Manteca 1lt", 2, 35.00, unidad="pza")
        t.total()
        t.corte()
"""

from datetime import datetime
from typing import Optional
import os
import sys

# La librería del dispositivo es opcional a propósito: generar el TEXTO del
# ticket no necesita impresora ni driver USB. Si no está instalada, todo el
# módulo sigue sirviendo para diseñar y probar; solo falla al conectar.
try:
    from escpos.printer import Usb
except ImportError:                                  # pragma: no cover
    Usb = None

# ── Configuración del dispositivo ─────────────────────────────────────────────
VID         = 0x0416
PID         = 0x5011
USB_TIMEOUT = 5000   # ms

# Perfil del dispositivo. Define ancho en columnas, codepages y capacidades
# gráficas. Sin él python-escpos asume un genérico y el centrado se calcula
# contra un ancho que no es el real, así que el texto sale corrido.
# VERIFICAR EN PAPEL: si el centrado sigue mal, probar otros perfiles de
# `python3 -c "from escpos.profile import get_profile; ..."`.
PERFIL = "POS-5890"

# Las térmicas arrancan en CP437, que no trae los acentos del español: sin
# fijar esto, "chicharrón" sale como basura en el ticket del cliente.
# VERIFICAR EN PAPEL con: á é í ó ú ñ Ñ ° $
CODEPAGE = "CP850"

# Líneas que se alimentan antes de cortar. Calibrado a la barra de arranque
# de la POS58, que está 2-3 cm por encima del cabezal: con menos, la última
# línea se queda dentro del mecanismo y se arranca a media palabra.
#
# NO BAJAR A 0. El avance no es desperdicio, es lo que deja el texto fuera
# de la barra. Se calibra en papel: probar 2, luego 3, y quedarse con el
# primero que corte limpio.
LINEAS_AVANCE_CORTE = 3

# ── Constantes de negocio ──────────────────────────────────────────────────────
NEGOCIO   = "PRODUCTOS EL BAYO"
GIRO      = "chicharrón y manteca"
CIUDAD    = "HERMOSILLO, SON."
DIRECCION = "Lauro Galvez #171"
COLONIA   = "Col. Palo Verde"

# El ticket no ampara fiscalmente. Se dice en el papel para no discutirlo
# después con quien lo quiera presentar como factura. Cabe en 32 columnas:
# la versión larga ("Este ticket no es comprobante fiscal") se envolvía.
NOTA_FISCAL = "No es comprobante fiscal"

# Ancho útil en caracteres con Font A en papel de 58mm. Todo el diseño vive
# dentro de estas columnas: una línea de 33 no se recorta, se envuelve, y
# rompe la alineación de todo lo que sigue.
COLS = 32

# Logo opcional. Si el archivo no está, el ticket sale sin logo — nunca se
# pierde una venta por un recurso gráfico ausente.
RUTA_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "assets", "logo_ticket.png")

# QR al pie. Cuesta papel y en un ticket de mostrador de $80 no se paga solo,
# así que va apagado por default.
QR_ACTIVO    = False
QR_CONTENIDO = ""


class ErrorImpresora(Exception):
    """Error al conectar o imprimir — no debe tumbar el flujo del POS."""
    pass


# ── DISPOSITIVO DE PAPEL SIMULADO ─────────────────────────────────────────────

class LienzoTexto:
    """
    Impresora de mentiras. Acepta el mismo vocabulario que el objeto de
    python-escpos pero acumula texto plano en vez de mandar bytes al USB.

    Existe para poder ver el ticket sin encender la impresora. Respeta el
    ancho real del papel y la alineación, así que lo que se ve aquí es lo
    que sale impreso — con una salvedad: el doble alto no se puede simular
    en una terminal, solo el doble ancho (que sí consume el doble de
    columnas y es el que rompe el layout).
    """

    def __init__(self, cols: int = COLS):
        self.cols     = cols
        self.lineas: list[str] = []
        self._buf     = ""
        self._align   = "left"
        self._doble_w = False

    # -- vocabulario ESC/POS que consume Ticket --------------------------------

    def set(self, align=None, bold=None, double_height=None,
            double_width=None, **_) -> None:
        if align is not None:
            self._align = align
        if double_width is not None:
            self._doble_w = double_width

    def text(self, s: str) -> None:
        for ch in s:
            if ch == "\n":
                self._volcar()
            else:
                self._buf += ch

    def ln(self, n: int = 1) -> None:
        self._volcar()
        self.lineas.extend([""] * max(0, n))

    def charcode(self, code: str) -> None:
        pass

    def cut(self, mode: str = "FULL", feed: bool = True) -> None:
        self._volcar()

    def image(self, *_a, **_k) -> None:
        self._volcar()
        self.lineas.append(self._alinear("[logo]", self.cols))

    def barcode(self, code: str, bc: str = "CODE39", **_k) -> None:
        self._volcar()
        self.lineas.append(self._alinear(f"|||| {code} ||||", self.cols))

    def qr(self, content: str, **_k) -> None:
        self._volcar()
        self.lineas.append(self._alinear("[QR]", self.cols))

    def close(self) -> None:
        self._volcar()

    # -- interno ---------------------------------------------------------------

    def _ancho_util(self) -> int:
        """Con doble ancho cada carácter ocupa dos columnas: caben la mitad."""
        return self.cols // 2 if self._doble_w else self.cols

    def _alinear(self, texto: str, ancho: int) -> str:
        if self._align == "center":
            return texto.center(ancho).rstrip()
        if self._align == "right":
            return texto.rjust(ancho)
        return texto

    def _volcar(self) -> None:
        if self._buf == "":
            return
        self.lineas.append(self._alinear(self._buf, self._ancho_util()))
        self._buf = ""

    def render(self) -> str:
        self._volcar()
        return "\n".join(self.lineas)


# ── REGLA DE ANCHURA ──────────────────────────────────────────────────────────

def lineas_excedidas(texto: str, cols: int = COLS) -> list:
    """
    Renglones que no caben en el papel, como (n° de línea, ancho, contenido).

    Una línea de 33 caracteres no se recorta: la impresora la envuelve y
    desde ahí toda la alineación de abajo queda corrida. Por eso esto se
    revisa en las pruebas y no a ojo sobre el papel.
    """
    return [(i, len(l), l)
            for i, l in enumerate(texto.split("\n"), 1) if len(l) > cols]


def validar_ancho(texto: str, cols: int = COLS) -> bool:
    """True si ninguna línea excede el ancho del papel."""
    return not lineas_excedidas(texto, cols)


def _envolver(texto: str, cols: int = COLS) -> list:
    """
    Parte el texto en renglones que caben en el papel.

    Envolver a mano es preferible a dejar que lo haga la impresora: así el
    corte cae entre palabras y no a media palabra, y lo que se ve en
    ticket_texto() es lo mismo que sale impreso.
    """
    import textwrap

    salida = []
    for parrafo in texto.split("\n"):
        if not parrafo:
            salida.append("")
        else:
            salida.extend(textwrap.wrap(parrafo, cols) or [""])
    return salida


class Ticket:
    """
    Contexto de impresión para un ticket completo.
    Soporta items mixtos: kg (chicharrón) y piezas (manteca, chorizo, cubeta).

    Sin argumentos abre la POS58 por USB. Con `dispositivo=LienzoTexto()`
    escribe a memoria y no toca hardware.

    Ejemplo:
        with Ticket() as t:
            t.encabezado(folio="0042")
            t.item("Chicharrón", 1.250, 230.00, unidad="kg")
            t.item("Manteca 1lt", 2, 35.00, unidad="pza")
            t.separador()
            t.total()
            t.corte()
    """

    def __init__(self, dispositivo=None):
        self._p = dispositivo
        self._propio = dispositivo is None   # ¿lo abrimos nosotros?
        self._items: list[dict] = []
        self._folio: str = ""

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self):
        if not self._propio:
            return self                       # dispositivo inyectado, ya listo

        if Usb is None:
            raise ErrorImpresora(
                "python-escpos no está instalado — usa ticket_texto() para "
                "generar el ticket sin impresora"
            )
        try:
            # El perfil es lo que le dice a la librería cuántas columnas tiene
            # el papel. Si el nombre no existe en esta versión, más vale un
            # ticket con el genérico que ningún ticket.
            try:
                self._p = Usb(VID, PID, timeout=USB_TIMEOUT,
                              in_ep=0x87, out_ep=0x06, profile=PERFIL)
            except Exception:
                self._p = Usb(VID, PID, timeout=USB_TIMEOUT,
                              in_ep=0x87, out_ep=0x06)
        except Exception as e:
            raise ErrorImpresora(f"no se pudo conectar a la impresora: {e}") from e

        # Acentos. Si el codepage no está disponible en este perfil, seguimos
        # con el default antes que abortar el ticket.
        try:
            self._p.charcode(CODEPAGE)
        except Exception:
            pass

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._p and self._propio:
            try:
                self._p.close()
            except Exception:
                pass
        return False  # no suprimir excepciones

    # ── Secciones del ticket ───────────────────────────────────────────────────

    def logo(self) -> "Ticket":
        """
        Logo rasterizado, si existe. Umbral duro de blanco y negro: el
        difuminado se ve sucio en térmica.

        Nunca revienta el ticket — si falta el archivo o falla la conversión,
        simplemente no hay logo.
        """
        ruta = os.path.normpath(RUTA_LOGO)
        if not os.path.exists(ruta):
            return self
        try:
            self._p.set(align="center")
            self._p.image(ruta)
        except Exception:
            pass
        return self

    def encabezado(self, folio: str = "") -> "Ticket":
        """Imprime el header del negocio con fecha/hora y folio."""
        self._folio = folio
        p = self._p

        self.logo()

        # El nombre del negocio es lo más grande del papel junto con el total.
        # Doble ancho parte las columnas a la mitad: 17 caracteres caben en 16
        # celdas justas, así que va a doble alto solamente.
        p.set(align="center", bold=True, double_height=True, double_width=False)
        p.text(f"{NEGOCIO}\n")

        p.set(align="center", bold=False, double_height=False)
        p.text(f"{GIRO}\n")
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
        self._p.set(align="left", double_width=False)
        self._p.text(char * COLS + "\n")
        return self

    def total(self, nota: str = "") -> "Ticket":
        """Imprime el gran total y nota opcional."""
        gran_total = sum(i["subtotal"] for i in self._items)

        self.separador("=")

        # El total es el dato que el cliente busca en el papel: va a doble
        # alto y doble ancho, lo más grande del ticket. La etiqueta se queda
        # en tamaño normal arriba — con doble ancho solo caben 16 celdas y el
        # importe se las lleva todas.
        self._p.set(align="right", bold=True,
                    double_height=False, double_width=False)
        self._p.text("TOTAL\n")
        self._p.set(align="right", bold=True,
                    double_height=True, double_width=True)
        self._p.text(f"${gran_total:>9.2f}\n")
        self._p.set(bold=False, double_height=False, double_width=False)

        if nota:
            self.separador()
            self._p.set(align="left")
            for linea in _envolver(nota):
                self._p.text(f"{linea}\n")

        self.separador()
        self._p.set(align="center")
        self._p.text("Gracias por su compra\n")
        self._p.text(f"{NOTA_FISCAL}\n")

        return self

    def codigo_barras(self, folio: str = "") -> "Ticket":
        """
        Folio como código de barras, para levantar el ticket con el escáner
        USB del mostrador en devoluciones o reimpresiones.

        El número va también en texto legible: si el código se borra por
        calor o roce, el ticket sigue sirviendo.
        """
        folio = folio or self._folio
        if not folio:
            return self

        codigo = str(folio).zfill(6)
        try:
            self._p.set(align="center")
            # pos="BELOW" hace que la impresora ponga el número debajo de las
            # barras; el text() de respaldo cubre el caso en que el perfil no
            # soporte esa opción.
            self._p.barcode(codigo, "CODE39", width=2, height=48,
                            pos="BELOW", align_ct=True)
        except Exception:
            try:
                self._p.set(align="center")
                self._p.text(f"*{codigo}*\n")
            except Exception:
                pass
        return self

    def qr(self, contenido: str = "") -> "Ticket":
        """QR opcional al pie — apagado por default, cuesta papel."""
        contenido = contenido or QR_CONTENIDO
        if not (QR_ACTIVO and contenido):
            return self
        try:
            self._p.set(align="center")
            self._p.qr(contenido, size=4)
        except Exception:
            pass
        return self

    def corte(self, parcial: bool = False) -> "Ticket":
        """
        Avanza el papel lo mínimo y corta.

        El avance lo controlamos nosotros: cut(feed=True) —el default de la
        librería— alimenta un número fijo de líneas que no está expuesto, y
        eran las 5 o 6 líneas que se desperdiciaban en cada ticket.

        `parcial` default en False: la POS58 no trae cuchilla automática, así
        que un corte parcial no corta nada pero la alimentación se paga igual.

        ADVERTENCIA: feed=False puede emitir un comando ESC/POS distinto al
        de la rama normal. VERIFICAR EN PAPEL que la POS58 responda; si deja
        de cortar, volver a feed=True y recortar el contenido previo.
        """
        self._p.ln(LINEAS_AVANCE_CORTE)
        try:
            self._p.cut(mode="PART" if parcial else "FULL", feed=False)
        except TypeError:
            # Versión de python-escpos sin el parámetro feed.
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


# ── ARMADO DEL TICKET — una sola receta para papel y para texto ───────────────

def _fila(etiqueta: str, importe: float) -> str:
    """Etiqueta a la izquierda, importe pegado a la derecha, en 32 columnas."""
    monto = f"${importe:,.2f}"
    return f"{etiqueta:<{COLS - len(monto)}}{monto}"


def _nota_de_pagos(pagos: dict, cambio: float) -> str:
    """
    Bloque de formas de pago, un renglón por método. Recibe importes ya
    calculados — aquí no se suma ni se recalcula nada.

    Van en renglones separados y no en una sola línea: juntos pasaban de 32
    columnas y la impresora los envolvía, corriendo todo lo de abajo.
    """
    filas = [_fila(etiqueta, pagos[clave])
             for clave, etiqueta in (("efectivo", "Efectivo"),
                                     ("transfer", "Transferencia"),
                                     ("tarjeta",  "Tarjeta"))
             if pagos.get(clave, 0) > 0]
    if cambio > 0:
        filas.append(_fila("Cambio", cambio))
    return "\n".join(filas)


def _armar(tk: "Ticket", t, items, pagos: dict, cambio: float) -> None:
    """
    Escribe el ticket completo sobre `tk`, venga de papel o de memoria.

    Los importes llegan ya calculados desde el POS: aquí no se recalcula
    nada, solo se acomoda en 32 columnas.
    """
    tk.encabezado(folio=str(t["id"]))
    for item in items:
        unidad = "kg" if item["sku"] == "CHI" else "pza"
        tk.item(item["descripcion"], item["cantidad"],
                item["precio_unit"], unidad=unidad)
    tk.total(nota=_nota_de_pagos(pagos, cambio))
    tk.codigo_barras(str(t["id"]))
    tk.qr()


def _leer_ticket(ticket_id: int):
    """Trae el ticket y sus items de pos.db. Lanza si no existe."""
    from pos_db import get_ticket_items, get_conn

    with get_conn() as conn:
        t = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    if not t:
        raise ErrorImpresora(f"ticket #{ticket_id} no encontrado en pos.db")
    return t, get_ticket_items(ticket_id)


def ticket_texto(ticket_id: int, pagos: Optional[dict] = None,
                 cambio: float = 0.0) -> str:
    """
    El ticket tal como saldría impreso, como string y sin tocar hardware.

    Es la forma de iterar el diseño sin quemar papel en cada prueba:

        python3 -c "from pos_ticket import ticket_texto; print(ticket_texto(1))"
    """
    t, items = _leer_ticket(ticket_id)
    if pagos is None:
        pagos = {}
        for clave, col in (("efectivo", "pago_efectivo"),
                           ("transfer", "pago_transfer"),
                           ("tarjeta",  "pago_tarjeta")):
            try:
                pagos[clave] = t[col] or 0.0
            except (IndexError, KeyError):
                pagos[clave] = 0.0
        try:
            cambio = t["cambio"] or 0.0
        except (IndexError, KeyError):
            cambio = 0.0

    lienzo = LienzoTexto()
    tk = Ticket(dispositivo=lienzo)
    with tk:
        _armar(tk, t, items, pagos, cambio)
        tk.corte()
    return lienzo.render()


# ── PUENTE CON EL POS — imprime un ticket ya cobrado en SQLite ──────────────────

def imprimir_ticket_fisico(ticket_id: int, pagos: dict, cambio: float) -> str:
    """
    Toma un ticket_id ya cobrado (existe en pos.db) e imprime el comprobante
    físico en la POS58. No falla el flujo del POS si la impresora no responde
    — el ticket ya está cobrado y guardado, esto es solo el comprobante físico.

    Lanza ErrorImpresora si algo sale mal — quien llama decide cómo avisar
    al usuario sin revertir el cobro.
    """
    t, items = _leer_ticket(ticket_id)

    try:
        with Ticket() as tk:
            _armar(tk, t, items, pagos, cambio)
            tk.corte()
    except ErrorImpresora:
        raise
    except Exception as e:
        raise ErrorImpresora(f"fallo al imprimir ticket #{ticket_id}: {e}") from e

    return f"ticket #{ticket_id:04d} impreso en POS58"


# ── Demo / test desde CLI ──────────────────────────────────────────────────────

def _demo_texto() -> str:
    """Ticket de muestra en texto — no necesita impresora ni base de datos."""
    lienzo = LienzoTexto()
    with Ticket(dispositivo=lienzo) as t:
        t.encabezado(folio="42")
        t.item("Chicharrón",    1.250, 230.00, unidad="kg")
        t.item("Manteca 1lt",   2,      35.00, unidad="pza")
        t.item("Manteca 500ml", 1,      20.00, unidad="pza")
        t.total(nota=_nota_de_pagos({"efectivo": 400.00}, 12.50))
        t.codigo_barras("42")
        t.corte()
    return lienzo.render()


def _demo():
    """Imprime un ticket de prueba completo con items mixtos."""
    print("Enviando ticket de prueba a POS58...")
    try:
        with Ticket() as t:
            t.encabezado(folio="1")
            t.item("Chicharrón",    1.250, 230.00, unidad="kg")
            t.item("Manteca 1lt",   2,      35.00, unidad="pza")
            t.item("Manteca 500ml", 1,      20.00, unidad="pza")
            t.total(nota=_nota_de_pagos({"efectivo": 400.00}, 12.50))
            t.codigo_barras("1")
            t.corte()
        print("✓ Ticket impreso correctamente.")
    except ErrorImpresora as e:
        print(f"✗ Error de impresora: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if "--texto" in sys.argv:
        muestra = _demo_texto()
        print(muestra)
        excedidas = lineas_excedidas(muestra)
        if excedidas:
            print(f"\n! {len(excedidas)} línea(s) exceden {COLS} columnas:")
            for n, ancho, linea in excedidas:
                print(f"   línea {n}: {ancho} cols — {linea!r}")
            sys.exit(1)
        print(f"\n✓ ninguna línea excede {COLS} columnas")
    else:
        _demo()
