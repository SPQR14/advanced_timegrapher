# Advanced Timegrapher v0.6.3-beta3: resumen de la documentación

Fecha: 27/10/2026. Versión del código: `0.6.3` (etiqueta `v0.6.3.beta.3`).

Resume en un solo archivo los 14 documentos de `docs/`. Lo marcado como **[verificado]** se
comprobó contra el código el 27/10/2026. Los documentos de origen tienen fechas distintas (del
23 al 27/10), y algunas de sus afirmaciones ya no son ciertas; la sección 9 señala esas
diferencias.

---

## 1. Qué es

Cronocomparador (timegrapher) por software para relojes mecánicos. Escucha el tic-tac con un
micrófono de contacto (probado con el **CM18 USB piezo**) y mide:

- marcha (s/d),
- error de batimiento (ms),
- amplitud (°),
- deriva del reloj de la tarjeta de sonido (ppm).

Python + PyQt6 + pyqtgraph, licencia **GPL-3.0-or-later**. El núcleo es implementación propia:
no se copia ni se porta código de `tg` (GPLv2-only, incompatible). `tg` 0.8.0 y Watch Accuracy
Meter (WAM) solo se usan como referencias de comparación.

## 2. Estado actual (v0.6.3, beta 3) [verificado]

- **Fases 0 a 5 terminadas**: la GUI mide de verdad con un dispositivo real. **Fase 6**
  (validación con reportes de usuarios y empaquetado) en curso.
- **Motor DSP 100 % Python/NumPy** (`src/adv_tg/dsp/`: `engine.py`, `filters.py`). La extensión
  C++ de la beta 2 y la dependencia de SciPy en tiempo de ejecución se retiraron.
- **Python ≥ 3.12** (`requires-python`). El soporte de 3.11 se quitó en el PR #32 para que el
  CI pasara; el README de la beta 1 todavía dice ≥ 3.11.
- Dependencias: `numpy`, `sounddevice`, `soundfile` (incluye libsndfile), `PyQt6`, `pyqtgraph`
  y `PyOpenGL`. `PyOpenGL_accelerate` se eliminó (PR #30).
- CI: `ci.yml` y `tests.yml` corren en Linux, macOS y Windows con Python 3.12 y
  `QT_QPA_PLATFORM=offscreen`; `build.yml` genera los paquetes.
- Seguridad: endurecimiento aplicado el 27/10 (PR #30 y #36; ver sección 8.5).
- **Licencia: GPL-3.0-or-later**. Se valoró pasar a MIT (27/10/2026) y se descartó: la GPL v3
  permite aceptar donaciones (botón "Buy me a coffee") y cobrar por los paquetes, y es la
  licencia de PyQt6, que la aplicación incluye.

## 3. Historial de versiones

| Versión | Fecha | Contenido |
|---|---|---|
| v0.1.0 | 23/10 | Fase 0: esqueleto, GUI, modo MOCK, Debugger, CI |
| v0.2.0 | 23/10 | Fase 1: captura (`SampleStream`, `AudioCapture`, `capture_check`) |
| v0.3.0 | 24/10 | Fase 2: detector de batimientos, audio WAV/FLAC |
| v0.4.0 | 24/10 | Fase 3: métricas |
| v0.5.0 | 26/10 | Fase 4: reloj de la tarjeta, calibraciones por dispositivo, etiquetas en grabaciones |
| v0.6.0 | 26/10 | Fase 5 + beta 1: medida real en la GUI, modo cuarzo, calibración manual, "Acerca de" |
| v0.6.1.beta.2 | 26–27/10 | Beta 2: cinta de papel real, mediana del error de batimiento; C++ probado y revertido a Python puro |
| v0.6.2.beta2.1 | 27/10 | Corrección de la beta 2 |
| v0.6.3.beta.3 | 27/10 | Beta 3: endurecimiento de seguridad, cambios en el CI |

La versión vive en `pyproject.toml` y en `src/adv_tg/__init__.py`, y `tests/test_smoke.py`
comprueba que coincidan. Después de cambiarla hay que ejecutar `pip install -e .`.

## 4. Arquitectura por capas

```text
Micrófono ─► proceso hijo de captura (PortAudio, sin Qt)
          ─► tubería ─► SampleStream (buffer circular SPSC, índice absoluto de muestra)
          ─► hilo lector de la GUI (QTimer 33 ms)
               ├─ forma de onda ~30/s, nivel ~10/s, salud de captura ~2/s
               └─ MeasureStreamWorker
                    ├─ grabación FLAC temporal de 24 bits con etiquetas
                    └─ proceso de análisis: MeasurementSession
                         = BeatDetector (capa 2) + MetricsTracker 30 s (capa 3)
                           + ClockDriftTracker (capa 4)
                         ─► telemetría 4/s, deriva 1/s
```

| Capa | Módulo principal | Función |
|---|---|---|
| 1 Captura | `core/sample_stream.py`, `audio/process_capture.py`, `audio/capture_process.py` | Audio sin pérdidas aunque la GUI retenga el GIL |
| 2 Detector | `core/beat_detector.py`, `core/beat_types.py`, `dsp/` | P1/P2/P3 por batimiento y `t_fine` por plantillas tic/tac |
| 3 Métricas | `core/metrics.py` | Marcha, error de batimiento, amplitud, tendencia, incertidumbres |
| 4 Reloj | `core/adc_clock.py`, `utils/adc_calibration.py` | ppm ± σ del ADC, niveles de confianza, PPS |
| 5 Integración | `core/session.py`, `ui/measure_worker.py`, `audio/analysis_process.py` | Motor real en la GUI |
| E/S | `utils/audio_files.py` | Único módulo que lee/escribe WAV y FLAC; etiquetas Vorbis/ICMT |

**Reglas de diseño** (de `fases-nucleo.md`):

- Los tiempos son **índices absolutos de muestra** (reloj del ADC), nunca segundos de pared.
- En el callback de audio está prohibido loguear, bloquear o asignar memoria.
- Cada capa trae una herramienta de consola (`python -m adv_tg.tools.*`) y tests sin pantalla.
- Una fase por vez; los agentes no hacen commits.

**Fórmulas**:

- f = BPH/7200; periodo P = 3600/BPH s.
- Amplitud A = LA / (2·sin(π·f·t_lift)), con t_lift = P3 − P1 (inicio del desbloqueo → inicio
  de la caída).
- Marcha = (P_nominal / P_medido − 1)·86400.
- 1 ppm = 0.0864 s/d.

## 5. Fases del núcleo

### Fase 0: esqueleto (v0.1.0)

GUI, modo MOCK (datos simulados, con la insignia "MOCK" visible), Debugger y CI.

### Fase 1: captura (v0.2.0)

- `SampleStream`: buffer circular sin bloqueos, con un único productor y el registro de bloques
  `(índice_fin, host_ns, adc_ns, wall_ns)`.
- **Fase 1d (captura en proceso aparte)**: con la captura en el mismo proceso que la GUI, el
  GIL hacía que CoreAudio descartara bloques en silencio. Solo llegaba el 17–25 % del audio.
  `ProcessAudioCapture` mueve PortAudio a un proceso hijo con contexto `spawn`; si la GUI se
  congela, el padre se pone al día sin perder muestras.
- `capture_check`: criterios ingesta ≥ 99 %, hueco < 3 bloques y callback < 2000 µs. Validado
  con el CM18: ingesta 100 %.

### Fase 2: detector de batimientos (v0.3.0)

Detalle en `resultados_fase2_win.md` (sintético en Windows) y `resultados_pruebas_fase2.md`
(grabaciones reales en macOS).

- **Envolvente**: Butterworth pasabanda de fase cero, Hilbert FIR con ventana Blackman y
  suavizado de 0.15 ms. Rejilla fija de 0.25 s anclada al índice absoluto, así que el resultado
  no depende del troceado.
- **Instantes**: inicio de cada sub-evento (cruce al 40 % del pico, interpolado). P1/P3 sin
  evidencia quedan en `NaN` con su flag; el modelo nunca los rellena.
- **Alineación fina (`t_fine`)**: una plantilla de envolvente por paridad (mediana de 24
  batimientos). Mide tic y tac en el mismo punto físico y corrigió el error de batimiento
  falso del H-50 (de 1.49 a 0.36 ms).
- **Selector "nivel/frente" para P3**: si tic y tac discrepan más de 20° en amplitud, usa el
  pie del frente abrupto. Corrigió la rampa del tac del 4R36 (de 341° a 269°).
- **Sintético**: 0 batimientos falsos y 0 perdidos con SNR ≥ 20 dB; error de P3 < 1 muestra
  (p95); ~95–140× tiempo real.
- **Límites**:
  - SNR < 17 dB pierde batimientos y a 10 dB no engancha;
  - un P1 débil (C07) queda `NaN`;
  - un BPH > 6 % equivocado no engancha;
  - la paridad ambigua tiene 500–830 µs de jitter frente a 110–220 µs de la limpia.
- **Normalizar el audio no ayuda**: el detector es invariante a la ganancia, y limitar o
  aplicar AGC sesga la medida igual que la saturación.

### Fase 3: métricas (v0.4.0)

Detalle en `fase3_resultados.md`.

- **Marcha**: regresión robusta `t = a + P·k + c·(−1)^k` sobre `t_fine`, cada paridad ponderada
  por 1/σ² y recortada a 5σ (MAD). La incertidumbre es la mayor entre la de covarianza y la de
  *jackknife* por bloques (el ruido real no es blanco).
- **Error de batimiento**: 2·|c| sobre los P1 de las plantillas.
- **Amplitud**: con el t_lift de las plantillas; tic, tac y media.
- **Tendencia**: término cuadrático de la regresión; avisa si supera 3σ.
- **Modos**: por lotes (`compute_metrics`) e incremental (`MetricsTracker`), con resultados
  idénticos.
- **Cuartos** (`--quartz`): Bulova 16/s, TMI VH31 4/s y Citizen H874 1/s reproducen la deriva
  de la tarjeta medida por tg (−0.72 s/d) con diferencias de 0.00, +0.17 y +0.22 s/d.
- **Rendimiento**: 113–128× tiempo real en mecánicos y 91–175× en cuartos.

### Fase 4: reloj de la tarjeta (v0.5.0)

Detalle en `resultados_fase4.md`.

- Comparar muestras con el reloj del host mide el cociente de **dos cristales**. El host solo
  es fiable si NTP lo disciplina: en Linux `CLOCK_MONOTONIC`, en macOS y Windows `time.time_ns`.
- **Estimador**: envolvente inferior de las marcas por bloque (los callbacks solo llegan tarde)
  y ajuste Theil-Sen con cortes en los saltos de > 5 ms. En sintético da σ ≈ 0.14 ppm en 60 s.
- **Niveles de confianza**:

  | Nivel | Fuente | Incertidumbre típica |
  |---|---|---|
  | 0 | Sin calibrar | ±30 ppm declarados |
  | 1 | Reloj del host sin disciplinar | Nunca se aplica |
  | 2 | Reloj del host disciplinado por NTP | 0.1–1 ppm |
  | 3 | Referencia externa (PPS/GPS, cuarzo) o calibración manual | < 0.5 ppm |

- **Persistencia**: `adc_calibration.json` por dispositivo y tasa de muestreo, con un
  envejecimiento del cristal de 1 ppm/año. La calibración viaja en las etiquetas
  `ADV_TG_ADC_DRIFT_*` de cada grabación, y `measure` la aplica sola.
- **Decisiones del usuario (25/10)**:
  - se mantiene el criterio "Bulova ≈ +8 ppm con el CM18";
  - soporte PPS completo (acústico y eléctrico);
  - SNTP propio aplazado.
- El CM18 va a unos **+8 a +9.5 ppm** (≈ −0.7 a −0.8 s/d de sesgo).

### Fase 5: integración completa (v0.6.0)

Detalle en `resultados_fase5.md`.

- **Cifras**: mediana de 7 actualizaciones (1.75 s); se ocultan (`---.-`) con confianza < 0.15
  y se atenúan con < 0.30.
- **Barra de calidad**: SNR × fracción de batimientos útiles, con `⚠` si hubo saturación.
- **Osciloscopio** con los P1/P2/P3 reales.
- **Cinta de papel** con autoescala.
- **Reloj**: `+8.10 ± 0.50 ppm (nivel 3)`.
- **Criterio cumplido**: 4 actualizaciones/s y ~30 FPS. El análisis va en un proceso aparte;
  dibujar las curvas densas con 1 px sin antialiasing eliminó los tirones (0 bloqueos > 33 ms).
- **Primera prueba con el CM18** (Seiko 4R36): +5.6 s/d, 0.05 ms, 287°, audio recibido 100 %.
  La dispersión entre sesiones (+4.4 a +10 s/d) es real: el tac es muy ruidoso y la ventana de
  30 s se llena al principio de cada sesión.
- **Ruido de motores**: subir el corte inferior del filtro a 2–4 kHz desestabiliza el error de
  batimiento, así que no se cambia sin una grabación con ruido real. Mientras tanto, aislar
  mecánicamente el soporte (espuma).

## 6. Betas

### Beta 1 (v0.6.0), `resumen_beta1.md`

- Duraciones de 30 s a 1 h y estabilización de 10, 15 o 20 s.
- **Modo cuarzo**: pasos de 1 a 16 por segundo; solo marcha.
- **Calibración manual del reloj**: nivel 3.
- **"Acerca de"**.
- **Limpieza**: fuera `HardwareAudioCapture`, la carga de libsndfile por ctypes, los
  codificadores por línea de comandos y el guardado Ogg.
- **Logs en la carpeta del usuario**: `~/Library/Logs/adv_tg`, `%LOCALAPPDATA%\adv_tg\logs` o
  `~/.local/state/adv_tg/logs`; `ADV_TG_LOG_DIR` la cambia.
- **Tras la beta 1**: núcleo DSP sin SciPy, port línea a línea con coeficientes idénticos.
  Extensión C++ con pybind11, un 12–19 % más rápida. Cinta de 1 h redibujada en 7 ms. La
  grabación se limita a pruebas de ≤ 240 s.

### Beta 2 (v0.6.1), `resultados_beta2.md`

- **Escalones de la marcha en el 4R36**: causa confirmada. El punto de referencia del tac salta
  entre lóbulos (±0.9–1.7 ms) al refrescarse la plantilla.
- **Histéresis de P1/P3**: probada y **revertida**. Estabiliza el vivo, pero mueve las cifras
  validadas (el H-50 se aleja de tg).
- **Adoptado**:
  - mediana de 20 actualizaciones (5 s) para el error de batimiento mostrado;
  - **cinta de papel real**: un punto por batimiento en ms, tic y tac en hileras separadas,
    pendiente = marcha. Antes sumaba ms a s/d.
- Hallazgo para la fase 6: la correlación entre plantillas debe ser **normalizada**.
- La extensión C++ se retiró después: el motor vuelve a ser Python puro.

### Beta 3 (v0.6.3), 27/10

- Endurecimiento de seguridad (sección 8.5).
- CI con chequeos de estado y solo Python 3.12.

## 7. Validación consolidada

Banda de aceptación: el intervalo entre tg 0.8.0 y WAM, ±1.5 s/d, ±0.15 ms y ±12°. Las cifras
son las finales de la fase 3 (`measure` por lotes).

| Grabación | Referencia (s/d / ms / °) | adv_tg | ¿En banda? |
|---|---|---|---|
| Miyota 9015 (28800, LA 51°) | tg +8/0.1/295 · WAM +7/0.1/304 | +8.06 / 0.15 / 293° | ✅ |
| Hamilton H-50 nuevo (21600, LA 50°) | tg +9/0.3/293 · WAM +9/0.3/290 | +8.10 / 0.35 / 297° | ✅ |
| Hamilton H-50 original (saturado) | tg +10/0.2/280 | +11.46 / 0.27 / 292° | ✅ |
| Seiko 4R36 (21600, LA 53°) | tg +4/0.3/275 · WAM +3/0.2/272 | −2.98 / 0.20 / 269° | ⚠️ marcha fuera |
| Bulova NM10 (cuarzo) | deriva de la tarjeta → −0.72 s/d | −0.71 s/d | ✅ |

- **4R36**: todos los estimadores, incluido uno independiente, dan una marcha negativa en esa
  grabación. Lo más probable es que tg y WAM midieran en otro momento. Queda como test `xfail`
  estricto.
- **Ruido por paso**: 0.2–1.4 ms, lejos de las decenas de µs de un cronocomparador comercial.
  Es la mejora pendiente más importante.
- Las referencias son medidas en vivo. Para la fase 6 conviene medir con tg y WAM sobre la
  reproducción del mismo archivo.

## 8. Auditorías e informes externos (Gemini)

### 8.1 Auditoría de código v0.2.0, `01_informe_auditoria_codigo_23_octubre_2026.md`

| Hallazgo | Estado al 27/10 |
|---|---|
| Cinta de papel sumaba ms a s/d | ✅ Corregido (beta 2) |
| La GUI recalculaba P1/P2/P3 asumiendo que el máximo era P3 | ✅ Corregido (fase 5: instantes del motor) |
| **"Guardar posición" sin telemetría inventa datos** (`POSITION_VARIATION`, confianza 0.97) | ❌ **Abierto** [verificado]: `main_window.py`, `save_current_position` |
| Respaldo a Ogg Vorbis (con pérdidas) | ✅ Eliminado (beta 1) |
| Logs en `parents[3]` del paquete | ✅ Corregido (carpeta del usuario) |
| `HardwareAudioCapture` sin uso | ✅ Eliminado |
| Estadísticas posicionales incompletas (DU−DD, horizontal−vertical) | Sin verificar |
| LA del C07 en `watch_db.csv` (51° frente a 53° de ETA) | Sin verificar |

### 8.2 Optimizaciones para el release, `02_optimizaciones_release_23_octubre_2026.md`

| Propuesta | Estado al 27/10 |
|---|---|
| Sacar `tools/` y `ml/` del paquete | Siguen en el paquete |
| Sacar `_FakeStream` de `capture_process.py` | Sigue en el código [verificado] |
| `minmax_decimate` sigue en `adv_tg.mock` y lo importa `capture_worker.py` | Abierto [verificado] |
| `null_engine.py` (respaldo que nunca se usa) | Sigue en el código [verificado] |
| `soundfile` como dependencia | ✅ Hecho |
| Doble copia por bloque en el IPC, estadísticas O(1) en la GUI, lecturas en float32 | Sin verificar |

### 8.3 Fallos del CI en macOS y Windows, `03_diagnostico_fallos_ci_py312_23_octubre_2026.md`

Seis causas propuestas:

1. falta `QT_QPA_PLATFORM=offscreen` (✅ ya está en los tres workflows);
2. faltaba libsndfile (✅ resuelto con `soundfile`);
3. Named Pipes de Windows y `spawn` con cobertura;
4. resolución del temporizador de ~15.6 ms en Windows y pausas de la VM en macOS;
5. archivos bloqueados en Windows (`faulthandler.log`);
6. señales Qt emitidas desde hilos de Python (`MockStreamWorker`).

La causa 4 sigue siendo la principal de los fallos intermitentes. El 27/10 se diagnosticó con
los registros reales de GitHub Actions: el criterio "callback < 2000 µs" de `capture_check`
falla en las máquinas compartidas del CI aunque la ingesta sea del 100 %. Hay un arreglo en
pruebas: los tests de repetición no dependen de ese tiempo, y el límite de producción vuelve a
2000 µs.

### 8.4 Hoja de ruta C/C++, `c_cpp_roadmap.md` (v0.4.0, 25/10)

- Recomendaba un port **híbrido** (audio, buffer y DSP en C++ con pybind11; 75–115 h) en lugar
  de reescribir todo (305–430 h).
- Prometía 1 000–1 400× tiempo real y un solo proceso.
- **Resultado real**: la extensión C++ solo del DSP dio un 12–19 % más de velocidad, y se
  revirtió a Python puro. El cuello de botella medido en vivo son las métricas
  (`fit_parity_weighted`, 60 % del tiempo), no el detector. Este documento queda como
  referencia histórica.

### 8.5 Seguridad: `security_.md` (v0.4.0) y `security_recommendations.md` (v0.6.1)

`security_.md` es la auditoría anterior. `security_recommendations.md` la sustituye: confirma
mitigados el DLL hijacking y los subprocesos sin timeout, y deja 11 hallazgos. Estado tras los
PR #30 y #36:

| ID | Hallazgo | Estado |
|---|---|---|
| SEC-01 | Inyección de fórmulas en CSV | ✅ `sanitize_csv_cell` (respeta los números) |
| SEC-02 | Datos personales en el reporte de fallos | ✅ `anonymize_text` (la carpeta personal pasa a `~`; el usuario solo como componente de ruta o palabra de ≥ 4 caracteres) |
| SEC-03 | Audio sin límite de memoria | ✅ `MAX_IN_MEMORY_BYTES` = 8 GiB (muestras × canales × 8) |
| SEC-04 | ctypes de `adjtimex` sin tipos | ✅ |
| SEC-05 | Rangos del CSV de calibres | ✅ BPH 1 000–120 000, LA 10–90° |
| SEC-06 | Permisos de las carpetas de logs y configuración | ✅ 0700 en POSIX |
| SEC-07 | HTML en las etiquetas de Qt | ✅ `html.escape` |
| SEC-08 | `pickle` en el IPC | ❌ No implementado (defensa en profundidad, prioridad P2) |
| SEC-09 | Nombre de prueba en los nombres de archivo | ✅ `safe_filename_base` |
| SEC-10 | `PyOpenGL_accelerate` | ✅ Eliminado (`pip-audit` y `bandit` en el CI: sin hacer) |
| SEC-11 | Conteo de comentarios Vorbis | ✅ |

Hallazgos de `security_.md` que no pasaron al informe nuevo: inyección en logs (CWE-117) y
excepciones silenciadas en el callback de audio (solo un contador, sin el tipo de excepción).

## 9. Diferencias entre documentos y código

- **C++**: `resumen_beta1.md` describe `native_dsp.cpp` y `setup.py`; ya no existen. El motor es
  Python/NumPy puro.
- **Versión de Python**: `resumen_beta1.md` dice ≥ 3.11; el código exige ≥ 3.12.
- **Callback de `capture_check`**: los documentos dicen 2000 µs; el PR #32 lo subió a 5000. La
  copia de trabajo actual lo devuelve a 2000 (sin commit).
- `fases-nucleo.md` describe la v0.5.0 y marca la fase 5 como pendiente; está terminada.
- `resultados_fase5.md`, sección 5, dice que el papel dibuja la marcha ± medio error de
  batimiento; la beta 2 lo cambió a una cinta real.
- `resultados_fase5.md`, sección 5, dice que la GUI no tiene modo cuarzo; la beta 1 lo añadió
  (casilla "Cuarzo").

## 10. Pendientes consolidados

**Medida (fase 6)**:

1. Bajar el ruido por paso (0.2–1.4 ms).
2. Estabilizar el inicio de P1/P3 de las plantillas en vivo (saltos de lóbulo del 4R36), con
   correlación normalizada; validarlo con grabaciones de 2–5 min medidas a la vez con tg/WAM.
3. Crear un sintético con lóbulos de forma cambiante que reproduzca los saltos.
4. Grabar con ruido de motores (60–90 s) y otra igual en silencio, y evaluar un paso alto solo
   para la detección, el descarte por suelo de ruido y la sustracción espectral.
5. Decidir si la cinta se proyecta a P1.

**Reloj (fase 4)**:

6. Crear la herramienta en vivo `python -m adv_tg.tools.adc_clock` (no existe [verificado]).
7. Validar con el Bulova (≈ +8 ppm con el CM18, 20–30 min).
8. Montar el PPS con el GPS NEO-6M y un buzzer de 4 kHz.

**Producto y código**:

9. No inventar telemetría al guardar una posición sin medición.
10. Mover `minmax_decimate` fuera de `mock`; decidir qué hacer con `tools/`, `ml/`,
    `_FakeStream` y `null_engine.py` en el paquete.
11. Volver a dar soporte a Python 3.11, o actualizar el README.
12. Seguridad: SEC-08 (`pickle`); `pip-audit` y `bandit` en el CI.

## 11. Índice de documentos de origen

Retirados de `docs/` en el commit `2187796`; se consultan con `git show 2187796^:docs/<archivo>`.

| Documento | Tema | Fecha / versión |
|---|---|---|
| `fases-nucleo.md` | Plan por fases, reglas, fórmulas y referencias | v0.5.0, 26/10 |
| `resultados_fase2_win.md` | Detector en sintético (Windows) | 24/10 |
| `resultados_pruebas_fase2.md` | Detector con grabaciones reales, plantillas, iteración 7 | 24/10 |
| `fase3_resultados.md` | Especificación y resultados de las métricas; cuartos | 24/10 |
| `resultados_fase4.md` | Reloj de la tarjeta, niveles, PPS, persistencia | 26/10 |
| `resultados_fase5.md` | Integración en la GUI, primera prueba con el CM18, tirones | 26/10 |
| `resumen_beta1.md` | Memoria técnica de la beta 1 y del periodo posterior | v0.6.0 |
| `resultados_beta2.md` | Escalones de la marcha, cinta de papel real | 26/10 |
| `01_informe_auditoria_codigo_23_octubre_2026.md` | Auditoría de código (Gemini) | v0.2.0 |
| `02_optimizaciones_release_23_octubre_2026.md` | Limpieza para el release (Gemini) | v0.2.0 |
| `03_diagnostico_fallos_ci_py312_23_octubre_2026.md` | Fallos del CI en macOS y Windows (Gemini) | 23/10 |
| `c_cpp_roadmap.md` | Evaluación de la migración a C/C++ (Gemini) | v0.4.0 |
| `security_.md` | Auditoría de seguridad anterior (Gemini) | v0.4.0 |
| `security_recommendations.md` | Auditoría de seguridad vigente (Gemini) | v0.6.1 |
