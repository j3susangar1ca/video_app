#!/usr/bin/env python3
"""Reproductor Multimedia — punto de entrada.

Fase 3 de la modernización (pasada de UI/UX):
  - Metadatos de aplicación (organización / nombre) coherentes con QSettings.
  - Excepthook instalado: los errores no capturados quedan en el log.
  - Módulos separados core/ vs ui/, logging real, miniaturas cacheadas,
    preferencias y galería persistentes.
  - Tema claro/oscuro detectado del sistema (KDE u otro) en vez de forzar
    siempre el claro, con reacción en caliente si el Qt disponible lo
    soporta (ver ui/theme.py).
  - Icono de aplicación propio (antes mostraba el genérico de Qt en la
    barra de tareas/alt-tab) y `setDesktopFileName` para que el gestor de
    ventanas de KDE pueda asociar la ventana a su entrada .desktop
    (agrupación en la barra de tareas, icono correcto si se fija/pinea).
"""
import sys

from PyQt6.QtWidgets import QApplication

from core.logging_config import configure_logging
from core.settings import APP_NAME, ORG_NAME
from ui.main_window import ModernVideoPlayer
from ui.theme import apply_theme, build_app_icon, system_prefers_dark, watch_system_theme

# Debe coincidir con el nombre del archivo .desktop que instale el
# paquete (p. ej. /usr/share/applications/reproductor-multimedia.desktop).
# Si ese archivo no existe todavía, esta llamada no falla: simplemente no
# hay nada que KDE pueda asociar, y la ventana sigue funcionando igual.
DESKTOP_FILE_NAME = "reproductor-multimedia"


def main():
    configure_logging()

    app = QApplication(sys.argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app.setWindowIcon(build_app_icon())

    # La detección de tema debe ir ANTES de setStyle("Fusion"): ese cambio
    # de estilo reemplaza la paleta nativa (KDE Breeze/Breeze Dark) por la
    # propia de Fusion y con ella se pierde la señal de tema del sistema.
    dark = system_prefers_dark(app)
    app.setStyle("Fusion")
    apply_theme(app, dark)
    watch_system_theme(app)

    initial_args = sys.argv[1:] if len(sys.argv) > 1 else None
    player = ModernVideoPlayer(initial_args)
    player.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
