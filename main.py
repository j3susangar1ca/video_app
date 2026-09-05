#!/usr/bin/env python3
"""Reproductor Multimedia — punto de entrada.

Fase 2 de la modernización:
  - Metadatos de aplicación (organización / nombre) coherentes con QSettings.
  - Excepthook instalado: los errores no capturados quedan en el log.
  - Módulos separados core/ vs ui/, logging real, miniaturas cacheadas,
    preferencias y galería persistentes, tema claro tipo Apple.
"""
import sys

from PyQt6.QtWidgets import QApplication

from core.logging_config import configure_logging
from core.settings import APP_NAME, ORG_NAME
from ui.main_window import ModernVideoPlayer
from ui.theme import PREMIUM_LIGHT_QSS


def main():
    configure_logging()

    app = QApplication(sys.argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(PREMIUM_LIGHT_QSS)

    initial_args = sys.argv[1:] if len(sys.argv) > 1 else None
    player = ModernVideoPlayer(initial_args)
    player.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
