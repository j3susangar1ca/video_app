#!/usr/bin/env python3
"""Ventana principal del reproductor multimedia.

Mejoras respecto a la versión anterior:
  - Mute real con QAudioOutput.setMuted(): el slider conserva su valor,
    y mover el volumen quita el silencio (como en VLC).
  - Pantalla completa real: se oculta el panel lateral, los controles se
    auto-ocultan tras 2.5 s de inactividad y reaparecen al mover el ratón.
    Esc sale de pantalla completa.
  - Errores de reproducción visibles en la barra de estado, no solo en el log.
  - Fin de la lista de reproducción DETIENE la app (antes un video suelto
    se reiniciaba en bucle infinito); los botones siguiente/anterior sí
    envuelven la lista de forma manual.
  - Slider de progreso y volumen que saltan a la posición clicada.
  - Galería persistente: se guarda al cerrar y se restaura al abrir.
  - Miniaturas recibidas como QImage y convertidas a QPixmap en el hilo
    de la GUI (seguro entre hilos).
  - Optimización: no se convierten frames a QImage si el video no es visible.
  - Al cerrar se detiene el reproductor y se cancelan miniaturas pendientes.
  - Pantalla dividida: un panel de video (izquierda) y un panel de imagen
    (derecha) visibles y manipulables SIMULTÁNEA e INDEPENDIENTEMENTE.
    El panel de imagen reutiliza DirectVideoWidget (misma lógica de
    rotación/zoom/paneo/relleno) y mantiene su propia lista de fotos,
    índice de navegación, rotación y zoom, totalmente separados del
    estado del reproductor de video.

Tercera pasada (UI/UX y accesibilidad, ver AUDIT_UX.md):
  - Estado vacío real en la galería de video (antes era una lista en
    blanco sin ninguna pista de qué hacer).
  - Miniatura "pendiente" unificada con la genérica de fallo (mismo
    dibujo, un solo lenguaje visual) en vez de un rectángulo gris plano.
  - Paneles de controles (video e imagen) con aspecto de tarjeta elevada
    (ver ui.theme.apply_elevation), igual que ya tenía la galería.
  - Nombres accesibles (QAccessible, vía setAccessibleName) en los
    botones que solo llevan un símbolo (⏮ ⏭ ↺ ↻ − + 🔊), para lectores
    de pantalla (Orca/AT-SPI en KDE).
  - Menú contextual (clic derecho) en la galería y en el panel de
    imagen: reproducir/quitar y "Abrir carpeta contenedora" (integración
    con el gestor de archivos vía QDesktopServices, patrón nativo de
    escritorio Linux).
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QSize, Qt, QTimer, QThreadPool, QUrl
from PyQt6.QtGui import (
    QDesktopServices,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.settings import AppSettings
from core.thumbnail_cache import ThumbWorker, fallback_thumb
from ui.theme import apply_elevation
from ui.video_widget import DirectVideoWidget

logger = logging.getLogger("reproductor.ui")

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".flv",
    ".ts",
    ".wmv",
    ".m4v",
    ".3gp",
    ".mpg",
    ".mpeg",
    ".ogv",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".ico",
}

IMAGE_PLACEHOLDER_TEXT = "Sin imagen cargada\nUsa 'Cargar imágenes' o arrástralas aquí"

# Tiempo sin mover el ratón antes de ocultar los controles en fullscreen.
AUTOHIDE_CONTROLS_MS = 2500


class SeekSlider(QSlider):
    """QSlider que salta a la posición clicada, como los players modernos.

    Sigue emitiendo sliderPressed/sliderMoved/sliderReleased de forma
    manual para que la ventana pueda distinguir "el usuario está
    arrastrando" de "el reproductor está actualizando la posición".
    """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            # setSliderDown() ya emite sliderPressed() internamente (Qt lo
            # dispara al detectar el cambio de estado); emitirla también a
            # mano la duplicaba y disparaba dos veces cualquier slot
            # conectado a ella.
            self.setSliderDown(True)
            self._apply_pos(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._apply_pos(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            # Igual que en mousePressEvent: setSliderDown(False) ya emite
            # sliderReleased() por sí solo.
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_pos(self, x: float):
        value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(x), max(1, self.width())
        )
        if value != self.value():
            self.setValue(value)
            self.sliderMoved.emit(value)


class ModernVideoPlayer(QMainWindow):
    def __init__(self, initial_files=None):
        super().__init__()
        self.base_title = "Reproductor"
        self.setWindowTitle(self.base_title)
        self.resize(1280, 760)
        self.setAcceptDrops(True)

        self.settings = AppSettings()

        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(4)

        self.current_rotation = 0
        self.zoom_level = 1.0
        self.speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
        self.speed_idx = self.settings.speed_index()
        self.is_seeking = False
        self.loop_video = self.settings.loop()
        self.items_map = {}

        # Estado del panel de imagen (pantalla dividida), completamente
        # independiente del estado del reproductor de video de arriba.
        self.split_mode = self.settings.split_mode()
        self.image_paths: list[str] = []
        self.image_index = -1
        self.image_rotation = 0
        self.image_zoom = 1.0

        self.init_player()
        self.init_ui()
        self.setup_shortcuts()

        self._hide_controls_timer = QTimer(self)
        self._hide_controls_timer.setSingleShot(True)
        self._hide_controls_timer.timeout.connect(self._hide_fullscreen_controls)

        self.restore_window_state()
        self.restore_playlist()
        self.restore_images()

        if initial_files:
            self.add_paths(initial_files, autoplay=True)

        logger.info("Ventana principal inicializada")

    # ------------------------------------------------------------------
    # Motor de reproducción
    # ------------------------------------------------------------------
    def init_player(self):
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.video_sink = QVideoSink(self)

        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoSink(self.video_sink)
        self.audio_output.setVolume(self.settings.volume() / 100.0)

        self.media_player.errorOccurred.connect(self.on_player_error)
        self.audio_output.mutedChanged.connect(self.on_muted_changed)

        self.video_sink.videoFrameChanged.connect(self.on_frame)
        self.media_player.positionChanged.connect(self.on_pos_changed)
        self.media_player.durationChanged.connect(self.on_dur_changed)
        self.media_player.playbackStateChanged.connect(self.on_playback_state)
        self.media_player.mediaStatusChanged.connect(self.on_media_status)

    # ------------------------------------------------------------------
    # Construcción de UI
    # ------------------------------------------------------------------
    def init_ui(self):
        """Orquesta la construcción de la ventana.

        Antes esto era un único método de ~260 líneas que mezclaba la
        creación de todos los widgets (video, barra de tiempo, transporte,
        giro/zoom/velocidad, galería) en un solo bloque de código: alta
        complejidad ciclomática y muy difícil de mantener o testear en
        aislado. Se dividió en un método por sección, cada uno responsable
        de una sola parte de la interfaz.
        """
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_gallery_panel())
        splitter.setSizes([900, 360])

        # Aplicar el estado guardado de "llenar pantalla" al widget de video
        self.video_view.set_fill_mode(self.btn_fill.isChecked())
        self.btn_fill.setText(
            "Llenar pantalla" if self.btn_fill.isChecked() else "Ajustar completo"
        )

        # Aplicar velocidad guardada
        if not (0 <= self.speed_idx < len(self.speeds)):
            self.speed_idx = self.speeds.index(1.0)
        self.apply_speed()

        # Barra de estado: feedback de errores y avisos al usuario
        self.statusBar().showMessage(
            "Arrastra videos o carpetas aquí, o usa 'Agregar videos'", 6000
        )

    def _build_left_panel(self) -> QWidget:
        """Área de medios: splitter interno con el panel de video y,
        opcionalmente, el panel de imagen en modo pantalla dividida.

        Ambos paneles conviven en `self.media_splitter`. El de video
        siempre está visible; el de imagen se muestra u oculta según
        `self.split_mode` (ver toggle_split_mode). Cada uno construye y
        controla sus propios widgets, así que se pueden manipular por
        completo por separado (cambiar de video no afecta a la imagen
        mostrada y viceversa).
        """
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(12, 12, 8, 12)
        left_layout.setSpacing(10)

        self.media_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.media_splitter.addWidget(self._build_video_panel())
        self.media_splitter.addWidget(self._build_image_panel())
        self.media_splitter.setSizes([1, 1])
        left_layout.addWidget(self.media_splitter, stretch=1)

        # La pantalla dividida es opcional: el panel de imagen arranca
        # oculto salvo que el usuario la haya dejado activada la última vez.
        self.image_panel.setVisible(self.split_mode)

        return left_box

    def _build_video_panel(self) -> QWidget:
        """Panel de video: área de reproducción + controles inferiores."""
        video_box = QWidget()
        video_layout = QVBoxLayout(video_box)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(10)

        self.video_view = DirectVideoWidget()
        self.video_view.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.clicked.connect(self.toggle_play)
        self.video_view.wheelScrolled.connect(self.adjust_volume)
        self.video_view.zoomScrolled.connect(self.adjust_zoom)
        self.video_view.mouseMoved.connect(self.on_video_mouse_moved)
        video_layout.addWidget(self.video_view, stretch=1)

        # Contenedor de controles inferiores: se oculta junto con el panel
        # lateral cuando se entra en pantalla completa. objectName
        # "controlsCard" le da el aspecto de tarjeta definido en el QSS
        # (ui/theme.py); apply_elevation añade la sombra por encima de eso.
        controls_panel = QWidget()
        controls_panel.setObjectName("controlsCard")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(14, 12, 14, 12)
        controls_layout.setSpacing(10)
        controls_layout.addLayout(self._build_time_bar())
        controls_layout.addLayout(self._build_transport_row())
        controls_layout.addLayout(self._build_rotation_zoom_speed_row())

        self.controls_panel = controls_panel
        apply_elevation(self.controls_panel, level=1)
        video_layout.addWidget(self.controls_panel)
        return video_box

    def _build_image_panel(self) -> QWidget:
        """Panel de imagen: visor + controles, independiente del video.

        Reutiliza DirectVideoWidget (soporta rotación/zoom/paneo/relleno
        para cualquier QImage, sea un frame de video o una foto) en vez
        de duplicar esa lógica de pintado en una clase nueva.
        """
        image_box = QWidget()
        image_layout = QVBoxLayout(image_box)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(10)

        self.image_view = DirectVideoWidget(placeholder_text=IMAGE_PLACEHOLDER_TEXT)
        self.image_view.zoomScrolled.connect(self.adjust_image_zoom)
        # A diferencia del video, aquí no hay volumen que ajustar con la
        # rueda normal: se reutiliza también para zoom (sin necesitar Ctrl).
        self.image_view.wheelScrolled.connect(
            lambda delta: self.adjust_image_zoom(1 if delta > 0 else -1)
        )
        self.image_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_view.customContextMenuRequested.connect(self._show_image_context_menu)
        image_layout.addWidget(self.image_view, stretch=1)

        # Misma tarjeta "controlsCard" que usa el panel de video, para que
        # ambos paneles lean como el mismo lenguaje visual.
        image_controls_panel = QWidget()
        image_controls_panel.setObjectName("controlsCard")
        image_controls_layout = QVBoxLayout(image_controls_panel)
        image_controls_layout.setContentsMargins(14, 12, 14, 12)
        image_controls_layout.setSpacing(10)
        image_controls_layout.addLayout(self._build_image_nav_row())
        image_controls_layout.addLayout(self._build_image_transform_row())

        self.image_controls_panel = image_controls_panel
        apply_elevation(self.image_controls_panel, level=1)
        image_layout.addWidget(self.image_controls_panel)

        self.image_panel = image_box
        return image_box

    def _build_image_nav_row(self) -> QHBoxLayout:
        """Fila 1 del panel de imagen: cargar y navegar entre fotos."""
        row = QHBoxLayout()
        row.setSpacing(8)

        btn_add_img = QPushButton("+  Cargar imágenes")
        btn_add_img.setObjectName("primaryBtn")
        btn_add_img.clicked.connect(self.open_image_dialog)
        row.addWidget(btn_add_img)

        row.addSpacing(8)

        self.btn_img_prev = QPushButton("⏮")
        self.btn_img_prev.setObjectName("transportBtn")
        self.btn_img_prev.setToolTip("Imagen anterior (,)")
        self.btn_img_prev.setAccessibleName("Imagen anterior")
        self.btn_img_prev.clicked.connect(self.show_previous_image)
        row.addWidget(self.btn_img_prev)

        self.lbl_img_counter = QLabel("0 / 0")
        self.lbl_img_counter.setObjectName("statLabel")
        row.addWidget(self.lbl_img_counter)

        self.btn_img_next = QPushButton("⏭")
        self.btn_img_next.setObjectName("transportBtn")
        self.btn_img_next.setToolTip("Siguiente imagen (.)")
        self.btn_img_next.setAccessibleName("Siguiente imagen")
        self.btn_img_next.clicked.connect(self.show_next_image)
        row.addWidget(self.btn_img_next)

        row.addStretch()

        btn_img_remove = QPushButton("Quitar")
        btn_img_remove.setObjectName("dangerBtn")
        btn_img_remove.setToolTip("Quitar la imagen actual de la lista")
        btn_img_remove.clicked.connect(self.remove_current_image)
        row.addWidget(btn_img_remove)

        return row

    def _build_image_transform_row(self) -> QHBoxLayout:
        """Fila 2 del panel de imagen: giro, zoom y relleno."""
        row = QHBoxLayout()
        row.setSpacing(8)

        cap_giro = QLabel("Giro")
        cap_giro.setObjectName("groupCaption")
        row.addWidget(cap_giro)

        btn_img_ccw = QPushButton("↺")
        btn_img_ccw.setToolTip("Girar antihorario (Alt+Shift+R)")
        btn_img_ccw.setAccessibleName("Girar imagen antihorario")
        btn_img_ccw.clicked.connect(lambda: self.rotate_image(-90))
        row.addWidget(btn_img_ccw)

        btn_img_cw = QPushButton("↻")
        btn_img_cw.setToolTip("Girar horario (Alt+R)")
        btn_img_cw.setAccessibleName("Girar imagen horario")
        btn_img_cw.clicked.connect(lambda: self.rotate_image(90))
        row.addWidget(btn_img_cw)

        self.lbl_img_rot = QLabel("0°")
        self.lbl_img_rot.setObjectName("statLabel")
        row.addWidget(self.lbl_img_rot)

        row.addSpacing(12)

        cap_zoom = QLabel("Zoom")
        cap_zoom.setObjectName("groupCaption")
        row.addWidget(cap_zoom)

        btn_img_zoom_out = QPushButton("−")
        btn_img_zoom_out.setToolTip("Alejar (Ctrl + Rueda abajo sobre la imagen / Alt+-)")
        btn_img_zoom_out.setAccessibleName("Alejar imagen")
        btn_img_zoom_out.clicked.connect(lambda: self.adjust_image_zoom(-1))
        row.addWidget(btn_img_zoom_out)

        self.lbl_img_zoom = QLabel("100%")
        self.lbl_img_zoom.setObjectName("statLabel")
        row.addWidget(self.lbl_img_zoom)

        btn_img_zoom_in = QPushButton("+")
        btn_img_zoom_in.setToolTip("Acercar (Ctrl + Rueda arriba sobre la imagen / Alt++)")
        btn_img_zoom_in.setAccessibleName("Acercar imagen")
        btn_img_zoom_in.clicked.connect(lambda: self.adjust_image_zoom(1))
        row.addWidget(btn_img_zoom_in)

        btn_img_zoom_reset = QPushButton("Restablecer")
        btn_img_zoom_reset.setToolTip("Restablecer zoom y posición (Alt+0)")
        btn_img_zoom_reset.clicked.connect(self.reset_image_zoom)
        row.addWidget(btn_img_zoom_reset)

        self.btn_img_fill = QPushButton("Llenar")
        self.btn_img_fill.setCheckable(True)
        self.btn_img_fill.setChecked(True)
        self.btn_img_fill.setToolTip(
            "Alternar: llenar todo el panel (recorta) / ajustar completo (barras negras)"
        )
        self.btn_img_fill.clicked.connect(self.toggle_image_fill_mode)
        row.addWidget(self.btn_img_fill)

        row.addStretch()
        return row

    def _build_time_bar(self) -> QHBoxLayout:
        """Barra de tiempo: etiqueta actual, slider de progreso, etiqueta total."""
        time_bar = QHBoxLayout()
        self.lbl_current = QLabel("00:00:00")
        self.lbl_current.setObjectName("timeLabel")
        self.slider_pos = SeekSlider(Qt.Orientation.Horizontal)
        self.slider_pos.setRange(0, 0)
        self.slider_pos.sliderPressed.connect(lambda: setattr(self, "is_seeking", True))
        self.slider_pos.sliderReleased.connect(self.apply_seek)
        self.slider_pos.sliderMoved.connect(
            lambda val: self.lbl_current.setText(self.fmt_time(val))
        )
        self.lbl_total = QLabel("00:00:00")
        self.lbl_total.setObjectName("timeLabel")

        time_bar.addWidget(self.lbl_current)
        time_bar.addWidget(self.slider_pos)
        time_bar.addWidget(self.lbl_total)
        return time_bar

    def _build_transport_row(self) -> QHBoxLayout:
        """Fila 1: transporte, salto ±5s, volumen, bucle, pantalla completa."""
        ctrl1 = QHBoxLayout()
        ctrl1.setSpacing(8)

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setObjectName("transportBtn")
        self.btn_prev.setToolTip("Video anterior (P)")
        self.btn_prev.setAccessibleName("Video anterior")
        self.btn_prev.clicked.connect(self.play_previous)
        ctrl1.addWidget(self.btn_prev)

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playPauseBtn")
        self.btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.btn_play.setToolTip("Reproducir / Pausar (Espacio)")
        self.btn_play.setAccessibleName("Reproducir o pausar")
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl1.addWidget(self.btn_play)

        self.btn_next = QPushButton("⏭")
        self.btn_next.setObjectName("transportBtn")
        self.btn_next.setToolTip("Siguiente video (N)")
        self.btn_next.setAccessibleName("Siguiente video")
        self.btn_next.clicked.connect(self.play_next)
        ctrl1.addWidget(self.btn_next)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("transportBtn")
        self.btn_stop.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        )
        self.btn_stop.setToolTip("Detener")
        self.btn_stop.setAccessibleName("Detener reproducción")
        self.btn_stop.clicked.connect(self.stop_video)
        ctrl1.addWidget(self.btn_stop)

        ctrl1.addSpacing(8)

        btn_back5 = QPushButton("-5s")
        btn_back5.setToolTip("Retroceder 5 segundos (Flecha Izquierda)")
        btn_back5.clicked.connect(lambda: self.seek_relative(-5000))
        ctrl1.addWidget(btn_back5)

        btn_fwd5 = QPushButton("+5s")
        btn_fwd5.setToolTip("Adelantar 5 segundos (Flecha Derecha)")
        btn_fwd5.clicked.connect(lambda: self.seek_relative(5000))
        ctrl1.addWidget(btn_fwd5)

        ctrl1.addSpacing(12)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setToolTip("Silenciar / Reactivar (M)")
        self.btn_mute.setAccessibleName("Silenciar o reactivar audio")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl1.addWidget(self.btn_mute)

        self.slider_vol = SeekSlider(Qt.Orientation.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(self.settings.volume())
        self.slider_vol.setFixedWidth(100)
        self.slider_vol.valueChanged.connect(self.on_volume_change)
        ctrl1.addWidget(self.slider_vol)

        ctrl1.addStretch()

        self.btn_loop = QPushButton("Bucle")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(self.loop_video)
        self.btn_loop.setToolTip("Repetir video actual")
        self.btn_loop.clicked.connect(self.toggle_loop)
        ctrl1.addWidget(self.btn_loop)

        self.btn_fs = QPushButton("Pantalla completa")
        self.btn_fs.setToolTip("Alternar pantalla completa (F / Doble Clic / Esc para salir)")
        self.btn_fs.clicked.connect(self.toggle_fullscreen)
        ctrl1.addWidget(self.btn_fs)

        self.btn_split = QPushButton("Pantalla dividida")
        self.btn_split.setCheckable(True)
        self.btn_split.setChecked(self.split_mode)
        self.btn_split.setToolTip(
            "Mostrar a la vez el video y un panel de imagen independiente (D)"
        )
        self.btn_split.clicked.connect(self.toggle_split_mode)
        ctrl1.addWidget(self.btn_split)

        return ctrl1

    def _build_rotation_zoom_speed_row(self) -> QHBoxLayout:
        """Fila 2: giro, zoom/relleno y velocidad de reproducción."""
        ctrl2 = QHBoxLayout()
        ctrl2.setSpacing(8)

        cap_giro = QLabel("Giro")
        cap_giro.setObjectName("groupCaption")
        ctrl2.addWidget(cap_giro)

        btn_ccw = QPushButton("↺ 90°")
        btn_ccw.setToolTip("Girar antihorario (Shift+R)")
        btn_ccw.setAccessibleName("Girar video antihorario 90 grados")
        btn_ccw.clicked.connect(lambda: self.rotate_video(-90))
        ctrl2.addWidget(btn_ccw)

        btn_cw = QPushButton("↻ 90°")
        btn_cw.setToolTip("Girar horario (R)")
        btn_cw.setAccessibleName("Girar video horario 90 grados")
        btn_cw.clicked.connect(lambda: self.rotate_video(90))
        ctrl2.addWidget(btn_cw)

        btn_180 = QPushButton("180°")
        btn_180.clicked.connect(lambda: self.rotate_video(180))
        ctrl2.addWidget(btn_180)

        self.lbl_rot = QLabel("0°")
        self.lbl_rot.setObjectName("statLabel")
        ctrl2.addWidget(self.lbl_rot)

        btn_reset_rot = QPushButton("Restablecer")
        btn_reset_rot.clicked.connect(lambda: self.set_rotation_absolute(0))
        ctrl2.addWidget(btn_reset_rot)

        ctrl2.addSpacing(16)

        cap_zoom = QLabel("Zoom")
        cap_zoom.setObjectName("groupCaption")
        ctrl2.addWidget(cap_zoom)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setToolTip("Alejar (Ctrl + Rueda abajo / -)")
        btn_zoom_out.setAccessibleName("Alejar video")
        btn_zoom_out.clicked.connect(lambda: self.adjust_zoom(-1))
        ctrl2.addWidget(btn_zoom_out)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("statLabel")
        ctrl2.addWidget(self.lbl_zoom)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setToolTip("Acercar (Ctrl + Rueda arriba / +)")
        btn_zoom_in.setAccessibleName("Acercar video")
        btn_zoom_in.clicked.connect(lambda: self.adjust_zoom(1))
        ctrl2.addWidget(btn_zoom_in)

        btn_zoom_reset = QPushButton("Restablecer")
        btn_zoom_reset.setToolTip("Restablecer zoom y posición (0)")
        btn_zoom_reset.clicked.connect(self.reset_zoom)
        ctrl2.addWidget(btn_zoom_reset)

        self.btn_fill = QPushButton("Llenar pantalla")
        self.btn_fill.setCheckable(True)
        self.btn_fill.setChecked(self.settings.fill_mode())
        self.btn_fill.setToolTip(
            "Alternar: llenar todo el panel (recorta) / ajustar completo (barras negras)"
        )
        self.btn_fill.clicked.connect(self.toggle_fill_mode)
        ctrl2.addWidget(self.btn_fill)

        ctrl2.addSpacing(16)

        cap_spd = QLabel("Velocidad")
        cap_spd.setObjectName("groupCaption")
        ctrl2.addWidget(cap_spd)

        btn_slower = QPushButton("◀ Lento")
        btn_slower.setToolTip("Disminuir velocidad ([)")
        btn_slower.clicked.connect(self.decrease_speed)
        ctrl2.addWidget(btn_slower)

        self.lbl_spd = QLabel("1.00x")
        self.lbl_spd.setObjectName("statLabel")
        ctrl2.addWidget(self.lbl_spd)

        btn_faster = QPushButton("Rápido ▶")
        btn_faster.setToolTip("Aumentar velocidad (])")
        btn_faster.clicked.connect(self.increase_speed)
        ctrl2.addWidget(btn_faster)

        btn_reset_spd = QPushButton("1.0x")
        btn_reset_spd.setToolTip("Velocidad normal (Backspace)")
        btn_reset_spd.clicked.connect(self.reset_speed)
        ctrl2.addWidget(btn_reset_spd)

        ctrl2.addStretch()
        return ctrl2

    def _build_gallery_panel(self) -> QWidget:
        """Panel derecho: galería de videos y sus acciones.

        Incluye un estado vacío real (antes una lista en blanco no daba
        ninguna pista de qué hacer) implementado con QStackedWidget: la
        página 0 es la lista, la 1 es el mensaje de "sin videos"; se
        alterna en _update_gallery_empty_state() cada vez que cambia el
        contenido de la galería.
        """
        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(8, 12, 12, 12)
        right_layout.setSpacing(8)

        lbl_pl = QLabel("Galería de videos")
        lbl_pl.setObjectName("sectionTitle")
        right_layout.addWidget(lbl_pl)

        self.playlist = QListWidget()
        self.playlist.setIconSize(QSize(130, 75))
        self.playlist.itemActivated.connect(self.play_item)  # doble clic o Enter
        self.playlist.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlist.customContextMenuRequested.connect(self._show_playlist_context_menu)
        apply_elevation(self.playlist, level=2)

        self.playlist_stack = QStackedWidget()
        self.playlist_stack.addWidget(self.playlist)
        self.playlist_stack.addWidget(self._build_gallery_empty_state())
        right_layout.addWidget(self.playlist_stack)

        btn_add = QPushButton("+  Agregar videos")
        btn_add.setObjectName("primaryBtn")
        btn_add.clicked.connect(self.open_file_dialog)
        apply_elevation(btn_add, level=1)
        right_layout.addWidget(btn_add)

        row_actions = QHBoxLayout()
        btn_remove = QPushButton("Quitar")
        btn_remove.setObjectName("dangerBtn")
        btn_remove.setToolTip("Quitar los videos seleccionados (doble clic o Enter para reproducir)")
        btn_remove.clicked.connect(self.remove_selected)
        row_actions.addWidget(btn_remove)

        btn_clear = QPushButton("Limpiar todo")
        btn_clear.setObjectName("dangerBtn")
        btn_clear.clicked.connect(self.clear_all)
        row_actions.addWidget(btn_clear)
        right_layout.addLayout(row_actions)

        self.right_panel = right_box
        self._update_gallery_empty_state()
        return right_box

    def _build_gallery_empty_state(self) -> QWidget:
        """Página del QStackedWidget mostrada cuando la galería está vacía."""
        empty = QWidget()
        empty.setObjectName("emptyState")
        layout = QVBoxLayout(empty)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        title = QLabel("Sin videos todavía")
        title.setObjectName("emptyStateTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Arrastra archivos o carpetas aquí,\no usa “+ Agregar videos” abajo")
        hint.setObjectName("emptyStateHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        return empty

    def _update_gallery_empty_state(self):
        """Alterna entre la lista y el mensaje de estado vacío."""
        self.playlist_stack.setCurrentIndex(0 if self.playlist.count() > 0 else 1)

    def _show_playlist_context_menu(self, pos):
        """Menú contextual (clic derecho) de la galería de video."""
        item = self.playlist.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            act_play = menu.addAction("Reproducir")
            act_play.triggered.connect(lambda: self.play_item(item))
            act_open_folder = menu.addAction("Abrir carpeta contenedora")
            act_open_folder.triggered.connect(
                lambda: self._open_containing_folder(item.data(Qt.ItemDataRole.UserRole))
            )
            menu.addSeparator()
            act_remove = menu.addAction("Quitar de la galería")
            act_remove.triggered.connect(lambda: self._remove_playlist_item(item))
        else:
            act_add = menu.addAction("Agregar videos…")
            act_add.triggered.connect(self.open_file_dialog)
        if self.playlist.count() > 0:
            menu.addSeparator()
            act_clear = menu.addAction("Limpiar toda la galería")
            act_clear.triggered.connect(self.clear_all)
        menu.exec(self.playlist.viewport().mapToGlobal(pos))

    def _show_image_context_menu(self, pos):
        """Menú contextual (clic derecho) del panel de imagen."""
        menu = QMenu(self)
        act_add = menu.addAction("Cargar imágenes…")
        act_add.triggered.connect(self.open_image_dialog)
        if self.image_paths and 0 <= self.image_index < len(self.image_paths):
            current_path = self.image_paths[self.image_index]
            act_open_folder = menu.addAction("Abrir carpeta contenedora")
            act_open_folder.triggered.connect(
                lambda: self._open_containing_folder(current_path)
            )
            menu.addSeparator()
            act_remove = menu.addAction("Quitar esta imagen")
            act_remove.triggered.connect(self.remove_current_image)
        menu.exec(self.image_view.mapToGlobal(pos))

    def _open_containing_folder(self, path):
        """Abre la carpeta que contiene `path` en el gestor de archivos
        del sistema (Dolphin en KDE), vía QDesktopServices — mismo patrón
        que "Abrir ubicación del archivo" en cualquier app de escritorio."""
        if not path:
            return
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self.statusBar().showMessage("La carpeta ya no existe", 4000)

    def setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(
            QKeySequence(Qt.Key.Key_Left), self, lambda: self.seek_relative(-5000)
        )
        QShortcut(
            QKeySequence(Qt.Key.Key_Right), self, lambda: self.seek_relative(5000)
        )
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.adjust_volume(5))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.adjust_volume(-5))
        QShortcut(QKeySequence(Qt.Key.Key_M), self, self.toggle_mute)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.handle_escape)
        QShortcut(QKeySequence(Qt.Key.Key_R), self, lambda: self.rotate_video(90))
        QShortcut(QKeySequence("Shift+R"), self, lambda: self.rotate_video(-90))
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, self.decrease_speed)
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, self.increase_speed)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, self.reset_speed)
        QShortcut(QKeySequence(Qt.Key.Key_N), self, self.play_next)
        QShortcut(QKeySequence(Qt.Key.Key_P), self, self.play_previous)
        QShortcut(QKeySequence(Qt.Key.Key_Plus), self, lambda: self.adjust_zoom(1))
        QShortcut(QKeySequence(Qt.Key.Key_Equal), self, lambda: self.adjust_zoom(1))
        QShortcut(QKeySequence(Qt.Key.Key_Minus), self, lambda: self.adjust_zoom(-1))
        QShortcut(QKeySequence(Qt.Key.Key_0), self, self.reset_zoom)

        # Pantalla dividida y panel de imagen: teclas separadas de las del
        # video de arriba para poder manipular ambos paneles sin choques
        # (p. ej. Izquierda/Derecha ya retroceden/adelantan el video).
        QShortcut(QKeySequence(Qt.Key.Key_D), self, self.toggle_split_mode)
        QShortcut(QKeySequence(Qt.Key.Key_Comma), self, self.show_previous_image)
        QShortcut(QKeySequence(Qt.Key.Key_Period), self, self.show_next_image)
        QShortcut(QKeySequence("Alt+R"), self, lambda: self.rotate_image(90))
        QShortcut(QKeySequence("Alt+Shift+R"), self, lambda: self.rotate_image(-90))
        QShortcut(QKeySequence("Alt++"), self, lambda: self.adjust_image_zoom(1))
        QShortcut(QKeySequence("Alt+-"), self, lambda: self.adjust_image_zoom(-1))
        QShortcut(QKeySequence("Alt+0"), self, self.reset_image_zoom)

    # ------------------------------------------------------------------
    # Persistencia de estado
    # ------------------------------------------------------------------
    def restore_window_state(self):
        geometry = self.settings.window_geometry()
        if geometry:
            self.restoreGeometry(geometry)

    def restore_playlist(self):
        """Reconstruye la galería con los videos de la sesión anterior.

        Sin reproducción automática: solo se puebla la lista. Las
        miniaturas salen enseguida del caché de disco.
        """
        saved = self.settings.playlist_paths()
        if saved:
            self.add_paths(saved, autoplay=False)
            logger.info("Galería restaurada: %d elemento(s)", self.playlist.count())

    def current_playlist_paths(self):
        return [
            self.playlist.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.playlist.count())
        ]

    def restore_images(self):
        """Reconstruye el panel de imagen con las fotos de la sesión anterior.

        Igual que restore_playlist(): solo puebla la lista, sin mostrar
        automáticamente ninguna imagen si el usuario no dejó el modo
        pantalla dividida activado.
        """
        saved = self.settings.image_paths()
        if saved:
            self.add_image_paths(saved, show_first=self.split_mode)
            logger.info("Panel de imagen restaurado: %d elemento(s)", len(self.image_paths))

    def closeEvent(self, event):
        self.settings.set_window_geometry(self.saveGeometry())
        self.settings.set_volume(self.slider_vol.value())
        self.settings.set_speed_index(self.speed_idx)
        self.settings.set_fill_mode(self.btn_fill.isChecked())
        self.settings.set_loop(self.loop_video)
        self.settings.set_playlist_paths(self.current_playlist_paths())
        self.settings.set_split_mode(self.split_mode)
        self.settings.set_image_paths(self.image_paths)
        self.settings.sync()

        # Liberar recursos y salir rápido: sin miniaturas pendientes.
        self.media_player.stop()
        self.thread_pool.clear()  # descarta las que ni siquiera empezaron
        # Las que ya estaban corriendo (subprocess de ffmpeg, escritura en
        # caché) son hilos nativos de Qt: si el intérprete termina con
        # ellas todavía vivas pueden intentar tocar objetos de la GUI ya
        # liberados. Se les da un margen acotado para terminar solas;
        # pasado ese tiempo se continúa con el cierre igualmente.
        if not self.thread_pool.waitForDone(2000):
            logger.warning(
                "Algunas miniaturas seguían generándose al cerrar la aplicación"
            )

        logger.info("Preferencias guardadas al cerrar la aplicación")
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Drag & drop / carga de archivos
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Reparte lo soltado entre el panel de video y el de imagen.

        Cada ruta se clasifica por extensión (o, si es una carpeta, se le
        pide a cada panel que se quede solo con lo suyo); así se puede
        soltar una mezcla de videos y fotos —o una carpeta con ambos— de
        una sola vez y cada uno termina en su panel correspondiente.
        """
        raw_paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        existing = []
        for p in raw_paths:
            norm = self._normalize_path(p)
            if os.path.isdir(norm) or os.path.isfile(norm):
                existing.append(p)
            else:
                logger.warning("Ruta ignorada (no existe): %s", p)
        if not existing:
            return
        self.add_paths(existing, autoplay=True)
        self.add_image_paths(existing, show_first=False)

    def open_file_dialog(self):
        start_dir = self.settings.last_folder()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Videos",
            start_dir,
            "Archivos de Video (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.ts *.wmv *.m4v *.3gp);;Todos (*.*)",
        )
        if files:
            self.settings.set_last_folder(os.path.dirname(files[0]))
            self.add_paths(files, autoplay=True)

    def open_image_dialog(self):
        start_dir = self.settings.last_image_folder()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Imágenes",
            start_dir,
            "Imágenes (*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff *.ico);;Todos (*.*)",
        )
        if files:
            self.settings.set_last_image_folder(os.path.dirname(files[0]))
            self.add_image_paths(files)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normaliza a una forma absoluta canónica.

        Sin esto, la misma película llegaba con claves distintas en
        items_map según se soltara como ruta relativa, con "~", con
        barras dobles o arrastrando la carpeta que la contiene vs. el
        archivo suelto: el filtro de duplicados ("if path in
        self.items_map") nunca la detectaba como repetida.
        """
        return os.path.normpath(os.path.abspath(os.path.expanduser(path)))

    @staticmethod
    def _collect_matching_files(paths, extensions: set, known: set) -> list:
        """Recorre `paths` (archivos sueltos o carpetas) y devuelve, en
        orden, las rutas nuevas cuya extensión está en `extensions` y que
        todavía no están en `known` (se actualiza in place a medida que
        se van encontrando, así una misma ruta no se repite ni dentro de
        esta misma llamada ni frente a lo que ya hubiera en la galería).

        Compartido por la galería de video y la de imagen: ambas
        necesitan exactamente este recorrido, solo cambia el conjunto de
        extensiones aceptado.
        """
        collected = []
        for raw_p in paths:
            p = ModernVideoPlayer._normalize_path(raw_p)
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in sorted(files):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in extensions:
                            full = os.path.normpath(os.path.join(root, f))
                            if full not in known:
                                known.add(full)
                                collected.append(full)
            elif os.path.isfile(p):
                if p not in known:
                    known.add(p)
                    collected.append(p)
            else:
                logger.warning("Ruta ignorada (no existe): %s", raw_p)
        return collected

    def add_paths(self, paths, autoplay: bool = True):
        all_files = self._collect_matching_files(
            paths, VIDEO_EXTENSIONS, set(self.items_map.keys())
        )

        # Mismo dibujo que la miniatura de "no se pudo generar" (ver
        # core/thumbnail_cache.fallback_thumb): un solo lenguaje visual
        # para "todavía no hay miniatura real", en vez del rectángulo
        # gris plano que se usaba antes solo aquí.
        placeholder = QIcon(QPixmap.fromImage(fallback_thumb()))

        first_added = None
        for path in all_files:
            name = os.path.basename(path)
            item = QListWidgetItem(placeholder, f" {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.playlist.addItem(item)
            self.items_map[path] = item

            if first_added is None:
                first_added = item

            worker = ThumbWorker(path)
            worker.signals.finished.connect(self.on_thumb_ready)
            self.thread_pool.start(worker)

        logger.info("%d archivo(s) nuevo(s) agregado(s) a la galería", len(all_files))
        self._update_gallery_empty_state()

        if (
            autoplay
            and first_added
            and self.media_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            self.play_item(first_added)

    def on_thumb_ready(self, path: str, image):
        """Recibe QImage del worker y la convierte a QPixmap aquí, en el
        hilo de la GUI (crear QPixmap fuera de este hilo no es seguro)."""
        item = self.items_map.get(path)
        if item is not None and image is not None and not image.isNull():
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def play_item(self, item):
        if item is None:
            return
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            self.playlist.setCurrentItem(item)
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.media_player.play()
            self.setWindowTitle(f"{os.path.basename(file_path)} — {self.base_title}")
        elif file_path:
            logger.warning("El archivo ya no existe: %s", file_path)
            self.statusBar().showMessage(
                f"El archivo ya no existe: {os.path.basename(file_path)}", 6000
            )

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------
    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            if self.media_player.source().isEmpty() and self.playlist.count() > 0:
                self.play_item(self.playlist.item(0))
            else:
                self.media_player.play()

    def stop_video(self):
        self.media_player.stop()
        self.video_view.clear()
        self.slider_pos.setValue(0)
        self.lbl_current.setText("00:00:00")
        self.setWindowTitle(self.base_title)

    def play_next(self):
        """Video siguiente (manual): envuelve al llegar al final."""
        count = self.playlist.count()
        if count == 0:
            return
        row = self.playlist.currentRow()
        next_row = row + 1 if row + 1 < count else 0
        self.play_item(self.playlist.item(next_row))

    def play_previous(self):
        """Video anterior (manual): envuelve al llegar al inicio."""
        count = self.playlist.count()
        if count == 0:
            return
        row = self.playlist.currentRow()
        prev_row = row - 1 if row - 1 >= 0 else count - 1
        self.play_item(self.playlist.item(prev_row))

    def seek_relative(self, delta_ms):
        pos = max(
            0,
            min(self.media_player.duration(), self.media_player.position() + delta_ms),
        )
        self.media_player.setPosition(pos)

    def apply_seek(self):
        self.is_seeking = False
        self.media_player.setPosition(self.slider_pos.value())

    # ------------------------------------------------------------------
    # Señales del reproductor
    # ------------------------------------------------------------------
    def on_pos_changed(self, pos_ms):
        if not self.is_seeking:
            self.slider_pos.setValue(pos_ms)
            self.lbl_current.setText(self.fmt_time(pos_ms))

    def on_dur_changed(self, dur_ms):
        self.slider_pos.setRange(0, dur_ms)
        self.lbl_total.setText(self.fmt_time(dur_ms))

    def on_playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            )
        else:
            self.btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )

    def on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.loop_video:
                self.media_player.setPosition(0)
                self.media_player.play()
            else:
                row = self.playlist.currentRow()
                if row + 1 < self.playlist.count():
                    self.play_item(self.playlist.item(row + 1))
                else:
                    # Fin de la lista: detener. Antes la lista envolvía sola
                    # y un video suelto se repetía en bucle infinito.
                    self.stop_video()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            logger.error("Medio inválido o formato no soportado")
            self.statusBar().showMessage(
                "No se puede reproducir: formato no soportado o archivo dañado", 8000
            )

    def on_player_error(self, error, error_string):
        if error != QMediaPlayer.Error.NoError:
            logger.error("Error de reproducción: %s (%s)", error_string, error)
            self.statusBar().showMessage(f"Error de reproducción: {error_string}", 8000)

    def on_frame(self, frame: QVideoFrame):
        if not frame.isValid():
            return
        # Optimización: no convertir frames si nadie los va a ver
        # (ventana minimizada o panel oculto). Ahorra CPU/GPU notable.
        if self.isMinimized() or not self.video_view.isVisible():
            return
        self.video_view.set_frame(frame.toImage())

    # ------------------------------------------------------------------
    # Rotación / zoom / relleno
    # ------------------------------------------------------------------
    def rotate_video(self, delta):
        """Gira el video. delta positivo = horario, negativo = antihorario."""
        self.current_rotation = (self.current_rotation + delta) % 360
        self.video_view.set_rotation(self.current_rotation)
        self.lbl_rot.setText(f"{self.current_rotation}°")

    def set_rotation_absolute(self, angle):
        self.current_rotation = angle % 360
        self.video_view.set_rotation(self.current_rotation)
        self.lbl_rot.setText(f"{self.current_rotation}°")

    def adjust_zoom(self, step):
        """step positivo = acercar, negativo = alejar."""
        increment = 0.1 * step
        self.zoom_level = max(0.2, min(8.0, self.zoom_level + increment))
        self.video_view.set_zoom(self.zoom_level)
        self.lbl_zoom.setText(f"{int(self.zoom_level * 100)}%")

    def reset_zoom(self):
        self.zoom_level = 1.0
        self.video_view.reset_view()
        self.lbl_zoom.setText("100%")

    def toggle_fill_mode(self):
        fill = self.btn_fill.isChecked()
        self.video_view.set_fill_mode(fill)
        self.btn_fill.setText("Llenar pantalla" if fill else "Ajustar completo")

    # ------------------------------------------------------------------
    # Velocidad
    # ------------------------------------------------------------------
    def increase_speed(self):
        if self.speed_idx < len(self.speeds) - 1:
            self.speed_idx += 1
            self.apply_speed()

    def decrease_speed(self):
        if self.speed_idx > 0:
            self.speed_idx -= 1
            self.apply_speed()

    def reset_speed(self):
        self.speed_idx = self.speeds.index(1.0)
        self.apply_speed()

    def apply_speed(self):
        spd = self.speeds[self.speed_idx]
        self.media_player.setPlaybackRate(spd)
        self.lbl_spd.setText(f"{spd:.2f}x")

    # ------------------------------------------------------------------
    # Volumen (mute real, independiente del nivel)
    # ------------------------------------------------------------------
    def on_volume_change(self, val):
        self.audio_output.setVolume(val / 100.0)
        # Mover el volumen con el mute activo lo quita (comportamiento VLC)
        if val > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)

    def adjust_volume(self, delta):
        new_val = max(0, min(100, self.slider_vol.value() + delta))
        self.slider_vol.setValue(new_val)

    def toggle_mute(self):
        self.audio_output.setMuted(not self.audio_output.isMuted())

    def on_muted_changed(self, muted: bool):
        self.btn_mute.setText("🔇" if muted else "🔊")

    def toggle_loop(self):
        self.loop_video = self.btn_loop.isChecked()

    # ------------------------------------------------------------------
    # Pantalla completa real
    # ------------------------------------------------------------------
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._restore_normal_ui()
        else:
            self.showFullScreen()
            self._enter_fullscreen_ui()

    def handle_escape(self):
        """Esc: salir de pantalla completa si está activa."""
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _enter_fullscreen_ui(self):
        self.right_panel.hide()
        self.controls_panel.show()
        self.btn_fs.setText("Salir de pantalla completa")
        # Los controles se ocultan solos tras unos segundos sin mover el ratón.
        self._hide_controls_timer.start(AUTOHIDE_CONTROLS_MS)

    def _restore_normal_ui(self):
        self._hide_controls_timer.stop()
        self.right_panel.show()
        self.controls_panel.show()
        self.video_view.setCursor(Qt.CursorShape.ArrowCursor)
        self.btn_fs.setText("Pantalla completa")

    def on_video_mouse_moved(self):
        if self.isFullScreen():
            if not self.controls_panel.isVisible():
                self.controls_panel.show()
                self.video_view.setCursor(Qt.CursorShape.ArrowCursor)
            self._hide_controls_timer.start(AUTOHIDE_CONTROLS_MS)

    def _hide_fullscreen_controls(self):
        if self.isFullScreen():
            self.controls_panel.hide()
            # Cursor invisible sobre el video para experiencia de cine.
            self.video_view.setCursor(Qt.CursorShape.BlankCursor)

    # ------------------------------------------------------------------
    # Gestión de galería
    # ------------------------------------------------------------------
    def _remove_playlist_item(self, item):
        """Quita un único item de la galería (usado por remove_selected y
        por el menú contextual, que puede operar sobre un item que ni
        siquiera está seleccionado)."""
        path = item.data(Qt.ItemDataRole.UserRole)
        self.items_map.pop(path, None)
        self.playlist.takeItem(self.playlist.row(item))
        self._update_gallery_empty_state()

    def remove_selected(self):
        items = self.playlist.selectedItems()
        if not items:
            self.statusBar().showMessage(
                "Selecciona primero uno o más videos de la galería", 4000
            )
            return
        for item in items:
            self._remove_playlist_item(item)

    def clear_all(self):
        self.stop_video()
        self.items_map.clear()
        self.playlist.clear()
        self._update_gallery_empty_state()

    # ------------------------------------------------------------------
    # Pantalla dividida
    # ------------------------------------------------------------------
    def toggle_split_mode(self):
        """Muestra/oculta el panel de imagen junto al de video.

        `self.split_mode` es la única fuente de verdad (se invierte aquí
        y se refleja en el botón), así el método funciona igual venga del
        clic en `btn_split` o del atajo de teclado (D), que no toca el
        estado del botón por su cuenta.

        Activar o desactivar la pantalla dividida no modifica nada del
        estado del video (posición, velocidad, rotación...) ni de la
        imagen actual: cada panel sigue siendo dueño de su propio estado.
        """
        self.split_mode = not self.split_mode
        self.btn_split.setChecked(self.split_mode)
        self.image_panel.setVisible(self.split_mode)
        if self.split_mode:
            # El ancho del propio splitter (no la suma de sizes(), que con
            # el panel de imagen recién oculto puede no reflejar 0 de forma
            # fiable según la versión de Qt) es lo único que garantiza
            # repartir el espacio realmente disponible en dos mitades.
            total = self.media_splitter.width() or 2
            self.media_splitter.setSizes([total // 2, total - total // 2])

    # ------------------------------------------------------------------
    # Panel de imagen (independiente del video)
    # ------------------------------------------------------------------
    def add_image_paths(self, paths, show_first: bool = True):
        """Agrega imágenes al panel de imagen (deduplicando por ruta).

        `show_first=False` se usa al restaurar la galería guardada o al
        recibir un drop mixto video+imagen, para no interrumpir lo que ya
        se estuviera viendo en el panel.
        """
        added = self._collect_matching_files(
            paths, IMAGE_EXTENSIONS, set(self.image_paths)
        )
        if not added:
            return

        self.image_paths.extend(added)
        logger.info("%d imagen(es) nueva(s) agregada(s) al panel de imagen", len(added))

        if show_first:
            self.show_image_at(len(self.image_paths) - len(added))
        else:
            self._update_image_counter()

    def show_image_at(self, index: int):
        """Carga y muestra la imagen en `index`; limpia la vista si la
        lista está vacía o el archivo ya no se puede leer."""
        if not self.image_paths:
            self.image_index = -1
            self.image_view.clear()
            self._update_image_counter()
            return

        index = max(0, min(index, len(self.image_paths) - 1))
        self.image_index = index
        path = self.image_paths[index]

        img = QImage(path) if os.path.exists(path) else QImage()
        if img.isNull():
            logger.warning("No se pudo cargar la imagen: %s", path)
            self.statusBar().showMessage(
                f"No se pudo cargar la imagen: {os.path.basename(path)}", 6000
            )
            self.image_view.clear()
            self._update_image_counter()
            return

        self.image_view.set_frame(img)
        # Reencuadra el paneo para el tamaño de la nueva imagen: sin esto,
        # un desplazamiento válido para la foto anterior podía quedar
        # fuera de rango para esta (aspecto distinto) hasta el próximo
        # zoom/giro manual, que es lo único que hoy recalcula el clamp.
        self.image_view.set_zoom(self.image_zoom)
        self._update_image_counter()

    def show_next_image(self):
        """Siguiente imagen (manual): envuelve al llegar al final."""
        count = len(self.image_paths)
        if count == 0:
            return
        next_index = self.image_index + 1 if self.image_index + 1 < count else 0
        self.show_image_at(next_index)

    def show_previous_image(self):
        """Imagen anterior (manual): envuelve al llegar al inicio."""
        count = len(self.image_paths)
        if count == 0:
            return
        prev_index = self.image_index - 1 if self.image_index - 1 >= 0 else count - 1
        self.show_image_at(prev_index)

    def remove_current_image(self):
        if not self.image_paths or self.image_index < 0:
            self.statusBar().showMessage(
                "No hay ninguna imagen cargada para quitar", 4000
            )
            return
        removed_index = self.image_index
        del self.image_paths[removed_index]
        self.show_image_at(removed_index)

    def _update_image_counter(self):
        total = len(self.image_paths)
        current = self.image_index + 1 if 0 <= self.image_index < total else 0
        self.lbl_img_counter.setText(f"{current} / {total}")

    def rotate_image(self, delta):
        """Gira la imagen. delta positivo = horario, negativo = antihorario."""
        self.image_rotation = (self.image_rotation + delta) % 360
        self.image_view.set_rotation(self.image_rotation)
        self.lbl_img_rot.setText(f"{self.image_rotation}°")

    def adjust_image_zoom(self, step):
        """step positivo = acercar, negativo = alejar."""
        increment = 0.1 * step
        self.image_zoom = max(0.2, min(8.0, self.image_zoom + increment))
        self.image_view.set_zoom(self.image_zoom)
        self.lbl_img_zoom.setText(f"{int(self.image_zoom * 100)}%")

    def reset_image_zoom(self):
        self.image_zoom = 1.0
        self.image_view.reset_view()
        self.lbl_img_zoom.setText("100%")

    def toggle_image_fill_mode(self):
        fill = self.btn_img_fill.isChecked()
        self.image_view.set_fill_mode(fill)
        self.btn_img_fill.setText("Llenar" if fill else "Ajustar")

    @staticmethod
    def fmt_time(ms):
        """Formatea milisegundos a HH:MM:SS.

        Usa aritmética entera (no float) para evitar errores de redondeo
        en los límites de minuto/hora (p. ej. "%60" con floats podía dar
        59.999999 en vez de 60 y mostrar "59" un instante de más) y
        aplica max(0, ...) para que una duración/posición negativa o
        desconocida no produzca un valor absurdo por el módulo de Python
        con enteros negativos.
        """
        ms = max(0, int(ms))
        total_s = ms // 1000
        s = total_s % 60
        m = (total_s // 60) % 60
        h = total_s // 3600
        return f"{h:02d}:{m:02d}:{s:02d}"
