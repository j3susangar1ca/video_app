#!/usr/bin/env python3
"""FlowLayout: reparte sus widgets en filas y pasa a la siguiente en
cuanto uno no cabe, en vez de comprimirlos o dejar que se corten.

Motivación (ver AUDIT_UX.md / historial de main_window.py): las filas de
controles (transporte, giro/zoom/velocidad...) usaban QHBoxLayout. Un
QHBoxLayout normal, cuando el contenedor es más angosto que la suma de
los anchos naturales de sus widgets, NO los reorganiza: simplemente dej
a que el contenido sobrante quede fuera del área visible del panel (que
por defecto recorta a sus hijos), así que el texto de los últimos
botones aparecía cortado a la mitad — justo el bug reportado al hacer
los paneles ajustables por el usuario (antes, con anchos de panel fijos
y generosos, esto casi nunca pasaba).

FlowLayout resuelve esto sin depender de que el panel tenga cierto
ancho mínimo: cuando un widget no cabe en la línea actual, baja a la
siguiente. El texto de cada botón se ve siempre completo, sin importar
cuánto se reduzca el panel; el único costo es que la fila crece en
alto en vez de en ancho cuando hace falta.

Adaptado del ejemplo oficial "Flow Layout" de Qt (Widgets/Layouts) a
PyQt6.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QStyle


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 8, v_spacing: int = 8):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        while self.count():
            self.takeAt(0)

    # ------------------------------------------------------------------
    # API mínima que QLayout exige implementar
    # ------------------------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self) -> int:
        return self._h_spacing

    def verticalSpacing(self) -> int:
        return self._v_spacing

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    # ------------------------------------------------------------------
    # El algoritmo de reparto en sí
    # ------------------------------------------------------------------
    def _do_layout(self, rect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x, y = effective.x(), effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            h_space = self.horizontalSpacing()
            v_space = self.verticalSpacing()
            if h_space == -1:
                h_space = widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
            if v_space == -1:
                v_space = widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )

            next_x = x + item.sizeHint().width() + h_space
            if next_x - h_space > effective.right() and line_height > 0:
                # No cabe uno más en esta línea: se pasa a la siguiente
                # en vez de comprimir o cortar el widget actual.
                x = effective.x()
                y += line_height + v_space
                next_x = x + item.sizeHint().width() + h_space
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom
