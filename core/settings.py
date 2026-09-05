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
        # Por defecto, índice 9 = nivel 10 ("Normal", 1.0x) en la escala
        # entera 1-40 de ui.main_window.ModernVideoPlayer.speeds. Si el
        # valor guardado viene de una versión anterior (escala distinta)
        # y queda fuera de rango, quien llama (init_ui) lo reajusta solo.
        try:
            return int(self._s.value("playback/speed_index", 9))
        except (TypeError, ValueError):
            return 9

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

    # --- Pantalla dividida (video + imagen) -------------------------------
    def split_mode(self) -> bool:
        try:
            return bool(self._s.value("layout/split_mode", False, type=bool))
        except (TypeError, ValueError):
            return False

    def set_split_mode(self, value: bool) -> None:
        self._s.setValue("layout/split_mode", value)

    # --- Tamaños de paneles (splitters ajustables por el usuario) ---------
    def _read_int_list(self, key: str) -> Optional[List[int]]:
        """Lee una lista de enteros guardada por un QSplitter.saveState()
        equivalente manual (sizes()). Tolerante a valores corruptos o
        ausentes: devuelve None para que el llamador use su propio
        reparto por defecto en vez de fallar."""
        raw = self._s.value(key)
        if not raw:
            return None
        try:
            sizes = [int(v) for v in raw]
        except (TypeError, ValueError):
            logger.warning("Tamaños de panel corruptos en settings (%s); se ignoran", key)
            return None
        return sizes if all(s >= 0 for s in sizes) else None

    def main_splitter_sizes(self) -> Optional[List[int]]:
        """Reparto guardado entre el área de medios y la galería."""
        return self._read_int_list("layout/main_splitter_sizes")

    def set_main_splitter_sizes(self, sizes: List[int]) -> None:
        self._s.setValue("layout/main_splitter_sizes", [int(s) for s in sizes])

    def media_splitter_sizes(self) -> Optional[List[int]]:
        """Reparto guardado entre el panel de video y el de imagen."""
        return self._read_int_list("layout/media_splitter_sizes")

    def set_media_splitter_sizes(self, sizes: List[int]) -> None:
        self._s.setValue("layout/media_splitter_sizes", [int(s) for s in sizes])

    # --- Sesión ----------------------------------------------------------
    def last_folder(self) -> str:
        return str(self._s.value("session/last_folder", ""))

    def set_last_folder(self, path: str) -> None:
        self._s.setValue("session/last_folder", path)

    def last_image_folder(self) -> str:
        return str(self._s.value("session/last_image_folder", ""))

    def set_last_image_folder(self, path: str) -> None:
        self._s.setValue("session/last_image_folder", path)

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

    # --- Galería de imágenes (panel de pantalla dividida) ------------------
    def image_paths(self) -> List[str]:
        """Misma normalización que playlist_paths(): ver ese método."""
        raw = self._s.value("session/images", [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw] if raw else []
        try:
            return [str(p) for p in raw if p]
        except TypeError:
            logger.warning("Lista de imágenes corrupta en settings; se ignora")
            return []

    def set_image_paths(self, paths: List[str]) -> None:
        self._s.setValue("session/images", [str(p) for p in paths])

    def sync(self) -> None:
        self._s.sync()
