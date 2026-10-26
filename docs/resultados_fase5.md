# Fase 5 (Integración completa): resultados

Estado al 26/10/2026: **implementada; primera prueba del usuario con el CM18 hecha (sección 7)**
(rama `develop`, sin commit). La GUI mide de verdad con un dispositivo de audio: marcha, error de batimiento,
amplitud, deriva del reloj de la tarjeta, calidad de la señal y grabación de la sesión.

Criterio de `docs/fases-nucleo.md`: "≥ 3 actualizaciones/s, plots a ~30 FPS, etiquetas de
deriva y calidad funcionando".

## 1. Arquitectura

```text
CM18 ──► proceso hijo (PortAudio) ──► SampleStream ──► hilo lector (QTimer 33 ms)
                                                        ├─ waveform_ready   ~30/s
                                                        ├─ level_updated    ~10/s
                                                        ├─ capture_health    ~2/s
                                                        └─ _SessionHook (MeasureStreamWorker)
                                                            ├─ grabación FLAC temporal (cada tick)
                                                            ├─ MeasurementSession.update  4/s ─► telemetry_ready
                                                            └─ deriva aplicada            1/s ─► drift_updated + clock_info
```

| Pieza | Archivo | Qué hace |
|---|---|---|
| `MeasurementSession` | `core/session.py` (nuevo, sin Qt) | Une detector (capa 2), `MetricsTracker` de 30 s (capa 3) y `ClockDriftTracker` (capa 4) sobre el `SampleStream`. Devuelve una instantánea con cifras, SNR, fiabilidad de detección, deriva aplicada y la forma de onda del último batimiento |
| `MeasureStreamWorker` | `ui/measure_worker.py` (nuevo) | Subclase de `CaptureStreamWorker`: mismo manejo del dispositivo, más el análisis y la grabación en el hilo lector mediante un gancho (`_make_hook`) |
| `ProcessAnalyzer` / `LocalAnalyzer` | `audio/analysis_process.py` (nuevo, sección 7.2) | Análisis en un proceso hijo persistente (o en el hilo lector como respaldo): recibe bloques y marcas, devuelve instantáneas |
| `FileReplay` | `audio/file_replay.py` (movido desde `tools/capture_check.py`) | Reproduce un archivo en tiempo real; ahora con `speed` (tests) y `exhausted` (fin normal) |
| `create_worker` | `ui/worker_factory.py` | Hardware → `MeasureStreamWorker` (antes `CaptureStreamWorker`, sin medida) |
| Contrato | `ui/stream_worker_base.py` | Señal nueva `clock_info(sigma_ppm, nivel, origen)` |
| Modelos | `core/models.py` | `BeatTelemetry` y `BeatEvent` con campos opcionales del motor real (incertidumbres, fiabilidad, avisos, confianza de tiempo, inicio de la forma de onda) |

Coste del análisis: ~9 ms por actualización con una toma del CM18 (detección + métricas). Desde la
sección 7 corre en un **proceso aparte** (`audio/analysis_process.py`); el hilo lector solo
reenvía las muestras nuevas y recibe las instantáneas.

## 2. Qué muestra la GUI con el motor real

- **Cifras** (tarjetas): mediana de las últimas 7 actualizaciones (1.75 s), ver sección 4.
  - Se **ocultan** (`---.-`) si no están disponibles o si la confianza de tiempo es < 0.15.
  - Se **atenúan** (gris) si es < 0.30. La amplitud también se atenúa si tic y tac discrepan
    (confianza global < 0.30), en vez de parpadear.
  - Solo los puntos visibles entran en el papel, las estadísticas y el CSV.
- **Barra de calidad**: con el nivel de entrada hasta que el detector engancha. Después:
  `SNR (12→25 dB, lineal) × fracción de batimientos que alimentan las cifras`, con texto
  `SNR 31 dB | 92 %`. Verde ≥ 65 %, ámbar ≥ 35 %, rojo debajo o si hubo saturación en los
  últimos 2 s (`⚠`). Calibrada con las tomas reales: SNR 23–32 dB y 75–93 % útiles dan 75–85 %.
- **Avisos** del detector y de las métricas (BPH probablemente incorrecto, amplitud de tic y
  tac distinta, batimientos saturados…) en la barra de estado, sin repetir el mismo.
- **Papel**: autoescala. Se recentra cuando un punto sale de la vista o la vista es 3 veces
  más ancha de lo necesario; se pausa si el usuario mueve o amplía el gráfico y "Center Trace"
  o una sesión nueva la reanudan.
- **Osciloscopio**: el último batimiento filtrado en la banda del detector (solo para dibujar;
  la medida nunca ve esta copia) con P1/P2/P3 en sus instantes reales. Un instante no resuelto
  oculta su marcador.
- **Reloj**: `+8.10 ± 0.50 ppm (nivel 3)`, con el origen en la ayuda emergente; sin calibrar,
  `+0.00 ppm (sin calibrar)`. La deriva aplicada es la de menor incertidumbre entre la
  calibración guardada del dispositivo (fase 4) y la estimación en vivo contra `time.time_ns`
  (nivel 2, a partir de ~20 s). Una estimación de nivel 1 nunca se aplica.
- **Estado**: "MIDIENDO" (ya no "CAPTURANDO (sin medición)").
- **Grabación**: cada sesión con hardware se graba a un FLAC temporal de 24 bits con etiquetas
  (dispositivo, fecha, calibración, BPH y ángulo de levantamiento). "Record Audio" durante la
  sesión la guarda al terminar; después de la sesión, la guarda en el momento. La siguiente
  sesión o el cierre borran la que no se guardó. Si el buffer se desborda, se anotan las
  muestras perdidas (`ADV_TG_LOST_SAMPLES`).

## 3. Validación

### 3.1 Grabaciones reales a través de la GUI (modo repetición, tiempo real, sin pantalla)

`ADV_TG_REPLAY_FILE=<toma> ` + GUI `offscreen`, 31 s por toma, 0 s de estabilización:

| Toma | Actualizaciones | Forma de onda | Primera cifra | GUI al final | `measure` (lote) |
|---|---|---|---|---|---|
| Miyota 9015 | 4.04/s | 30.3/s | 6.8 s | +8.0 s/d, 0.23 ms, 298° | +8.05, 0.18, 294° |
| Hamilton H-50 | 4.03/s | 30.2/s | 7.6 s | +11.5 s/d, 0.30 ms, 292° | +11.47, 0.30, 292° |
| Hamilton H-50 nuevo | 4.02/s | 30.3/s | 12.4 s | +8.1 s/d, 0.36 ms, 298° | +8.11, 0.38, 297° |
| Seiko 4R36 | 4.02/s | 30.3/s | 8.9 s | −3.0 s/d, 0.20 ms, 269° | −3.00, 0.21, 269° |

- **Criterio cumplido** sin pantalla: ≥ 3 actualizaciones/s y ~30 FPS. Falta confirmarlo con
  la ventana visible y el CM18 (sección 6).
- Las cifras de la GUI son las de la ventana deslizante de 30 s con mediana de 1.75 s, así que
  difieren de las del lote en décimas, dentro de su incertidumbre.
- Saturación: la barra marca `⚠` en Miyota (2 muestras a fondo de escala) y H-50 (208), que
  están realmente recortadas.
- Slot de la forma de onda: máx. 0.5–4.3 ms.

### 3.2 Tests nuevos (headless)

- `tests/test_session.py` (8): sintético 28800 BPH (marcha ±0.5 s/d, error de batimiento
  ±0.05 ms, amplitud ±3 %), forma de onda del osciloscopio, deriva en vivo (ADC 20 ppm rápido
  → nivel 2, ±0.5 ppm, marcha corregida a ~0), calibración guardada más precisa, nivel 1 nunca
  aplicado, batimientos perdidos, silencio, filtro de presentación.
- `tests/test_measure_worker.py` (6): worker con repetición acelerada (calibración aplicada,
  ≥ 3 actualizaciones/s, grabación con etiquetas, descarte de grabaciones no guardadas,
  modo repetición por variable de entorno, frecuencia de muestreo incorrecta, fallo de
  apertura sin temporales huérfanos, aviso al cambiar BPH o ángulo durante la sesión).
- `tests/test_gui_measure.py` (4): flujo completo en la GUI, ocultar/atenuar por confianza,
  etiqueta del reloj, pausa de la autoescala.
- Ajustados: `test_capture_worker.py` (un dispositivo que se detiene sigue siendo error; un
  archivo que termina, no), `test_mock.py` (contrato con `clock_info`; la fábrica devuelve
  `MeasureStreamWorker`), `test_gui_capture.py`.

## 4. Hallazgo: plantillas inestables en vivo

En modo vivo la plantilla de una paridad puede dar un inicio de P1 equivocado durante 1–5
refrescos:

- Miyota: amplitud del tac 295° → 127° → 295° en 0.6 s.
- 4R36: tac 266° ↔ 314–320° durante ~1.3 s, varias veces.

La confianza (factor de discrepancia tic/tac) cae entonces de 0.55 a 0.07. El modo por lotes
usa solo las plantillas finales y no lo ve.

Mitigación en la presentación (fase 5), sin tocar las capas 2 y 3 validadas:

1. **Mediana de 7 actualizaciones** de cada cifra (tic y tac por separado): elimina los saltos de
   hasta 3 actualizaciones. Miyota: 0 de 75 actualizaciones ocultas (antes parpadeaba).
2. **Confianza de tiempo** (`metrics.timing_confidence`, sin el factor tic/tac) para la marcha
   y el error de batimiento: el 4R36 pasa de 34/73 actualizaciones ocultas a 0.
3. **Amplitud atenuada**, no oculta, cuando tic y tac discrepan.

Causa de fondo pendiente para la fase 6: el inicio de P1 de la plantilla
(`template_p1_fraction`) es marginal en algunas paridades.

## 5. Limitaciones conocidas

- **Cuarzo**: la GUI no tiene modo cuarzo (`WatchConfig` no lo lleva); `measure --quartz`
  sigue siendo la vía. `MeasurementSession` ya lo admite (`SessionConfig.quartz`).
- **Papel**: dibuja la marcha de la ventana de 30 s en cada actualización (± medio error de
  batimiento), no la desviación de cada batimiento.
- **Grabación Ogg**: la ruta antigua (`save_audio_recording`, FLAC 16 bits/Ogg) ya no la usa el
  hardware; solo queda para motores sin grabación propia. La sesión se guarda siempre en FLAC
  24 bits.
- **Cambiar BPH o ángulo durante una sesión** no reinicia el detector: el motor real avisa
  ("se aplicará en la próxima sesión") en la barra de estado. El MOCK sí los aplica en vivo.

## 6. Pendiente de confirmación (usuario, con el CM18)

1. `adv-tg` con el CM18 y un reloj: cifras parecidas a `measure` sobre la misma toma; el
   registro (`logs/adv_tg.log`) muestra `plot ~30 FPS` y `análisis de sesión updates=…`.
2. Ventana visible: 4 actualizaciones/s y ~30 FPS sin perder audio (`tramas perdidas 0`).
3. El reloj pasa a "nivel 2" tras ~20 s, o muestra la calibración guardada.
4. "Record Audio" guarda el FLAC; `python -m adv_tg.tools.adc_calibration show toma.flac`
   muestra sus etiquetas.
5. Sin micrófono:
   `ADV_TG_REPLAY_FILE=docs/samples/miyota_9015.flac adv-tg`, elegir cualquier dispositivo,
   28800 BPH, 51°.

## 7. Primera prueba con el CM18 (26/10/2026)

Seiko 4R36, 21600 A/h, 53°, sesiones de 60 s con 10 s de estabilización, micrófono
"USB PnP Sound Device" (CM18). Resultado del usuario: +5.6 s/d, 0.05 ms, 287°; reloj de la tarjeta
+9.46 ppm (48000.45 Hz, −0.82 s/d), audio recibido 100 %, 0 desbordes. La grabación funciona.

### 7.1 Las primeras pruebas "sin sentido"

No fue la autocalibración: las sesiones estaban "sin calibrar" y la estimación en vivo
(+9.5 ppm) mueve la marcha solo 0.8 s/d, igual en todas. El log y la toma de 70 s muestran:

- **El reloj varía de verdad:** ventanas de 10 s de la toma de 70 s: +1.5, +5.0, +6.8, +3.2,
  +3.6, +8.9 s/d. El tac es muy ruidoso (580 µs frente a 188 µs del tic).
- **La ventana de 30 s se llena al principio de cada sesión:** las primeras cifras salen con
  10–15 s de datos, y la marcha subía de forma continua en casi todas las sesiones (p. ej.
  +1.5 → +2.1 → +2.6 → +3.7 → +4.1 → +4.6 → +4.9).
- Entre sesiones el resultado fue de +4.4 a +10 s/d, en línea con esa dispersión. tg y WAM dan
  +4 y +3 s/d en la grabación de referencia del 4R36.
- La primera sesión enganchó con un periodo de 8152 muestras (nominal 8000, +1.9 %) y se
  corrigió sola. Las demás engancharon a 7996–8005.

### 7.2 Tirones de la interfaz

Medido con la GUI reproduciendo la toma de 70 s del CM18 (sin pantalla). Se cuenta cuántas
veces el hilo de la GUI quedó bloqueado más de 33 ms (un cuadro a 30 FPS) en 40 s:

| Configuración | > 33 ms | p99 | máx. |
|---|---|---|---|
| Solo captura, sin análisis | 0–1 | 25 ms | 29 ms |
| Análisis en el hilo lector | 7–11 | 27 ms | 60 ms |
| Análisis en proceso aparte | 116–126 | 42 ms | 79 ms |
| Análisis en proceso aparte, sin pintar la telemetría | 1 | 25 ms | 35 ms |
| **Proceso aparte + curvas densas de 1 px sin antialiasing** | **0** | **10 ms** | **21 ms** |

- **Causa principal: el dibujo.** El perfil del hilo de la GUI mostró `drawPath` (Qt) en 14 de
  cada 25 s: curvas de 1 400–1 900 puntos con antialiasing y trazos de 1.2–1.6 px. Con trazo de
  1 px y sin antialiasing en esas tres curvas (forma de onda, osciloscopio y su envolvente),
  `drawPath` baja a 0.1 s. El papel y los marcadores conservan su estilo.
- **Causa secundaria: el GIL.** En el log real el análisis tardó 40–107 ms por actualización
  (9 ms sin la GUI) y bloqueó la interfaz. Ahora corre en un proceso aparte, persistente durante
  la vida del worker. Un hilo lector en el hijo evita el interbloqueo por los búferes
  pequeños de las tuberías de macOS (~8 KB). `ADV_TG_ANALYSIS_INPROC=1` lo devuelve al hilo.
- **Osciloscopio:** ventana de longitud fija (P1 visible hasta 150° de amplitud) y eje X fijo;
  antes se reescalaba con cada batimiento.

### 7.3 Otros cambios

- **Aviso tic/tac:** solo aparece si la discrepancia persiste en las amplitudes mostradas
  (mediana de 1.75 s), con el texto "la amplitud es orientativa". Antes salía con cada salto
  momentáneo de la plantilla (sección 4).
- **MOCK desactivado al arrancar:** la ventana elige la entrada por defecto del sistema (si no,
  el primer dispositivo de hardware). El MOCK solo arranca si no hay hardware, y sigue
  disponible con su botón.

### 7.4 Ruido ambiental (motores): análisis y plan

Distribución de energía del tic del 4R36 con el CM18 (toma de 70 s): casi toda entre 4 y 15 kHz
(picos de −10 a −15 dBFS sobre un ruido de −53/−55 dBFS); entre 1 y 2 kHz apenas hay tic (−35
dBFS). El ruido de motores se concentra por debajo de 2–3 kHz.

Subir el corte inferior del detector, probado en las 7 tomas (5 de referencia + 2 del CM18):

| Corte | Marcha | Error de batimiento | Amplitud |
|---|---|---|---|
| 1 kHz (actual) | referencia | referencia | referencia |
| 2–4 kHz | cambia dentro de su incertidumbre | **inestable**: 4R36 0.20 → 0.66–0.94 ms; toma del CM18 0.37 → 1.50 ms a 4 kHz | ±1–4° |

Sin ruido de motores, subir el corte no mejora nada y empeora el error de batimiento: este
depende del inicio de P1, que es débil y tiene parte de su energía en graves. No se cambia a
ciegas.

**Plan** (necesita una grabación con ruido real; el botón Record ya la guarda con etiquetas):

1. Grabar 60–90 s con el reloj en el CM18 mientras pasan motos o coches, y otra igual en
   silencio justo después.
2. Con esas dos tomas, comparar con números:
   a. un paso alto más pronunciado solo para la detección y la marcha, dejando el error de
      batimiento en la banda actual;
   b. descartar los batimientos cuyo suelo de ruido local se dispara (marca nueva, como
      `SATURATED`), para que no entren en las cifras ni en el papel;
   c. una sustracción espectral del ruido estimado entre batimientos.
3. Mientras tanto, lo más eficaz es mecánico: apoyar el soporte del CM18 sobre espuma o una
   superficie que no transmita vibraciones del suelo o la mesa, porque un micrófono de contacto
   capta sobre todo la vibración, no el aire.
