# Verificación del documento de contexto (CONTEXTO_CLAUDE_CODE_2.md)

Auditoría del documento contra el código real. Fecha: 2026-08-15.
Verificado sobre `origin/dev` = `880dba6`.

Nada de lo que sigue modifica lógica de negocio. Es solo lectura y reporte.

---

## Resumen

| # | Afirmación del documento | Resultado |
|---|---|---|
| 1 | Rama base `dev` en `880dba6` | ✅ correcto |
| 2 | Diffstat del rediseño (§2) | ✅ exacto, los 9 archivos |
| 3 | 7 referencias de línea de §4 | ✅ exactas |
| 4 | Schema de `pedidos_mayoreo` (§4) | ✅ idéntico |
| 5 | `pos_ticket.py:71` y `:186` (§6) | ✅ exactas |
| 6 | La rama exporter "contiene ya **un** commit" | ❌ contiene **4** — la Parte A ya está hecha |
| 7 | `_submenu_mayoreo` tiene "solo [alta] y [capturar]" | ❌ tiene **3** opciones |
| 8 | Defecto 1 de la Parte C: punto flotante IEEE-754 | ❌ diagnóstico equivocado — causa real distinta |
| 9 | Caso `40 → 100.05` "pendiente de reproducir" | ✅ **reproducido** (ver C) |
| 10 | §7: `crear_pedido_mayoreo` sin `commit` / ruta de DB inconsistente | ❌ ambas hipótesis descartadas |
| 11 | `storage.py` en la tabla de autorización | ❌ ese archivo no existe |

---

## 1. Estado del repo

`dev` (`880dba6`) contiene estrictamente a `main` (`2072acb`); el merge-base es
`main`, no hay divergencia. El diffstat del rediseño coincide **exactamente** con
el listado de §2:

```
estilos.py +814 · pos_tui.py 506 · main.py 422 · tui.py 261
registro.py 147 · guardian.py 122 · cierre.py 116 · analisis.py 101 · nuke.py 60
```

**Matiz sobre §2 ("no cambiaron `pos.py`, `pos_db.py`, `models.py`, `calcular.py`"):**
es cierto *dentro del rediseño*, pero entre `main` y `dev` esos archivos sí
cambiaron (`pos_db.py` +277, `pos.py` +98, `calcular.py` +28, `models.py` +10),
por los commits del pool de manteca (`d5795e2`, `c2b1ef4`, `75461db`). La frase se
lee como "están intactos" y no lo están respecto de `main`.

**`storage.py` no existe** en el repo. La capa de persistencia JSON vive en
`config.py`. La tabla de autorización de §0 lo prohíbe, lo cual es inofensivo,
pero conviene corregir el nombre.

**Pre-flight de §8:** `ast.parse` pasa en los 17 módulos y
`import estilos, pos_db, pos, pos_tui` retorna OK. `mypy` y `pyflakes` no están
instalados en este entorno, así que esas dos líneas no se pudieron correr.

---

## 2. Hallazgo mayor — la Parte A ya está implementada

La rama `claude/bayosys-excel-exporter-1detr7` **no tiene un commit, tiene cuatro**:

```
ccf8917  Getters de rango para el exportador: pos_db.py y config.py
348a408  reportes.py — exportador a Excel, una libreta con 7 pestañas
c889252  Engancha el exportador: manual en analisis.py, automatico en los 3 cortes
017ab1f  Los cortes salen en el dia que cierran, no en el que se guardaron
```

Es decir: `reportes.py` (432 líneas), `requirements.txt`, los getters de rango y
los dos puntos de entrada **ya existen**. Construir la Parte A desde cero sería
trabajo duplicado y en conflicto.

### Dos problemas en esa rama

**a) Toca un archivo PROHIBIDO.** El commit `c889252` modifica `pos.py`, que §0
marca como PROHIBIDO y que §3 pide explícitamente dejar como `TODO`
("Deja el hueco marcado con un `TODO` y repórtalo — Luis inserta esa línea a mano").
Lo que insertó:

```python
from reportes import exportar_dia_seguro          # pos.py:34
...
corte["export_xlsx"] = exportar_dia_seguro()      # pos.py:427, dentro de hacer_corte
```

El llamado sí está después de `guardar_corte` y `exportar_dia_seguro()` está
diseñada para no lanzar, así que el riesgo funcional es bajo — pero la línea la
puso Claude Code, no Luis, y eso es justo lo que §0 prohíbe. **Requiere tu
transcripción y visto bueno.**

**b) `requirements.txt` está incompleto.** Contiene solo `openpyxl>=3.1,<4.0`.
§3 pide las tres líneas y fijar `openpyxl`:

```
python-escpos[usb]==3.1
mypy==2.3.0
openpyxl==3.1.5
```

Versiones confirmadas contra PyPI: `mypy 2.3.0` existe (última 2.3.1),
`python-escpos 3.1` existe, y `openpyxl 3.1.5` es la última estable
(`requires_python >=3.8`, compatible con 3.10).

---

## 3. Parte B — todo confirmado, con una corrección

Las 7 referencias de línea de §4 son **exactas**:

| Referencia | Estado |
|---|---|
| `pos.py:535` `ajustar_pedido_mayoreo` | ✅ |
| `pos.py:544` `entregar_pedido_mayoreo` | ✅ |
| `pos_tui.py:38` ambas importadas | ✅ |
| `pos_tui.py:681` `flujo_capturar_pedido_mayoreo` | ✅ |
| `pos_tui.py:782` `_texto_ticker_mayoreo` | ✅ |
| `pos_tui.py:1078` `_submenu_mayoreo` | ✅ |
| `pos_db.py:1075` `UPDATE ... SET entregado = 1` | ✅ (el `def` está en 1072) |

El "cable suelto" es real: `ajustar_pedido_mayoreo` y `entregar_pedido_mayoreo`
aparecen **una sola vez** en todo `pos_tui.py`, en la línea 38 del import. Nunca
se invocan. El schema de `pedidos_mayoreo` es idéntico al transcrito en §4.

**Filtro del ticker:** confirmado, pero vive en `pos_db.py:1090`
(`WHERE p.entregado = 0`, dentro de `get_pedidos_pendientes`), no en la capa TUI.
`_texto_ticker_mayoreo` solo formatea. Un pedido despachado desaparece solo.

**Corrección a §4.B.4:** el submenú **no** tiene dos opciones, tiene tres:

```
[1] nuevo cliente   [2] capturar pedido   [3] status de pedidos   [Esc] volver
```

Las opciones nuevas deben ser **`[4] entregar pedido` y `[5] ajustar pedido`**.
Usar `[3]` y `[4]` como dice el documento pisaría "status de pedidos".

---

## 4. Parte C — el diagnóstico del documento está equivocado, y el bug real es peor

### El "Defecto 1" descrito en §C.0 no existe

El documento afirma que una comparación `if efectivo >= total` falla por 1e-14.
En el código real, `pos.py:351` **ya compara con tolerancia**:

```python
if total_pagado < total - 0.01:   # tolerancia de 1 centavo
    raise ErrorPOS(...)
```

Cobrar un ticket de `50.00` con un billete de `50` **funciona hoy** (criterio de
aceptación "prueba del float": ya pasa). Y el ejemplo del documento es incorrecto:

```
documento:  repr(0.35 * 143.0) == '50.050000000000004'
real:       repr(0.35 * 143.0) == '50.05'
```

El rechazo de `50.10` pagando `50` **no es un bug de float**: son 10 centavos
reales de faltante, aritmética correcta. Lo que falta es la política de redondeo
(el "Defecto 2"), que sí es válido.

### El defecto real: la cuantización de kg a 3 decimales

Reproduje el caso `100.05`. El culpable es `pos_tui.py:176`, en la captura por
monto (`pedir_kg_o_monto_modal`):

```python
kg = round(monto / precio_kg, 3)     # línea 176
...
return kg, round(monto, 2)           # línea 180 — devuelve el monto ORIGINAL
```

Luego, en `pos_tui.py:882-884`, se cobra `agregar_chicharron(sesion, kg)` — que
factura `kg × precio` (`pos.py:47`) — pero se **muestra en pantalla el monto que
tecleó el operador**. Los dos números no coinciden.

Con el precio por defecto de `230.0/kg` (`models.py:205`), redondear kg a 3
decimales da pasos de **$0.23**, así que cualquier captura por monto se desvía
hasta ±$0.115:

```
monto tecleado -> kg -> total realmente cobrado
   $ 40  -> 0.174 kg -> $ 40.02   (+0.02)
   $ 50  -> 0.217 kg -> $ 49.91   (-0.09)
   $100  -> 0.435 kg -> $100.05   (+0.05)   <-- el caso reportado
   $200  -> 0.870 kg -> $200.10   (+0.10)
   $300  -> 1.304 kg -> $299.92   (-0.08)
```

**El caso queda reproducido**: teclear `$100` produce un ticket de `$100.05`. El
"40" del reporte parece ser otra captura de la misma sesión (`$40 → $40.02`); el
mecanismo es el mismo y la cifra `100.05` sale exacta.

Esto **no es error de IEEE-754**. Es redondeo decimal deliberado, y su magnitud
(centésimas de peso) es catorce órdenes de magnitud mayor que el epsilon de float.
Corregirlo con `round(x, 2)` en las fronteras —la "intervención quirúrgica" de
§C.1— **no lo arregla**: el desvío nace antes, al cuantizar los kg.

Consecuencia para §C.2: la política de redondeo de efectivo es necesaria pero no
suficiente. Mientras la captura por monto siga recalculando el total desde kg
redondeados, el operador seguirá viendo una cifra en pantalla y otra en el ticket.

> Capa de lógica ⇒ **no lo toqué**. Queda reportado para que decidas el arreglo.

---

## 5. Parte D — confirmada línea por línea

| Afirmación §6 | Estado |
|---|---|
| `pos_ticket.py:71` conexión `Usb(...)` sin `profile=` | ✅ exacta |
| `pos_ticket.py:186` `cut(mode="PART" if parcial else "FULL")` | ✅ exacta |
| "No hay saltos de línea hardcodeados" | ✅ cero llamadas a `.ln(` en el módulo |
| No se fija codepage (D.2) | ✅ cero llamadas a `charcode`/`codepage` |

No se pudo verificar la firma `cut(self, mode='FULL', feed=True)`: `python-escpos`
no está instalado en este entorno. Hay que correr el `inspect.getsource` de §6.D.1
en una máquina con la librería antes de escribir el reemplazo.

---

## 6. §7 — el misterio de la tabla vacía: ambas hipótesis de código descartadas

- **`crear_pedido_mayoreo` sí hace `commit`** (`pos_db.py:1048`).
- **La ruta de la DB es única y consistente**: `pos_db.py:24-25` define un solo
  `BASE_DIR = ~/bayosys/data` y `POS_DB = BASE_DIR/pos.db`, usados por el único
  `get_conn()`. No hay segunda ruta en el código.

Queda en pie la hipótesis 1 (se capturó en otra máquina) y añado una tercera:
`get_conn()` activa `PRAGMA journal_mode = WAL` (`pos_db.py:41`). Si se copió
`pos.db` entre máquinas **sin** su `pos.db-wal`, los commits que aún vivían en el
WAL no viajan y la tabla se ve vacía en el destino. Vale la pena revisar si existe
`~/bayosys/data/pos.db-wal` en la máquina donde sí apareció el pedido.

**Nota de capas (solo reporte):** `crear_pedido_mayoreo` valida prioridad y
existencia del cliente y lanza `ValueError` desde `pos_db.py`. Según §1, esa capa
debe ser SQL puro. No lo moví.

---

## 7. Qué NO se tocó

Ningún archivo de lógica de negocio fue modificado. No se escribió
`entregar_pedido_mayoreo`, ni `total_a_cobrar_efectivo`, ni se alteró
`config.py`, `pos.py`, `models.py`, `calcular.py`, `cierre.py` ni `guardian.py`.

---

# Entrega — Partes B, C.4 y D

Implementadas sobre `dev`, sin la Parte A (decisión tomada tras la auditoría:
la Parte A ya existe en la rama del exportador y construir encima habría
duplicado trabajo).

## Cada `TODO` que quedó y por qué

| Dónde | Qué falta | Por qué no lo hice |
|---|---|---|
| `pos_tui.py`, en `flujo_entregar_pedido_mayoreo` | Reescribir `entregar_pedido_mayoreo` (`pos.py:544`) con el orden leer → validar stock → cobrar → marcar | §4.B.3 lo prohíbe explícitamente. El orden es la garantía contra pedidos fantasma |
| `pos_tui.py`, antes de `_pantalla_efectivo` | `total_a_cobrar_efectivo` en `pos.py` + `REDONDEO_EFECTIVO` en `config.py` | §5.C.2 — capa de lógica. Queda un stub que devuelve el total sin tocar |
| `pos_tui.py`, en `flujo_cobro` | Que `cobrar()` reciba el importe redondeado y guarde `diferencia_redondeo` | §5.C.3 — la migración ya está puesta; la ecuación del corte es lógica |

## Archivos restringidos que habría necesitado tocar

**`pos.py`** — tres veces, y en las tres me detuve:

1. `entregar_pedido_mayoreo` (línea 544) sigue con la firma vieja.
2. `marcar_pedido_entregado` ya pide cuatro argumentos y `pos.py:545` la llama
   con uno. **Esto rompe el despacho hasta que hagas B.3**, y es a propósito:
   le puse los parámetros como obligatorios en vez de darles default. Con
   default, la llamada vieja escribiría NULL en `kg_real` y `ticket_id` sin
   avisar — o sea, volvería a abrir el agujero de dinero en silencio. Así
   truena de inmediato y no puede marcar un pedido como entregado sin dejar
   constancia de la venta. La pantalla atrapa ese `TypeError` y responde
   *"despacho no disponible"*, dejando el pedido pendiente.
3. `total_a_cobrar_efectivo` no existe; la pantalla la importa con
   `try/except ImportError` y cae a un stub. En cuanto la escribas, la
   pantalla la toma sola, sin tocar la UI.

**`config.py`** — no se modificó. `REDONDEO_EFECTIVO` la agregas tú.

## Suposiciones que hice

1. **El submenú de mayoreo ya tenía `[3] status`**, así que entregar y ajustar
   entraron como `[4]` y `[5]`. El documento decía `[3]` y `[4]`, lo cual
   habría pisado una opción existente.
2. **`_Cancelado` no existía en `pos_tui.py`.** Está duplicada en `main.py:62`
   y `registro.py:25`; agregué una tercera copia local siguiendo esa
   convención en vez de importarla entre módulos de UI. Sigue pendiente el
   refactor de extraerla a un módulo compartido.
3. **La tolerancia de comparación de efectivo (`TOLERANCIA_CENTAVO`) quedó en
   la capa de UI.** §5.C.1 la ubica en la lógica; cuando hagas ese refactor,
   la pantalla debería tomarla de ahí.
4. **El corte de ticket pasó a `FULL` por default** (antes `PART`). Si la POS58
   no tiene cuchilla, el corte parcial no corta pero la alimentación se paga
   igual. **Confirma el modelo físico.**
5. **La nota fiscal se acortó** a "No es comprobante fiscal": la versión larga
   medía 36 caracteres y se envolvía.
6. **`requirements.txt` se creó aquí con las tres líneas de §3.** La rama del
   exportador tiene su propia versión con solo `openpyxl`, así que **al juntar
   las ramas hay conflicto en ese archivo** — trivial de resolver, pero avisado.

## Resultado de `mypy`

```
mypy . --ignore-missing-imports --explicit-package-bases
```

| Rama | Errores |
|---|---|
| `origin/dev` (base) | **93** |
| esta rama | **64** |

Bajaron 29. Los 29 que desaparecieron son ruido de `Optional[Usb]` en
`pos_ticket.py`, que se fue al volver el dispositivo intercambiable.

Se introdujeron **3 errores nuevos, los tres intencionales** — son exactamente
los `TODO` de arriba, y mypy sirve como recordatorio de que faltan:

```
pos.py:545      Missing positional arguments "kg_real", "ticket_id",
                "fecha_entrega_real" in call to "marcar_pedido_entregado"
pos_tui.py:380  Module "pos" has no attribute "total_a_cobrar_efectivo"
pos_tui.py:1019 Too many arguments for "entregar_pedido_mayoreo"
```

`pyflakes` confirma que el cable suelto quedó conectado: en `dev` había 6
imports muertos en `pos_tui.py` y ahora quedan 3. Los que se fueron son
`ajustar_pedido_mayoreo`, `entregar_pedido_mayoreo` y `BOTON` — justo las
funciones que §4 reportaba importadas y nunca invocadas. Los 3 restantes
(`PRIORIDADES_MAYOREO`, `get_ticket_items`, `anular_ticket`) ya estaban.

## Pruebas

- `python3 -m unittest discover tests` → **55 pruebas, todas pasan**
  (18 nuevas de `pos_ticket.py`).
- Migración corrida 3 veces seguidas sin error.
- Despacho verificado contra SQLite: el pedido sale del ticker al entregarse
  y quedan grabados `kg_real`, `ticket_id` y `fecha_entrega_real`.
- Pantalla de efectivo ejercitada en una sesión curses real con teclas
  guionadas: 7 casos, incluida la prueba del float (ticket de `50.00` pagado
  con un billete de `50`) y la acumulación de denominaciones.
- Simulando la política de redondeo de C.2 (`REDONDEO_EFECTIVO = 0.50`), el
  ticket de `50.10` muestra `redondeo −0.10`, cobra `50.00` y acepta el
  billete de 50 — el síntoma exacto que reportó el operador.

## Falta verificar en papel real

Nada de esto se puede cerrar sin la POS58 enfrente:

- Que la impresora responda a `cut(feed=False)`. La rama sin alimentación
  puede emitir un comando distinto; si deja de cortar, vuelve a `feed=True`
  y recorta el contenido previo.
- Calibrar `LINEAS_AVANCE_CORTE`: probar 2, luego 3, y quedarse con el primero
  que corte limpio sin dejar la última línea dentro del mecanismo.
- Que `profile="POS-5890"` centre bien.
- Que los acentos sobrevivan al `CP850` (`á é í ó ú ñ Ñ ° $`).
- Si el modelo físico tiene cuchilla, para decidir `PART` vs `FULL`.

También falta `assets/logo_ticket.png`: no lo inventé. El código ya lo carga
si aparece y sigue sin él si no — hay una prueba que lo cubre.

## Firma de `python-escpos` que no pude verificar

§6.D.1 afirma que `cut(self, mode='FULL', feed=True)`. **No lo pude
confirmar**: la librería no está instalada en este entorno. El código llama
`cut(..., feed=False)` y atrapa `TypeError` por si esa versión no expone el
parámetro. Corre esto en una máquina con la librería:

```bash
python3 -c "import inspect; from escpos.escpos import Escpos; print(inspect.getsource(Escpos.cut))"
```
