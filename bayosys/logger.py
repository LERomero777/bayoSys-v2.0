"""
logger.py — bayoSys · Productos El Bayo
Log estilo dmesg — timestamp, módulo, nivel, mensaje.
Un solo archivo rotativo en ~/bayosys/data/logs/bayosys.log

Uso desde cualquier módulo:
    from logger import log
    log.info("config", "cargar_config() OK")
    log.debug("calcular", f"rend_chi: {r.rend_chi_pct:.1f}%")
    log.error("cierre", f"archivo corrupto: {ruta}")
    log.warn("pos_db", "stock negativo en CUB")
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# ── RUTA ─────────────────────────────────────────────────────────────────────

LOG_DIR  = os.path.join(os.path.expanduser("~"), "bayosys", "data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "bayosys.log")

# ── FORMATO DMESG ─────────────────────────────────────────────────────────────

class DmesgFormatter(logging.Formatter):
    """
    Formato: [YYYY-MM-DD HH:MM:SS] [LEVEL] [modulo   ] mensaje
    """
    LEVELS = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO ",
        logging.WARNING:  "WARN ",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRIT ",
    }

    def format(self, record):
        ts      = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level   = self.LEVELS.get(record.levelno, "?????")
        # extraer solo la parte después del último punto (child logger)
        parts   = record.name.split(".")
        modulo  = parts[-1][:10].ljust(10)
        mensaje = record.getMessage()
        linea   = f"[{ts}] [{level}] [{modulo}] {mensaje}"

        # si hay excepción la agrega indentada
        if record.exc_info:
            import traceback
            exc = "".join(traceback.format_exception(*record.exc_info))
            linea += "\n" + "\n".join(f"    {l}" for l in exc.splitlines())

        return linea


# ── SETUP ─────────────────────────────────────────────────────────────────────

def _setup() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("bayosys")
    if logger.handlers:
        return logger   # ya inicializado

    logger.setLevel(logging.DEBUG)

    # archivo rotativo — max 1MB, guarda 3 backups
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(DmesgFormatter())
    logger.addHandler(fh)

    # consola — solo ERROR y CRITICAL para no ensuciar el TUI
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)
    ch.setFormatter(DmesgFormatter())
    logger.addHandler(ch)

    return logger


# ── INTERFAZ PÚBLICA ──────────────────────────────────────────────────────────

class _Log:
    """
    Wrapper que agrega el nombre del módulo como logger child.
    Uso: log.info("config", "mensaje")
    """
    def __init__(self):
        self._root = _setup()

    def _get(self, modulo: str) -> logging.Logger:
        return self._root.getChild(modulo)

    def debug(self, modulo: str, msg: str):
        self._get(modulo).debug(msg)

    def info(self, modulo: str, msg: str):
        self._get(modulo).info(msg)

    def warn(self, modulo: str, msg: str):
        self._get(modulo).warning(msg)

    def error(self, modulo: str, msg: str, exc: bool = False):
        """exc=True captura el traceback activo si hay uno."""
        self._get(modulo).error(msg, exc_info=exc)

    def critical(self, modulo: str, msg: str, exc: bool = False):
        self._get(modulo).critical(msg, exc_info=exc)

    @property
    def path(self) -> str:
        return LOG_FILE


# singleton — todos los módulos importan este objeto
log = _Log()


# ── TAIL (para ver el log desde main) ────────────────────────────────────────

def tail_log(n: int = 40) -> str:
    """Devuelve las últimas n líneas del log como string."""
    if not os.path.exists(LOG_FILE):
        return "  (log vacío)"
    with open(LOG_FILE, encoding="utf-8") as f:
        lineas = f.readlines()
    return "".join(lineas[-n:])


def ver_log(n: int = 40):
    """Imprime las últimas n líneas — para el menú de bayosys."""
    print(f"\n  log: {LOG_FILE}")
    print("  " + "─" * 70)
    contenido = tail_log(n)
    if contenido.strip():
        for linea in contenido.splitlines():
            print(f"  {linea}")
    else:
        print("  (log vacío)")
    print()


# ── TEST ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("logger",   "sistema iniciado")
    log.debug("config",  "cargar_config() — defaults cargados")
    log.info("pos_db",   "init_db() — pos.db lista, 6 SKUs")
    log.info("registro", "batch #1 guardado — 25.0kg grasa → 5.5kg chi")
    log.warn("calcular", "merma alta: 18.3% en batch #2")
    log.error("cierre",  "archivo no encontrado: 2026-06-12_cierre.json")

    try:
        x = 1 / 0
    except ZeroDivisionError:
        log.error("test", "división por cero capturada", exc=True)

    print(f"log escrito en: {log.path}")
    print()
    ver_log(20)
