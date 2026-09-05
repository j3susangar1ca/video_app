#!/usr/bin/env python3
"""Tema visual claro, minimalista y 'premium' (inspirado en macOS/Apple).

Paleta:
    Fondo de ventana:      #F5F5F7  (gris muy claro tipo Apple)
    Paneles/tarjetas:      #FFFFFF
    Bordes sutiles:        #E4E4E9
    Texto principal:       #1D1D1F
    Texto secundario:      #6E6E73
    Acento (azul Apple):   #0A84FF
    Acento hover:          #2F98FF
    Fondo hover neutro:    #EDEDF2

Mejoras respecto a la versión anterior:
  - Fuentes de Linux añadidas a la pila (Noto Sans, Ubuntu, Cantarell).
  - Estado :focus visible en botones (navegación por teclado).
  - Estado :pressed en el handle de los sliders.
  - Estilos para la barra de estado (mensajes de error/avisos).
  - Scrollbar horizontal y estado hover de la selección en la galería.
"""

FONT_STACK = (
    '-apple-system, "SF Pro Display", "SF Pro Text", "Helvetica Neue", '
    '"Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", Arial, sans-serif'
)

PREMIUM_LIGHT_QSS = f"""
* {{
    font-family: {FONT_STACK};
}}

QMainWindow, QWidget {{
    background-color: #F5F5F7;
    color: #1D1D1F;
}}

/* ---------- Contenedores tipo tarjeta ---------- */
QSplitter::handle {{
    background-color: #E4E4E9;
    width: 1px;
}}

QSplitter::handle:hover {{
    background-color: #C9C9D2;
}}

/* ---------- Etiquetas ---------- */
QLabel {{
    color: #1D1D1F;
    background: transparent;
}}

QLabel#sectionTitle {{
    font-size: 15px;
    font-weight: 600;
    color: #1D1D1F;
    padding: 2px 0;
}}

QLabel#timeLabel {{
    color: #6E6E73;
    font-size: 12px;
    min-width: 62px;
}}

QLabel#statLabel {{
    color: #1D1D1F;
    font-weight: 600;
    font-size: 12px;
    padding: 0 4px;
}}

QLabel#groupCaption {{
    color: #6E6E73;
    font-size: 12px;
    font-weight: 500;
}}

/* ---------- Botones base ---------- */
QPushButton {{
    background-color: #FFFFFF;
    color: #1D1D1F;
    border: 1px solid #E4E4E9;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12.5px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: #F0F6FF;
    border-color: #C7E0FF;
}}

QPushButton:pressed {{
    background-color: #E4EFFF;
}}

QPushButton:focus {{
    border-color: #0A84FF;
}}

QPushButton:checked {{
    background-color: #0A84FF;
    color: #FFFFFF;
    border-color: #0A84FF;
}}

QPushButton:checked:hover {{
    background-color: #2F98FF;
}}

QPushButton:disabled {{
    color: #B8B8BD;
    background-color: #FAFAFB;
    border-color: #EDEDF2;
}}

/* ---------- Transporte (play/pause/prev/next/stop) ---------- */
QPushButton#transportBtn {{
    border-radius: 20px;
    min-width: 38px;
    min-height: 38px;
    max-width: 38px;
    max-height: 38px;
    font-size: 15px;
    padding: 0;
}}

QPushButton#playPauseBtn {{
    background-color: #0A84FF;
    color: #FFFFFF;
    border: none;
    border-radius: 24px;
    min-width: 46px;
    min-height: 46px;
    max-width: 46px;
    max-height: 46px;
    font-size: 17px;
}}

QPushButton#playPauseBtn:hover {{
    background-color: #2F98FF;
}}

QPushButton#playPauseBtn:pressed {{
    background-color: #0068D6;
}}

/* ---------- Botones primarios (agregar videos, etc) ---------- */
QPushButton#primaryBtn {{
    background-color: #0A84FF;
    color: #FFFFFF;
    border: none;
    font-weight: 600;
    padding: 9px 14px;
    border-radius: 10px;
}}

QPushButton#primaryBtn:hover {{
    background-color: #2F98FF;
}}

QPushButton#primaryBtn:pressed {{
    background-color: #0068D6;
}}

/* ---------- Botones destructivos (quitar / limpiar) ---------- */
QPushButton#dangerBtn {{
    color: #D70015;
    border-color: #FBD5D8;
    background-color: #FFF5F5;
}}

QPushButton#dangerBtn:hover {{
    background-color: #FFE5E7;
    border-color: #F6ABB1;
}}

/* ---------- Sliders ---------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: #E4E4E9;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: #0A84FF;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 1px solid #D2D2D7;
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}

QSlider::handle:horizontal:hover {{
    border: 1px solid #0A84FF;
}}

QSlider::handle:horizontal:pressed {{
    border: 1px solid #0068D6;
    background: #F0F6FF;
}}

/* ---------- Lista / galería ---------- */
QListWidget {{
    background-color: #FFFFFF;
    border: 1px solid #E4E4E9;
    border-radius: 12px;
    padding: 6px;
    outline: none;
}}

QListWidget::item {{
    border-radius: 8px;
    padding: 6px;
    margin: 2px 0;
    color: #1D1D1F;
}}

QListWidget::item:hover {{
    background-color: #F5F5F7;
}}

QListWidget::item:selected {{
    background-color: #E8F1FF;
    color: #0A2540;
}}

QListWidget::item:selected:hover {{
    background-color: #DDEBFF;
}}

/* ---------- Barra de estado ---------- */
QStatusBar {{
    background-color: #F5F5F7;
    color: #6E6E73;
    border-top: 1px solid #E4E4E9;
    font-size: 12px;
}}

QStatusBar::item {{
    border: none;
}}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: #D2D2D7;
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: #B8B8BD;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: #D2D2D7;
    border-radius: 5px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #B8B8BD;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ---------- Tooltips ---------- */
QToolTip {{
    background-color: #1D1D1F;
    color: #FFFFFF;
    border: none;
    padding: 5px 8px;
    border-radius: 6px;
    font-size: 12px;
}}
"""
