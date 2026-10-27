# Advanced Timegrapher: memoria técnica (beta 1)

Documento técnico del estado del proyecto al publicar la primera beta (v0.6.0). El README de
usuario está en la raíz del repositorio.

Cronocomparador (timegrapher) por software para relojes mecánicos: escucha el tic-tac con un
micrófono y mostrará marcha (s/d), error de batimiento (ms) y amplitud (°), con una GUI en
PyQt6 + pyqtgraph. Desciende del proyecto anterior "Watch Escape Analyzer EVO".

## Estado (v0.6.0)

- Primera versión beta pública (ver "Beta 1: cambios para la publicación").

- El núcleo de medición (DSP) se ha **reconstruido desde cero, por capas**: captura (fase 1),
  detector de batimientos (fase 2), métricas (fase 3) y corrección del reloj de la tarjeta de
  sonido (fase 4). Desde la fase 5 **la GUI mide con un dispositivo real** (probada con el CM18
  y un Seiko 4R36): marcha, error de batimiento, amplitud, deriva del reloj,
  calidad de la señal y grabación de la sesión (ver "Fase 5").
- Las mismas capas se usan desde la consola (`python -m adv_tg.tools.*`).
- La deriva del reloj de la tarjeta (ppm) se calibra por dispositivo y viaja dentro de cada
  grabación, de modo que `measure` corrige la marcha sola (ver "Fase 4").
- El botón **☕ Buy me a coffee** abre https://buymeacoffee.com/sqpr14 en el navegador.
- La GUI tiene todos los paneles, exportaciones e idiomas (EN/ES).
- El botón **MOCK** (barra superior, desactivado al arrancar) activa un motor simulado que genera datos realistas
  (marcha, error de batimiento, amplitud, forma de onda, deriva de reloj, calidad de señal)
  para probar la GUI completa. Mientras está activo se ve la insignia "MOCK" en la barra de
  estado y `[MOCK]` en el título. Los datos MOCK no son mediciones.

| Fase | Contenido | Estado |
|------|-----------|--------|
| 0 | Esqueleto: GUI, modo MOCK, Debugger, CI | **terminada** (v0.1.0) |
| 1 | Captura: flujo de muestras sin bloqueos + herramienta de consola; captura en proceso aparte (1d) y audio real en la GUI sin medir (1b) | **terminada**; validada con el CM18 (ingesta 100 %) |
| 2 | Detector de batimientos incremental (P1/P2/P3), alineación fina por plantillas de tic y tac (`t_fine`) + audio WAV/FLAC | **terminada**: validado con sintético y con 5 grabaciones reales; frente a tg queda a ≤ 1.4 s/d y ≤ 0.25 ms (ver [`docs/resultados_pruebas_fase2.md`](resultados_pruebas_fase2.md), sección 6) |
| 3 | Métricas: marcha, error de batimiento, amplitud (`core/metrics.py`, `tools/measure.py`) | **implementada**, pendiente de confirmación: sintético dentro de tolerancia y grabaciones reales dentro de la banda de tg/WAM (ver [`docs/fase3_resultados.md`](fase3_resultados.md)) |
| 4 | Reloj: deriva del ADC frente al reloj del host + NTP, referencias externas (PPS/GPS, cuarzo), calibración manual, calibraciones por dispositivo y etiquetas en las grabaciones | **terminada** (v0.5.0); quedan la herramienta de medida en vivo y la validación con el Bulova (ver [`docs/resultados_fase4.md`](resultados_fase4.md), sección 8) |
| 5 | Integración completa: motor real en la GUI (`MeasureStreamWorker`), análisis en proceso aparte, barra de calidad, autoescala del papel, cifras ocultas o atenuadas por confianza, grabación de la sesión | **terminada** (v0.6.0): 4 actualizaciones/s y 30 FPS; probada con el CM18 (ver [`docs/resultados_fase5.md`](resultados_fase5.md), sección 7) |
| 6 | Beta pública: validación con los reportes de los usuarios, empaquetado (macOS, Windows, Linux) | **en curso** |

### Versiones

Cada versión menor cierra una fase. La versión vive en `pyproject.toml` y en
`src/adv_tg/__init__.py` (`__version__`); `tests/test_smoke.py` comprueba que coincidan, así que
después de cambiarla hay que reinstalar el paquete (`.venv/bin/pip install -e .`). Cada versión
lleva su etiqueta de git (`vX.Y.Z`).

| Versión | Fecha | Contenido |
|---------|-------|-----------|
| v0.1.0 | 23/10/2026 | Fase 0: esqueleto, GUI, modo MOCK, Debugger, CI |
| v0.2.0 | 23/10/2026 | Fase 1: captura de audio (`SampleStream`, `AudioCapture`, `capture_check`) |
| v0.3.0 | 24/10/2026 | Fase 2: detector de batimientos, audio WAV/FLAC |
| v0.4.0 | 24/10/2026 | Fase 3: métricas (marcha, error de batimiento, amplitud) |
| v0.5.0 | 26/10/2026 | Fase 4: reloj de la tarjeta (ppm ± σ, niveles de confianza, PPS/GPS, calibraciones por dispositivo, etiquetas en FLAC/WAV); botón "Buy me a coffee" |
| v0.6.0 | 26/10/2026 | Fase 5: medida real en la GUI; beta 1 (modo cuarzo, calibración manual, "Acerca de", listas de duración y estabilización, limpieza) |

### Beta 1: cambios para la publicación

- **Duración y estabilización** como listas: duración 30 s, 45 s, 60 s (por defecto), 120 s,
  240 s, 5 min, 15 min, 30 min y 1 h; estabilización 10 s (por defecto), 15 s y 20 s.
  `MainWin.set_session_timing()` admite otros valores para automatizar (tests, `gil_probe`).
- **Modo cuarzo** (casilla "Cuarzo"): la lista de A/h pasa a pasos por segundo del segundero
  (1, 2, 4, 8 y 16/s; se guardan como pasos por hora), sin ángulo de levantamiento ni calibres;
  solo se mide la marcha (`WatchConfig.quartz` → `SessionConfig.quartz`). Las grabaciones
  llevan `ADV_TG_QUARTZ=1`.
- **Calibración manual del reloj** ("Calibrar reloj…" bajo las estadísticas,
  `ui/dialogs.py`): muestra la calibración que se aplica y el historial del dispositivo,
  guarda un valor manual (nivel 3) o la estimación en vivo de la última sesión (nivel 2) y
  borra las calibraciones. Mismo archivo que `tools/adc_calibration`.
- **Acerca de**: versión, licencia, repositorio, versiones de Python/Qt, sistema y carpetas de
  ajustes y registros (lo que pide un reporte de fallo).
- **Limpieza**: `utils/audio_io.py` queda solo con la enumeración de dispositivos (fuera
  `HardwareAudioCapture`, la carga de libsndfile por ctypes, los codificadores por línea de
  comandos y el guardado Ogg); la GUI ya no tiene la ruta de grabación antigua; 8 textos sin
  uso eliminados de la traducción.

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
│   ├── analysis_process.py # análisis en vivo en un proceso aparte (fase 5)
│   ├── file_replay.py     # FileReplay: un archivo reproducido en tiempo real (sin hardware)
│   └── process_capture.py # ProcessAudioCapture: misma interfaz, captura en proceso aparte
├── core/
│   ├── __init__.py
│   ├── adc_clock.py       # capa 4: deriva del reloj del ADC (ppm ± σ), PPS, etiquetas
│   ├── beat_detector.py   # BeatDetector: capa 2, P1/P2/P3 en índices absolutos
│   ├── beat_types.py      # Beat, BeatFlags, ParityTemplate y definiciones físicas
│   ├── metrics.py         # capa 3: marcha, error de batimiento, amplitud, tendencia
│   ├── models.py          # WatchConfig, BeatEvent, BeatTelemetry, TelemetryResult
│   ├── sample_stream.py   # SampleStream: buffer circular SPSC con índice absoluto
│   └── session.py         # capa 5: MeasurementSession (detector + métricas + reloj en vivo)
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
│   ├── measure.py         # CLI: marcha, error de batimiento y amplitud de un WAV/FLAC (fase 3)
│   ├── adc_calibration.py # CLI: calibraciones del reloj de la tarjeta por dispositivo (fase 4)
│   └── gil_probe.py       # banco de pruebas GIL / hilos / GUI (fases 1c-1d)
├── ui/
│   ├── __init__.py
│   ├── assets/            # flechas SVG de los spinbox/combos
│   ├── main_window.py     # ventana principal (MainWin)
│   ├── capture_worker.py  # CaptureStreamWorker: audio real en la GUI, sin medir (fase 1b)
│   ├── measure_worker.py  # MeasureStreamWorker: motor real de la GUI (fase 5)
│   ├── null_engine.py     # NullStreamWorker: respaldo si la captura no está disponible
│   ├── stream_worker_base.py  # BaseStreamWorker: contrato GUI <-> motor
│   └── worker_factory.py  # create_worker(device_id)
└── utils/
    ├── __init__.py
    ├── adc_calibration.py # calibraciones del reloj por dispositivo (JSON del usuario)
    ├── audio_files.py     # lectura/escritura WAV y FLAC + etiquetas (único módulo que usa soundfile)
    ├── audio_io.py        # enumeración de dispositivos de entrada
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
- Etiquetas `KEY=value` (`read_tags`, `update_tags`, parámetro `tags=`): en FLAC son comentarios
  Vorbis de verdad (`metaflac --list` los muestra), escritos sin dependencias nuevas; en WAV van
  como líneas `KEY=value` del comentario. Las grabaciones de `capture_check` llevan el
  dispositivo, la fecha y la calibración del reloj de la tarjeta (`ADV_TG_ADC_DRIFT_*`).
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

**Alineación fina por plantillas** (`Beat.t_fine`, `BeatDetector.templates()`): tic y tac
pueden sonar distinto (p. ej. en el H-50 la caída del tac tiene dos lóbulos y el más fuerte
cambia de un batimiento a otro), y entonces los inicios por batimiento miden tic y tac en
puntos físicos distintos: un error de batimiento falso y un residuo ruidoso. El detector
mantiene una plantilla de envolvente por paridad de `k` (mediana de los últimos 24 batimientos
alineados), alinea cada batimiento con la suya por correlación (solo sobre la caída) y mide
los inicios de P1 y P3 una sola vez, sobre la plantilla limpia. `t_fine` es ese inicio de P3
llevado a cada batimiento: el mismo punto físico en tic y tac. La fase 3 calculará la marcha
con `t_fine` (regresión ponderada por paridad), el error de batimiento con los P1 de las
plantillas y la amplitud con su t_lift (inicio de P1 → inicio de P3).

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
BPH, LA 50°; `9015`/`miyota` → 28800 BPH, LA 51°; `4r36`/`seiko` → 21600 BPH, LA 53°;
`nm10`/`bulova`, `vh31`/`tmi` y `h874`/`citizen` → cuartos de 16, 4 y 1 paso por segundo,
informativos) o
por un manifiesto JSON (`[{"pattern": "c07", "bph": 21600, "lift_angle": 50}]`), repite la
detección con otro troceado para comprobar la invarianza y guarda una tabla Markdown en
`logs/check_samples_<fecha>.md` con veredicto OK/REVISAR por archivo (conteo ±1 %, P3 en
≥ 95 % de los batimientos, tic/tac sin saltos de paridad, t_lift físico, invarianza, `t_fine`
en ≥ 80 % de los batimientos y t_lift de las plantillas en el rango físico).

**Rendimiento y exactitud** (sintético, 48 kHz): ~95–120× tiempo real en un núcleo; a
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
- Los P1/P2/P3 por batimiento siguen siendo frágiles cuando tic y tac suenan distinto (H-50:
  P3 en un lóbulo tardío, P1 desplazado); por eso las métricas usan `t_fine` y las plantillas,
  no los inicios por batimiento.
- La paridad con la caída ambigua (tac del H-50 y del 4R36) tiene un jitter de 500–800 µs,
  frente a 110–220 µs de la limpia: la fase 3 debe ponderar por paridad.
- `t_fine` necesita un calentamiento: ~6 batimientos hasta que el modelo de fase madura y 6
  más por paridad hasta que madura su plantilla.
- El detector es invariante a la ganancia: normalizar no cambia nada. Limitar o comprimir los
  picos (o aplicar AGC) sesga la medida igual que la saturación; no se aplica en la ruta de
  medida (ver sección 3 del informe de pruebas).

**Validación con grabaciones reales** (`docs/samples/`, macOS, 24/10/2026; detalle en
[`docs/resultados_pruebas_fase2.md`](resultados_pruebas_fase2.md)):

| Grabación | Encontrados / esperados | P1 / P3 | σ residuo | Veredicto `check_samples` |
|-----------|-------------------------|---------|-----------|---------------------------|
| Miyota 9015 (28800 BPH) | 245 / 244.1 | 98 % / 100 % | 631 µs | OK |
| Seiko 4R36 (21600 BPH) | 181 / 180.1 | 97 % / 99 % | 217 µs | OK |
| Hamilton H-50 (21600 BPH), toma original saturada | 181 / 182.3 | 63 % / 100 % | 289 µs | OK |
| Hamilton H-50, toma nueva (pico −9.4 dBFS) | 180 / 179.5 | 97 % / 100 % | 388 µs | OK |
| Bulova NM10 (cuarzo) | 4738 / 4799.3 | 81 % / 84 % | 354 µs | INFO (no evalúa) |

- La precisión por batimiento en señal real (σ de cientos de µs) es mucho peor que en
  sintético (~5 µs); con `t_fine` baja a 110–220 µs en las paridades limpias.
- Frente a tg 0.8.0 y Watch Accuracy Meter (`docs/samples/tests.csv`; con `t_fine`, regresión
  ponderada por paridad y plantillas): error de batimiento y amplitud dentro de banda (±0.15 ms,
  ±12°) en los cuatro calibres mecánicos; marcha dentro de ±1.5 s/d salvo el 4R36, cuya
  grabación tiene otra marcha que la medida en vivo (el reloj se asentaba durante la toma). El
  Bulova reproduce la deriva de la tarjeta que mide tg (−0.71 frente a −0.72 s/d).
- Inicio de P3 en la plantilla: por nivel (cruce hacia atrás desde el primer lóbulo); si tic y
  tac discrepan en amplitud más de 20° (no es físico), se usa el pie del primer frente abrupto
  donde haga falta (rampa lenta antes de la caída, como el tac del 4R36).
- El "Periodo" que imprime `detect_beats` es una mediana de intervalos, **no** un estimador de
  marcha (en el cuarzo se desvía el equivalente a ~15 s/d); la marcha se calculará por
  regresión en la fase 3.

## Fase 3: métricas

`core/metrics.py` calcula, a partir de los batimientos y las plantillas del detector (nunca del
audio):

- **Marcha (s/d):**
  - regresión robusta `t = a + P·k + c·(−1)^k` sobre `t_fine`, ponderando cada paridad con
    1/σ² y recortando a 5σ (MAD) por paridad;
  - incertidumbre por covarianza y por *jackknife* de bloques (más realista en grabaciones
    reales);
  - marcha por ventanas (10 s; solapadas con `window_step_s`) y tendencia en s/d por minuto,
    con aviso si supera 3σ;
  - corrección del reloj de la tarjeta (`adc_drift_ppm` y su σ, fase 4; con 0 se avisa de
    "sin calibrar").
- **Error de batimiento (ms):** en los inicios de P1 de las plantillas, 2·|c| de la misma
  regresión.
- **Amplitud (°):** con el t_lift de las plantillas (inicio de P1 → inicio de P3); tic, tac y
  media, ±1° de LA declarado, orientativa si el LA es el de por defecto.
- **Exclusiones:** batimientos `SATURATED` y los que no tienen `t_fine` (los `TIMING_OUTLIER`
  se conservan: esa marca juzga el P3 de cada batimiento, no `t_fine`); recorte robusto por
  paridad; mínimo de 10 por paridad.
- **Incertidumbre reportada:** la mayor entre la de covarianza y la de *jackknife* por bloques
  (el ruido temporal real no es blanco; en las grabaciones es 2–4 veces mayor).
- **Modos:** por lotes (`compute_metrics`) e incremental (`MetricsTracker`, ventana
  deslizante), con resultados idénticos.

```bash
python -m adv_tg.tools.measure toma.flac --bph 28800 --lift-angle 51 --json m.json --csv v.csv --plot m.png
python -m adv_tg.tools.measure cuarzo.flac --bph 57600 --quartz          # cuarzo: solo marcha, un evento por paso
python -m adv_tg.tools.measure vieja.flac --bph 28800 --device "CM18 USB" # usa la calibración guardada del CM18
```

Corrección del reloj de la tarjeta (fase 4), por orden: `--adc-drift-ppm`/`--adc-drift-sigma-ppm`
(`--adc-drift-ppm 0` la desactiva); si no, la de las etiquetas del archivo o la guardada para
`--device` (la de menor incertidumbre); si no, sin calibrar.

```bash
python -m adv_tg.tools.adc_calibration set --device "CM18 USB" --ppm 8.3 --sigma 6 --note "Bulova NM10"
python -m adv_tg.tools.adc_calibration list                       # historial y la que se aplica hoy
python -m adv_tg.tools.adc_calibration show toma.flac             # etiquetas ADV_TG_* de una grabación
python -m adv_tg.tools.adc_calibration tag vieja.flac --device "CM18 USB" --date 2026-10-20
```

Validación (detalle en [`docs/fase3_resultados.md`](fase3_resultados.md)):

- **Sintético:** marcha ±0.2 s/d, error de batimiento ±0.03 ms y amplitud ±2 % en limpio
  (18000–36000 BPH); márgenes de 3 a 10 veces también con realismo, dos lóbulos y rampa.
- **Grabaciones reales:** dentro de la banda de tg 0.8.0 y Watch Accuracy Meter (±1.5 s/d,
  ±0.15 ms, ±12°) salvo la marcha del 4R36, cuya grabación tiene otra marcha que la medida en
  vivo.
- **Rendimiento:** ~110–128× el tiempo real con detector y métricas en relojes mecánicos y
  ~100–160× con cuartos.
- **Cuartos de referencia** (Bulova 16/s, TMI VH31 4/s y Citizen H874 1/s; tomas de 300 s, con
  `--quartz`): los tres reproducen la deriva de la tarjeta medida por tg (−0.72 s/d) dentro de
  su especificación (0.00, +0.17 y +0.22 s/d de diferencia). El Bulova muestra la compensación
  por inhibición de 10 s del cuarzo: la marcha de un cuarzo debe medirse sobre muchos ciclos
  (`docs/fase3_resultados.md`, secciones 7 y 8).
- **Incertidumbre de la marcha por ventanas:** la σ de covarianza por un factor de ruido
  correlacionado (`window_sigma_factor`, ≥ 1).

## Fase 4: reloj de la tarjeta de sonido

La marcha se mide contando muestras, así que hereda el error del cristal de la tarjeta:
1 ppm = 0.0864 s/d. El CM18 va a unos +8 ppm (≈ −0.7 s/d de sesgo); una tarjeta USB típica
puede llegar a ±30–50 ppm (±2.6–4.3 s/d). Detalle completo en
[`docs/resultados_fase4.md`](resultados_fase4.md).

- `core/adc_clock.py`: estima los ppm ± σ comparando el contador de muestras con el reloj del
  host (envolvente inferior de las marcas de cada bloque, robusta a retrasos y saltos de reloj)
  o con una referencia externa (`drift_from_ticks`: PPS de un GPS, cuarzo de error conocido;
  `detect_pps_edges`: PPS eléctrico por entrada de línea).
- Niveles de confianza: 0 sin calibrar (±30 ppm declarados), 1 reloj del host sin disciplinar,
  2 reloj disciplinado por NTP, 3 referencia externa o calibración manual.
- `utils/adc_calibration.py`: calibraciones guardadas por dispositivo y tasa de muestreo
  (macOS: `~/Library/Application Support/adv_tg/adc_calibration.json`; `ADV_TG_CONFIG_DIR` la
  cambia). Se aplica la de menor incertidumbre tras sumar el envejecimiento del cristal
  (1 ppm/año).
- Las grabaciones de `capture_check` llevan la calibración en sus etiquetas (`ADV_TG_ADC_DRIFT_*`);
  `measure` la aplica sola. Orden: `--adc-drift-ppm`/`--adc-drift-sigma-ppm`
  (`--adc-drift-ppm 0` la desactiva); si no, la de menor incertidumbre entre las etiquetas del
  archivo y la guardada para `--device`; si no, sin calibrar.

```bash
python -m adv_tg.tools.adc_calibration set --device "CM18 USB" --ppm 8.3 --sigma 6 --note "Bulova NM10"
python -m adv_tg.tools.adc_calibration list                       # historial y la que se aplica hoy
python -m adv_tg.tools.adc_calibration show toma.flac             # etiquetas ADV_TG_* de una grabación
python -m adv_tg.tools.adc_calibration tag vieja.flac --device "CM18 USB" --date 2026-10-20
```

Pendiente: herramienta de medida en vivo (`python -m adv_tg.tools.adc_clock`) y validación con
el Bulova (≈ +8 ppm con el CM18). Para la referencia PPS acústica basta un GPS con salida PPS y
un zumbador pegado al micrófono (`docs/resultados_fase4.md`, secciones 2.3 y 8.3).

## Fase 5: integración completa

Con un dispositivo de audio la GUI usa `MeasureStreamWorker` (`ui/measure_worker.py`): la
captura de la fase 1 más una `MeasurementSession` (`core/session.py`) que, 4 veces por segundo,
une detector, métricas de los últimos 30 s y reloj. El análisis corre en un proceso aparte
(`audio/analysis_process.py`) para no frenar la interfaz; `ADV_TG_ANALYSIS_INPROC=1` lo deja en
el proceso de la GUI. La aplicación arranca con la entrada de audio por defecto del sistema;
el MOCK solo se activa con su botón (o si no hay ningún dispositivo). Detalle en
[`docs/resultados_fase5.md`](resultados_fase5.md).

- **Cifras**: mediana de las últimas 7 actualizaciones; ocultas (`---.-`) con confianza < 0.15
  y en gris con < 0.30. Solo las visibles entran en el papel, las estadísticas y el CSV.
- **Barra de calidad**: nivel de entrada hasta que el detector engancha; después SNR ×
  fracción de batimientos útiles, en rojo si hubo saturación (`⚠`).
- **Papel** con autoescala (se pausa si lo mueves; "Center Trace" la reanuda) y
  **osciloscopio** con P1/P2/P3 reales.
- **Reloj**: la deriva aplicada (calibración guardada o estimación en vivo de nivel 2) con
  su ± σ y su nivel.
- **Grabación**: cada sesión se graba en un FLAC temporal con etiquetas; "Record Audio" lo
  guarda.

Probar la GUI sin micrófono, con una grabación (su frecuencia de muestreo debe coincidir con
la elegida):

```bash
ADV_TG_REPLAY_FILE=docs/samples/miyota_9015.flac adv-tg   # elegir cualquier dispositivo, 28800 BPH, 51°
```

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

- Carpeta de logs (`LoggerSetup.LOG_DIR`, `utils/logger_setup.default_log_dir`): desde la beta 1
  ya no es `logs/` del repositorio sino la del usuario: `~/Library/Logs/adv_tg` (macOS),
  `%LOCALAPPDATA%\adv_tg\logs` (Windows) o `~/.local/state/adv_tg/logs` (Linux).
  `ADV_TG_LOG_DIR=logs adv-tg` la devuelve al repositorio mientras se desarrolla; los tests usan
  una carpeta temporal. "Acerca de" muestra la ruta.
- Archivos: `adv_tg.log` (rotativo, 5 MB × 3 copias; incluye el nombre del hilo),
  `faulthandler.log` (fallos nativos), `adv_tg_capture.log` (proceso de captura) y
  `adv_tg_analysis.log` (proceso de análisis). En el texto de las fases, `logs/…` se refiere a
  esa carpeta.
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

## Después de la beta 1: C++, sin SciPy, pruebas largas

Rama `feature/UI_comprehensive_paths_CPP_optimizations` (26/10/2026).

### Núcleo DSP sin SciPy (`src/adv_tg/dsp/`)

- `filters.py` (numpy): diseño Butterworth paso banda en secciones SOS (`butter_bandpass_sos`),
  `sosfilt_zi` y la regla de relleno de `sosfiltfilt`. Es un port línea a línea de SciPy 1.18
  (`buttap`, `lp2bp_zpk`, `bilinear_zpk`, `zpk2sos` con emparejamiento `"nearest"`): coeficientes
  **idénticos** (diferencia 0) en 96 configuraciones (44.1–192 kHz, órdenes 1–6, 4 bandas).
- `native_dsp.cpp` (pybind11, `setup.py`): envolvente de cada celda (filtro de fase cero, FIR de
  Hilbert y media móvil), búsqueda de sub-eventos (con la regla de mesetas de
  `scipy.signal.find_peaks` y su respaldo por `argmax` solo si no hay ningún máximo local),
  `sosfilt` y `sosfiltfilt`. Libera el GIL.
- `engine.py`: usa el C++; sin la extensión recurre a SciPy si está instalado
  (`DspBackendError` si tampoco). SciPy queda solo como dependencia de desarrollo (referencia
  de los tests) y el `.spec` lo excluye del paquete (~98 MB menos en el entorno).
- **Noise gate**: toda división cuyo denominador sea < `GATE` = 1e-12 cuenta como cero (pendiente
  de la interpolación del inicio, `a0` de cada sección). Compilación con `-ffast-math
  -fno-finite-math-only` (NaN/inf conservan su sentido); en Windows `/fp:fast`.
- Verificación: las 5 grabaciones dan **los mismos batimientos** que el detector original con
  SciPy (máx. Δ`t_fine` 2.3e-10 muestras, Δp3 0); el detector va un 12–19 % más rápido.
- Requisito nuevo: instalar desde el código necesita un compilador de C++ (README).
- Eliminado: `BeatDetector._split` (ahora en `engine`) y `correlate_burst` (sin uso).

### Pruebas largas y gráficas

- Duraciones de 12 s a **1 h** (se quitaron 2, 12 y 24 h hasta optimizar el resto).
- **Grabación** solo en pruebas de hasta 240 s (`RECORDING_MAX_DURATION_S`): botón desactivado
  con explicación, el motor no crea el FLAC temporal (`MeasureStreamWorker.set_recording_enabled`)
  y el MOCK ya no puede generar en memoria el audio de una prueba larga.
- **Cinta de papel**: trazo de 1 px, *downsampling* por picos y recorte a la vista; símbolos
  solo hasta 240 puntos por curva (1 min). Redibujado con 1 h de datos: **7 ms** (antes 1.8 s;
  un trazo de 1.8 px con antialiasing costaba 1.5 s aun con *downsampling*).
- Carpetas de registros y calibraciones: solo en el menú **Herramientas** y en **Acerca de**
  (función común `ui.dialogs.open_folder`).

### Perfil del análisis en vivo (4R36, con el C++)

| Etapa | % del tiempo |
|---|---|
| Métricas (`compute_metrics`), de ello `fit_parity_weighted` 55 % | 60 % |
| Detector: seguimiento 13 %, alineación fina 9 %, sub-eventos 9 %, envolvente 8.5 %, suelo de ruido 7.6 % | 32 % |
| Forma de onda del osciloscopio | 5 % |

Siguiente candidato a C++ si hiciera falta: el ajuste robusto de las métricas
(`fit_parity_weighted`).
