# Advanced Timegrapher

Cronocomparador (timegrapher) por software para relojes mecánicos: escucha el tic-tac con un
micrófono y mostrará marcha (s/d), error de batimiento (ms) y amplitud (°), con una GUI en
PyQt6 + pyqtgraph. Desciende del proyecto anterior "Watch Escape Analyzer EVO".

## Estado (v0.2.0)

- El núcleo de medición (DSP) se está **reconstruyendo desde cero, por capas**. Ya existen la
  captura (fase 1, terminada) y el detector de batimientos (fase 2, en validación); las
  métricas (marcha, error de batimiento, amplitud) son la fase 3 y todavía no existen.
- La GUI tiene todos los paneles, exportaciones e idiomas (EN/ES). **Con un dispositivo de
  audio real muestra la señal pero todavía NO mide**: forma de onda cruda, nivel/saturación e
  ingesta de audio (`CaptureStreamWorker`), sin marcha, error de batimiento ni amplitud.
- El botón **MOCK** (barra superior) activa un motor simulado que genera datos realistas
  (marcha, error de batimiento, amplitud, forma de onda, deriva de reloj, calidad de señal)
  para probar la GUI completa. Mientras está activo se ve la insignia "MOCK" en la barra de
  estado y `[MOCK]` en el título. Los datos MOCK no son mediciones.

| Fase | Contenido | Estado |
|------|-----------|--------|
| 0 | Esqueleto: GUI, modo MOCK, Debugger, CI | **terminada** (v0.1.0) |
| 1 | Captura: flujo de muestras sin bloqueos + herramienta de consola; captura en proceso aparte (1d) y audio real en la GUI sin medir (1b) | **terminada**; validada con el CM18 (ingesta 100 %) |
| 2 | Detector de batimientos incremental (P1/P2/P3) + audio WAV/FLAC | **en validación**: pasa con sintético y con las grabaciones reales; quedan pendientes en el H-50 (ver [`docs/resultados_pruebas_fase2.md`](docs/resultados_pruebas_fase2.md)) |
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
│   ├── beat_detector.py   # BeatDetector: capa 2, P1/P2/P3 en índices absolutos
│   ├── beat_types.py      # Beat, BeatFlags y definiciones físicas (t_lift, amplitud)
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
│   ├── check_samples.py   # CLI: valida el detector con grabaciones reales (fase 2)
│   ├── detect_beats.py    # CLI: detecta batimientos en un WAV/FLAC (fase 2)
│   └── gil_probe.py       # banco de pruebas GIL / hilos / GUI (fases 1c-1d)
├── ui/
│   ├── __init__.py
│   ├── assets/            # flechas SVG de los spinbox/combos
│   ├── main_window.py     # ventana principal (MainWin)
│   ├── capture_worker.py  # CaptureStreamWorker: audio real en la GUI, sin medir (fase 1b)
│   ├── null_engine.py     # NullStreamWorker: respaldo si la captura no está disponible
│   ├── stream_worker_base.py  # BaseStreamWorker: contrato GUI <-> motor
│   └── worker_factory.py  # create_worker(device_id)
└── utils/
    ├── __init__.py
    ├── audio_files.py     # lectura/escritura WAV y FLAC (único módulo que usa soundfile)
    ├── audio_io.py        # enumeración de dispositivos, guardado FLAC/Ogg de la GUI
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
`--wav RUTA` o `--out RUTA` (guarda lo capturado al final en `.wav` o `.flac`;
`--wav-format int16|int24|float32`, por defecto WAV float32 y FLAC int24) y `--file RUTA`
(modo repetición sin micrófono: inyecta un WAV o FLAC en tiempo real).

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

## Audio: WAV y FLAC

Toda lectura y escritura de archivos de audio pasa por `adv_tg.utils.audio_files`
(`read_audio`, `iter_audio_blocks`, `write_audio`, `AudioWriter`), basado en `soundfile`.

| Formato | Subtipo por defecto | Motivo |
|---------|--------------------|--------|
| `.wav`  | `FLOAT` (32 bits flotante) | conserva la señal capturada sin pérdida |
| `.flac` | `PCM_24` | FLAC no admite flotante; 24 bits ≈ −144 dBFS de cuantización |

- El formato lo decide la extensión (sin distinguir mayúsculas). Otros subtipos: `PCM_16`,
  `PCM_24` (WAV y FLAC) y `FLOAT` (solo WAV).
- Si la señal supera [-1, 1] y el subtipo es PCM, se recorta de forma explícita y se emite un
  aviso (nunca en silencio).
- La escritura es atómica: se escribe un temporal en el mismo directorio y se renombra; un
  fallo o una interrupción no deja archivos corruptos.
- Lectura: `float64` en [-1, 1]; `select_channel` elige por defecto el canal de **mayor
  energía** (el promedio de canales puede cancelarse). Comentario/metadatos en WAV y FLAC.
- Grabar desde la consola: `python -m adv_tg.tools.capture_check --device N --secs 60 --out toma.flac`.
- Licencias: `soundfile` es BSD-3; la `libsndfile` que incluyen sus ruedas es LGPL-2.1 y se
  carga dinámicamente, compatible con la GPLv3 de este proyecto. Las ruedas para Windows,
  macOS (arm64 y x86_64) y Linux ya traen la biblioteca: no hace falta instalar nada más.

## Fase 2: detector de batimientos

`core/beat_detector.py` — `BeatDetector` convierte audio en batimientos (`core/beat_types.py`:
`Beat`, `BeatFlags`). Solo detecta: marcha, error de batimiento y amplitud son la fase 3.

**Definiciones** (únicas para todo el sistema, en `beat_types.py`):

- Frecuencia del volante `f = BPH/7200` Hz; periodo de batimiento `P = 3600/BPH` s.
- **P1** = desbloqueo (inicio del primer sub-evento), **P2** = impulso (~0,45·t_lift después),
  **P3** = caída (inicio del último sub-evento); `t_lift = P3 − P1`.
- `A = LA / (2·sin(π·f·t_lift))`. Aquí solo acota dónde buscar P1 y P3 (rango de amplitud
  configurable, por defecto 90–400°).
- Cada instante es el **inicio** del sub-evento (cruce de umbral interpolado sobre la
  envolvente) y se expresa como **índice absoluto de muestra** (float64, sub-muestra) en el
  reloj del ADC, el mismo del `SampleStream`. Nunca segundos de reloj de pared.
- El instante de referencia del modelo de fase es **P3** (el sonido más fuerte y estable) y,
  si falta, P1. P1/P3 sin evidencia quedan en `NaN` con su flag; nunca se rellenan con el
  modelo.

**Uso como biblioteca**: `process(chunk, start_index)` devuelve solo batimientos nuevos y
definitivos; `advance(sample_stream)` lee lo llegado al `SampleStream` y trata un
desbordamiento como hueco (`AFTER_GAP`, sin reiniciar `k`); `flush()` al final del archivo.
El resultado es idéntico sin importar el troceado (rejilla fija de 0,25 s anclada al índice
absoluto). Un detector por hilo (no es thread-safe).

**Herramientas**:

```bash
# Un archivo: resumen, CSV por batimiento, JSON, PNG con --plot (requiere el extra: pip install -e ".[plot]")
python -m adv_tg.tools.detect_beats toma.flac --bph 28800 --lift-angle 51 --csv b.csv --json s.json

# Validación con las grabaciones reales (en la Mac): docs/samples/*.wav|*.flac
python -m adv_tg.tools.check_samples
python -m adv_tg.tools.check_samples --dir otra/carpeta --manifest calibres.json
python -m adv_tg.tools.check_samples --self-test     # sin grabaciones: genera 3 sintéticos
```

`check_samples` asigna el calibre por el nombre del archivo (`c07`/`h50`/`hamilton` → 21600
BPH, LA 50°; `9015`/`miyota` → 28800 BPH, LA 51°; `nm10`/`bulova` → cuarzo, informativo) o
por un manifiesto JSON (`[{"pattern": "c07", "bph": 21600, "lift_angle": 50}]`), repite la
detección con otro troceado para comprobar la invarianza y guarda una tabla Markdown en
`logs/check_samples_<fecha>.md` con veredicto OK/REVISAR por archivo (conteo ±1 %, P3 en
≥ 95 % de los batimientos, tic/tac sin saltos de paridad, t_lift físico, invarianza).

**Rendimiento y exactitud** (sintético, 48 kHz): ~90–140× tiempo real en un núcleo; a
SNR ≥ 20 dB, 0 falsos y 0 perdidos; error de P3 tras restar el sesgo constante de ~2,1
muestras: mediana < 0,3 y p95 < 1 muestra a 25 dB.

**Límites conocidos**:

- SNR (pico de P3 sobre el ruido en banda) < ~17 dB: se pierden batimientos (≈20 % a 15 dB;
  quedan como huecos en `k`); a 10 dB no engancha y `stats` avisa. Nunca inventa batimientos.
- P1 débil (p. ej. ETA C07): P1 sale `NaN` con `P1_MISSING` y t_lift no se puede evaluar; P3
  y la cadencia no se ven afectados.
- Con P1 débil **y** amplitud baja (< ~230° a 21600 BPH) P2 puede caer en la ventana física
  de P1; se decide P1/P2 por la amplitud implícita más plausible (`typical_amplitude_deg`).
- Sub-eventos con SNR propio < 12 dB dan `NaN` en vez de un instante impreciso; con ruido de
  red, colas largas y SNR 20 dB, P1 puede tener hasta ~0,2 ms de incertidumbre.
- BPH mal configurado (> 6 % de diferencia): no engancha y avisa; la mitad del BPH real sí
  engancharía (contaría solo los tic o los tac).
- En el Hamilton H-50 real (P1 débil) con SNR bajo se observan P3 mal asignados (~7 % de los
  batimientos, residuo de +5 a +11 ms) y un t_lift bimodal (posible confusión P1/P2). Con una
  toma limpia la detección se corrige, pero la marcha según P1 y según P3 aún difiere ~2.5 s/d.
- El detector es invariante a la ganancia: normalizar no cambia nada. Limitar o comprimir los
  picos (o aplicar AGC) sesga la medida igual que la saturación; no se aplica en la ruta de
  medida (ver sección 3 del informe de pruebas).

**Validación con grabaciones reales** (`docs/samples/`, macOS, 24/10/2026; detalle en
[`docs/resultados_pruebas_fase2.md`](docs/resultados_pruebas_fase2.md)):

| Grabación | Encontrados / esperados | P1 / P3 | σ residuo | Veredicto `check_samples` |
|-----------|-------------------------|---------|-----------|---------------------------|
| Miyota 9015 (28800 BPH) | 245 / 244.1 | 98 % / 100 % | 631 µs | OK |
| Hamilton H-50 (21600 BPH), toma original saturada | 181 / 182.3 | 63 % / 100 % | 289 µs | OK |
| Hamilton H-50, toma nueva (pico −9.4 dBFS) | 180 / 179.5 | 97 % / 100 % | 388 µs | OK |
| Bulova NM10 (cuarzo) | 4738 / 4799.3 | 81 % / 84 % | 354 µs | INFO (no evalúa) |

- La precisión por batimiento en señal real (σ de cientos de µs) es mucho peor que en
  sintético (~5 µs).
- El "Periodo" que imprime `detect_beats` es una mediana de intervalos, **no** un estimador de
  marcha (en el cuarzo se desvía el equivalente a ~15 s/d); la marcha se calculará por
  regresión en la fase 3.

## Instalación

Requiere **Python >= 3.11**.

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip          # Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -e ".[plot]"         # opcional: gráficas de detect_beats --plot
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
