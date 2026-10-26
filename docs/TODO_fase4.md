# Fase 4 (Reloj): recomendaciones y pendientes

Estado al 25/10/2026: fase 4 **en curso** (rama `feature/fase4_timeDrift`, sin commit). Hechos el
módulo `core/adc_clock.py`, su integración con la marcha y las marcas de reloj en la captura; ver
la sección 6. Siguiente paso: herramienta de consola en vivo (sección 6.4).

Objetivo: conocer la frecuencia real del ADC (ppm ± σ) para que la marcha no lleve el sesgo del
reloj de la tarjeta. 1 ppm = 0.0864 s/d.

## 1. La trampa principal: el reloj del ordenador también deriva

Comparar el contador de muestras con el reloj del host solo mide el cociente entre **dos cristales
sin calibrar** (ADC y ordenador, típicamente ±10–50 ppm cada uno). Su utilidad depende del reloj:

| Sistema | `perf_counter`/`monotonic` (el que usa hoy `block_log`) | ¿Disciplinado por NTP? |
|---|---|---|
| Linux | `CLOCK_MONOTONIC` | **Sí**, en frecuencia (chrony/ntpd); `CLOCK_MONOTONIC_RAW` no |
| macOS | `mach_absolute_time` | Probablemente no (no confirmado en documentación de Apple); `time.time()` sí, vía `timed` |
| Windows | `QueryPerformanceCounter` | No; la hora del sistema la ajusta W32Time, de baja calidad por defecto |

### Corolario sobre el criterio de la fase 4

El criterio actual ("el Bulova da ≈ +8 ppm con el CM18") mezcla dos errores: los +8.3 ppm de tg son
el error de la tarjeta **más** el error propio del Bulova (±15 s/mes ≈ ±5.7 ppm). Un método
independiente puede dar +4 o +12 ppm y estar bien.

Propuesta de criterio: `|ppm_medido − 8.3| ≤ error_Bulova + 2σ`.

### Calibración propia del Bulova (sin hardware)

El error del Bulova no hace falta suponerlo a partir de su especificación: se puede medir.

1. Comprobar la especificación del calibre NM10: los 16 pasos/s son típicos de los Precisionist
   (±10 s/año ≈ 0.3 ppm), no de un cuarzo estándar (±15 s/mes ≈ ±5.7 ppm). Sin confirmar.
2. Día 0: foto del Bulova junto a una pantalla con hora sincronizada por NTP (p. ej. time.is en
   un móvil o PC sincronizado). Anotar la diferencia `d0` (s); la aguja avanza en pasos de 1/16 s.
3. Día 20–30: repetir la foto en condiciones parecidas (temperatura, posición) y anotar `d1`.
4. `ppm_Bulova = (d1 − d0) / (t1 − t0) · 1e6`, con `t1 − t0` en segundos. Con ±0.1 s de lectura en
   30 días: σ ≈ 0.14 s / 2.6e6 s ≈ ±0.05 ppm.
5. Con la convención de `adc_drift_ppm` (> 0 = ADC rápido), un ADC rápido alarga los periodos en
   muestras y el reloj parece atrasar: `marcha_aparente ≈ ppm_Bulova − adc_drift_ppm`. Si tg da
   +8.3 ppm, `adc_drift_esperado = ppm_Bulova − 8.3` y el criterio pasa a
   `|adc_drift_medido − (ppm_Bulova − 8.3)| ≤ σ_Bulova + 2σ`.
6. Registrar fechas, lecturas y temperatura en `docs/` como referencia de la validación.

## 2. Métodos, por calidad creciente

### 2.1 Contador de muestras frente al reloj del host (base)

- Ajuste lineal `t_host = a + b·n` sobre el registro de bloques `(end_index, host_ns)` de
  `SampleStream.block_log()`.
- Los callbacks solo pueden llegar **tarde** (retardo positivo): ajustar la **envolvente inferior**
  (mínimo por intervalo de ~1 s y luego Theil-Sen o regresión robusta), no la media.
- Incertidumbre: σ_b ≈ σ_j·√12 / (T·√N). Con σ_j = 1 ms y bloques de ~20 ms: ~1 ppm en 60 s,
  ~0.1 ppm en 10 min.
- Mejora: usar `time_info.inputBufferAdcTime` de PortAudio en vez de `perf_counter` en el callback.
  En CoreAudio procede de las marcas de tiempo del HAL y tiene mucho menos jitter. En WASAPI es de
  peor calidad: medir antes de fiarse.
- Verificación cruzada en macOS: CoreAudio publica su estimación de la frecuencia real
  (`kAudioDevicePropertyActualSampleRate`, `'asrt'`, presente en `AudioHardware.h` del SDK).
  Requiere ctypes; opcional.
- Ojo: `capture_process.py` estampa `perf_counter_ns()` en el proceso de captura; ambos procesos
  comparten reloj, pero el retardo del IPC no debe entrar en la marca.

### 2.2 Hacer fiable el reloj del host (NTP)

- **Linux:** con NTP sincronizado, `CLOCK_MONOTONIC` ya va a la frecuencia correcta (residual
  chrony < 0.1 ppm). Comprobar estado de sincronización con `adjtimex` (ctypes).
- **macOS / Windows:** comparar el contador de muestras con la hora ajustada (`time.time_ns()`):
  - detectar saltos (> 5 ms → cortar en tramos),
  - vigilar correcciones de offset en curso (el slew puede alterar la frecuencia temporalmente),
  - alternativa: calibrar el reloj crudo frente a la hora ajustada durante horas para conocer el
    error del cristal del host.
- **SNTP directo:** 48 bytes por UDP con `socket`, sin dependencias nuevas. Error por consulta
  1–10 ms (asimetría del RTT; filtrar por RTT mínimo). Solo útil con bases largas: en 10 min da
  2–17 ppm; > 1 h para bajar de 0.3 ppm. Sirve como calibración en segundo plano, no por medida.

### 2.3 Referencia externa (máxima confianza)

- **GPS con salida PPS** (módulo de ~15 €):
  - Acústico: zumbador piezo excitado por el PPS junto al CM18. El detector ya lo trata como un
    "cuarzo" a 3600 BPH (como el Citizen H874). Con ~0.7 ms de ruido por paso: ~0.5 ppm en 5 min.
  - Eléctrico: PPS por cable a una entrada estéreo de línea: ~0.01 ppm. El CM18 es mono, haría falta
    otra interfaz.
  - Único método independiente del ordenador y de la red.
- **Cuarzo de alta precisión** (HAQ, ±10 s/año ≈ 0.3 ppm) como referencia acústica, igual que el
  Bulova pero con error propio despreciable.

### 2.4 Descartados

- Loopback DAC→ADC del mismo dispositivo: comparten cristal, mide 0.
- Tono generado por un móvil u otro equipo: es otro cristal sin disciplinar.

## 3. Arquitectura propuesta

### Niveles de confianza

| Nivel | Fuente | Incertidumbre típica |
|---|---|---|
| 0 | Sin calibrar | ±30 ppm declarados (±2.6 s/d), con aviso |
| 1 | ADC frente a host sin disciplinar | σ del ajuste ⊕ error del host (±10–50 ppm) |
| 2 | Host disciplinado por NTP y verificado | ~0.1–1 ppm |
| 3 | Referencia externa (PPS/HAQ) o calibración manual | < 0.5 ppm |

### Comportamiento

- **Estimación continua** durante la sesión: el cristal USB se mueve ~1–3 ppm durante el
  calentamiento y con la temperatura. Avisar si el ppm cambia de forma significativa dentro de la
  sesión.
- **Persistencia por dispositivo:** nombre, frecuencia de muestreo, ppm ± σ, método, nivel y fecha.
- **Grabaciones:** guardar el ppm ± σ y el nivel en las etiquetas (Vorbis comment) del FLAC al
  grabar; los archivos offline no llevan marcas del host.
- **Calibración manual opcional:** el usuario introduce ppm ± σ, o mide un cuarzo de error conocido.
- **Meta:** ≤ 1 ppm (0.09 s/d), por debajo de la incertidumbre estadística en mecánicos
  (0.15–0.6 s/d). Solo para cuarzos merece la pena bajar a 0.1 ppm.

## 4. Entregables (según `docs/fases-nucleo.md`)

- [x] Módulo `core/` del reloj: estimador de envolvente inferior, ppm ± σ, niveles de confianza.
- [x] Integración con `MetricsConfig.adc_drift_ppm` (y su σ en la incertidumbre de la marcha).
- [x] Marcas de reloj alternativas en la captura (`inputBufferAdcTime`, `time.time_ns()`).
- [ ] Herramienta de consola `python -m adv_tg.tools.*` (medir ppm en vivo con el CM18).
- [x] Tests headless: un ppm inyectado en sintético se recupera (bloques con jitter positivo,
      saltos de reloj, deriva lenta); los que necesitan hardware o red se saltan.
- [ ] Persistencia por dispositivo y ppm ± σ en las etiquetas del FLAC.
- [ ] Validación con el Bulova: "≈ +8 ppm con el CM18" (criterio mantenido, ver sección 5).

## 5. Decisiones del usuario (25/10/2026)

1. Criterio del Bulova: **se mantiene** "≈ +8 ppm con el CM18" (no se adopta el criterio corregido
   de la sección 1; la calibración propia del Bulova queda como referencia opcional).
2. PPS/GPS: **soporte completo** (acústico y eléctrico por entrada de línea), sin dependencias nuevas.
3. SNTP propio: **aplazado**. El nivel 2 usa el reloj del sistema disciplinado por el SO.

## 6. Avance (25/10/2026)

### 6.1 `src/adv_tg/core/adc_clock.py` (capa 4, nuevo; sin hardware ni red)

- Convención: `ppm > 0` = ADC rápido (igual que `MetricsConfig.adc_drift_ppm`);
  `AdcDrift.rate_bias_s_per_day = −0.0864 · ppm`.
- `lower_envelope`: residuo frente a la recta nominal, binning de 1 s por tiempo del ADC y se
  queda el bloque más temprano de cada bin (descarta el primero y el último, parciales).
- `fit_envelope`: corta en tramos si hay un salto > 5 ms entre mínimos consecutivos (pasos de
  NTP, suspensión, muestras perdidas por overflow), descarta tramos de < 3 puntos, ajusta una
  pendiente Theil-Sen (pares del mismo tramo; submuestreo a 200 000 pares si hay más), recorta
  atípicos a 5σ robustas y hace el ajuste final por mínimos cuadrados con pendiente común y
  ordenada por tramo. Requiere ≥ 10 puntos y ≥ 20 s; si no, `AdcDrift.uncalibrated` con nota.
- `estimate_drift` = envolvente + ajuste sobre un log completo; ignora marcas ≤ 0.
- `ClockDriftTracker`: envolvente incremental (un punto por segundo, memoria O(duración)),
  porque `block_log` solo guarda 8192 bloques (~3 min). Descarta bloques ya vistos, se reinicia
  si los índices retroceden, `estimate(last_s=...)` y `stability(window_s, n_sigma)` para
  avisar si el ppm cambia durante la sesión (calentamiento).
- `AdcDrift`: ppm, `sigma_ppm` (estadística), `systematic_ppm` (base de tiempo), nivel,
  método (`none`, `host-envelope`, `reference`, `manual`), tramos, residuo;
  `uncertainty_ppm = hypot(sigma, systematic)`. `AdcDrift.manual(ppm, σ)` = nivel 3.
- `host_clock(source)`: `"perf_counter"` y `"pa_adc"` solo cuentan como disciplinados en Linux
  con NTP sincronizado (`linux_ntp_synchronized`, `adjtimex` por ctypes; ±0.5 ppm), si no ±30 ppm;
  `"time"` (`time.time_ns`) disciplinado, ±1 ppm (±2 ppm en Windows).
- Referencias externas (nivel 3): `drift_from_ticks(ticks, fs, nominal_period_s,
  reference_ppm, reference_sigma_ppm)` (PPS, HAQ o pasos de 1/16 s del Bulova; tolera pulsos
  perdidos y espurios) y `detect_pps_edges` (flanco del PPS eléctrico con interpolación
  sub-muestra, polaridad ±1). El PPS acústico pasa por `BeatDetector` como cuarzo a 3600 BPH y
  sus instantes van a `drift_from_ticks`.

Rendimiento en sintético (jitter exponencial de 1 ms, picos de 5–60 ms): σ ≈ 0.14 ppm en 60 s,
≈ 0.005 ppm en 10 min; cobertura a 2σ: 95/100 ejecuciones. Con jitter de 8 ms: error 0.14 ppm en
300 s. Con dos saltos de reloj (+40 / −25 ms): error 0.06 ppm y 3 tramos.

### 6.2 Integración con la marcha

- `MetricsConfig.adc_drift_sigma_ppm` (nuevo): se suma en cuadratura a
  `rate_uncertainty_s_per_day` mediante `Metrics.rate_clock_sigma_s_per_day`. No afecta a las
  ventanas ni a la tendencia (es un desplazamiento común). La marcha cuenta como calibrada si el
  ppm ≠ 0 o la σ > 0.
- `python -m adv_tg.tools.measure`: opción nueva `--adc-drift-sigma-ppm`.

### 6.3 Marcas de reloj en la captura

- `SampleStream.write(block, host_ns, *, adc_ns=0, wall_ns=0)` y `clock_log()` → `ClockLog`
  (`end_indices`, `host_ns`, `adc_ns`, `wall_ns`; 0 = no disponible). `block_log()` no cambia.
- `adc_end_ns(time_info, frames, fs)` (en `audio/capture_process.py`): `inputBufferAdcTime`
  marca la PRIMERA muestra; se le suma `frames / fs` para referirlo al final del bloque, como
  `end_index` (los bloques pueden variar de tamaño con `blocksize=0`). Redondeo con `round`;
  nunca lanza excepciones.
- Ambos callbacks (`capture.py` y `capture_process.py`) estampan además `time.time_ns()`.
- Protocolo hijo → padre: `FRAME_HEADER` pasa de `<QqI` a `<QqqqI` (seq, host_ns, adc_ns,
  wall_ns, n). La fuente falsa estampa `inputBufferAdcTime` programado.

### 6.4 Pendiente inmediato

1. Herramienta `python -m adv_tg.tools.adc_clock`: ppm en vivo con el CM18 comparando las tres
   marcas (`host_ns`, `adc_ns`, `wall_ns`) para ver cuál tiene menos jitter; modos PPS acústico y
   eléctrico (entrada de línea, otra interfaz: el CM18 es mono).
2. Persistencia del ppm por dispositivo; ppm ± σ y nivel en las etiquetas Vorbis del FLAC.
3. Validación con el Bulova (≈ +8 ppm con el CM18).

### 6.5 Tests y estado de la suite

- Nuevos: `tests/test_adc_clock.py` (22), `test_metrics.py::test_adc_drift_sigma_adds_to_rate_uncertainty`,
  `test_sample_stream.py::test_clock_log_extra_stamps_and_wrap`,
  `test_audio_capture.py` (marcas en el callback y `adc_end_ns`), comprobación de `clock_log` en
  `test_process_capture.py::test_round_trip_ramp_without_gaps`.
- Suite: 432 pasan, 2 saltados, 1 xfail; ruff limpio.
- Fallo ajeno a la fase: `test_smoke.py::test_version`, porque el `.venv` conserva los
  metadatos de la 0.3.0 tras el salto a 0.4.0. Se arregla con `.venv/bin/pip install -e .`.

## Fuentes

- [Mac OS X System.nanoTime Improvements (OpenJDK)](https://mail.openjdk.org/pipermail/hotspot-dev/2013-May/009510.html)
- [Wine configure.ac (mach_absolute_time / CLOCK_MONOTONIC_RAW)](https://gitlab.winehq.org/uncoveryoureyestheresnosun/wine/-/blob/a4131ca625cf7b1c76ba8f230dda7c6b9d682bc3/configure.ac)
- [Issues with clock_gettime pre macOS 10.13 (MacPorts)](https://lists.macports.org/pipermail/macports-dev/2018-October/039622.html)
- [Monotonic clock on OS X (Stack Overflow)](https://dev.library.kiwix.org/content/stackoverflow_en_nopic_2021-08/questions/11680461/monotonic-clock-on-osx)
- [Apple Technical Q&A QA1196](https://developer.apple.com/library/content/qa/qa1196/_index.html)
- [AudioTimeStamp.mRateScalar (dplug)](https://dplug.dpldocs.dlang.org/derelict.carbon.coreaudio.AudioTimeStamp.mRateScalar.html)
