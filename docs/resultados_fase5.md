# Fase 5 (Integración completa): resultados

Estado al 26/10/2026: **implementada, pendiente de confirmación con el CM18** (rama `develop`,
sin commit). La GUI mide de verdad con un dispositivo de audio: marcha, error de batimiento,
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
| `FileReplay` | `audio/file_replay.py` (movido desde `tools/capture_check.py`) | Reproduce un archivo en tiempo real; ahora con `speed` (tests) y `exhausted` (fin normal) |
| `create_worker` | `ui/worker_factory.py` | Hardware → `MeasureStreamWorker` (antes `CaptureStreamWorker`, sin medida) |
| Contrato | `ui/stream_worker_base.py` | Señal nueva `clock_info(sigma_ppm, nivel, origen)` |
| Modelos | `core/models.py` | `BeatTelemetry` y `BeatEvent` con campos opcionales del motor real (incertidumbres, fiabilidad, avisos, confianza de tiempo, inicio de la forma de onda) |

Coste del análisis: ~2 ms de detección + ~2.5 ms de métricas por actualización (máx. 7 ms) en
las grabaciones reales, ~2 % de un núcleo. Por eso vive en el hilo lector, sin hilo propio.

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
