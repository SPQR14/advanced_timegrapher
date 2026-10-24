# Resultados fase 2 (Windows)

Detector de batimientos (capa 2) y soporte de audio WAV/FLAC, verificados en Windows con
señal sintética. Rama `feature/detection`. Sin commits ni push; git solo en lectura
(`status`, `diff`, `log`).

Falta la validación con grabaciones reales (ver "Qué no se pudo verificar").

## Estado del entorno

- **`docs/samples/`**: no existe en esta máquina. El directorio en sí no está ignorado, pero
  `.gitignore` ignora `*.wav` y `*.flac` en todo el repo (`.gitignore:35:*.wav`), así que las
  grabaciones nunca se subirán.
- **`soundfile`**: `>=0.14.0` (instalada 0.14.0, con libsndfile 1.2.2). Hay ruedas para
  Windows, macOS arm64/x86_64 y Linux, y la de Linux ya incluye `libsndfile_x86_64.so`.
  - El CI **no necesita `libsndfile1`**; el workflow no se tocó.
- **matplotlib**: no se añadió como dependencia. `detect_beats --plot` la importa de forma
  opcional; si no está instalada, avisa y no genera la gráfica.

## Resultados con sintético (48 kHz)

Configuración: 30 s, 28800 BPH, SNR 25 dB, error de batimiento 0.3 ms, amplitud 280°,
marcha +5 s/d.

| | Limpio | Con realismo |
|---|---|---|
| Sesgo constante P3 / P1 (muestras) | −2.12 / −2.08 (~44 µs) | −2.6 / −3.0 |
| Error de P3 tras restar el sesgo (mediana / p95, muestras) | 0.16 / 0.47 | 0.17 / 0.57 |
| Error de P1 (mediana / p95, muestras) | 0.19 / 0.71 | 0.29 / 0.81 |
| Error de t_lift (mediana / p95, muestras) | 0.26 / 0.90 | 0.34 / 1.06 |
| Batimientos detectados | 240 de 240, sin falsos ni perdidos | igual |
| Tiempo hasta el enganche | 2.25 s | 2.25 s |
| Rendimiento | ~140× tiempo real | ~140× |

Notas:

- **P1** aparece en 238 de los 240 batimientos: los 2 primeros necesitan confirmación y
  quedan sin P1.
- **Rendimiento**: con 60 s y realismo, 144× en bloques de 4096 muestras y ~90× en bloques
  de 1024 o procesando todo de golpe.
- **Sesgo a otras frecuencias de muestreo**: −2.1 muestras a 44.1 kHz y −4.5 a 96 kHz
  (unos 44 µs en todos los casos).
- **Estabilidad del sesgo**: varía menos de 0.5 muestras entre SNR 30, 25 y 20 dB y entre
  amplitudes de 160, 280 y 320° (lo comprueba un test).
- **Barridos**: no hay batimientos falsos ni perdidos en ninguna de las variantes probadas,
  tanto en limpio como con realismo:
  - BPH 18000, 21600, 28800 y 36000;
  - error de batimiento 0, 0.5, 1.5 y 3 ms;
  - amplitud 160, 220, 280 y 320°;
  - marcha −300, 0 y +300 s/d;
  - fs 44.1, 48 y 96 kHz;
  - SNR 30, 25 y 20 dB.
- **Invarianza al troceado**: exacta por construcción, con diferencias ≤ 1e-6 muestras
  entre bloques de 64, 1024, 4096 y 48000 muestras, todo de golpe y bloques irregulares.
- **P1 débil**: nunca sale un P1 espurio; el detector deja NaN y marca `P1_MISSING`.
- **SNR baja**:
  - A 15 dB se pierde ~20 % de los batimientos, sin falsos.
  - A 10 dB no engancha y emite un aviso.

## Verificación ejecutada

### Lint y tests (mismos comandos que el CI)

```
ruff check src/ tests/            -> All checks passed
ruff format --check src/ tests/   -> 60 files already formatted
pytest -v --cov=adv_tg            -> 244 passed, 4 skipped, 1 failed
```

Cobertura de los módulos nuevos:

| Módulo | Cobertura |
|---|---|
| `beat_detector` | 96 % |
| `beat_types` | 96 % |
| `audio_files` | 89 % |
| `detect_beats` | 90 % |
| `check_samples` | 89 % |
| Total del paquete | 73 % |

El único fallo es `tests/test_process_capture.py::test_child_logs_to_its_own_file`, que viene
de la fase 1 y es anterior a este trabajo:

- El test mide el log en **bytes** (`st_size`) pero lo corta en **caracteres**
  (`read_text()[size0:]`).
- El log local acumula caracteres multibyte (158154 bytes frente a 157748 caracteres), así
  que el corte se salta las líneas nuevas.
- En el CI el log empieza vacío y el test pasa.
- Arreglo propuesto (no aplicado):
  `Path(log).read_bytes()[size0:].decode("utf-8", "replace")`.

### `check_samples --self-test` (código de salida 0)

Valida el detector contra tres grabaciones sintéticas que imitan cada calibre.

| Archivo | BPH/LA | Esperados | Encontrados | Perdidos | P1/P2/P3 % | t_lift | Amplitud | Tic/tac | Invariante | Veredicto |
|---|---|---|---|---|---|---|---|---|---|---|
| selftest_bulova_nm10.flac | 57600/— | 319.6 | 320 | 0 | 0/0/100 | — | — | — | — | INFO |
| selftest_hamilton_c07_h50.flac | 21600/50 | 119.0 | 119 | 0 | 0/100/100 | n/d | — | sí | sí | OK |
| selftest_miyota_9015.wav | 28800/51 | 159.1 | 160 | 0 | 99/100/100 | 7.52 ms | 270° | sí | sí | OK |

La fila del Miyota 9015 da además σ = 4.8 µs.

### `detect_beats` con un sintético tipo C07 (P1 débil)

Señal: 21600 BPH, LA 50°, amplitud 270°, P1 al 10 %, SNR 25 dB, error de batimiento 0.6 ms,
marcha +4 s/d, red de 50 Hz, clics a 1 Hz, 30 s.

| | WAV FLOAT | FLAC PCM_24 |
|---|---|---|
| Encontrados / esperados | 180 / 180.0 | 180 / 180.0 |
| Perdidos | 0 | 0 |
| Rechazados (clics) | 19 | 19 |
| P1 / P2 / P3 | 0.0 / 99.4 / 100.0 % | igual |
| t_lift, plausibilidad | n/d | n/d |
| σ del residuo | 5.2 µs | 5.4 µs |
| Enganche | 2.25 s | 2.25 s |
| Rendimiento | 153× | 152× |
| Periodo | 166.6588 ms frente a 166.6667 ms nominal (−0.005 %) | igual |
| Tic/tac sin saltos | sí | sí |
| Código de salida | 0 | 0 |

### `git status` / `git diff --stat`

- 8 archivos modificados: 191 líneas añadidas y 68 borradas.
- 13 archivos nuevos, unas 4100 líneas.
- Sin commits.

## Puntos de la GUI que leen o escriben audio (para la fase 5)

La GUI solo escribe audio: no lee archivos de audio en ningún sitio. Todas las referencias son
de `src/adv_tg/ui/main_window.py`.

- **Importaciones**:
  - `:48` importa `save_wav` de `mock.generator`.
  - `:62-64` importa `is_flac_supported` y `save_audio_recording` de `utils/audio_io.py`.
- **Grabación**:
  - `:955-960`: el botón `btn_record` llama a `record_audio`.
  - `:1866` `_on_recording_ready()` guarda en memoria el audio que manda el worker.
  - `:1876` `_ensure_test_length_recording()` genera audio simulado.
  - `:1918` `record_audio()`.
- **Guardado de la grabación** en `:1937` `save_recorded_audio()`:
  - `:1950` comprueba si hay FLAC con `is_flac_supported()`.
  - `:1957-1961` abre un diálogo con filtros FLAC/Ogg.
  - `:1974` guarda con `save_audio_recording()`.
- **Guardado del audio simulado** en `:1983` `_save_mock_recording()`:
  - `:1988-1992` abre un diálogo WAV.
  - `:1999` guarda con `save_wav`, que ahora usa `audio_files` (PCM_16, mismo resultado que
    antes).
- **Fin de la prueba**: `:2227` ofrece guardar automáticamente.

`utils/audio_io.py` `save_audio_recording()` sigue usando `soundfile` directamente:

- Recurre a libsndfile por ctypes y a codificadores de línea de comandos si hace falta.
- Normaliza por pico y guarda en FLAC PCM_16, o en Ogg Vorbis como alternativa.

No se migró porque cambiaría lo que guarda la GUI. En la fase 5 hay que pasarlo a
`audio_files` y decidir qué hacer con Ogg, que `audio_files` no admite.

## Archivos

### Nuevos

**Código:**

- `src/adv_tg/utils/audio_files.py`
- `src/adv_tg/core/beat_types.py`
- `src/adv_tg/core/beat_detector.py`
- `src/adv_tg/tools/detect_beats.py`
- `src/adv_tg/tools/check_samples.py`

**Tests:**

- `tests/synth.py`
- `tests/beat_helpers.py`
- `tests/test_audio_files.py`
- `tests/test_beat_detector_physics.py`
- `tests/test_beat_detector_accuracy.py`
- `tests/test_beat_detector_robustness.py`
- `tests/test_beat_detector_tools.py`
- `tests/test_beat_detector_samples.py`

### Modificados

- `pyproject.toml`
- `README.md`
- `src/adv_tg/core/__init__.py`
- `src/adv_tg/tools/capture_check.py`
- `src/adv_tg/mock/generator.py`
- `tests/test_capture_check.py`
- `tests/test_capture_worker.py`
- `tests/test_samples_replay.py`

## Decisiones de diseño no especificadas

- **Envolvente**:
  - Filtro Butterworth pasabanda con `sosfiltfilt` (sin desfase).
  - Hilbert **FIR** con ventana Blackman (|H| = 1.000 entre 1 y 15 kHz, sin efectos de borde
    de la FFT).
  - Suavizado centrado de 0.15 ms.
  - Se prefirió a Teager-Kaiser porque es lineal en amplitud.
- **Márgenes de cada ventana**: 3 × la memoria del filtro (269 muestras, 5.6 ms), más medio
  Hilbert y medio suavizado: 883 muestras (18 ms) en total.
- **Rejilla fija**: celdas de 0.25 s ancladas al índice absoluto de muestra; eso garantiza
  que el resultado no depende de cómo se trocee la entrada.
- **Inicio de cada sub-evento**: cruce al 40 % del propio pico, con línea base 0, para que el
  sesgo no dependa del SNR.
- **Calidad mínima de P1/P2/P3**: 12 dB de SNR propio (`min_subevent_snr_db`). Por debajo, el
  instante queda en NaN en lugar de dar un valor impreciso.
- **Defensas contra clics**:
  - Con el modelo maduro, el ancla se busca cerca de la predicción.
  - Un P1, o un P3 buscado hacia delante, solo se acepta si su t_lift coincide con al menos
    2 de los últimos 8 batimientos.
  - P1 frente a P2 se decide por la amplitud implícita más plausible (200–330°).
  - Tres valores atípicos consistentes seguidos re-siembran el modelo.
- **Enganche**:
  - El seguimiento arranca desde el inicio de la ventana de enganche, así que también se
    reportan los primeros 2 s.
  - Las rachas de ceros exactos de 10 ms o más cuentan como hueco.
- **Confianza**: `q_snr · q_struct · q_res · q_flags`, documentada en el docstring.
- **Subtipos por defecto**: WAV FLOAT y FLAC PCM_24.
- **Comentarios con caracteres no ASCII**: libsndfile los pierde, así que se guardan
  escapados (`\uXXXX`) y se restauran al leer.
- **Escritura atómica**: se escribe un archivo temporal en el mismo directorio y luego se
  renombra.
- **Cambios de comportamiento en `capture_check`**:
  - `--wav`/`--out` guarda por defecto en WAV float32 (antes, int16).
  - `read_wav_mono` elige el canal de mayor energía en lugar del primero.

## Límites conocidos

- **SNR por debajo de 20 dB**:
  - A 15 dB se pierde ~20 % de los batimientos, que quedan como huecos en `k`.
  - A 10 dB no engancha y avisa.
  - Nunca inventa batimientos.
- **P1 débil**: t_lift no se puede evaluar. `check_samples` lo marca "n/d" y no falla por
  eso.
- **P1 débil y amplitud baja** (< ~230° a 21600 BPH): P2 puede caer en la ventana de P1.
- **Realismo a SNR 20 dB**: P1 se resuelve en el 58 % de los batimientos, con hasta ~0.2 ms
  de incertidumbre.
- **BPH mal configurado** (> 6 % de diferencia): no engancha y avisa. Con la mitad del BPH
  real sí engancha, pero cuenta uno de cada dos batimientos sin avisar.

## Qué no se pudo verificar

No se pudieron probar las grabaciones reales ni el CI de macOS y Linux. En la Mac, desde la
raíz del repo y con los archivos copiados en `docs/samples/`:

```
python -m adv_tg.tools.check_samples
pytest tests/test_beat_detector_samples.py -v
```

## Siguientes pasos (verificación del usuario)

La fase 2 está cerrada; la fase 3 no empieza hasta que se confirme.

- **En la Mac**, con las grabaciones copiadas en `docs/samples/`:
  - Ejecutar `python -m adv_tg.tools.check_samples` y
    `pytest tests/test_beat_detector_samples.py -v`.
  - La tabla se guarda en `logs/check_samples_<fecha>.md`. Si algún archivo sale con
    veredicto REVISAR, compartir ese archivo para revisarlo.
- **Test de log de la fase 1**: `test_child_logs_to_its_own_file` puede fallar en una máquina
  local si el log tiene caracteres con acento. El arreglo de una línea
  (`Path(log).read_bytes()[size0:].decode("utf-8", "replace")`) se aplica bajo petición.
