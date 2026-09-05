#!/usr/bin/env python3
"""Widget de video con soporte de rotación, zoom y paneo manual.

Mejoras respecto a la versión anterior:
  - El clic ya no se emite al PRESIONAR sino al SOLTAR sin arrastre:
    hacer paneo sobre el video ya no pausa/reproduce por accidente.
  - El paneo está limitado a los bordes del contenido: no se puede
    arrastrar el video fuera de la pantalla.
  - Nueva señal mouseMoved para auto-ocultar controles en pantalla
    completa.
  - El texto de placeholder usa la fuente por defecto del sistema
    ("-apple-system" no existe en Linux).
  - Bug corregido: un doble clic (pantalla completa) generaba también un
    clic simple de por medio (play/pausa), porque Qt siempre entrega
    press→release→doubleClick→release para una secuencia de doble clic.
    El clic simple ahora se retrasa el intervalo estándar de doble clic
    y se cancela si llega a confirmarse un doble clic.
  - `placeholder_text` configurable: el widget se reutiliza tal cual
    para el panel de imagen en modo pantalla dividida (misma lógica de
    rotación/zoom/paneo/relleno, solo cambia el mensaje cuando no hay
    contenido cargado).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QWidget


class DirectVideoWidget(QWidget):
    doubleClicked = pyqtSignal()
    clicked = pyqtSignal()
    mouseMoved = pyqtSignal()
    wheelScrolled = pyqtSignal(int)
    zoomScrolled = pyqtSignal(int)

    # Movimiento máximo (px) que se sigue considerando "clic" y no paneo.
    CLICK_THRESHOLD = 6.0

    DEFAULT_PLACEHOLDER = "Arrastra videos o carpetas aquí\no usa el botón 'Agregar videos'"

    def __init__(self, parent=None, placeholder_text: Optional[str] = None):
        super().__init__(parent)
        self.placeholder_text = placeholder_text or self.DEFAULT_PLACEHOLDER
        self.current_frame: Optional[QImage] = None
        self.rotation_angle = 0
        self.zoom_factor = 1.0
        self.flip_h = False  # espejo horizontal (independiente del giro)
        self.fill_mode = True  # True = "cover" (llena), False = "contain"
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._press_pos: Optional[QPointF] = None
        self._pan_start = (0.0, 0.0)
        self._moved = False
        # Retardo del clic simple para poder cancelarlo si resulta ser la
        # primera mitad de un doble clic (ver mouseReleaseEvent).
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.clicked.emit)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_frame(self, image: QImage):
        self.current_frame = image
        self.update()

    def set_rotation(self, angle: int):
        self.rotation_angle = angle % 360
        self._clamp_pan()
        self.update()

    def set_zoom(self, zoom: float):
        self.zoom_factor = max(0.2, min(8.0, zoom))
        self._clamp_pan()
        self.update()

    def set_flip_horizontal(self, flip: bool):
        """Espeja el contenido en el eje horizontal, independiente del
        giro: se aplica ANTES de rotate() para que sea siempre un espejo
        horizontal del contenido original, sin importar qué rotación
        tenga aplicada encima."""
        self.flip_h = flip
        self.update()

    def reset_view(self):
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    def set_fill_mode(self, fill: bool):
        self.fill_mode = fill
        self._clamp_pan()
        self.update()

    def clear(self):
        self.current_frame = None
        self.update()

    # ------------------------------------------------------------------
    # Pintado
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)

        if self.current_frame is None or self.current_frame.isNull():
            painter.setPen(QColor(150, 150, 155))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self.placeholder_text,
            )
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        img_w = self.current_frame.width()
        img_h = self.current_frame.height()
        rotated = self.rotation_angle in (90, 270)
        # Dimensiones del video ya rotado (lo que ocupa en pantalla).
        eff_w = img_h if rotated else img_w
        eff_h = img_w if rotated else img_h

        w_w = self.width()
        w_h = self.height()
        if eff_w == 0 or eff_h == 0:
            return

        # "cover": escala para llenar todo el widget (puede recortar bordes)
        # "contain": escala para que quepa completo (puede dejar barras negras)
        if self.fill_mode:
            base_scale = max(w_w / eff_w, w_h / eff_h)
        else:
            base_scale = min(w_w / eff_w, w_h / eff_h)

        scale = base_scale * self.zoom_factor
        dest_w = img_w * scale
        dest_h = img_h * scale

        painter.translate(w_w / 2.0 + self.pan_x, w_h / 2.0 + self.pan_y)
        if self.flip_h:
            painter.scale(-1, 1)
        painter.rotate(self.rotation_angle)
        painter.drawImage(
            QRectF(-dest_w / 2.0, -dest_h / 2.0, dest_w, dest_h), self.current_frame
        )

    # ------------------------------------------------------------------
    # Interacción
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # El clic se confirma en mouseReleaseEvent solo si no hubo
            # arrastre; así el paneo no dispara play/pausa.
            self._press_pos = event.position()
            self._pan_start = (self.pan_x, self.pan_y)
            self._moved = False

    def mouseMoveEvent(self, event):
        # Señal para auto-ocultar controles en pantalla completa.
        self.mouseMoved.emit()

        if self._press_pos is None:
            return
        delta = event.position() - self._press_pos
        if not self._moved and delta.manhattanLength() > self.CLICK_THRESHOLD:
            self._moved = True
        if self._moved:
            self.pan_x = self._pan_start[0] + delta.x()
            self.pan_y = self._pan_start[1] + delta.y()
            self._clamp_pan()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._press_pos is not None and not self._moved:
                # No emitir todavía: si esto es la primera mitad de un
                # doble clic, mouseDoubleClickEvent cancelará este timer
                # antes de que dispare, evitando un play/pausa espurio.
                self._click_timer.start(QApplication.doubleClickInterval())
            self._press_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.doubleClicked.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomScrolled.emit(1 if delta > 0 else -1)
        else:
            self.wheelScrolled.emit(5 if delta > 0 else -5)

    def resizeEvent(self, event):
        self._clamp_pan()
        super().resizeEvent(event)
        # Forzado explícito: en algunos gestores de ventanas de Linux, un
        # cambio de tamaño disparado por maximizar/restaurar la ventana
        # (en vez de un arrastre manual del usuario) no siempre programa
        # el repintado completo por sí solo, dejando el widget mostrando
        # el frame anterior a medio recuadrar hasta el siguiente frame de
        # video. Pedirlo aquí es barato (una vez por resize, no por frame).
        self.update()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _clamp_pan(self):
        """Limita el paneo para que los bordes del contenido no pasen del
        centro del widget. Con zoom 1 y modo "ajustar completo" el paneo
        queda bloqueado; en modo "llenar pantalla" permite desplazarse por
        la parte recortada, que es justo lo que se espera."""
        if self.current_frame is None or self.current_frame.isNull():
            return

        img_w = self.current_frame.width()
        img_h = self.current_frame.height()
        rotated = self.rotation_angle in (90, 270)
        eff_w = img_h if rotated else img_w
        eff_h = img_w if rotated else img_h
        if eff_w == 0 or eff_h == 0:
            return

        if self.fill_mode:
            base_scale = max(self.width() / eff_w, self.height() / eff_h)
        else:
            base_scale = min(self.width() / eff_w, self.height() / eff_h)

        scale = base_scale * self.zoom_factor
        overflow_x = max(0.0, (eff_w * scale - self.width()) / 2.0)
        overflow_y = max(0.0, (eff_h * scale - self.height()) / 2.0)
        self.pan_x = min(max(self.pan_x, -overflow_x), overflow_x)
        self.pan_y = min(max(self.pan_y, -overflow_y), overflow_y)
