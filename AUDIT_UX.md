# Auditoría UI/UX — Reproductor Multimedia (Apple HIG × Fedora/KDE)

Segunda auditoría de este repositorio (la primera, `AUDIT.md`, cubrió
lógica/rendimiento/calidad de código). Esta se centra en **funcionalidad
de escritorio, rendimiento de renderizado, y UX/UI/accesibilidad**, con
la mirada puesta en Apple HIG adaptado a un escritorio KDE Plasma en
Fedora.

**Limitación honesta de este entorno:** esta sesión no tiene PyQt6
instalado ni un display disponible, así que ningún cambio visual pudo
capturarse en pantalla ni probarse en runtime — solo se verificó
`py_compile` (sintaxis) y se razonó cada cambio contra la documentación
de Qt/HIG y cálculos de contraste WCAG hechos a mano. **Se recomienda un
smoke test visual real antes de dar el trabajo por cerrado.**

---

## 1. ⚙️ Funcionalidad y robustez

### Estado vacío de la galería de video
**Antes:** una `QListWidget` en blanco cuando no había videos cargados —
ninguna pista de qué hacer, indistinguible de "la app está rota" para un
usuario nuevo.
**Ahora:** `QStackedWidget` con dos páginas (`ui/main_window.py`,
`_build_gallery_panel`/`_build_gallery_empty_state`): la lista, o un
mensaje centrado ("Sin videos todavía" + cómo agregarlos). Se alterna en
`_update_gallery_empty_state()`, invocado desde `add_paths`,
`remove_selected`/`_remove_playlist_item` y `clear_all` — cualquier
cambio en el conteo de la galería lo refleja.

El panel de imagen ya tenía su propio estado vacío (el placeholder de
`DirectVideoWidget` cuando `current_frame is None`); solo se le dio un
texto propio ("Sin imagen cargada…") en vez de heredar el del video.

### Miniatura "pendiente" inconsistente con la de "fallo"
**Antes:** mientras ffmpeg generaba la miniatura real, la galería
mostraba un rectángulo gris plano (`QPixmap(130,75); fill(lightGray)`);
si la generación fallaba definitivamente, en cambio, se veía el dibujo
más cuidado de `fallback_thumb()` (rectángulo redondeado + triángulo de
play). Dos lenguajes visuales distintos para, en la práctica, el mismo
mensaje ("todavía no hay una miniatura real").
**Ahora:** `add_paths` usa `fallback_thumb()` también como placeholder
inicial — un único dibujo para ambos casos, y una línea menos de código
duplicado dibujando un "placeholder" ad hoc.

### Menús contextuales (clic derecho)
**Antes:** ninguno — atípico en un escritorio KDE, donde el clic derecho
es el mecanismo estándar para acciones secundarias.
**Ahora:**
- Galería de video: clic derecho sobre un item → Reproducir / Abrir
  carpeta contenedora / Quitar de la galería; clic derecho en vacío →
  Agregar videos…; siempre, si hay contenido, Limpiar toda la galería.
- Panel de imagen: clic derecho → Cargar imágenes… y, si hay una imagen
  activa, Abrir carpeta contenedora / Quitar esta imagen.
- "Abrir carpeta contenedora" usa `QDesktopServices.openUrl` sobre la
  carpeta del archivo — abre el gestor de archivos del sistema (Dolphin
  en KDE), el mismo patrón que "Mostrar en el gestor de archivos" de
  cualquier app de escritorio nativa.

### Refactor de apoyo
`remove_selected` duplicaba, por cada item seleccionado, la lógica de
"sacarlo de `items_map` y de la lista"; se extrajo a
`_remove_playlist_item()` para que el menú contextual (que puede operar
sobre un item ni siquiera seleccionado) la reutilice en vez de
duplicarla.

---

## 2. ⚡ Rendimiento y eficiencia

### Dónde SÍ se puso sombra de elevación, y dónde deliberadamente no
`ui/theme.apply_elevation()` usa `QGraphicsDropShadowEffect`, que obliga
a Qt a componer ese widget en un buffer offscreen por software en cada
repintado. Es barato para un widget que solo se repinta al interactuar
(un botón, una lista, una barra de controles), pero sería carísimo sobre
algo que se repinta a 30-60 fps.

Por eso la sombra se aplicó únicamente a tres superficies estáticas —
`self.playlist` (nivel 2, la más prominente), `self.controls_panel` y
`self.image_controls_panel` (nivel 1), y el botón `+ Agregar videos`
(nivel 1) — y **nunca** a `video_view`/`image_view`, que reciben un
frame nuevo constantemente mientras hay reproducción.

### Nada más que optimizar de fondo
Se revisó de nuevo el camino de pintado (`DirectVideoWidget.paintEvent`,
`on_frame`) buscando trabajo redundante por frame: sigue siendo el ya
optimizado en la auditoría anterior (sin conversiones si la ventana está
minimizada o el panel oculto). `QListWidget` no es una virtualización
"a mano" pero tampoco necesita una: Qt la pinta vía el mecanismo
item-view estándar (un delegate, no un `QWidget` por fila), así que
listas de miles de elementos no crean miles de widgets reales — no se
encontró justificación para añadir virtualización propia.

---

## 3. 🍎 UX/UI (Apple HIG) y accesibilidad

### Tema claro/oscuro real, no forzado
**Antes:** `main.py` aplicaba `PREMIUM_LIGHT_QSS` sin condición alguna,
sin importar si KDE Plasma corría en Breeze Dark — la app "peleaba"
visualmente contra la preferencia del sistema, justo lo opuesto a
"integración con el escritorio".
**Ahora:** `ui/theme.system_prefers_dark(app)` lee la paleta que Qt ya
cargó del tema nativo (debe llamarse ANTES de `app.setStyle("Fusion")`,
que la reemplazaría) y `apply_theme()` aplica la hoja de estilos que
corresponde. Si el Qt instalado expone
`QStyleHints.colorSchemeChanged` (Qt ≥ 6.5), `watch_system_theme()`
además reacciona en caliente a un cambio de tema desde Configuración
del Sistema, sin reiniciar la app; en Qt más antiguo simplemente no se
conecta nada (degradación silenciosa, la app sigue funcionando con el
tema detectado al arrancar).

Las dos hojas de estilo (`PREMIUM_LIGHT_QSS`/`PREMIUM_DARK_QSS`) se
generan desde **una sola plantilla** (`_QSS_TEMPLATE`, con `$token` en
vez de f-strings para no tener que escapar cada `{ }` literal de QSS) y
dos diccionarios de tokens — evita mantener el doble de CSS a mano.

### Colores de acento corregidos contra Apple HIG
El azul de acento original (`#0A84FF`) es, según la documentación de
Apple, el **systemBlue de modo OSCURO** — el tema "claro" original ya
estaba usando por error el azul pensado para fondos oscuros. Ahora:
modo claro usa `#007AFF` (systemBlue claro), modo oscuro usa `#0A84FF`
(el original, en el tema que sí le corresponde).

### Contraste de texto verificado (WCAG 2.1 AA, ratio ≥ 4.5:1)
- Texto secundario claro (`timeLabel`/`groupCaption`, 12px): el original
  `#6E6E73` sobre `#F5F5F7` da **4.65:1** — pasa, pero al límite. Se
  oscureció a `#57575D` → **≈ 6.6:1**, con margen real.
- Texto secundario oscuro: `#A0A0A6` sobre `#1C1C1E` → **≈ 6.5:1**.
- Texto primario en ambos temas: > 15:1 (AAA de sobra).
- Rojo de "peligro" (texto sobre chip): se **mantuvo** `#D70015` en modo
  claro en vez de migrar al systemRed de Apple (`#FF3B30`) porque, en
  este contexto concreto (texto de 12px sobre `#FFF5F5`), el original da
  **≈ 5.1:1** contra **≈ 3.3:1** del rojo de Apple — el "correcto" según
  HIG en este caso hubiera sido, paradójicamente, menos accesible. En
  modo oscuro se usó `#FF453A` (systemRed oscuro) sobre un chip rojo
  oscuro propio, verificado en **≈ 4.7:1**.

### Nombres accesibles en botones de solo símbolo
Botones como ⏮ ⏭ ↺ ↻ − + 🔊 no dicen nada útil a un lector de pantalla
(Orca/AT-SPI, el que usa KDE) más allá de deletrear el carácter. Se les
añadió `setAccessibleName(...)` con la acción real ("Video anterior",
"Girar imagen horario", "Alejar video", etc.) a los 13 botones
icon-only del transporte de video, giro/zoom de video, y navegación/giro/
zoom de imagen. Los botones con texto real (Bucle, Pantalla completa,
Restablecer, -5s/+5s...) no lo necesitaban: su `text()` ya es
descriptivo.

### Icono de aplicación e integración con KDE
**Antes:** sin `setWindowIcon`, la ventana y el alt-tab de KDE mostraban
el icono genérico de Qt.
**Ahora:** `ui/theme.build_app_icon()` dibuja un icono propio (rectángulo
redondeado con degradado + triángulo de play, mismo lenguaje visual que
`fallback_thumb()`) en 7 resoluciones explícitas (16 a 256px, dibujado a
cada tamaño en vez de escalar un único pixmap grande, para que se vea
nítido también en la bandeja). Se añadió además
`app.setDesktopFileName("reproductor-multimedia")` para que, si el
paquete llega a instalar un `.desktop` con ese nombre, KDE pueda asociar
correctamente ventana ↔ entrada de menú/anclado en la barra de tareas.
**Nota:** esta llamada no crea el archivo `.desktop` en sí — si el
paquete no lo instala, simplemente no hay nada que asociar, sin error.

### Escala de espaciado 4/8px
Un puñado de `setSpacing(6)`/`addSpacing(6)`/`addSpacing(10)` en las
filas de controles no eran múltiplos de 4; se normalizaron a 8/12 para
que toda la app respete la misma escala (ya usada, por ejemplo, en los
márgenes de los layouts principales: 8/12/16).

### Fuera de alcance, deliberadamente
- **"Vibrancy"/frosted glass real** (macOS): requiere blur del
  compositor detrás de la ventana. En X11 existe la propiedad KWin
  `_KDE_NET_WM_BLUR_BEHIND_REGION`, pero no hay equivalente estable ni
  portable en Wayland vía Qt Widgets puro; implementarlo a medias
  (funciona en X11, no en Wayland, sin manera de detectarlo de forma
  confiable) generaría una experiencia inconsistente peor que no
  tenerlo. Se optó por paneles translúcidos "de mentira" (color sólido +
  sombra) en vez de un blur real a medias.
- **Esquinas continuas ("squircle")**: el `border-radius` de Qt Style
  Sheets dibuja un arco circular verdadero, no la curva continua de
  Apple. Conseguir la curva real exige pintar cada widget a mano con
  `QPainterPath` (una superficie custom por botón/tarjeta), un costo de
  ingeniería desproporcionado para una app de escritorio utilitaria.
- **Microinteracciones animadas** (springs, easing): Qt permite
  `QPropertyAnimation`, pero animar decenas de botones sin poder
  verificar visualmente el resultado en este entorno es un riesgo real
  de introducir jank en vez de pulirlo. Se mantuvieron las transiciones
  instantáneas de hover/pressed/focus/disabled ya presentes en el QSS
  (completas y con buen contraste, solo les faltaba la variante oscura,
  ya añadida).

---

## Resumen de archivos tocados

| Archivo | Cambio principal |
|---|---|
| `ui/theme.py` | Reescrito: tokens claro/oscuro, detección de tema del sistema, helper de elevación, icono de app generado por código |
| `main.py` | Detección de tema antes de `setStyle("Fusion")`, `apply_theme`, `setWindowIcon`, `setDesktopFileName`, `watch_system_theme` |
| `ui/main_window.py` | Estado vacío de galería, miniatura placeholder unificada, tarjetas elevadas para las barras de controles, nombres accesibles, menús contextuales, "abrir carpeta contenedora", espaciado normalizado a 4/8px |

Ningún cambio de esta pasada modifica atajos de teclado existentes,
persistencia de `AppSettings`, ni el comportamiento de reproducción/zoom/
rotación ya auditado en `AUDIT.md` — es una capa de presentación y
accesibilidad sobre la misma lógica.
