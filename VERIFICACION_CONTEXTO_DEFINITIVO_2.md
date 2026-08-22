# Verificación de `CONTEXTO_BAYOSYS_DEFINITIVO_2.md`

Auditoría del documento contra el código real y evaluación de viabilidad de la
Parte E. Fecha: 2026-08-21.
Verificado sobre `claude/file-analysis-verification-6vahzs` = `dce7cad`, que es
la rama base que §7 manda usar.

Solo lectura y reporte. No se modificó ninguna línea de lógica.

---

## Resumen

| # | Afirmación del documento | Resultado |
|---|---|---|
| 1 | SHAs de las cuatro ramas (§2) | ✅ exactos |
| 2 | Diffstat de la Parte A: +697 líneas (§3) | ✅ exacto (omite `main.py` +11 y `.gitignore` +1) |
| 3 | 55 tests en verde (§9) | ✅ 55 pasan en 0.67 s |
| 4 | Migración de mayoreo: 3 columnas nullable (§4) | ✅ presentes, con `PRAGMA table_info` |
| 5 | Maqueta de 48 columnas y geometría de E.5 | ✅ internamente exacta, salvo el TOTAL |
| 6 | `pos_ticket.py` **no** llama a `charcode()` (§E.9) | ❌ sí lo llama, con `CP850` |
| 7 | Queda el `text("\n\n")` de `total()` por borrar (§E.8) | ❌ ya no existe |
| 8 | Queda el comentario "42 cols" por corregir (§E.8) | ❌ ya no existe — y ese 42 no era un error |
| 9 | `_warn` "puede no existir" en `guardian.py` (§3) | ❌ existe, línea 56 |
| 10 | `storage.py` en la tabla de autorización (§0) | ❌ ese archivo no existe en el repo |
| 11 | Referencias de línea de §4 | ⚠️ dos corridas, dos exactas |
| 12 | `[3] entregar` y `[4] ajustar` en el submenú (§4) | ⚠️ entregar es `[4]`, ajustar es `[5]` |
| 13 | Font B = 8 px = 48 columnas (§E.1) | ⚠️ **el perfil en uso dice 42** — riesgo mayor |
| 14 | La maqueta de E.4 suma correctamente | ❌ los items suman 1,112.50, no 1,127.50 |

---

## 1. Lo que el documento acierta

**Estado del repo (§2).** Los cuatro SHAs remotos coinciden al carácter:

```
dev                                       880dba6
claude/bayosys-excel-exporter-1detr7      017ab1f
claude/file-analysis-verification-6vahzs  dce7cad
main                                      2072acb
```

**Parte A (§3).** El diffstat contra `dev` es exactamente el listado:
`reportes.py` +432, `pos_db.py` +145, `analisis.py` +61, `config.py` +20,
`guardian.py` +20, `pos.py` +8, `requirements.txt` +1 — 697 inserciones.
El documento no menciona `main.py` +11 (engancha `exportar_dia_seguro()` en
`hacer_corte_entre_batches`) ni `.gitignore` +1; el total sí cuadra porque esas
líneas están contadas.

**Tests (§9).** 55 en verde: `test_calcular` 12, `test_cierre_manteca` 8,
`test_pool_manteca` 17, `test_pos_ticket` 18. Existe además `test_models.py`,
que no aparece en el listado del documento porque no contiene ningún test.
Matiz: §6 dice "17 tests en verde" del ticket; son 18.

**Parte B (§4).** Las tres columnas están en `pos_db.py` (`fecha_entrega_real`,
`kg_real`, `ticket_id`), la migración inspecciona `PRAGMA table_info` y es
idempotente. `marcar_pedido_entregado` escribe las tres. El ticker filtra por
`p.entregado = 0` (`pos_db.py:1187`). `TOLERANCIA_KG_PEDIDO = 0.15` está en
`pos_tui.py:896`, tal como describe la validación de plausibilidad física.

**Parte C (§5).** `TOLERANCIA_CENTAVO = 0.005` en `pos_tui.py:371` y la columna
`diferencia_redondeo` ya está migrada en `tickets` (`pos_db.py:332`). La UI de
C.4 está entregada y `REDONDEO_EFECTIVO` sigue siendo un stub, como dice el
documento.

**Parte D (§6).** `LINEAS_AVANCE_CORTE = 3` en `pos_ticket.py:71`, `ticket_texto`,
`_demo_texto` y `_demo` existen, y `corte()` ya hace `ln()` + `cut(feed=False)`.

**Ruta de la DB (§1).** `pos_db.py:24-25` — ruta absoluta fija, sin override.

**Maqueta (§E.4/E.5).** Reproduje las fórmulas de E.5 contra la maqueta línea
por línea: cero desbordes de 48, y `_dinero`, `_par`, la línea de item normal, la
de nombre largo, la cabecera de tabla y el renglón de folio/fecha generan
**exactamente** las cadenas de la maqueta. La geometría está bien pensada.

---

## 2. Lo que ya está hecho y el documento pide otra vez

Tres puntos de §E.8 y uno de §3 son trabajo ya entregado. Si se ejecutan como
están escritos, se pierde tiempo buscando algo que no está:

1. **`charcode()` (§E.9).** El documento dice que `pos_ticket.py` no lo llama.
   Sí lo llama: `pos_ticket.py:281`, `self._p.charcode(CODEPAGE)` con
   `CODEPAGE = "CP850"` (línea 62), envuelto en `try/except` para no abortar el
   ticket si el perfil no lo soporta. Lo que falta no es el código: es la prueba
   en papel con `á é í ó ú ñ Ñ ° $`.
2. **El comentario de `COLS` (§E.8).** Ya no dice "42 cols". Hoy dice
   *"Ancho útil en caracteres con Font A en papel de 58 mm"*, que es correcto.
3. **El `text("\n\n")` de `total()` (§E.8 y §6 D.2).** No existe en el archivo.
   El avance ya cuelga exclusivamente de `LINEAS_AVANCE_CORTE`.
4. **`_warn` en `guardian.py` (§3).** Existe, `guardian.py:56` de la rama de
   Excel, y se usa en cinco lugares. La ruta de fallo del exportador no explota.

Además, el arreglo que §6 D.2 propone —`cut(mode="FULL", feed=False)`— está
desfasado respecto de lo entregado, que es mejor: conserva el parámetro `parcial`
y trae un `except TypeError` para versiones de python-escpos sin `feed`.

---

## 3. Referencias corridas

| Documento | Real |
|---|---|
| `pos_db.py:1075` `marcar_pedido_entregado()` | `pos_db.py:1155` |
| `pos_tui.py:1078` `_submenu_mayoreo` | `pos_tui.py:1428` |
| `pos.py:535` `ajustar_pedido_mayoreo()` | ✅ exacta |
| `pos.py:544` `entregar_pedido_mayoreo()` | ✅ exacta |
| `pos_tui.py:38` imports de mayoreo | ✅ el bloque va de la 36 a la 39 |

El submenú entregado tiene cinco opciones, no las dos de antes ni las cuatro que
implica §4: `[1] nuevo cliente`, `[2] capturar pedido`, `[3] status de pedidos`,
`[4] entregar pedido`, `[5] ajustar pedido`. Entregar es `[4]` y ajustar `[5]`.

`storage.py` no existe en el repo: la persistencia JSON vive en `config.py`. La
prohibición es inofensiva pero el nombre está mal, y ya se había señalado en la
verificación anterior.

**Contradicción interna de §0 vs §1.** §1 llama "infraestructura, autorizada" al
override `os.getenv("BAYOSYS_DATA", default)`, pero esa ruta vive en `pos_db.py`
(donde §0 solo permite `SELECT` y migraciones) y en `config.py` (**PROHIBIDO**).
Son dos líneas y valen mucho, pero necesitan autorización explícita de Luis o
quedan bloqueadas por la propia regla del documento.

---

## 4. Viabilidad de la Parte E

**Autorización: viable sin fricción.** Todo el trabajo cae en `pos_ticket.py` y
`tests/`, ambos LIBRE. El único consumidor externo del módulo es
`pos_tui.py:52` y `:650`, vía `imprimir_ticket_fisico(ticket_id, pagos, cambio)`;
conservando esa firma —criterio de aceptación ya listado— el radio de daño es
cero. No hace falta tocar ningún archivo prohibido.

**Técnicamente: hacedero, con cinco cosas que resolver antes de escribir código.**

### R1 — CRÍTICO: 48 columnas puede ser 42

El módulo usa `PERFIL = "POS-5890"` (`pos_ticket.py:57`). Ese perfil de
python-escpos declara:

```
POS-5890 -> {'0': {'columns': 32, 'name': 'Font A'},
             '1': {'columns': 42, 'name': 'Font B'}}   media: 384 px
```

Font B = **42 columnas**, es decir glifo de 9 px (384 / 9 = 42.67), no de 8. El
"42 cols" del comentario viejo que §E.8 califica de contradictorio era
precisamente este número, y probablemente salió de aquí.

Y la librería no va a avisar: `profile.get_columns()` solo se usa dentro de
`block_text()`, que el módulo no llama nunca. `text()` manda los bytes tal cual y
quien envuelve es el firmware de la impresora. Si el cabezal resulta ser de 9 px,
cada línea de 48 se parte en 42 + 6 y **toda** la alineación se cae — exactamente
el defecto que la Parte E existe para eliminar, pero peor.

§E.1 dice que 48 se validó en hardware. Si esa prueba fue una regla de 48
caracteres impresa en Font B y salió en un solo renglón, adelante. Si fue "se veía
chiquita y cabía más", hay que repetirla antes de escribir una línea de layout:

```
ESC M 1  +  "123456789012345678901234567890123456789012345678\n"
```

Si el `8` final queda en el mismo renglón → `COLS = 48`. Si aparece `34567` en un
segundo renglón → `COLS = 42`, y la geometría de E.5 hay que rehacerla: a 42
columnas `W_DET` sería `42 − 18 − 11 = 13`, y `"1.250 kg x $230.00"` mide 18
caracteres. No cabría ni un item normal.

**Mitigación barata:** derivar `W_NOM` y `W_DET` de `COLS` en vez de fijarlos, y
que el caso "el detalle no cabe" caiga solo al formato de dos líneas. Así un
cambio de 48 a 42 es cambiar una constante, no rediseñar el ticket.

### R2 — La maqueta de E.4 no cuadra consigo misma

Los items suman:

```
287.50 + 70.00 + 620.00 + 135.00 = 1,112.50
```

pero la maqueta imprime `TOTAL  $  1,127.50`. Son 15.00 de diferencia. El resto
del bloque sí es coherente consigo mismo (1,127.50 − 0.50 = 1,127.00;
1,200.00 − 1,127.00 = 73.00), así que el error está en el TOTAL o en algún
precio.

Esto choca de frente con el criterio de aceptación *"`_demo_texto()` reproduce la
maqueta de E.4"*: un demo que sume honestamente sus items jamás va a imprimir
1,127.50. Hay que corregir la maqueta a 1,112.50 (con A PAGAR 1,112.00 y CAMBIO
88.00) o corregir un precio. Es decisión de Luis, pero sin tomarla ese criterio
queda en rojo para siempre.

Segundo detalle del mismo bloque: un redondeo de −0.50 sobre 1,127.50 solo existe
si `REDONDEO_EFECTIVO = 1.00`. Con 0.50 —el default propuesto en §8-3— ese total
ya es múltiplo exacto y la línea de redondeo no se imprimiría. La maqueta está
asumiendo peso cerrado, que es justo la decisión que §8 declara pendiente.

### R3 — `_reset_modos()` rompe `_demo_texto()` tal como está escrito

`LienzoTexto`, el dispositivo de papel simulado, implementa `set`, `text`, `ln`,
`charcode`, `cut`, `image`, `barcode`, `qr` y `close` — **no tiene `_raw()`**.
Si `_reset_modos()` llama `self._p._raw(ESC + b"!" + ...)` sin más, el modo texto
truena con `AttributeError` y se cae el criterio *"`_demo_texto()` funciona sin
impresora conectada"*.

Arreglo trivial —un `_raw()` no-op en `LienzoTexto`, o un guard con
`getattr(self._p, "_raw", None)`— pero hay que no olvidarlo, porque el fallo
aparece en el primer `python3 -c "from pos_ticket import _demo_texto"`.

### R4 — El reset sobrevive, salvo que se reintroduzca doble tamaño

Verificado contra python-escpos 3.1 instalado:

- `set(font=None)` —el default— **no** emite `ESC M`. O sea que el `ESC M 1` de
  `_reset_modos()` sobrevive a todos los `set(align=..., bold=...)` posteriores.
  El plan de §E.2 funciona.
- Pero la rama `elif normal_textsize or double_height or double_width:` emite
  `TXT_NORMAL` = `ESC ! 0`, y el bit 0 de `ESC !` **es el selector de fuente**.
  Cualquier `double_*=True` regresa la impresora a Font A en silencio.

Conclusión que vale la pena escribir en el propio código: la regla "cero
`double_height`, cero `double_width`" de E.3 no es estética, es lo que sostiene el
Font B. Los `double_*=False` que hay hoy son inofensivos —con todo falsy esa rama
no se ejecuta—, pero conviene borrarlos igual para que nadie los cambie a `True`.

### R5 — Bloques que la maqueta no cubre

Nada de esto es bloqueante, pero hay que decidirlo o el diseño queda a medias:

- **Tres formas de pago.** `_nota_de_pagos` imprime Efectivo / Transferencia /
  Tarjeta y luego Cambio. El bloque 5 de §E.6 solo enumera efectivo. Lo natural es
  que transferencia y tarjeta entren por `_par()` con la misma columna de `$`.
- **De dónde sale `redondeo`.** La columna `diferencia_redondeo` ya existe en
  `tickets`, y `_leer_ticket` ya hace `SELECT * FROM tickets`, así que el valor
  llega solo, sin tocar lógica. Mientras la lógica de C.1–C.3 siga pendiente
  valdrá 0 y las líneas `redondeo` y `A PAGAR` se omitirán por la propia regla de
  §E.6. La Parte E se puede entregar completa sin invadir nada.
- **Detalle de item sin tope.** `f"{cant:.3f} kg x ${precio:,.2f}"` con 99.999 kg
  y precio de cuatro cifras mide 21 caracteres contra `W_DET = 19`. Falta la regla
  "si el detalle no cabe, cae también a dos líneas".
- **Código de barras en modo texto.** La maqueta pinta `*000042*` y `000042`;
  el stub `LienzoTexto.barcode()` renderiza `|||| 000042 ||||`. Para que
  `_demo_texto()` reproduzca la maqueta hay que alinear el stub.
- **Textos que faltan.** `NOTA_FISCAL` hoy es `"No es comprobante fiscal"`
  (se acortó para caber en 32); la maqueta usa la versión larga, que sí cabe en 48.
  La firma `bayoSys v2.0` no existe todavía. Y `GIRO` ya trae acentos
  (`"chicharrón y manteca"`) mientras la maqueta está sin ellos: el criterio
  "reproduce la maqueta" y §E.9 se contradicen hasta que se pruebe el codepage en
  papel.
- **Demo distinto.** `_demo_texto()` usa hoy Chicharrón / Manteca 1lt /
  Manteca 500ml. Reproducir E.4 implica cambiar el juego de datos a los cuatro
  items de la maqueta, incluyendo los dos de nombre largo que ejercitan el
  formato de dos líneas. Eso es bueno: el demo pasa a ser la prueba del diseño.

---

## 5. Entorno de verificación

`pytest` y `python-escpos` no venían instalados en este contenedor; los instalé
para poder correr la suite e inspeccionar la librería. `mypy==2.3.0` existe y es
instalable, pero no está presente, así que el pre-flight de §9 solo corre a medias
aquí. `ast.parse` e `import` de los módulos sí pasan.

---

## 6. Recomendación

La Parte E es viable y está bien acotada: un solo archivo libre, un solo
consumidor externo, geometría ya validada en aritmética. Antes de empezar hacen
falta dos respuestas de Luis:

1. **¿48 o 42?** Una tira de papel con la regla de 48 caracteres en Font B lo
   resuelve en un minuto y decide si la geometría de E.5 sirve tal cual.
2. **¿Cuál es el TOTAL correcto de la maqueta?** 1,112.50 según sus propios items,
   o el precio que esté mal escrito.

Con eso, el resto del §7 se implementa sin tocar ningún archivo restringido y
sin dejar `TODO` de lógica.

---

## 7. Apéndice — `bayosys/prueba_ancho.py`

Herramienta para zanjar el punto R1. No toca `pos.db` ni ningún archivo
restringido; solo habla con la impresora.

```bash
cd bayosys
python3 prueba_ancho.py            # las dos maquetas en pantalla
python3 prueba_ancho.py --papel    # la regla a la POS58 — esto decide
```

La prueba de papel manda, en Font B, una regla de 48 caracteres:

- sale en **un** renglón que termina en `8` → `COLS = 48`
- sale en **dos**, el segundo empezando en `3` → `COLS = 42`

Incluye una línea de control en Font A (si esas 32 se envuelven, el problema es
otro) y, ya que el papel está corriendo, la prueba de acentos de §E.9 en CP850 y
CP858.

### Las dos geometrías

Ambas cierran en cero desbordes con el catálogo real de `SKUs_DEFAULT`:

| | 48 columnas | 42 columnas |
|---|---|---|
| nombre | 17 + 1 de holgura | 12 + 1 |
| detalle | 18 + 1 | 17 + 1, unidad pegada (`1.253kg`) |
| importe | 11 | 11 |
| nombres del catálogo en una línea | **5 de 6** | 3 de 6 |
| items a dos líneas en el demo | 1 de 5 | 2 de 5 |
| renglones del ticket | 31 | 32 |

A 42 se caen a dos líneas `Manteca 500ml` y `Artículo libre` — el segundo es el
de descripción libre, que el operador teclea y no tiene tope.

### Dos correcciones a la geometría de §E.5

La maqueta aprobada define los anchos como campos exactos. Con nombres o
detalles que llenan el campo justo, quedan dos columnas pegadas:

```
Manteca 500ml1pza x $20.00     $     20.00     <- nombre al ras
Chicharrón   1.253kg x $230.00$    288.19      <- detalle al ras
```

La regla correcta es **contenido ≤ campo − 1** en nombre y detalle. A 48 no se
nota porque ningún producto real llega a 18 caracteres, pero es la misma clase
de defecto que la Parte E viene a eliminar, y a 42 sale a la primera.

### El TOTAL ya no se escribe a mano

La maqueta del apéndice suma sus propios items y calcula el redondeo con
`REDONDEO_EFECTIVO`, así que el descuadre de 15.00 del R2 no puede repetirse.
Con los valores del catálogo real el total es 1,133.19, que no es múltiplo ni de
0.50 ni de 1.00: la línea de `redondeo` se imprime bajo cualquiera de las dos
políticas y el demo deja de prejuzgar la decisión §8-3.
