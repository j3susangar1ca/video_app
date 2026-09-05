#!/usr/bin/env python3
"""Generación de miniaturas con caché persistente en disco.

Mejoras respecto a la versión anterior:
  - La señal entrega QImage (seguro para cruzar hilos). Crear QPixmap
    fuera del hilo de la GUI NO es seguro y podía provocar crashes;
    la conversión a QPixmap se hace en la ventana principal.
  - ffmpeg se detecta UNA sola vez por ejecución (shutil.which); si no
    está, no se lanzan subprocess en vano por cada archivo.
  - Si falta ffmpeg o falla la extracción, se emite una miniatura
    genérica dibujada por la app: ningún elemento queda en gris eterno.
  - Los fallos NO se cachean en disco: si el usuario instala ffmpeg o
    el archivo vuelve a estar bien, la miniatura se genera al reabrir.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QPointF, QRunnable, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPolygonF

from core.logging_config import cache_dir

logger = logging.getLogger("reproductor.thumbnails")

THUMB_W = 160
THUMB_H = 90
FFMPEG_TIMEOUT_S = 10

_FFMPEG_AVAILABLE: Optional[bool] = None


def thumbs_dir() -> Path:
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(file_path: str) -> str:
    try:
        stat = os.stat(file_path)
        raw = f"{file_path}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        raw = file_path
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def cached_thumb_path(file_path: str) -> Path:
    return thumbs_dir() / f"{_cache_key(file_path)}.png"


def ffmpeg_available() -> bool:
    """Detecta ffmpeg una sola vez por ejecución de la app."""
    global _FFMPEG_AVAILABLE
    if _FFMPEG_AVAILABLE is None:
        _FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
        if not _FFMPEG_AVAILABLE:
            logger.error(
                "ffmpeg no está instalado o no está en PATH; se usarán "
                "miniaturas genéricas hasta instalarlo"
            )
    return _FFMPEG_AVAILABLE


def fallback_thumb() -> QImage:
    """Miniatura genérica: rectángulo oscuro con un triángulo de 'play'.

    Se dibuja con primitivas vectoriales (no texto ni emojis) para que se
    vea igual en cualquier sistema y fuente instalada.
    """
    img = QImage(THUMB_W, THUMB_H, QImage.Format.Format_ARGB32)
    img.fill(QColor("#2A2A2E"))

    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#5A5A61"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(4, 4, THUMB_W - 8, THUMB_H - 8, 8, 8)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#98989E"))
        cx, cy = THUMB_W / 2.0, THUMB_H / 2.0
        triangle = QPolygonF(
            [
                QPointF(cx - 7.0, cy - 11.0),
                QPointF(cx - 7.0, cy + 11.0),
                QPointF(cx + 13.0, cy),
            ]
        )
        painter.drawPolygon(triangle)
    finally:
        painter.end()
    return img


class ThumbSignals(QObject):
    # QImage y no QPixmap: QImage es seguro entre hilos, QPixmap no.
    finished = pyqtSignal(str, QImage)


class ThumbWorker(QRunnable):
    """Genera (o recupera de caché en disco) la miniatura de un video."""

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = ThumbSignals()

    def run(self):
        try:
            cache_path = cached_thumb_path(self.file_path)

            if cache_path.exists():
                img = QImage(str(cache_path))
                if not img.isNull():
                    self.signals.finished.emit(self.file_path, img)
                    return
                # Caché corrupta: la descartamos y regeneramos.
                logger.warning("Miniatura en caché corrupta, regenerando: %s", cache_path)

            png_bytes = self._extract_frame()
            if png_bytes is not None:
                try:
                    cache_path.write_bytes(png_bytes)
                except OSError:
                    logger.exception("No se pudo escribir en caché de miniaturas")

                img = QImage.fromData(png_bytes)
                if not img.isNull():
                    self.signals.finished.emit(self.file_path, img)
                    return

            # Fallo (ffmpeg ausente, timeout, codec o archivo ilegible):
            # miniatura genérica y SIN cachear el fallo.
            self.signals.finished.emit(self.file_path, fallback_thumb())
        except Exception:
            logger.exception("Fallo generando miniatura para %s", self.file_path)
            try:
                self.signals.finished.emit(self.file_path, fallback_thumb())
            except RuntimeError:
                # La app se cerró mientras el worker corría: nada que hacer.
                pass

    def _extract_frame(self) -> Optional[bytes]:
        if not ffmpeg_available():
            return None

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "quiet",
            "-ss",
            "00:00:00.5",
            "-i",
            self.file_path,
            "-vframes",
            "1",
            "-vf",
            f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,"
            f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:black",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=FFMPEG_TIMEOUT_S,
            )
        except FileNotFoundError:
            # Carrera teórica: ffmpeg desapareció tras el shutil.which.
            global _FFMPEG_AVAILABLE
            _FFMPEG_AVAILABLE = False
            logger.error("ffmpeg desapareció del PATH durante la ejecución")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Timeout generando miniatura para %s", self.file_path)
            return None

        if res.returncode == 0 and res.stdout:
            return res.stdout

        logger.warning(
            "ffmpeg devolvió código %s para %s", res.returncode, self.file_path
        )
        return None


class ImageThumbSignals(QObject):
    # Misma forma que ThumbSignals: QImage, seguro entre hilos.
    finished = pyqtSignal(str, QImage)


class ImageThumbWorker(QRunnable):
    """Genera la miniatura de una imagen (galería de fotos).

    A diferencia de ThumbWorker (video, necesita ffmpeg + subprocess),
    aquí basta con cargar el archivo y reescalarlo: no hace falta caché
    en disco porque decodificar y reescalar una imagen ya es barato de
    por sí. Se hace en un QRunnable de todos modos para no bloquear la
    GUI si el usuario carga muchas fotos grandes de golpe.
    """

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = ImageThumbSignals()

    def run(self):
        try:
            img = QImage(self.file_path)
            if img.isNull():
                self.signals.finished.emit(self.file_path, fallback_thumb())
                return
            scaled = img.scaled(
                THUMB_W,
                THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.signals.finished.emit(self.file_path, scaled)
        except Exception:
            logger.exception(
                "Fallo generando miniatura de imagen para %s", self.file_path
            )
            try:
                self.signals.finished.emit(self.file_path, fallback_thumb())
            except RuntimeError:
                # La app se cerró mientras el worker corría: nada que hacer.
                pass
