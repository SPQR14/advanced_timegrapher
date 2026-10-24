# Diagnóstico de Fallos en GitHub Actions para macOS y Windows (Python 3.12)

> **Alcance del diagnóstico:** Análisis cruzado de [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml), [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml) y el código fuente en [`src/adv_tg/`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg) para explicar por qué la matriz de pruebas en `macos-latest` y `windows-latest` con **Python 3.12** falla en GitHub Actions mientras que `ubuntu-latest` pasa.

---

## Resumen de las Causas Raíz (Ordenadas por Probabilidad e Impacto)

| # | Causa Raíz | SO afectado | Archivos implicados |
|---|---|---|---|
| **1** | **Ausencia de `QT_QPA_PLATFORM=offscreen` en macOS y Windows** (ejecución de PyQt6/OpenGL/QPrinter sin servidor de pantalla ni GPU) | macOS y Windows | [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L52-L55), [`main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L74-L109), [`gil_probe.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/gil_probe.py#L374-L400) |
| **2** | **Ausencia de `libsndfile` / `soundfile` y fallo de `ffmpeg -c:a libvorbis`** al codificar audio en runners no-Linux | macOS y Windows | [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L29-L35), [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L15-L23), [`audio_io.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L291-L569) |
| **3** | **Semántica de Named Pipes en Windows + sobrecarga de `multiprocessing` (`spawn`) con `pytest-cov` (`sys.monitoring` en Python 3.12)** | Windows y macOS (Py 3.12) | [`process_capture.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L262-L376), [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L353-L362), [`ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L55) |
| **4** | **Resolución del temporizador del SO (~15.6 ms en Windows) y pausas de hipervisor en macOS** frente a umbrales estrictos de tiempo real (`ingest_ratio >= 0.99`, `max_gap_ms`) | Windows y macOS | [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L53-L114), [`capture_check.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/capture_check.py#L41-L43), [`sample_stream.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/sample_stream.py#L292-L309) |
| **5** | **Bloqueo de archivos abiertos (`WinError 32`) por `faulthandler.log` y `RotatingFileHandler`** en Windows | Windows | [`main.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/main.py#L40-L47), [`logger_setup.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/logger_setup.py#L90-L117) |
| **6** | **Emisión de señales Qt desde `threading.Thread` de Python (`MockStreamWorker`) tras `disconnect()` / `deleteLater()` en Python 3.12 + PyQt6** | macOS y Windows (Py 3.12) | [`mock/worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/worker.py#L88-L116), [`main_window.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1321-L1340) |

---

## Análisis Técnico Detallado de Cada Causa

### Causa 1: Ejecución de PyQt6 sin `QT_QPA_PLATFORM=offscreen` en macOS y Windows
En [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L47-L55):
```yaml
- name: Run Pytest (Linux headless)
  if: runner.os == 'Linux'
  run: |
    xvfb-run -a pytest -v --cov=adv_tg

- name: Run Pytest (macOS and Windows)
  if: runner.os != 'Linux'
  run: |
    pytest -v --cov=adv_tg
```
- **Por qué falla en macOS y Windows:**
  1. **Rama de OpenGL en [`configure_pyqtgraph_gpu`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L74-L106):**
     ```python
     if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or not enable_opengl:
         pg.setConfigOptions(useOpenGL=False, enableExperimental=False, antialias=True)
         return False
     ```
     Cuando un test invoca `configure_pyqtgraph_gpu(enable_opengl=True)`, si `QT_QPA_PLATFORM` no es `"offscreen"`, el código ejecuta `QSurfaceFormat.setDefaultFormat(fmt)` y activa `pg.setConfigOptions(useOpenGL=True)`. En los runners headless de Windows Server (`windows-latest`, sesión 0 sin GPU física) y macOS (`macos-latest`, VM sin aceleración completa), inicializar contextos OpenGL en `PyQt6` con `PyOpenGL_accelerate` en Python 3.12 provoca **violaciones de acceso nativas (código de salida `0xC0000005` en Windows / `SIGSEGV` en macOS)** al instanciar `PlotWidget`.
  2. **Espera de exposición de ventana (`isExposed`) y captura de pantalla (`grab`):**
     En [`gil_probe.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/gil_probe.py#L391-L398), `_make_window(show=True)` entra en un bucle esperando hasta 5 segundos (`EXPOSE_TIMEOUT_S`) a que `win.windowHandle().isExposed()` sea `True`. En una sesión de CI sin monitor conectado o con el plugin de plataforma por defecto sin compositor activo, `isExposed()` puede no activarse nunca, marcando los escenarios GUI como fallidos (`NO REPRESENTATIVA`), o haciendo fallar [`MainWin._render_traces_image`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1719-L1725) (`self.graphs_splitter.grab()`).

---

### Causa 2: Fallo en `save_audio_recording` por falta de `libsndfile`, `portaudio` y `libvorbis` en macOS y Windows
En [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml#L29-L35), las dependencias de sistema sólo se instalan cuando `runner.os == 'Linux'`:
```yaml
- name: Install System Dependencies (Linux only)
  if: runner.os == 'Linux'
  run: |
    sudo apt-get update
    sudo apt-get install -y libportaudio2 xvfb ...
```
- **Qué ocurre en [`src/adv_tg/utils/audio_io.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L333-L569) en Windows y macOS:**
  1. `soundfile` **no está listado** en [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L15-L32), por lo que `import soundfile as sf` lanza `ModuleNotFoundError` en los 3 sistemas operativos.
  2. `_load_libsndfile()` busca la librería dinámica `libsndfile`. En `windows-latest` no existe `sndfile.dll`, y en `macos-latest` (ARM64) no se ejecuta `brew install libsndfile portaudio`, por lo que `_SNDFILE_LIB` resulta `None`.
  3. El código cae entonces al tercer respaldo: las utilidades CLI (`shutil.which("ffmpeg")` en [`is_flac_supported`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L355) e [`is_vorbis_supported`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L383)).
  4. Como los runners de GitHub Actions suelen tener `ffmpeg` preinstalado, `is_vorbis_supported()` devuelve `True`. Sin embargo, cuando un test solicita guardar en Ogg Vorbis (`prefer_flac=False` o extensión `.ogg`), [`_write_via_cli`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L476-L488) ejecuta:
     ```python
     codec = "flac" if target_fmt == "flac" else "libvorbis"
     cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_wav), "-c:a", codec, str(out_path)]
     ```
     Si la build de `ffmpeg` del runner no incluye `libvorbis` habilitado (o si `ffmpeg` no está en el `PATH` del entorno virtual de Python 3.12 en Windows), `_write_via_cli` devuelve `False` y [`save_audio_recording`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L567-L569) lanza `RuntimeError: Could not encode audio to FLAC or Ogg Vorbis`.

---

### Causa 3: Condición de carrera en Named Pipes de Windows y lentitud de `spawn` con `pytest-cov` en Python 3.12
[`ProcessAudioCapture`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L262-L286) utiliza `mp.get_context("spawn")` y tres tuberías unidireccionales (`ctx.Pipe(duplex=False)`).
1. **Cierre prematuro de Named Pipes en Windows (`WinError 109` / `EOFError` antes de vaciar el buffer):**
   - En [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L353-L362), al recibir `"stop"`, el proceso hijo detiene el hilo emisor, envía el evento final `_send_event(evt_conn, "stopped", final)` y **en la línea inmediatamente siguiente cierra `data_conn`, `evt_conn` y `ctrl_conn` y finaliza el proceso**.
   - En Linux/POSIX (`AF_UNIX`), los bytes ya escritos en el buffer del kernel siguen disponibles para `recv()` en el padre aunque el hijo haya cerrado el descriptor y terminado.
   - **En Windows (`win32`)**, `multiprocessing.Pipe` usa **Named Pipes de Windows**. Cuando el proceso servidor/hijo cierra su handle y el proceso muere antes de que el hilo receptor del padre ([`_receive_loop`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/process_capture.py#L356-L376)) haya drenado todos los mensajes en vuelo, Windows invalida el pipe con `BrokenPipeError` / `OSError: [WinError 109] The pipe has been ended`. Como resultado, en Windows el padre puede perder las últimas tramas de `data_conn` o no llegar a recibir nunca el mensaje `("stopped", final)`, quedando `self.callbacks == 0` o `frames_received < seq_sent` en pruebas rápidas.
2. **Sobrecarga de `spawn` + `pytest-cov` en Python 3.12 (`sys.monitoring`):**
   - En Python 3.12, `coverage.py` cambió su motor interno para usar `sys.monitoring` (PEP 669) e intercepta cada subproceso `spawn` mediante un hook `.pth`.
   - En `windows-latest` (con Windows Defender activo) y `macos-latest`, arrancar un nuevo intérprete Python 3.12 mediante `spawn`, inicializar `coverage`, ejecutar [`_bootstrap_windows_dlls()`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/__init__.py#L11-L42) e importar `numpy` dentro del hijo puede tardar **entre 1.5 s y 4.5 s**. Cualquier test que configure `open_timeout_s` ajustado o que lance múltiples subprocesos consecutivos (como los tests de `ProcessAudioCapture` o `gil_probe`) sufre timeouts en Python 3.12 bajo `--cov=adv_tg`.
   - Además, [`gil_probe._child`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/gil_probe.py#L653) termina el subproceso llamando a `os._exit(0)`, lo que impide que `coverage` cierre limpiamente su archivo de datos en Python 3.12.

---

### Causa 4: Granularidad de `time.sleep` / `Event.wait` en Windows (~15.6 ms) y Jitter de VM en macOS
En [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L53-L114) y [`capture_check.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/capture_check.py#L168-L189):
- `_FakeStream._run()` y `FileReplay._run()` marcan el ritmo de los bloques usando `self._stop.wait(wait)`. Para el bloque por defecto de `_FakeStream` (`FAKE_BLOCKSIZE = 512` a `48 000 Hz`), el periodo entre bloques es **10.67 ms**.
- Asimismo, `_ChildCapture._send_loop()` duerme `time.sleep(SENDER_IDLE_S)` con `SENDER_IDLE_S = 0.002` (**2 ms**) cuando la cola está vacía.
- **Por qué falla en Windows y macOS:**
  - En **Windows**, la resolución por defecto del temporizador del kernel (`NtDelayExecution` / `WaitForSingleObject`) es de **15.625 ms** (64 Hz). Una llamada a `time.sleep(0.002)` o `Event.wait(0.0106)` duerme en realidad **~15.6 ms a 31 ms**.
  - En [`SampleStream.stats()`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/core/sample_stream.py#L299-L301), `ingest_ratio` se calcula como:
    $$\text{ratio} = \frac{\text{total} - n_{\text{primer\_bloque}}}{f_s \cdot (t_{\text{último}} - t_{\text{primero}})}$$
    En pruebas de corta duración (p. ej. `0.2 s` a `1.0 s`), un único retraso de 15.6 ms del planificador de Windows o una pausa de hipervisor en `macos-latest` en el último bloque reduce `ingest_ratio` por debajo del umbral `MIN_INGEST = 0.99` (99 %) o dispara `max_gap_ms` por encima de `3 * block_ms` (`32 ms`), haciendo fallar `evaluate()` en [`capture_check.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/capture_check.py#L231-L252).

---

### Causa 5: Bloqueo de descriptores de archivo en Windows (`faulthandler.log` y `RotatingFileHandler`)
- En [`src/adv_tg/main.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/main.py#L40-L47):
  ```python
  def _enable_faulthandler() -> None:
      global _FAULT_LOG_FH
      LoggerSetup.LOG_DIR.mkdir(parents=True, exist_ok=True)
      fault_path = LoggerSetup.LOG_DIR / "faulthandler.log"
      _FAULT_LOG_FH = fault_path.open("a", encoding="utf-8")
      faulthandler.enable(file=_FAULT_LOG_FH, all_threads=True)
  ```
- Si las pruebas redirigen `LoggerSetup.LOG_DIR` a un directorio temporal (`tmp_path` de `pytest`) y llaman a `main()` o a `LoggerSetup.setup_logger()`, **ni `_FAULT_LOG_FH` se cierra antes de reabrirse ni `RotatingFileHandler` se cierra al terminar el test**. En Windows (NTFS), mantener un handle abierto impide renombrar, rotar o borrar el archivo/directorio temporal al finalizar el fixture (`PermissionError: [WinError 32]`).

---

### Causa 6: Emisión de señales PyQt6 desde `threading.Thread` nativo en Python 3.12 (`MockStreamWorker`)
- En [`src/adv_tg/mock/worker.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/mock/worker.py#L94-L97), `MockStreamWorker` (que es un `QObject`) lanza un `threading.Thread` estándar de Python (en lugar de un `QThread`) y emite señales `pyqtSignal` desde ese hilo Python (`waveform_ready`, `telemetry_ready`, `session_finished`).
- En [`MainWin._install_worker`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/ui/main_window.py#L1327-L1339), al cambiar de dispositivo o cerrar la ventana:
  ```python
  old.shutdown()
  if isinstance(old, MockStreamWorker):
      old.drift_detail.disconnect()
  for sig in (...):
      sig.disconnect()
  old.deleteLater()
  ```
- Con **Python 3.12 y PyQt6 (>= 6.6)** en macOS y Windows, si una señal no tiene slots conectados o si el hilo de Python sigue saliendo del bloque `finally:` (`self.session_finished.emit()`) mientras se ejecuta `disconnect()` / `deleteLater()`, PyQt6 en Python 3.12 es más estricto con el estado del GIL en hilos no creados por Qt (`QThread`) y con llamadas a `disconnect()` sin receptor válido (`TypeError: disconnect() failed between ...`), provocando fallos o crashes al desmontar tests de GUI.

---

## Plan de Corrección Recomendado

### 1. En [`.github/workflows/ci.yml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/.github/workflows/ci.yml)
1. **Definir `QT_QPA_PLATFORM: offscreen` globalmente** en el job `test` para los tres sistemas operativos (Linux, macOS y Windows).
2. **Instalar `soundfile` (que empaqueta `libsndfile` binario para Windows y macOS)** en `pyproject.toml`, o instalar `portaudio` y `libsndfile` en macOS (`brew install portaudio libsndfile`).
3. **Configurar `git` con `autocrlf: false`** en `actions/checkout@v4` para evitar fallos de `ruff format --check` en Windows por conversión `LF -> CRLF`.

```yaml
    env:
      QT_QPA_PLATFORM: offscreen
```

### 2. En el código de [`src/adv_tg/`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg)
1. **Sincronización de cierre en [`capture_process.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/audio/capture_process.py#L353-L362):** Antes de que el proceso hijo cierre `data_conn` y `evt_conn` y salga, esperar confirmación (EOF o lectura en `ctrl_conn`) tras enviar `("stopped", final)`, garantizando en Windows que el padre haya leído el evento `"stopped"` y todas las tramas de `data_conn` antes de que el Named Pipe se destruya.
2. **Eliminar `os._exit(0)` en [`gil_probe.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/tools/gil_probe.py#L653)** (usar `return 0` tras cerrar limpiamente el stream) para que `pytest-cov` en Python 3.12 no pierda ni corrompa el archivo `.coverage` del subproceso.
3. **Añadir `"soundfile>=0.12"` en [`pyproject.toml`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/pyproject.toml#L15-L23)** y cambiar el fallback de `save_audio_recording` en [`audio_io.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/utils/audio_io.py#L518-L569) a WAV sin pérdidas cuando FLAC/Ogg no estén soportados.
4. **Cerrar `_FAULT_LOG_FH` previo en [`main.py`](file:///Users/sqpr14/Desarrollo/advanced_timegrapher/src/adv_tg/main.py#L40-L47):** Si `_FAULT_LOG_FH` ya estaba abierto, llamar a `faulthandler.disable()` y `_FAULT_LOG_FH.close()` antes de abrir un nuevo archivo, evitando `WinError 32` en Windows.
