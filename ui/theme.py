#!/usr/bin/env python3
"""Tema visual "premium" (Apple HIG) adaptado a escritorio Linux/KDE.

Segunda pasada de UI/UX sobre el tema original:
  - Ya no fuerza un tema claro sin importar lo que diga el sistema: se
    detecta si KDE/el entorno de escritorio corre en modo oscuro
    (`system_prefers_dark`) y se aplica la hoja de estilos que
    corresponde (`apply_theme`). Si el Qt disponible expone
    `QStyleHints.colorSchemeChanged` (Qt >= 6.5) el cambio de tema en
    caliente (Configuración del Sistema → Apariencia) se refleja sin
    reiniciar la app.
  - Los dos temas se generan a partir de UN solo template (`_QSS_TEMPLATE`)
    y un diccionario de tokens por tema, en vez de mantener dos hojas de
    estilo completas por separado: cualquier ajuste de estructura solo
    se toca una vez.
  - Colores de acento corregidos para ser fieles a Apple HIG: el azul de
    sistema en modo claro es #007AFF (no #0A84FF, que es la variante de
    modo OSCURO — el tema original usaba la de oscuro también en claro).
  - Contraste de texto secundario verificado contra WCAG 2.1 AA (ratio
    ≥ 4.5:1 para texto normal) en ambos temas; el rojo de "peligro" en
    modo claro se mantuvo tal cual porque ya supera al rojo de sistema
    de Apple en este contexto (texto pequeño sobre chip claro).
  - `apply_elevation()`: sombras de elevación (QGraphicsDropShadowEffect)
    para dar profundidad tipo tarjeta a paneles estáticos. Deliberadamente
    NO se usa sobre video_view/image_view: un QGraphicsEffect fuerza un
    repintado por buffer offscreen en software, carísimo si el widget se
    repinta a 30-60 fps.
  - `build_app_icon()`: icono de aplicación generado por código (sin
    depender de un asset externo) para que la ventana y la barra de
    tareas de KDE dejen de mostrar el icono genérico de Qt.

Fuera de alcance deliberado (ver AUDIT_UX.md): "vibrancy"/blur real tipo
macOS y esquinas continuas ("squircle") — ninguna de las dos tiene una
API estable y portable en Qt Widgets sin pintar cada widget a mano con
QPainterPath, lo que no compensa el riesgo para una app de escritorio
utilitaria como esta.
"""
from __future__ import annotations

from string import Template
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPixmap, QPolygonF
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

if TYPE_CHECKING:  # pragma: no cover - solo para tipado, evita import en runtime
    from PyQt6.QtWidgets import QApplication, QWidget

FONT_STACK = (
    '-apple-system, "SF Pro Display", "SF Pro Text", "Helvetica Neue", '
    '"Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", Arial, sans-serif'
)

# ---------------------------------------------------------------------
# Tokens de diseño: un diccionario por tema, misma forma en los dos.
# ---------------------------------------------------------------------
LIGHT_TOKENS = {
    "font_stack": FONT_STACK,
    "bg_window": "#F5F5F7",
    "bg_card": "#FFFFFF",
    "border": "#E4E4E9",
    "border_hover": "#C9C9D2",
    "text_primary": "#1D1D1F",
    # Oscurecido respecto al original (#6E6E73 → ratio 4.65:1, al límite
    # de AA) para tener margen real sobre el mínimo de 4.5:1 en texto de
    # 12px (timeLabel/groupCaption): #57575D da ≈ 6.6:1.
    "text_secondary": "#57575D",
    "accent": "#007AFF",  # Apple systemBlue — variante de modo CLARO
    "accent_hover": "#3395FF",
    "accent_active": "#005ECB",
    "accent_text": "#FFFFFF",
    # D70015 se conserva: como texto pequeño sobre el chip claro de abajo
    # da ≈ 5.1:1, más contraste que el systemRed de Apple (#FF3B30) en
    # ese mismo contexto (≈ 3.3:1, no pasaría AA aquí).
    "danger_text": "#D70015",
    "danger_bg": "#FFF5F5",
    "danger_border": "#FBD5D8",
    "danger_hover_bg": "#FFE5E7",
    "danger_hover_border": "#F6ABB1",
    "disabled_text": "#B8B8BD",
    "disabled_bg": "#FAFAFB",
    "disabled_border": "#EDEDF2",
    "hover_bg": "#F0F6FF",
    "pressed_bg": "#E4EFFF",
    "scrollbar_handle": "#D2D2D7",
    "scrollbar_handle_hover": "#B8B8BD",
    "selection_bg": "#E8F1FF",
    "selection_text": "#0A2540",
    "selection_hover_bg": "#DDEBFF",
    "list_hover_bg": "#F5F5F7",
    "tooltip_bg": "#1D1D1F",
    "tooltip_text": "#FFFFFF",
}

DARK_TOKENS = {
    "font_stack": FONT_STACK,
    "bg_window": "#1C1C1E",
    "bg_card": "#2C2C2E",
    "border": "#3A3A3C",
    "border_hover": "#48484A",
    "text_primary": "#F5F5F7",
    # ≈ 6.5:1 sobre bg_window (#1C1C1E): AA cómodo para texto pequeño.
    "text_secondary": "#A0A0A6",
    "accent": "#0A84FF",  # Apple systemBlue — variante de modo OSCURO
    "accent_hover": "#409CFF",
    "accent_active": "#0060C2",
    "accent_text": "#FFFFFF",
    # systemRed de modo oscuro (#FF453A) sobre un chip rojo oscuro:
    # ≈ 4.7:1, pasa AA con margen pequeño pero suficiente.
    "danger_text": "#FF453A",
    "danger_bg": "#3A1618",
    "danger_border": "#5C2328",
    "danger_hover_bg": "#4A1A1E",
    "danger_hover_border": "#7A2E34",
    "disabled_text": "#6C6C70",
    "disabled_bg": "#242426",
    "disabled_border": "#333335",
    "hover_bg": "#2A3A4D",
    "pressed_bg": "#20344A",
    "scrollbar_handle": "#48484A",
    "scrollbar_handle_hover": "#5A5A5C",
    "selection_bg": "#123A5C",
    "selection_text": "#EAF4FF",
    "selection_hover_bg": "#164A73",
    "list_hover_bg": "#242426",
    "tooltip_bg": "#F5F5F7",
    "tooltip_text": "#1D1D1F",
}

# Se usa Template ($token) en vez de f-strings: las reglas QSS están
# llenas de llaves { } literales que con f-strings habría que escapar
# como {{ }} en cada bloque; con $sustituciones no hace falta tocarlas.
_QSS_TEMPLATE = Template(
    """
* {
    font-family: $font_stack;
}

QMainWindow, QWidget {
    background-color: $bg_window;
    color: $text_primary;
}

/* ---------- Contenedores tipo tarjeta ---------- */
/* Los paneles (video/imagen/galería) son ajustables por el usuario
   arrastrando estos separadores. 1px era casi imposible de "agarrar"
   con el ratón y hacía que los paneles parecieran fijos; 6px deja
   margen de sobra para el arrastre y sigue leyendo como una línea
   fina gracias al color de fondo. */
QSplitter::handle {
    background-color: $border;
    width: 6px;
    margin: 2px 0;
    border-radius: 2px;
}

QSplitter::handle:horizontal {
    width: 6px;
}

QSplitter::handle:vertical {
    height: 6px;
}

QSplitter::handle:hover {
    background-color: $accent;
}

/* Paneles de controles (debajo del video/imagen) y galería: tarjetas
   elevadas mediante apply_elevation(), aquí solo se define su "piel". */
QWidget#controlsCard {
    background-color: $bg_card;
    border: 1px solid $border;
    border-radius: 16px;
}

QWidget#emptyState {
    background: transparent;
}

/* Envoltorios de FlowLayout (ver ui/flow_layout.py y
   ModernVideoPlayer._make_control_group): son contenedores puramente
   estructurales dentro de controlsCard, no deben pintar su propio
   fondo (heredarían el color de fondo de ventana y se verían como un
   parche rectangular dentro de la tarjeta). */
QWidget#controlGroup {
    background: transparent;
}

QLabel#emptyStateTitle {
    color: $text_primary;
    font-size: 14px;
    font-weight: 600;
}

QLabel#emptyStateHint {
    color: $text_secondary;
    font-size: 12px;
}

/* ---------- Etiquetas ---------- */
QLabel {
    color: $text_primary;
    background: transparent;
}

QLabel#sectionTitle {
    font-size: 16px;
    font-weight: 600;
    color: $text_primary;
    padding: 2px 0;
}

QLabel#timeLabel {
    color: $text_secondary;
    font-size: 12px;
    min-width: 62px;
}

QLabel#statLabel {
    color: $text_primary;
    font-weight: 600;
    font-size: 12px;
    padding: 0 4px;
}

QLabel#groupCaption {
    color: $text_secondary;
    font-size: 12px;
    font-weight: 500;
}

/* ---------- Botones base ---------- */
QPushButton {
    background-color: $bg_card;
    color: $text_primary;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12.5px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: $hover_bg;
    border-color: $accent_hover;
}

QPushButton:pressed {
    background-color: $pressed_bg;
}

QPushButton:focus {
    border-color: $accent;
}

QPushButton:checked {
    background-color: $accent;
    color: $accent_text;
    border-color: $accent;
}

QPushButton:checked:hover {
    background-color: $accent_hover;
}

QPushButton:disabled {
    color: $disabled_text;
    background-color: $disabled_bg;
    border-color: $disabled_border;
}

/* ---------- Transporte (play/pause/prev/next/stop) ---------- */
QPushButton#transportBtn {
    border-radius: 20px;
    min-width: 38px;
    min-height: 38px;
    max-width: 38px;
    max-height: 38px;
    font-size: 15px;
    padding: 0;
}

QPushButton#playPauseBtn {
    background-color: $accent;
    color: $accent_text;
    border: none;
    border-radius: 24px;
    min-width: 46px;
    min-height: 46px;
    max-width: 46px;
    max-height: 46px;
    font-size: 17px;
}

QPushButton#playPauseBtn:hover {
    background-color: $accent_hover;
}

QPushButton#playPauseBtn:pressed {
    background-color: $accent_active;
}

/* ---------- Botones primarios (agregar videos, cargar imágenes...) --- */
QPushButton#primaryBtn {
    background-color: $accent;
    color: $accent_text;
    border: none;
    font-weight: 600;
    padding: 9px 14px;
    border-radius: 10px;
}

QPushButton#primaryBtn:hover {
    background-color: $accent_hover;
}

QPushButton#primaryBtn:pressed {
    background-color: $accent_active;
}

/* ---------- Botones destructivos (quitar / limpiar) ---------- */
QPushButton#dangerBtn {
    color: $danger_text;
    border-color: $danger_border;
    background-color: $danger_bg;
}

QPushButton#dangerBtn:hover {
    background-color: $danger_hover_bg;
    border-color: $danger_hover_border;
}

/* ---------- Sliders ---------- */
QSlider::groove:horizontal {
    height: 4px;
    background: $border;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: $bg_card;
    border: 1px solid $border_hover;
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    border: 1px solid $accent;
}

QSlider::handle:horizontal:pressed {
    border: 1px solid $accent_active;
    background: $hover_bg;
}

/* ---------- Lista / galería ---------- */
QListWidget {
    background-color: $bg_card;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 6px;
    outline: none;
}

QListWidget:focus {
    border: 1px solid $accent;
}

QListWidget::item {
    border-radius: 8px;
    padding: 6px;
    margin: 2px 0;
    color: $text_primary;
}

QListWidget::item:hover {
    background-color: $list_hover_bg;
}

QListWidget::item:selected {
    background-color: $selection_bg;
    color: $selection_text;
}

QListWidget::item:selected:hover {
    background-color: $selection_hover_bg;
}

/* ---------- Menús contextuales ---------- */
QMenu {
    background-color: $bg_card;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 6px;
}

QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 6px;
    color: $text_primary;
}

QMenu::item:selected {
    background-color: $accent;
    color: $accent_text;
}

QMenu::item:disabled {
    color: $disabled_text;
}

QMenu::separator {
    height: 1px;
    background: $border;
    margin: 6px 8px;
}

/* ---------- Barra de estado ---------- */
QStatusBar {
    background-color: $bg_window;
    color: $text_secondary;
    border-top: 1px solid $border;
    font-size: 12px;
}

QStatusBar::item {
    border: none;
}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: $scrollbar_handle;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background: $scrollbar_handle_hover;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: $scrollbar_handle;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background: $scrollbar_handle_hover;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ---------- Tooltips ---------- */
QToolTip {
    background-color: $tooltip_bg;
    color: $tooltip_text;
    border: none;
    padding: 5px 8px;
    border-radius: 6px;
    font-size: 12px;
}
"""
)


def build_qss(dark: bool) -> str:
    """Genera la hoja de estilos completa para el tema pedido."""
    return _QSS_TEMPLATE.substitute(DARK_TOKENS if dark else LIGHT_TOKENS)


# Mantenidos por compatibilidad (por si algo externo los importa
# directamente); el punto de entrada real es apply_theme().
PREMIUM_LIGHT_QSS = build_qss(dark=False)
PREMIUM_DARK_QSS = build_qss(dark=True)


def system_prefers_dark(app: "QApplication") -> bool:
    """Heurística de detección de tema oscuro del sistema (KDE u otro).

    Se apoya en la paleta que Qt ya carga del tema nativo al construir la
    QApplication (KDE Breeze/Breeze Dark vía el plugin de plataforma, GTK
    en otros escritorios), así que debe llamarse ANTES de
    app.setStyle("Fusion"): cambiar de estilo puede reemplazar esa
    paleta por la propia de Fusion y perder la señal.

    Se prefiere esto a QStyleHints.colorScheme() (solo Qt >= 6.5) porque
    la paleta nativa la expone cualquier Qt6 con integración de
    plataforma, sin depender de una versión mínima concreta.
    """
    window_color = app.palette().color(QPalette.ColorRole.Window)
    # Luminancia perceptual (coeficientes Rec. 601): el ojo pesa más el
    # canal verde que el rojo o el azul, un promedio simple de RGB
    # clasificaría mal algunos temas de acento verdoso/azulado.
    luminance = (
        0.299 * window_color.red()
        + 0.587 * window_color.green()
        + 0.114 * window_color.blue()
    )
    return luminance < 128


def apply_theme(app: "QApplication", dark: bool) -> None:
    """Aplica el tema claro u oscuro a toda la aplicación."""
    app.setStyleSheet(PREMIUM_DARK_QSS if dark else PREMIUM_LIGHT_QSS)


def watch_system_theme(app: "QApplication") -> None:
    """Reacciona en caliente a un cambio de tema del sistema, si Qt lo permite.

    QStyleHints.colorSchemeChanged existe desde Qt 6.5; en instalaciones
    con un Qt6 más antiguo simplemente no se conecta nada y el tema
    queda fijo al detectado en el arranque (degradación silenciosa, no
    un error: la app sigue siendo perfectamente usable sin esto).
    """
    try:
        hints = app.styleHints()
        hints.colorSchemeChanged.connect(
            lambda scheme: apply_theme(app, scheme == Qt.ColorScheme.Dark)
        )
    except AttributeError:
        pass


# ---------------------------------------------------------------------
# Elevación (sombras tipo tarjeta)
# ---------------------------------------------------------------------
# Blur/offset/opacidad crecientes = "varios niveles" de elevación, tal
# como pide HIG (los paneles más prominentes flotan un poco más alto).
_ELEVATION_LEVELS = {
    1: {"blur": 24.0, "y_offset": 4.0, "alpha": 50},
    2: {"blur": 36.0, "y_offset": 8.0, "alpha": 60},
    3: {"blur": 48.0, "y_offset": 12.0, "alpha": 70},
}


def apply_elevation(widget: "QWidget", level: int = 1) -> QGraphicsDropShadowEffect:
    """Sombra de elevación suave (QGraphicsDropShadowEffect) sobre `widget`.

    Pensado para paneles ESTÁTICOS (la galería, las barras de controles):
    un QGraphicsEffect obliga a Qt a componer ese widget en un buffer
    offscreen por software en cada repintado, lo cual es barato para algo
    que solo se repinta al interactuar mas NO para algo que cambia a
    30-60 fps — por eso nunca se aplica a video_view/image_view.
    """
    cfg = _ELEVATION_LEVELS.get(level, _ELEVATION_LEVELS[1])
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(cfg["blur"])
    effect.setOffset(0, cfg["y_offset"])
    effect.setColor(QColor(0, 0, 0, cfg["alpha"]))
    widget.setGraphicsEffect(effect)
    return effect


# ---------------------------------------------------------------------
# Icono de aplicación (generado por código, sin asset externo)
# ---------------------------------------------------------------------
def _paint_app_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = size * 0.06
        rect_size = size - 2 * margin
        radius = rect_size * 0.24  # esquina redondeada, proporcional al tamaño

        path = QPainterPath()
        path.addRoundedRect(margin, margin, rect_size, rect_size, radius, radius)

        gradient = QLinearGradient(0, 0, 0, size)
        gradient.setColorAt(0.0, QColor("#3395FF"))
        gradient.setColorAt(1.0, QColor("#0A2540"))
        painter.fillPath(path, gradient)

        # Triángulo de "play", igual que el usado en las miniaturas
        # genéricas: mismo lenguaje visual en toda la app.
        cx, cy = size / 2.0, size / 2.0
        tri_h = rect_size * 0.34
        tri_w = rect_size * 0.30
        triangle = QPolygonF(
            [
                QPointF(cx - tri_w * 0.35, cy - tri_h / 2),
                QPointF(cx - tri_w * 0.35, cy + tri_h / 2),
                QPointF(cx + tri_w * 0.65, cy),
            ]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPolygon(triangle)
    finally:
        painter.end()
    return pixmap


def build_app_icon() -> QIcon:
    """Icono multi-resolución para ventana/barra de tareas.

    Se dibuja explícitamente en cada tamaño (en vez de escalar un único
    pixmap grande) para que se vea nítido también en los tamaños
    pequeños de la bandeja/alt-tab de KDE.
    """
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_paint_app_icon(size))
    return icon
