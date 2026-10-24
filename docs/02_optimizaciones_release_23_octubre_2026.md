# Sugerencias de Optimizaciones para un Release Limpio (`adv_tg`)

> **Objetivo:** Depurar el árbol [`src/adv_tg/`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg) y [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml) eliminando todos los scripts de banco de pruebas, sondas de diagnóstico de desarrollo, código muerto y sobrecargas de ejecución para generar una distribución (`wheel` / ejecutable) limpia, ligera y lista para producción.

---

## 1. Extracción y Exclusión de Scripts de Pruebas y Diagnóstico

Actualmente, casi el **30 % de las líneas de código dentro de `src/adv_tg/`** corresponden a scripts de diagnóstico y bancos de pruebas de las fases de desarrollo 1b, 1c y 1d. Ninguno de ellos es necesario para el usuario final del cronocomparador.

### 1.1. Retirar el paquete `src/adv_tg/tools/` del paquete distribuible
- **Archivos afectados (1 509 líneas en total):**
  - [`src/adv_tg/tools/gil_probe.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/gil_probe.py) (914 líneas): Banco de pruebas que lanza subprocesos, parchea clases en caliente (`capture_worker.AudioCapture = ProbeCapture`), inyecta módulos falsos en `sys.modules["sounddevice"]` y escribe reportes en `logs/gil_probe_*.md`.
  - [`src/adv_tg/tools/capture_check.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/capture_check.py) (546 líneas): CLI de verificación de ingesta y reproducción de WAV con `FileReplay`.
  - [`src/adv_tg/tools/README.md`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/README.md) y [`src/adv_tg/tools/__init__.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/__init__.py).
- **Acción recomendada:**
  1. Mover estos scripts fuera de `src/` hacia una carpeta de desarrollo en la raíz del repositorio (p. ej. `scripts/diagnostics/`) o eliminarlos antes de etiquetar el release.
  2. Si se mantienen en el repositorio durante el desarrollo, excluirlos explícitamente del `wheel` en [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L37-L40):
     ```toml
     [tool.setuptools.packages.find]
     where = ["src"]
     include = ["adv_tg*"]
     exclude = ["adv_tg.tools*", "adv_tg.ml*"]
     ```

### 1.2. Extraer las fuentes falsas de prueba (`_FakeStream` / `fake_source`) del código de producción
- **Archivos afectados:**
  - [`src/adv_tg/audio/capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L55-L122): La clase `_FakeStream` y las constantes `FAKE_BLOCKSIZE`, `FAKE_RAMP_MODULO` están incrustadas dentro del módulo de producción del proceso hijo únicamente para ejecutar tests sin micrófono.
  - [`src/adv_tg/audio/process_capture.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L84-L122): El argumento `fake_source: str | None = None` y su validación están expuestos en la API pública de `ProcessAudioCapture`.
- **Acción recomendada:** Eliminar `_FakeStream` de `capture_process.py` y mover los dobles de prueba (fakes/mocks de `sounddevice`) exclusivamente a los fixtures de `tests/`.

### 1.3. Desacoplar o condicionar el paquete `src/adv_tg/mock/` y los controles `MOCK` de la GUI
- **Archivos afectados:**
  - [`src/adv_tg/mock/generator.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/generator.py) y [`src/adv_tg/mock/worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/worker.py)
  - [`src/adv_tg/ui/main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L759-L765) (`self.btn_mock`, `Synthetic Ground-Truth Generator` en el selector de dispositivos, `_ensure_test_length_recording`, `POSITION_VARIATION`).
- **Acción recomendada para el release:**
  - Una vez integrado el motor de medición real (Capa 5), un cronocomparador de producción **no debe mostrar por defecto un botón `MOCK` ni un micrófono sintético** que pueda confundirse con una lectura real, ni inventar telemetrías en [`save_current_position`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1481-L1488).
  - Restringir la carga de `adv_tg.mock` y la visibilidad de `btn_mock` exclusivamente cuando se pase un flag de desarrollo (`adv-tg --dev-mock` o `ADV_TG_DEV_MOCK=1`), o excluir `adv_tg.mock*` del build de release.
  - Nota: La función utilitaria [`minmax_decimate`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/generator.py#L85-L98), que actualmente es importada por [`capture_worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L48), **debe moverse a `adv_tg.core` o `adv_tg.utils`** para que el código de producción no dependa del paquete `adv_tg.mock`.

---

## 2. Eliminación de Instrumentación de Diagnóstico en Tiempo Real

El código conserva sondas de perfilado que se ejecutan en cada bloque de audio y en cada frame de 30 FPS:

| Componente de diagnóstico | Ubicación | Optimización para el Release |
|---|---|---|
| **`SlotProfiler`** (`_perf_waveform`, `_perf_level`) | [`capture_worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L154-L190) y [`main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L230-L231) | **Eliminar.** Mide `time.perf_counter()` y adquiere el lock de `_dbg.throttled` 30 veces por segundo en el hilo de la GUI. |
| **`_log_level_1s` y acumuladores `_lvl_*`** | [`capture_worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L230-L236) y [`L478-L503`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L478-L503) | **Eliminar.** Fue escrito para calibrar la barra de nivel durante el desarrollo; calcula `np.dot(x, x)` y `np.max(np.abs(x))` por duplicado tras `_emit_level()`. |
| **Histograma de espera del GIL (`gil_stats.py`)** | [`gil_stats.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/gil_stats.py), [`capture.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture.py#L108-L192), [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L140-L172) | **Simplificar o eliminar.** Con la captura aislada en subproceso (`ProcessAudioCapture`), la contención del GIL ya quedó resuelta arquitectónicamente. Eliminar la consulta a `_pa_clock.time` y `bisect_right` en cada callback reduce la latencia del callback de tiempo real. |
| **Comprobación `qt_loaded` en el hijo** | [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L310) y [`process_capture.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L127) | **Eliminar.** Es una aserción de desarrollo que recorre `sys.modules` al abrir el stream; basta con cubrirla en un test unitario. |
| **Modo en-proceso (`ADV_TG_CAPTURE_INPROC`)** | [`capture_worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L89-L107) y [`audio/capture.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture.py) | Si el release usa siempre `ProcessAudioCapture`, puede retirarse el flag de depuración `ADV_TG_CAPTURE_INPROC` y dejar un único camino de captura en producción. |

---

## 3. Eliminación de Código Muerto y Módulos Vacíos

1. **Eliminar [`HardwareAudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L136-L268) y su exportación en [`utils/__init__.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/__init__.py#L6):**
   - Son 133 líneas de código obsoleto que ningún módulo de la aplicación utiliza (sustituido por `adv_tg.audio`).
2. **Eliminar [`src/adv_tg/ui/null_engine.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/null_engine.py):**
   - `NullStreamWorker` sólo actúa como fallback en [`worker_factory.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/worker_factory.py#L36-L40) si `CaptureStreamWorker(config, parent=parent)` lanza una excepción en `__init__`, lo cual nunca ocurre porque `__init__` sólo asigna atributos en memoria.
3. **Eliminar [`src/adv_tg/ml/__init__.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ml/__init__.py):**
   - Paquete placeholder vacío (`__all__: list[str] = []`). No aporta funcionalidad al release v0.2.0.
4. **Eliminar el botón placeholder *"Buy me a coffee"* sin acción:**
   - En [`main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L766-L771) y [`_on_buy_coffee_clicked`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1715-L1717), el botón es visible en la barra superior pero al pulsarlo sólo escribe un log (`"'Buy me a coffee' placeholder button clicked"`) sin abrir ninguna URL. Debe conectarse a `QDesktopServices.openUrl(QUrl(...))` o retirarse para el release.

---

## 4. Optimizaciones de Rendimiento en Memoria y CPU

### 4.1. Evitar la doble copia por bloque en el IPC de captura de audio
- **Ubicación:** [`_ChildCapture.callback`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L159) y [`_ChildCapture._send_loop`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L248)
- **Problema:**
  1. En el callback se hace `indata[:, 0].tobytes()` (copia #1).
  2. En `_send_loop` se ejecuta `send(pack(self.seq_sent, host_ns, len(payload) // 4) + payload)`, lo que concatena dos objetos `bytes` y aloja un nuevo bloque completo en cada envío (copia #2).
- **Optimización:** Preasignar en el callback (o usar un `bytearray` de tamaño `FRAME_HEADER.size + n_bytes` donde se empaquete la cabecera en los primeros 20 bytes con `FRAME_HEADER.pack_into(buf, 0, ...)` y se copien las muestras directamente en `memoryview(buf)[20:]`). Así `send_bytes(buf)` transmite sin realojar ni concatenar.

### 4.2. Cálculo incremental de estadísticas en `MainWin` y preasignación de buffers de trazado
- **Ubicación:** [`MainWin._on_telemetry_ready`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L2325-L2342)
- **Problema:** En cada actualización de telemetría (4 Hz) se recorre toda la lista histórica `self._rate_history` tres veces (`max(...)`, `min(...)`, `np.mean(...)`) y se pasan listas dinámicas de Python a `PlotDataItem.setData(self._tape_times_even, self._tape_rate_even)`, obligando a `PyQtGraph` a convertir listas a `np.ndarray` en cada frame.
- **Optimización:**
  - Mantener variables escalares de sesión (`_rate_min`, `_rate_max`, `_rate_sum`, `_rate_count`) para actualización en $O(1)$.
  - Usar un `np.ndarray` preasignado al tamaño máximo de la sesión (`int(duration_s * 4) + 16`) y pasar vistas (`self._tape_buf[:n]`) a `setData()`.

### 4.3. Evitar conversiones innecesarias a `float64` en lectura de niveles y formas de onda
- **Ubicación:** [`_CaptureReader._emit_waveform`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L451) y [`_CaptureReader._emit_level`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/capture_worker.py#L468)
- **Problema:** `SampleStream` almacena las muestras en `np.float32`, pero tanto `_emit_waveform` (30 veces/s sobre 28 800 muestras a 48 kHz o 57 600 muestras a 96 kHz) como `_emit_level` (10 veces/s) piden `dtype=np.float64`, duplicando el ancho de banda de memoria y convirtiendo tipos en cada tick sólo para diezmar la onda o calcular pico/RMS.
- **Optimización:** Leer directamente en `dtype=np.float32` (`self._stream.read_latest(n, np.float32)`) y ajustar la firma de `minmax_decimate` para operar sobre `np.float32` / `NDArray[np.floating]`. En `_emit_level`, usar `np.dot(x, x)` sobre `float32` o un acumulador `dtype=np.float64` sin convertir el array de entrada.

### 4.4. Eliminar efectos secundarios en importación y llamadas triplicadas a `configure_pyqtgraph_gpu()`
- **Ubicación:** [`src/adv_tg/ui/main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L109-L113) y [`L197`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L197)
- **Optimización:**
  - Eliminar la llamada a nivel de módulo `configure_pyqtgraph_gpu()` en la línea 109 de `main_window.py`, dejando únicamente la llamada controlada en `main.py` o `MainWin.__init__`.
  - Cargar `CALIBER_PRESETS = load_watch_db()` de forma perezosa o dentro de `MainWin.__init__` para que importar `main_window` no realice operaciones de E/S en disco.
  - Hacer que `LoggerSetup.setup_logger()` no configure handlers ni cree carpetas hasta que `main()` lo inicialice explícitamente (usando `logging.getLogger(__name__)` estándar en cada módulo).

---

## 5. Limpieza de Configuración y Empaquetado para el Release

1. **Depurar [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml):**
   - Mover `PyOpenGL` y `PyOpenGL_accelerate` a un grupo opcional `[project.optional-dependencies] gpu = ["PyOpenGL", "PyOpenGL_accelerate"]`, ya que el renderizado GPU está deshabilitado por defecto.
   - Añadir `soundfile>=0.12` (que incluye wheels precompilados con `libsndfile` para Windows y macOS) o usar exclusivamente `scipy.io.wavfile` / `wave` para grabación WAV sin pérdidas.
   - Eliminar el segundo `"scipy"` duplicado dentro de `[project.optional-dependencies] dev`.
2. **Corregir la ruta de logs para instalaciones empaquetadas ([`LoggerSetup`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/logger_setup.py#L30-L32)):**
   - Reemplazar `Path(__file__).resolve().parents[3] / "logs"` por un directorio de usuario escribible (p. ej. `QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)` o `~/.adv_tg/logs` cuando el directorio raíz no sea un checkout de desarrollo).
3. **Limpiar archivos basura del árbol `src/` y `.github/`:**
   - Eliminar los archivos `.DS_Store` presentes en `src/.DS_Store`, `src/adv_tg/.DS_Store`, `src/adv_tg/ui/.DS_Store`, `src/adv_tg/utils/.DS_Store` y `.github/.DS_Store` antes de construir el sdist/wheel.
