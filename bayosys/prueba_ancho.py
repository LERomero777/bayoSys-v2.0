"""
prueba_ancho.py — Decide si la Font B de la POS58 son 48 o 42 columnas.

No es una preferencia de diseño: es un dato físico del cabezal. El perfil
POS-5890 que usa pos_ticket.py declara Font B a 42 columnas (glifo de 9 px:
384 / 9 = 42.67). El documento de la Parte E afirma 48 (glifo de 8 px).
Uno de los dos está mal, y python-escpos no lo va a decir — solo consulta las
columnas del perfil dentro de block_text(), que el módulo no llama nunca.
text() manda los bytes tal cual: quien envuelve es el firmware, y eso se ve
hasta el papel.

    python3 prueba_ancho.py            # las dos maquetas en pantalla
    python3 prueba_ancho.py --papel    # la regla a la POS58 — esto decide

CÓMO SE LEE LA PRUEBA DE PAPEL
    La regla de Font B son 48 caracteres en una sola línea lógica.
      · Si sale en UN renglón que termina en 8  → COLS = 48
      · Si sale en DOS renglones, el segundo empezando en 3 → COLS = 42
    Las barras sólidas de 42 y 48 confirman: si son igual de largas, el
    cabezal está cortando a 42.

Ni este archivo ni la prueba tocan pos.db. Solo hablan con la impresora.
"""

from typing import Optional
import sys

from pos_ticket import VID, PID, USB_TIMEOUT, PERFIL

ESC = b"\x1b"
GS  = b"\x1d"

# Catálogo real del negocio (SKUs_DEFAULT de pos_db.py). El nombre más largo
# es "Artículo libre" con 14 caracteres — el que decide si el ancho del campo
# de nombre alcanza o si medio ticket se va a dos líneas.
CATALOGO = [
    ("Chicharrón",     1.253, "kg",  230.00),
    ("Manteca 1lt",    2,     "pza",  35.00),
    ("Manteca 500ml",  1,     "pza",  20.00),
    ("Cubeta 19lt",    1,     "pza", 620.00),
    # Artículo libre: el operador teclea la descripción, así que aquí no hay
    # tope. Este es el caso que ejercita la caída a dos líneas.
    ("Chorizo artesanal de la casa", 0.750, "kg", 180.00),
]

EFECTIVO_RECIBIDO = 1200.00
REDONDEO_EFECTIVO = 1.00   # múltiplo de cobro en efectivo — decisión §8-3


# ── GEOMETRÍA ─────────────────────────────────────────────────────────────────

def geometria(cols: int) -> dict:
    """
    Reparte el ancho disponible. El importe y el detalle mandan; el nombre se
    queda con lo que sobra, que es justo lo que cambia entre 48 y 42.

    A 42 columnas el detalle tiene que apretarse un espacio ("1.250kg" en vez
    de "1.250 kg") o el nombre se queda en 12 caracteres y "Manteca 500ml" no
    cabe. Es la única diferencia real entre las dos geometrías.
    """
    w_imp = 11                       # '$' + 10 → hasta $99,999.99
    apretado = cols < 48             # a 42 se pega la unidad a la cantidad

    # El campo de detalle lleva SIEMPRE un espacio más que su contenido
    # máximo. Sin esa holgura, un detalle que llena el campo deja
    # "$230.00$    288.19" — dos signos de peso pegados, ilegible de un
    # vistazo, que es justo lo que la Parte E viene a arreglar.
    det_max = 17 if apretado else 18
    w_det   = det_max + 1
    w_nom   = cols - w_imp - w_det
    # Misma holgura del lado del nombre: sin ella queda "Manteca 500ml1pza".
    return dict(cols=cols, w_imp=w_imp, w_det=w_det, det_max=det_max,
                w_nom=w_nom, nom_max=w_nom - 1, apretado=apretado)


def dinero(v: float) -> str:
    """Importe a ancho fijo de 11. Cubre hasta $99,999.99 sin desbordar."""
    return f"${v:>10,.2f}"


def detalle(cant: float, unidad: str, precio: float, apretado: bool) -> str:
    sep = "" if apretado else " "
    if unidad == "kg":
        return f"{cant:.3f}{sep}kg x ${precio:,.2f}"
    return f"{cant:g}{sep}pza x ${precio:,.2f}"


def sep_linea(g: dict, ch: str = "-") -> str:
    return ch * g["cols"]


def centro(g: dict, t: str) -> str:
    return t.center(g["cols"]).rstrip()


def par(g: dict, label: str, valor: float) -> str:
    v = dinero(valor)
    return f"{label:<{g['cols'] - len(v)}}{v}"


def item(g: dict, nombre: str, cant: float, unidad: str, precio: float) -> list:
    """
    Una línea si nombre y detalle caben; dos si alguno se pasa.

    La caída a dos líneas no es un defecto: es lo que impide que el firmware
    envuelva por su cuenta y corra la columna de importes de ahí para abajo.
    """
    det = detalle(cant, unidad, precio, g["apretado"])
    imp = dinero(cant * precio)

    if len(nombre) <= g["nom_max"] and len(det) <= g["det_max"]:
        return [f"{nombre:<{g['w_nom']}}{det:<{g['w_det']}}{imp}"]
    return [nombre[:g["cols"]],
            f"{'  ' + det:<{g['cols'] - g['w_imp']}}{imp}"]


# ── MAQUETA ───────────────────────────────────────────────────────────────────

def maqueta(cols: int) -> list:
    """El ticket completo al ancho pedido. El TOTAL se suma, no se escribe."""
    g = geometria(cols)
    L = []

    L.append(sep_linea(g, "="))
    L.append(centro(g, "PRODUCTOS EL BAYO"))
    L.append(centro(g, "chicharron y manteca"))
    L.append(centro(g, "Lauro Galvez #171, Col. Palo Verde"))
    L.append(centro(g, "Hermosillo, Son."))
    L.append(sep_linea(g, "="))
    L.append(f"{'Folio: 000042':<{g['cols'] - 16}}{'2026-08-17 14:32':>16}")
    L.append(sep_linea(g))
    L.append(f"{'PRODUCTO':<{g['w_nom']}}"
             f"{'CANT x PRECIO':<{g['w_det']}}"
             f"{'IMPORTE':>{g['w_imp']}}")
    L.append(sep_linea(g))

    total = 0.0
    for nombre, cant, unidad, precio in CATALOGO:
        L += item(g, nombre, cant, unidad, precio)
        total += cant * precio

    # A cobrar en efectivo: se redondea el COBRO, nunca la venta registrada.
    a_pagar   = round(round(total / REDONDEO_EFECTIVO) * REDONDEO_EFECTIVO, 2)
    diferencia = round(a_pagar - total, 2)
    cambio     = round(EFECTIVO_RECIBIDO - a_pagar, 2)

    L.append(sep_linea(g))
    L.append(par(g, "TOTAL", round(total, 2)))
    if diferencia:                       # sin redondeo, la línea no se imprime
        L.append(par(g, "redondeo", diferencia))
        L.append(par(g, "A PAGAR", a_pagar))
    L.append(sep_linea(g))
    L.append(par(g, "efectivo", EFECTIVO_RECIBIDO))
    L.append(par(g, "CAMBIO", cambio))
    L.append(sep_linea(g, "="))
    L.append(centro(g, "Gracias por su compra"))
    L.append("")
    L.append(centro(g, "*000042*"))
    L.append(centro(g, "000042"))
    L.append("")
    L.append(centro(g, "este ticket no es comprobante fiscal"))
    L.append(centro(g, "bayoSys v2.0"))
    return L


def informe(cols: int) -> None:
    g = geometria(cols)
    L = maqueta(cols)
    dos_lineas = sum(1 for n, c, u, p in CATALOGO
                     if len(item(g, n, c, u, p)) == 2)

    print()
    print(f"══ COLS = {cols} "
          f"│ nombre {g['nom_max']}+1 · detalle {g['det_max']}+1 · importe {g['w_imp']}"
          f"{' · detalle apretado' if g['apretado'] else ''} ══")
    print("     " + "".join(str(i % 10) for i in range(1, cols + 1)))
    malas = 0
    for l in L:
        pasa = len(l) > cols
        malas += pasa
        print(f"{len(l):>3} |{l}|{'  <<< SE PASA' if pasa else ''}")
    print(f"\n  fuera de ancho : {malas}")
    print(f"  items a 2 líneas: {dos_lineas} de {len(CATALOGO)}")
    print(f"  renglones totales: {len(L)}")


# ── PRUEBA EN PAPEL — ESTO ES LO QUE DECIDE ───────────────────────────────────

def _reset_modos(p) -> None:
    """
    Limpia los modos heredados y deja Font B activa.

    python-escpos no toca ESC G (doble golpe), que muchas POS58 traen activo
    de fábrica y engrosa toda la letra. p.set(bold=False) no lo apaga.
    """
    p._raw(ESC + b"!" + bytes([0]))    # modo de impresión a cero (y Font A)
    p._raw(GS  + b"!" + bytes([0]))    # multiplicadores de tamaño a 1x1
    p._raw(ESC + b"E" + bytes([0]))    # negrita OFF
    p._raw(ESC + b"G" + bytes([0]))    # doble golpe OFF
    p._raw(ESC + b" " + bytes([0]))    # espaciado derecho = 0
    p._raw(ESC + b"M" + bytes([1]))    # Font B — va al final, ESC ! la pisa


def regla(n: int) -> str:
    """Regla de n caracteres: el último dígito dice dónde terminó."""
    return "".join(str(i % 10) for i in range(1, n + 1))


def prueba_papel() -> None:
    """Manda la regla a la POS58. Cuesta unos 10 cm de papel y zanja el punto."""
    try:
        from escpos.printer import Usb
    except ImportError:
        print("✗ falta python-escpos — pip install -r requirements.txt")
        sys.exit(1)

    try:
        try:
            p = Usb(VID, PID, timeout=USB_TIMEOUT,
                    in_ep=0x87, out_ep=0x06, profile=PERFIL)
        except Exception:
            p = Usb(VID, PID, timeout=USB_TIMEOUT, in_ep=0x87, out_ep=0x06)
    except Exception as e:
        print(f"✗ no se pudo conectar a la POS58: {e}")
        sys.exit(1)

    try:
        # Control en Font A: si estas 32 se envuelven, el problema es otro
        # y no tiene nada que ver con la Font B.
        _reset_modos(p)
        p._raw(ESC + b"M" + bytes([0]))
        p.text("FONT A - CONTROL - 32\n")
        p.text(regla(32) + "\n")

        _reset_modos(p)                          # vuelve a Font B
        p.text("FONT B - REGLA - 48\n")
        p.text(regla(48) + "\n")
        p.text("un renglon que acaba en 8 = 48\n")
        p.text("dos renglones, 2do en 3   = 42\n")
        p.text("-" * 42 + "  <- 42\n")
        p.text("-" * 48 + "  <- 48\n")
        p.text("si las dos barras miden igual, son 42\n")

        # Prueba de acentos del §E.9, ya que el papel está corriendo.
        for cp in ("CP850", "CP858"):
            try:
                p.charcode(cp)
                p.text(f"{cp}: a e i o u n N grados peso\n")
                p.text(f"{cp}: á é í ó ú "
                       f"ñ Ñ ° $\n")
            except Exception:
                p.text(f"{cp}: no disponible en este perfil\n")

        p.ln(3)
        try:
            p.cut(mode="FULL", feed=False)
        except TypeError:
            p.cut(mode="FULL")
    finally:
        try:
            p.close()
        except Exception:
            pass

    print("✓ regla impresa. Cuenta los renglones de la Font B.")


if __name__ == "__main__":
    if "--papel" in sys.argv:
        prueba_papel()
    else:
        for cols in (48, 42):
            informe(cols)
        print("\nEl papel decide cuál de las dos es real: "
              "python3 prueba_ancho.py --papel")
