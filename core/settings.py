#!/usr/bin/env python3
"""Persistencia de preferencias del usuario usando QSettings.

Guarda entre sesiones: geometría de ventana, volumen, velocidad, modo de
relleno, bucle, última carpeta abierta y (nuevo) la lista de videos de la
galería para restaurarla al reabrir la app.

En Linux se guarda en ~/.config/ReproductorApp/ReproductorMultimedia.ini.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import QByteArray, QSettings

logger = logging.getLogger("reproductor.settings")

ORG_NAME = "ReproductorApp"
APP_NAME = "ReproductorMultimedia"


class AppSettings:
    """Wrapper delgado sobre QSettings con valores por defecto tipados.

    Todas las lecturas son tolerantes a valores corruptos o con formato
    inesperado: si algo no se puede convertir, se devuelve el defecto
    en lugar de lanzar una excepción que impida arrancar la app.
    """

    def __init__(self):
        self._s = QSettings(ORG_NAME, APP_NAME)

    # --- Ventana -----------------------------------------------------
    def window_geometry(self) -> Optional[QByteArray]:
        geo = self._s.value("window/geometry")
        return geo if isinstance(geo, QByteArray) else None

    def set_window_geometry(self, geometry: QByteArray) -> None:
        self._s.setValue("window/geometry", geometry)

    # --- Reproducción --------------------------------------------------
    def volume(self) -> int:
        try:
            value = int(self._s.value("playback/volume", 80))
        except (TypeError, ValueError):
            return 80
        return max(0, min(100, value))

    def set_volume(self, value: int) -> None:
        self._s.setValue("playback/volume", max(0, min(100, int(value))))

    def speed_index(self) -> int:
        try:
            return int(self._s.value("playback/speed_index", 3))
        except (TypeError, ValueError):
            return 3

    def set_speed_index(self, value: int) -> None:
        self._s.setValue("playback/speed_index", value)

    def fill_mode(self) -> bool:
        try:
            return bool(self._s.value("playback/fill_mode", True, type=bool))
        except (TypeError, ValueError):
            return True

    def set_fill_mode(self, value: bool) -> None:
        self._s.setValue("playback/fill_mode", value)

    def loop(self) -> bool:
        try:
            return bool(self._s.value("playback/loop", False, type=bool))
        except (TypeError, ValueError):
            return False

    def set_loop(self, value: bool) -> None:
        self._s.setValue("playback/loop", value)

    # --- Sesión ----------------------------------------------------------
    def last_folder(self) -> str:
        return str(self._s.value("session/last_folder", ""))

    def set_last_folder(self, path: str) -> None:
        self._s.setValue("session/last_folder", path)

    # --- Galería persistente ----------------------------------------------
    def playlist_paths(self) -> List[str]:
        """Recupera la lista de rutas guardada al cerrar.

        QSettings devuelve str (no list) cuando solo hay un elemento, así
        que se normaliza aquí para que la llamada siempre reciba una lista.
        """
        raw = self._s.value("session/playlist", [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw] if raw else []
        try:
            return [str(p) for p in raw if p]
        except TypeError:
            logger.warning("Lista de galería corrupta en settings; se ignora")
            return []

    def set_playlist_paths(self, paths: List[str]) -> None:
        self._s.setValue("session/playlist", [str(p) for p in paths])

    def sync(self) -> None:
        self._s.sync()
