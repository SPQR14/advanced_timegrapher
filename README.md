# Advanced Timegrapher

Cronocomparador (timegrapher) por software para relojes mecánicos: escucha el tic-tac con un
micrófono y mostrará marcha (s/d), error de batimiento (ms) y amplitud (°), con una GUI en
PyQt6 + pyqtgraph. Desciende del proyecto anterior "Watch Escape Analyzer EVO".

## Estado (v0.1.0)

- El núcleo de medición (DSP) se está **reconstruyendo desde cero, por capas**. Todavía no existe.
- La GUI es el esqueleto: todos los paneles, exportaciones e idiomas (EN/ES) están, pero
  **con un dispositivo de audio real todavía NO mide**: al pulsar INICIAR se avisa
  "Motor de medición aún no implementado".
- El botón **MOCK** (barra superior) activa un motor simulado que genera datos realistas
  (marcha, error de batimiento, amplitud, forma de onda, deriva de reloj, calidad de señal)
  para probar la GUI completa. Mientras está activo se ve la insignia "MOCK" en la barra de
  estado y `[MOCK]` en el título. Los datos MOCK no son mediciones.

| Capa | Contenido | Estado |
|------|-----------|--------|
| 1 | Captura: flujo de muestras sin bloqueos + herramienta de consola | implementada, pendiente de validar con hardware |
| 2 | Detector de batimientos incremental | pendiente |
| 3 | Métricas: marcha, error de batimiento, amplitud | pendiente |
| 4 | Reloj: deriva del ADC vs host + NTP, calibración manual opcional | pendiente |
| 5 | Integración con la GUI (reemplaza `NullStreamWorker`; `MockStreamWorker` queda como herramienta de pruebas) | pendiente |

## Estructura

```text
src/adv_tg/
├── __init__.py            # versión + aislamiento de DLL de Qt en Windows
├── __main__.py            # python -m adv_tg
├── main.py                # punto de entrada (adv-tg)
├── audio/
│   ├── __init__.py
│   ├── capture.py         # AudioCapture: InputStream mono float32 -> SampleStream (en proceso)
│   ├── capture_process.py # proceso hijo de captura (sin Qt): callback -> deque -> tubería
│   ├── gil_stats.py       # estadística de espera por el GIL (compartida)
│   └── process_capture.py # ProcessAudioCapture: misma interfaz, captura en proceso aparte
├── core/
│   ├── __init__.py
│   ├── models.py          # WatchConfig, BeatEvent, BeatTelemetry, TelemetryResult
│   └── sample_stream.py   # SampleStream: buffer circular SPSC con índice absoluto
├── i18n/
│   ├── __init__.py
│   └── translator.py      # textos EN/ES
├── ml/__init__.py         # reservado
├── mock/
│   ├── __init__.py
│   ├── generator.py       # señal y telemetría simuladas (numpy, sin DSP)
│   └── worker.py          # MockStreamWorker (hilo propio)
├── tools/
│   ├── __init__.py
│   ├── README.md          # notas de las herramientas (gil_probe, hallazgos)
│   ├── capture_check.py   # CLI de verificación de la captura (fase 1)
│   └── gil_probe.py       # banco de pruebas GIL / hilos / GUI (fases 1c-1d)
├── ui/
│   ├── __init__.py
│   ├── assets/            # flechas SVG de los spinbox/combos
│   ├── main_window.py     # ventana principal (MainWin)
│   ├── null_engine.py     # NullStreamWorker: placeholder para hardware
│   ├── stream_worker_base.py  # BaseStreamWorker: contrato GUI <-> motor
│   └── worker_factory.py  # create_worker(device_id)
└── utils/
    ├── __init__.py
    ├── audio_io.py        # enumeración de dispositivos, guardado FLAC/Ogg
    ├── db/watch_db.csv    # calibres (BPH, ángulo de levantamiento)
    ├── debugger.py        # clase Debugger (trazas)
    ├── logger_setup.py    # configuración de logging
    └── watch_db.py        # carga del CSV de calibres
```

## Fase 1: captura

La capa 1 recibe el audio del micrófono sin perder muestras:

- `core/sample_stream.py` — `SampleStream`: buffer circular de muestras mono (float32) sin
  locks, con un único productor. Cada muestra tiene un **índice absoluto** (0, 1, 2…) en el
  dominio del reloj del ADC; además guarda, por bloque, el índice de fin y la hora del host.
- `audio/capture.py` — `AudioCapture`: abre un `sounddevice.InputStream` de 1 canal float32.
  El callback solo escribe en el `SampleStream` y cuenta eventos (sin logs, locks ni I/O).

Herramienta de verificación:

```bash
python -m adv_tg.tools.capture_check --list                 # lista dispositivos de entrada
python -m adv_tg.tools.capture_check --device N --secs 30   # prueba de 30 s con el dispositivo N
```

Opciones: `--fs` (48000), `--blocksize` (0 = lo elige PortAudio), `--latency low|high` (high),
`--wav RUTA` (guarda lo capturado al final; `--wav-format int16|float32`) y `--file RUTA.wav`
(modo repetición sin micrófono: inyecta un WAV en tiempo real).

Imprime una línea por segundo (muestras, ingesta, hueco máximo entre bloques, coste máximo del
callback, pico en dBFS y aviso de posible saturación si el pico ≥ -1 dBFS) y un veredicto:

| Criterio | Umbral |
|----------|--------|
| Ingesta (muestras recibidas / esperadas según el reloj del host) | ≥ 99 % |
| Hueco máximo entre bloques | < 3 bloques nominales (`blocksize/fs`, o el bloque típico observado si `blocksize=0`) |
| Duración máxima del callback | < 2000 µs |

`VEREDICTO: OK` (código de salida 0) significa que el dispositivo y la tubería entregan el
audio completo. `NO CUMPLE` (código 1) indica qué criterio falló y qué probar
(`--latency high`, `--blocksize 1024` o `4096`, otro dispositivo). Código 2: error de
dispositivo o de argumentos. Validado con el CM18 (ingesta 100 %).

### Captura en proceso aparte (fase 1d)

Mientras la GUI repinta, el hilo principal retiene el GIL en tramos largos (llamadas C/C++ de
Qt/pyqtgraph). Si el callback de audio vive en el mismo proceso, espera ese GIL hasta cientos
de ms y CoreAudio descarta bloques **sin avisar** (overflows = 0): en la GUI real se llegó a
recibir solo ~17–25 % del audio (diagnóstico con `gil_probe`, ver `src/adv_tg/tools/README.md`).
`sys.setswitchinterval` no ayuda y bloques más grandes solo mitigan.

Por eso la GUI captura con `ProcessAudioCapture`: el stream de PortAudio vive en un **proceso
hijo** (`multiprocessing`, contexto `spawn`) que no importa Qt y tiene su propio GIL. El hijo
envía los bloques por una tubería con su marca de tiempo `perf_counter_ns` (reloj común a todos
los procesos), y un hilo del proceso de la GUI los escribe en el `SampleStream`. Si la GUI se
congela, el hijo acumula y el padre se pone al día después, sin perder muestras
(`dropped_frames` debe ser 0).

- Forzar la captura en el mismo proceso (depuración): `ADV_TG_CAPTURE_INPROC=1 adv-tg`.
- Log del proceso hijo: `logs/adv_tg_capture.log` (archivo propio; nunca comparte handler con
  `logs/adv_tg.log`). Al parar, el resumen de sesión de `adv_tg.log` incluye `backlog máx`,
  `retraso del padre máx` y `tramas perdidas`.
- Verificación: `python -m adv_tg.tools.capture_check --isolated --device N --secs 30` y
  `python -m adv_tg.tools.gil_probe --all --isolated --device N --secs 20` (con la ventana de
  la GUI visible al frente).

## Instalación

Requiere **Python >= 3.11**.

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip          # Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install -e ".[dev]"
```

## Ejecución

```bash
adv-tg              # script instalado en el venv
python -m adv_tg    # equivalente
```

## Pruebas y lint

```bash
pytest                      # headless: tests/conftest.py fija QT_QPA_PLATFORM=offscreen
ruff check .
ruff format --check .
```

`ruff` no está en las dependencias `dev`; se usa el de `pre-commit` o uno instalado aparte.

## Depuración

- Log: `logs/adv_tg.log` en la raíz del repo (rotativo, 5 MB × 3 copias; incluye el nombre del
  hilo). Los fallos nativos (segfaults) van a `logs/faulthandler.log`. El proceso hijo de
  captura escribe en `logs/adv_tg_capture.log`.
- Por defecto la consola muestra INFO y el archivo DEBUG. Con `ADV_TG_DEBUG=1` la consola
  también muestra DEBUG.
- `adv_tg.utils.debugger.Debugger` (`Debugger.get(__name__)`): `phase()` (inicio/fin con
  duración), `state_change()`, `process_start()/process_end()` (hilos, workers, timers),
  `file_read()/file_write()`, el decorador `traced` y `throttled()` para rutas frecuentes.
- Regla: **prohibido** loguear, bloquear o asignar memoria en callbacks de audio o bucles por
  muestra; en slots de alta frecuencia solo `throttled` a DEBUG.

## Windows

Al importar el paquete se aíslan las DLL de Qt del venv frente a otras instalaciones
(MiKTeX, Anaconda…): ver `_bootstrap_windows_dlls()` en `src/adv_tg/__init__.py`.

## Licencia

GNU General Public License v3.0 (ver `LICENSE`). El código de otros timegraphers con licencia
incompatible (p. ej. `tg`, GPLv2-only) no se copia ni se porta: el núcleo es implementación
propia.
