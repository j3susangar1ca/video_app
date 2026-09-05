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
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QSize, Qt, QTimer, QThreadPool, QUrl
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSlider,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.settings import AppSettings
from core.thumbnail_cache import ThumbWorker
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

        self.init_player()
        self.init_ui()
        self.setup_shortcuts()

        self._hide_controls_timer = QTimer(self)
        self._hide_controls_timer.setSingleShot(True)
        self._hide_controls_timer.timeout.connect(self._hide_fullscreen_controls)

        self.restore_window_state()
        self.restore_playlist()

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
        """Panel izquierdo: área de video + controles inferiores."""
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(12, 12, 8, 12)
        left_layout.setSpacing(10)

        self.video_view = DirectVideoWidget()
        self.video_view.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.clicked.connect(self.toggle_play)
        self.video_view.wheelScrolled.connect(self.adjust_volume)
        self.video_view.zoomScrolled.connect(self.adjust_zoom)
        self.video_view.mouseMoved.connect(self.on_video_mouse_moved)
        left_layout.addWidget(self.video_view, stretch=1)

        # Contenedor de controles inferiores: se oculta junto con el panel
        # lateral cuando se entra en pantalla completa.
        controls_panel = QWidget()
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        controls_layout.addLayout(self._build_time_bar())
        controls_layout.addLayout(self._build_transport_row())
        controls_layout.addLayout(self._build_rotation_zoom_speed_row())

        self.controls_panel = controls_panel
        left_layout.addWidget(self.controls_panel)
        return left_box

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
        ctrl1.setSpacing(6)

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setObjectName("transportBtn")
        self.btn_prev.setToolTip("Video anterior (P)")
        self.btn_prev.clicked.connect(self.play_previous)
        ctrl1.addWidget(self.btn_prev)

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playPauseBtn")
        self.btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.btn_play.setToolTip("Reproducir / Pausar (Espacio)")
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl1.addWidget(self.btn_play)

        self.btn_next = QPushButton("⏭")
        self.btn_next.setObjectName("transportBtn")
        self.btn_next.setToolTip("Siguiente video (N)")
        self.btn_next.clicked.connect(self.play_next)
        ctrl1.addWidget(self.btn_next)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("transportBtn")
        self.btn_stop.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        )
        self.btn_stop.setToolTip("Detener")
        self.btn_stop.clicked.connect(self.stop_video)
        ctrl1.addWidget(self.btn_stop)

        ctrl1.addSpacing(6)

        btn_back5 = QPushButton("-5s")
        btn_back5.setToolTip("Retroceder 5 segundos (Flecha Izquierda)")
        btn_back5.clicked.connect(lambda: self.seek_relative(-5000))
        ctrl1.addWidget(btn_back5)

        btn_fwd5 = QPushButton("+5s")
        btn_fwd5.setToolTip("Adelantar 5 segundos (Flecha Derecha)")
        btn_fwd5.clicked.connect(lambda: self.seek_relative(5000))
        ctrl1.addWidget(btn_fwd5)

        ctrl1.addSpacing(10)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setToolTip("Silenciar / Reactivar (M)")
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

        return ctrl1

    def _build_rotation_zoom_speed_row(self) -> QHBoxLayout:
        """Fila 2: giro, zoom/relleno y velocidad de reproducción."""
        ctrl2 = QHBoxLayout()
        ctrl2.setSpacing(6)

        cap_giro = QLabel("Giro")
        cap_giro.setObjectName("groupCaption")
        ctrl2.addWidget(cap_giro)

        btn_ccw = QPushButton("↺ 90°")
        btn_ccw.setToolTip("Girar antihorario (Shift+R)")
        btn_ccw.clicked.connect(lambda: self.rotate_video(-90))
        ctrl2.addWidget(btn_ccw)

        btn_cw = QPushButton("↻ 90°")
        btn_cw.setToolTip("Girar horario (R)")
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
        btn_zoom_out.clicked.connect(lambda: self.adjust_zoom(-1))
        ctrl2.addWidget(btn_zoom_out)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("statLabel")
        ctrl2.addWidget(self.lbl_zoom)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setToolTip("Acercar (Ctrl + Rueda arriba / +)")
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
        """Panel derecho: galería de videos y sus acciones."""
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
        right_layout.addWidget(self.playlist)

        btn_add = QPushButton("+  Agregar videos")
        btn_add.setObjectName("primaryBtn")
        btn_add.clicked.connect(self.open_file_dialog)
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
        return right_box

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

    def closeEvent(self, event):
        self.settings.set_window_geometry(self.saveGeometry())
        self.settings.set_volume(self.slider_vol.value())
        self.settings.set_speed_index(self.speed_idx)
        self.settings.set_fill_mode(self.btn_fill.isChecked())
        self.settings.set_loop(self.loop_video)
        self.settings.set_playlist_paths(self.current_playlist_paths())
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
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths, autoplay=True)

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

    def add_paths(self, paths, autoplay: bool = True):
        all_files = []
        seen = set()
        for raw_p in paths:
            p = self._normalize_path(raw_p)
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in sorted(files):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in VIDEO_EXTENSIONS:
                            full = os.path.normpath(os.path.join(root, f))
                            if full not in seen:
                                seen.add(full)
                                all_files.append(full)
            elif os.path.isfile(p):
                if p not in seen:
                    seen.add(p)
                    all_files.append(p)
            else:
                logger.warning("Ruta ignorada (no existe): %s", raw_p)

        placeholder = QPixmap(130, 75)
        placeholder.fill(Qt.GlobalColor.lightGray)

        first_added = None
        added_new = 0
        for path in all_files:
            if path in self.items_map:
                continue

            name = os.path.basename(path)
            item = QListWidgetItem(QIcon(placeholder), f" {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.playlist.addItem(item)
            self.items_map[path] = item
            added_new += 1

            if first_added is None:
                first_added = item

            worker = ThumbWorker(path)
            worker.signals.finished.connect(self.on_thumb_ready)
            self.thread_pool.start(worker)

        logger.info("%d archivo(s) nuevo(s) agregado(s) a la galería", added_new)

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
    def remove_selected(self):
        items = self.playlist.selectedItems()
        if not items:
            self.statusBar().showMessage(
                "Selecciona primero uno o más videos de la galería", 4000
            )
            return
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            self.items_map.pop(path, None)
            self.playlist.takeItem(self.playlist.row(item))

    def clear_all(self):
        self.stop_video()
        self.items_map.clear()
        self.playlist.clear()

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
