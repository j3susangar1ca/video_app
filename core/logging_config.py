#!/usr/bin/env python3
"""Configuración de logging centralizada para la aplicación.

- Los mensajes propios de la app van a un archivo de log rotativo
  (útil para diagnosticar problemas del usuario) y los errores/warnings
  también se muestran en consola.
- El ruido conocido de librerías nativas (VA-API/VDPAU/Qt Multimedia)
  se filtra, pero solo ese ruido puntual, no el canal de errores completo.
- Nuevo: se instala un excepthook para que las excepciones no capturadas
  queden registradas en el log en lugar de perderse en silencio.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "reproductor"


def cache_dir() -> Path:
    """Directorio de caché de la app, respetando XDG_CACHE_HOME si existe."""
    base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file_path() -> Path:
    """Ruta del archivo de log principal (útil para mostrarla al usuario)."""
    return cache_dir() / "logs" / f"{APP_NAME}.log"


def _install_excepthook(logger: logging.Logger) -> None:
    """Registra en el log las excepciones no capturadas antes de morir.

    Antes, si algo fallaba fuera de un try/except (p. ej. dentro de un slot
    de Qt), el error desaparecía sin dejar rastro. Ahora queda en el log
    con traceback completo y además se imprime por stderr como siempre.
    """
    default_hook = sys.__excepthook__

    def hook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            logger.critical(
                "Excepción no capturada", exc_info=(exc_type, exc_value, exc_tb)
            )
        default_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Inicializa logging a archivo + consola y el excepthook global."""
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
    os.environ.setdefault("LIBVA_MESSAGING_LEVEL", "0")
    os.environ.setdefault("VDPAU_LOG", "0")

    log_dir = cache_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{APP_NAME}.log"

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger  # ya configurado, evita duplicar handlers

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.WARNING)
    logger.addHandler(console_handler)

    _install_excepthook(logger)

    logger.info("Logging inicializado. Archivo de log: %s", log_file)
    return logger
