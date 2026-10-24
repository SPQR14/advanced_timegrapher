# Informe de Auditoría del Código — Advanced Timegrapher (`adv_tg` v0.2.0)

> **Alcance de la auditoría:** Directorio [`src/adv_tg/`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg), configuración de empaquetado [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml) y pipeline de integración continua [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml).
> **Perfil de auditoría:** Ingeniería de software senior en Python, DSP en tiempo real y metrología horológica (`escape-analyzer-engineer` / `relojeria-dsp`).

---

## 1. Resumen Ejecutivo

El proyecto se encuentra en un estado **híbrido (v0.2.0)**:
- **Capa 1 (Adquisición y transporte de muestras)** está sólidamente diseñada y resuelve el problema real de contención del GIL mediante captura aislada en un subproceso (`spawn`) y un buffer circular SPSC sin bloqueos ([`SampleStream`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/sample_stream.py#L75-L309)).
- **Capas 2 a 4 (Detección acústica de batimientos P1/P2/P3, regresión de marcha/error de batimiento/amplitud y estimación de deriva del reloj del ADC)** **no están implementadas aún** en [`src/adv_tg/core/`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/__init__.py#L1-L8). Con un micrófono físico seleccionado, la aplicación usa [`CaptureStreamWorker`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L505-L625), que únicamente muestra la onda cruda y el nivel de entrada (dBFS), sin calcular marcha, error de batimiento ni amplitud.
- **Capa 5 (GUI en PyQt6 + PyQtGraph)** está completa visualmente, pero presenta **errores metrológicos importantes** en el trazado de la cinta de papel y del osciloscopio, acoplamiento con lógica de simulación (`MOCK`), efectos secundarios en tiempo de importación y código legado duplicado.

---

## 2. Hallazgos Críticos de Metrología Horológica y DSP

### 2.1. Error dimensional y físico en el gráfico "Paper Tape Trace"
- **Ubicación:** [`MainWin._on_telemetry_ready`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L2335-L2342)
- **Código actual:**
  ```python
  half_be_offset = 0.5 * beat_error_ms
  self._tape_times_even.append(t_meas)
  self._tape_rate_even.append(rate_spd - half_be_offset)
  self._tape_times_odd.append(t_meas)
  self._tape_rate_odd.append(rate_spd + half_be_offset)
  ```
- **Diagnóstico:**
  1. **Inconsistencia dimensional:** Se está sumando y restando `0.5 * beat_error_ms` (en **milisegundos**) directamente a `rate_spd` (en **segundos por día, s/d**). Sumar `ms` a `s/d` carece de sentido físico.
  2. **Confusión entre traza de fase (cinta de papel) y serie temporal de marcha:**
     - En un cronocomparador clásico (cinta de papel), el eje vertical representa la **desviación de fase acumulada en milisegundos** respecto al periodo nominal: $\Delta t_k = (k \cdot P_{\text{nom}} - (t_k - t_0)) \times 10^3\text{ ms}$. La **pendiente** de la traza es la marcha ($\text{s/d}$) y la **separación vertical** entre la rama par (tic) y la impar (tac) es el error de batimiento ($2|c|$ en $\text{ms}$).
     - En cambio, [`MainWin`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L991-L1021) configura el eje Y como **marcha (`s/d`)** con límites de tolerancia COSC horizontales en `-4 s/d` y `+6 s/d`. Si el eje Y es marcha en `s/d`, el error de batimiento no desplaza la marcha en $\pm 0.5\text{ s/d por cada ms}$.
- **Recomendación:** Definir con precisión qué magnitud representa el gráfico superior:
  - Si es **Marcha vs. Tiempo** (`s/d` con bandas COSC `-4/+6 s/d`), graficar una única curva de marcha estimada por regresión (que ya separa tic y tac mediante el término $c(-1)^k$) junto con su banda de incertidumbre.
  - Si es **Cinta de Papel real**, cambiar el eje Y a desviación acumulada (`ms`) para cada batimiento detectado `beat_times_sec` y eliminar las líneas horizontales fijas en `-4` y `+6`.

### 2.2. Detección de P1 / P2 / P3 recalculada erróneamente en la GUI
- **Ubicación:** [`MainWin._on_telemetry_ready`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L2351-L2388)
- **Diagnóstico:**
  1. La GUI busca el pico máximo absoluto de la ventana (`p3_idx = int(np.argmax(np.abs(norm_wave)))`) y **asume siempre que dicho máximo es P3 (caída)**, reconstruyendo P1 y P2 hacia atrás (`t_p1_ms = max(0.2, t_p3_ms - dt_p3)`).
  2. En relojería mecánica real, según el calibre, el estado de lubricación y el acoplamiento acústico del piezo, **el pico de mayor amplitud puede ser el desbloqueo (P1), el impulso (P2) o la caída (P3)**. Además, el instante metrológico válido de cada sub-evento es el **cruce ascendente de umbral (inicio del sonido)** con interpolación sub-muestra, no el máximo de la onda.
  3. En las líneas [`2356-2363`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L2356-L2363), la propia ventana `MainWin` modifica la señal recibida en modo sintético inyectando una modulación de 1100 Hz y ruido gaussiano adicional antes de graficarla, rompiendo la separación entre motor y vista.
- **Recomendación:** [`BeatEvent`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/models.py#L58-L75) debe entregar los instantes relativos dentro de `raw_waveform` (o el offset de inicio de la ventana) calculados por el motor DSP, y la GUI debe limitarse a posicionar `line_p1`, `line_p2` y `line_p3` en esos instantes sin reinterpretar la física del escape.

### 2.3. Invención de datos al guardar posición sin telemetría activa
- **Ubicación:** [`MainWin.save_current_position`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1476-L1498)
- **Diagnóstico:** Si el usuario pulsa *"Guardar Posición"* cuando `self._last_telemetry is None` (por ejemplo, antes de medir o usando un micrófono real con `CaptureStreamWorker`), el método **crea un `TelemetryResult` ficticio** a partir del diccionario hardcodeado `POSITION_VARIATION` con `confidence=0.97` y lo añade a la tabla de estadísticas globales y a los CSV exportados.
- **Recomendación:** Deshabilitar `btn_save_position` mientras no exista una medición real válida (`self._last_telemetry is not None`), y mostrar aviso en la barra de estado si se intenta guardar sin datos.

### 2.4. Fallback a formato con pérdidas (Ogg Vorbis) al guardar grabaciones
- **Ubicación:** [`save_audio_recording`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L497-L569) y [`MainWin.save_recorded_audio`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1937-L1981)
- **Diagnóstico:** Cuando la codificación FLAC no está disponible, el sistema recurre automáticamente a **Ogg Vorbis (`.ogg`)**. La compresión perceptual con pérdidas (Vorbis, MP3, AAC) introduce pre-eco y difumina los transitorios de ataque (desbloqueo P1 y caída P3), sesgando tanto el cálculo de la amplitud ($t_{\text{lift}}$) como el instante del batimiento.
- **Recomendación:** El formato de respaldo cuando FLAC no esté disponible debe ser **WAV sin pérdidas (PCM 16/24 bits o IEEE float32)** usando la librería estándar `wave` o `scipy.io.wavfile` (ya incluida en `dependencies`), nunca Ogg Vorbis.

### 2.5. Estadísticas posicionales incompletas para diagnóstico de banco
- **Ubicación:** [`MainWin._refresh_global_stats_table`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1499-L1529) y [`watch_db.csv`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/db/watch_db.csv#L1-L15)
- **Diagnóstico:**
  - La tabla global sólo calcula la media aritmética y el delta máximo (`max - min`), pero omite métricas esenciales para el relojero: **diferencia horizontal (`DU - DD`)**, **delta horizontal–vertical de marcha y amplitud** (clave para detectar desgaste de pivotes o falta de lubricación) y **dispersión entre verticales** (error de pesaje/equilibrio del volante).
  - En [`watch_db.csv`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/db/watch_db.csv#L7), el calibre `ETA C07.XXX (powermatic 80)` figura con ángulo de levantamiento de `51°`, mientras que la especificación técnica de ETA para la familia C07 (escape de geometría modificada a 21 600 A/h) es **53°** (un error de $2^\circ$ en el ángulo de levantamiento introduce un sesgo sistemático de $\approx 4\%$ en la amplitud, unos $\sim 11^\circ$ a $280^\circ$). Asimismo, `ETA 2824-2` figura con `50°` (valor de tablas antiguas; muchas referencias modernas de 2824-2 / SW200 usan `50°–52°`).

---

## 3. Auditoría de Arquitectura e Ingeniería de Software (`/src`)

### 3.1. Escritura de logs en directorios del paquete instalado (`LoggerSetup`)
- **Ubicación:** [`LoggerSetup`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/logger_setup.py#L30-L32) y [`_enable_faulthandler`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/main.py#L40-L47)
- **Diagnóstico:**
  `BASE_DIR: Path = Path(__file__).resolve().parents[3]` asume que el código corre siempre desde un árbol de desarrollo (`<repo>/src/adv_tg/utils/logger_setup.py`). Cuando el paquete se instala mediante wheel en un entorno de usuario o sistema (`site-packages/adv_tg/utils/logger_setup.py`), `parents[3]` apunta fuera del paquete (p. ej. `.venv/lib/` o `/usr/local/lib/`), lo que provoca contaminación del entorno virtual o un fallo `PermissionError` al ejecutar `LoggerSetup.LOG_DIR.mkdir(...)` en el momento de importar cualquier módulo.

### 3.2. Efectos secundarios en tiempo de importación
- **Ubicaciones:**
  - [`src/adv_tg/ui/main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L70-L113): Al importar el módulo se ejecuta `LoggerSetup.setup_logger()` (que crea carpetas, archivos de log y reemplaza handlers del root logger con `logging.basicConfig(force=True)`), `configure_pyqtgraph_gpu()` y `load_watch_db()` (lectura de disco). Además, `configure_pyqtgraph_gpu()` se invoca **tres veces** durante el arranque: al importar [`main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L109), en [`main.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/main.py#L80) y en [`MainWin.__init__`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L197).
  - [`src/adv_tg/utils/audio_io.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L330): `_SNDFILE_LIB = _load_libsndfile()` invoca `ctypes.util.find_library("sndfile")` y `ctypes.CDLL(...)` en tiempo de importación en lugar de hacerlo de forma perezosa (`functools.lru_cache`) cuando realmente se necesite guardar audio.
  - [`src/adv_tg/core/models.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/models.py#L12-L45): `WatchConfig.__post_init__` registra un mensaje `INFO` en el archivo de log cada vez que se instancia la dataclass.

### 3.3. Código duplicado y clases legadas sin uso
1. **[`HardwareAudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L136-L268):** Clase legada de captura PortAudio que aloja memoria (`np.ascontiguousarray(..., dtype=np.float64)`) dentro del callback de tiempo real. Fue superada por [`AudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture.py#L57-L309) y [`ProcessAudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L75-L489), y no es utilizada por ningún módulo de `/src`.
2. **Duplicación de [`GilWaitRecorder`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/gil_stats.py#L61-L104):** [`AudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture.py#L109-L192) reimplementa manualmente los contadores y el histograma de espera del GIL en lugar de componer una instancia de `GilWaitRecorder` como hace [`_ChildCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L140).
3. **Múltiples implementaciones de lectura/escritura WAV:** Dispersas entre [`mock/generator.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/generator.py#L132-L140), [`tools/capture_check.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/capture_check.py#L72-L119) y [`utils/audio_io.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L450-L462).

### 3.4. Traducciones incompletas en `Translator`
- **Ubicación:** [`Translator.DICTIONARIES["es"]`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/i18n/translator.py#L127-L128)
- Las claves `"btn_save_csv"` y `"btn_export_test_results"` en el diccionario español mantienen el texto en inglés (`"Export Results to CSV"` y `"Export Test Results"`).

---

## 4. Auditoría de Configuración (`pyproject.toml` y `.github/workflows/ci.yml`)

| Archivo | Hallazgo | Impacto |
|---|---|---|
| [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L15-L32) | `scipy` aparece duplicado en `dependencies` y en `[project.optional-dependencies] dev`. | Redundancia en especificación de dependencias. |
| [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L21-L22) | `PyOpenGL` y `PyOpenGL_accelerate` son dependencias obligatorias aunque el modo OpenGL está desactivado por defecto (`enable_opengl=False`). | Mayor peso de instalación y fallos potenciales con extensiones C de `PyOpenGL_accelerate` en Python 3.12+. |
| [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L15-L23) | `soundfile` es invocado como primera opción en [`save_audio_recording`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L538), pero no está declarado en `dependencies`. | En Windows y macOS sin `libsndfile`/`ffmpeg` en el sistema, la exportación de audio falla en tiempo de ejecución. |
| [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L37-L40) | `[tool.setuptools.packages.find]` incluye todo `adv_tg*`, empaquetando las herramientas internas de diagnóstico (`adv_tg.tools`) y placeholders vacíos (`adv_tg.ml`). | Incrementa en ~30% el código distribuido en el wheel con scripts de banco de pruebas. |
| [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L52-L55) | En macOS y Windows se ejecuta `pytest` sin `QT_QPA_PLATFORM=offscreen`, sin dependencias nativas de audio (`libsndfile`/`portaudio`) y con cobertura en subprocesos `spawn`. | Provoca fallos e inestabilidad en los jobs de macOS y Windows con Python 3.12 (detallado en el Entregable 3). |
