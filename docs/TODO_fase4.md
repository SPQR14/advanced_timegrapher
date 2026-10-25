# Fase 4 (Reloj): recomendaciones y pendientes

Estado al 24/10/2026: fase 4 **por empezar** (rama `feature/fase4_timeDrift`, sin código aún).
Lo único existente es el gancho `adc_drift_ppm` de `core/metrics.py`
(`P_real = P_muestras / (fs · (1 + ppm·1e-6))`), que nadie calcula todavía.

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

- [ ] Módulo `core/` del reloj: estimador de envolvente inferior, ppm ± σ, niveles de confianza.
- [ ] Integración con `MetricsConfig.adc_drift_ppm` (y su σ en la incertidumbre de la marcha).
- [ ] Herramienta de consola `python -m adv_tg.tools.*` (medir ppm en vivo con el CM18).
- [ ] Tests headless: un ppm inyectado en sintético se recupera (bloques con jitter positivo,
      saltos de reloj, deriva lenta); los que necesitan hardware o red se saltan.
- [ ] Validación con el Bulova según el criterio corregido de la sección 1.

## 5. Decisiones pendientes del usuario

1. ¿Se acepta el cambio del criterio del Bulova (sección 1)?
2. ¿Se incluye soporte de PPS/GPS (sin dependencias nuevas, pero requiere hardware)?
3. ¿SNTP propio con `socket` o se descarta la consulta NTP directa?

## Fuentes

- [Mac OS X System.nanoTime Improvements (OpenJDK)](https://mail.openjdk.org/pipermail/hotspot-dev/2013-May/009510.html)
- [Wine configure.ac (mach_absolute_time / CLOCK_MONOTONIC_RAW)](https://gitlab.winehq.org/uncoveryoureyestheresnosun/wine/-/blob/a4131ca625cf7b1c76ba8f230dda7c6b9d682bc3/configure.ac)
- [Issues with clock_gettime pre macOS 10.13 (MacPorts)](https://lists.macports.org/pipermail/macports-dev/2018-October/039622.html)
- [Monotonic clock on OS X (Stack Overflow)](https://dev.library.kiwix.org/content/stackoverflow_en_nopic_2021-08/questions/11680461/monotonic-clock-on-osx)
- [Apple Technical Q&A QA1196](https://developer.apple.com/library/content/qa/qa1196/_index.html)
- [AudioTimeStamp.mRateScalar (dplug)](https://dplug.dpldocs.dlang.org/derelict.carbon.coreaudio.AudioTimeStamp.mRateScalar.html)
