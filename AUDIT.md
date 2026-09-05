# Autopsia técnica — Reproductor Multimedia (PyQt6)

**Alcance:** `main.py`, `core/settings.py`, `core/logging_config.py`,
`core/thumbnail_cache.py`, `ui/main_window.py`, `ui/theme.py`,
`ui/video_widget.py`.

**Fuera de alcance (por instrucción explícita):** ciberseguridad,
inyecciones, ataques o vulnerabilidades de red/servidor. Este informe se
centra exclusivamente en corrección lógica, estabilidad, rendimiento
funcional y calidad de código.

Convención de severidad: 🔴 Bug real (comportamiento incorrecto
observable) · 🟠 Code smell / antipatrón · 🟡 Rendimiento · 🔵
Modernización / mantenibilidad.

Todos los hallazgos 🔴 y el 🟠 de complejidad ciclomática más relevante
fueron corregidos en este mismo cambio. Al final se listan los que
quedan como recomendación (fuera de este diff) por ser refactors de
mayor alcance.

---

## 1. Bugs y errores de lógica

### 🔴 1.1 Doble clic disparaba también play/pausa (`ui/video_widget.py`)

**Síntoma:** al hacer doble clic sobre el video para entrar/salir de
pantalla completa, el video se pausaba (o reanudaba) de forma visible un
instante antes de que cambiara el modo de pantalla.

**Causa:** Qt entrega una secuencia de doble clic como
`MousePress → MouseRelease → MouseButtonDblClick → MouseRelease` (no hay
un segundo `MousePress`). El código emitía `clicked` inmediatamente en el
primer `mouseReleaseEvent`, sin esperar a saber si ese clic terminaría
siendo la primera mitad de un doble clic. Resultado: `toggle_play()` se
ejecutaba siempre, y adicionalmente `toggle_fullscreen()` si el segundo
clic llegaba a tiempo.

**Corrección:** el clic simple ahora se retrasa
`QApplication.doubleClickInterval()` con un `QTimer` de un solo disparo;
si llega `mouseDoubleClickEvent` antes de que venza, se cancela el timer
y solo se emite `doubleClicked`.

### 🔴 1.2 Doble emisión de `sliderPressed` / `sliderReleased` (`ui/main_window.py`, `SeekSlider`)

**Síntoma:** cada interacción con el slider de progreso ejecutaba
`apply_seek()` dos veces por cada soltado del ratón (idéntico problema en
`sliderPressed`).

**Causa:** `QAbstractSlider.setSliderDown(bool)` ya emite
`sliderPressed()`/`sliderReleased()` internamente al detectar el cambio
de estado (comportamiento documentado de Qt, pensado justamente para
simular una interacción de ratón). El código además llamaba
`self.sliderPressed.emit()` / `self.sliderReleased.emit()` a mano,
duplicando la señal.

**Por qué importaba:** hoy es inofensivo porque `apply_seek` es
idempotente y `sliderPressed` solo pone un flag, pero es un efecto
secundario oculto (doble ejecución silenciosa) que morderá al primer
`slot` no idempotente que se conecte ahí (p. ej. telemetría de
"segundos buscados", o si se reutiliza `SeekSlider` para el volumen con
un `sliderReleased` que sí tenga efecto).

**Corrección:** se eliminaron los `.emit()` manuales; se deja que
`setSliderDown()` dispare las señales una sola vez.

### 🔴 1.3 `fmt_time` con aritmética de punto flotante y sin protección ante negativos

**Síntoma potencial:** parpadeo ocasional del contador de tiempo (mostrar
un segundo "de más" o "de menos" justo en los límites de minuto/hora), y
un valor sin sentido si `position()`/`duration()` entregaran un negativo
(caso límite documentado de algunos backends de `QMediaPlayer` mientras
el medio aún no reporta duración fiable).

**Causa:** `(ms / 1000) % 60` en Python usa `float`; en los bordes exactos
esto puede evaluar a `59.999999999` en vez de `60.0` por error de
representación, y `int()` trunca hacia abajo. Además, el operador `%` de
Python con un `ms` negativo devuelve un resultado no negativo por su
semántica de "módulo con signo del divisor", produciendo tiempos
absurdos en vez de `00:00:00`.

**Corrección:** se reescribió con aritmética entera (`//`, `%` sobre
`int`) y se clampea `ms = max(0, int(ms))` antes de calcular.

### 🔴 1.4 Duplicados en la galería por rutas no normalizadas (`ui/main_window.py`, `add_paths`)

**Síntoma:** el mismo archivo podía aparecer dos veces en la galería
(con dos miniaturas generándose en paralelo) si llegaba por dos vías
distintas: p. ej. arrastrando la carpeta contenedora y luego el archivo
suelto, una ruta relativa vs. absoluta, o con `~` sin expandir.

**Causa:** la deduplicación (`if path in self.items_map`) comparaba
cadenas de texto sin normalizar. Dos rutas que apuntan al mismo archivo
en disco pero difieren textualmente (`./video.mp4` vs
`/home/user/video.mp4`, dobles separadores, etc.) nunca coincidían.

**Corrección:** nuevo helper `_normalize_path()`
(`os.path.abspath(os.path.expanduser(...))` + `normpath`) aplicado a
toda ruta antes de comprobar duplicados, tanto entre llamadas como
dentro del propio recorrido de una carpeta (`os.walk`).

### 🔴 1.5 Cierre de la aplicación sin esperar a los workers de miniaturas en curso (`ui/main_window.py`, `closeEvent`)

**Síntoma potencial:** al cerrar la app justo mientras se generan
miniaturas, el proceso puede terminar (`sys.exit`) con hilos nativos de
Qt (`QRunnable` ejecutando `ffmpeg` o escribiendo en la caché de disco)
todavía vivos, lo que puede desembocar en un cierre abrupto o un intento
de acceso a objetos de Qt ya liberados.

**Causa:** `self.thread_pool.clear()` solo descarta las tareas que
**aún no empezaron**; las que ya están corriendo siguen en segundo plano
sin que nada espere su finalización.

**Corrección:** tras `clear()`, se llama a
`self.thread_pool.waitForDone(2000)` con un margen acotado (no bloquea
indefinidamente el cierre) y se registra un aviso en el log si alguna
miniatura no llegó a tiempo.

---

## 2. Malas prácticas y antipatrones detectados

### 🟠 2.1 Función gigante que viola responsabilidad única — `init_ui` (~260 líneas)

Construía en un solo método el splitter, el panel de video, la barra de
tiempo, dos filas completas de controles y el panel de galería, todo
entremezclado con las conexiones de señales. Alta complejidad
ciclomática, imposible de testear una sección sin instanciar toda la
ventana, y cualquier cambio pequeño obligaba a leer el método completo.

**Corrección aplicada:** dividido en `_build_left_panel`,
`_build_time_bar`, `_build_transport_row`,
`_build_rotation_zoom_speed_row` y `_build_gallery_panel`, cada uno con
una sola responsabilidad y devolviendo el widget/layout que construye.
`init_ui` quedó como orquestador de ~25 líneas. Sin cambios de
comportamiento: mismos widgets, mismos nombres de atributo, mismas
conexiones.

### 🟠 2.2 `try/except` que enmascara un `type=bool` que nunca falla (`core/settings.py`, `fill_mode`/`loop`)

```python
def fill_mode(self) -> bool:
    try:
        return bool(self._s.value("playback/fill_mode", True, type=bool))
    except (TypeError, ValueError):
        return True
```

Pasar `type=bool` a `QSettings.value()` ya hace que Qt intente la
coerción y, si falla, siga devolviendo el valor por defecto sin lanzar
excepción — el `try/except` es código muerto que sugiere una protección
que en realidad no existe. No es un bug (no hay comportamiento
incorrecto observable) pero es ruido que puede confundir a quien lo lea
pensando que cubre un caso que no cubre. Se deja documentado para una
limpieza futura de bajo riesgo (no se tocó en este diff para no mezclar
cambios cosméticos con los de comportamiento).

### 🟡 2.3 Carrera teórica benigna sobre `_FFMPEG_AVAILABLE` (`core/thumbnail_cache.py`)

`_FFMPEG_AVAILABLE` es un global mutable leído/escrito desde múltiples
hilos del `QThreadPool` sin lock. En el peor caso dos hilos ejecutan
`shutil.which("ffmpeg")` a la vez la primera vez — resultado idéntico,
sin corrupción de datos posible (asignación atómica de un `bool` en
CPython). Se documenta como área de mejora (usar `functools.lru_cache`
o un lock explícito) pero no se corrige por no tener impacto funcional
real.

---

## 3. Rendimiento algorítmico

No se detectaron cuellos de botella algorítmicos de fondo ($O(n^2)$
evitable, recomputación pesada en bucles, etc.): las estructuras usadas
(`dict` `items_map` para lookup O(1), `set` para deduplicación tras el
fix 1.4, caché de miniaturas en disco con clave por hash de
ruta+tamaño+mtime) ya son las adecuadas para el volumen de datos que
maneja este reproductor (listas de videos de un usuario, no un catálogo
masivo). El único punto de atención real de rendimiento — no repintar
frames cuando la ventana está minimizada u oculta (`on_frame`) — ya
estaba correctamente resuelto en el código original.

**Recomendación de bajo riesgo:** `add_paths` reconstruye `all_files`
como lista antes de iterarla para crear los `QListWidgetItem`; con
galerías muy grandes (miles de archivos) podría beneficiarse de
`QListWidget.setUpdatesEnabled(False)` alrededor del bucle de inserción
para evitar relayouts intermedios. No se aplicó por no tener evidencia
de que el volumen de uso real lo justifique (agregar esta complejidad
sin caso de uso sería sobre-ingeniería).

---

## 4. Modernización — oportunidades no aplicadas en este diff

Se listan para una segunda pasada, deliberadamente no mezcladas con las
correcciones de bugs de esta entrega:

- Migrar `os.path.*` a `pathlib.Path` de forma consistente en
  `main_window.py` (ahora mezcla ambos estilos; `thumbnail_cache.py` ya
  usa `pathlib` para el caché pero `os.stat`/`os.path` para el resto).
- Añadir *type hints* a las firmas públicas de `ModernVideoPlayer` y
  `DirectVideoWidget` (hoy solo `core/` y `video_widget.py` los usan
  parcialmente).
- Extraer las constantes mágicas de UI (tamaños de icono, márgenes,
  `AUTOHIDE_CONTROLS_MS`) que ya están centralizadas en su mayoría, a un
  módulo único de configuración de UI junto con `THUMB_W`/`THUMB_H`.
- Dividir `ui/main_window.py` (todavía ~900 líneas) en módulos más
  pequeños por responsabilidad (p. ej. `gallery.py` para la galería,
  `fullscreen.py` para el modo pantalla completa) ahora que `init_ui` ya
  no concentra toda la complejidad.

---

## Resumen de cambios aplicados en este commit

| Archivo | Cambio | Categoría |
|---|---|---|
| `ui/video_widget.py` | Debounce de clic simple vs. doble clic | 🔴 Bug lógico |
| `ui/main_window.py` (`SeekSlider`) | Elimina doble emisión de `sliderPressed`/`sliderReleased` | 🔴 Bug lógico |
| `ui/main_window.py` (`fmt_time`) | Aritmética entera + clamp de negativos | 🔴 Bug de caso borde |
| `ui/main_window.py` (`add_paths`) | Normalización de rutas para deduplicar correctamente | 🔴 Bug lógico |
| `ui/main_window.py` (`closeEvent`) | `waitForDone` acotado antes de salir | 🔴 Robustez / recursos |
| `ui/main_window.py` (`init_ui`) | Split en 5 métodos de una sola responsabilidad | 🟠 Complejidad ciclomática |

Ningún cambio altera la interfaz pública, los nombres de atributos
usados por otras partes de la app, ni el comportamiento observable
salvo la corrección explícita de cada bug descrito arriba.
