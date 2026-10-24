# Fase 3: métricas (criterios y resultados)

Fecha de creación: 24 de octubre de 2026. Rama `feature/detection`.

Este documento fija los criterios con los que se diseña y se acepta la fase 3 (marcha, error de
batimiento y amplitud). La sección "Resultados" se completará al terminar la fase.
Antecedentes: `docs/resultados_pruebas_fase2.md`, secciones 6 y 7.

**Estado (24/10/2026):** implementada en la rama `feature/metrics` y verificada (sección 6).
Pendiente de commit y de la confirmación del usuario.

## 1. Objetivo

Calcular, a partir de la salida del detector de la fase 2, las tres magnitudes del
cronocomparador: marcha (s/d), error de batimiento (ms) y amplitud (°). Cada cifra debe llevar
su incertidumbre y la condición en que se midió, y deben ser estables en una ventana de 30 s
(criterio de `docs/fases-nucleo.md`).

Fuera de alcance:

- la corrección de la deriva del reloj del ADC (fase 4);
- la integración en la GUI (fase 5);
- la validación formal contra tg (fase 6).

## 2. Entradas (contrato con la fase 2)

La fase 3 solo recibe la lista de `Beat` y las plantillas del detector, nunca el audio.

| Entrada | Origen | Uso en la fase 3 |
|---|---|---|
| `Beat.k` | detector | índice entero del batimiento: robusto ante pérdidas; su paridad distingue tic de tac |
| `Beat.t_fine` | plantillas por paridad | instante para la marcha y el error de batimiento (NaN durante el calentamiento o si no correlaciona) |
| `Beat.flags` | detector | exclusiones: `SATURATED`, `TIMING_OUTLIER`, `AFTER_GAP` |
| `BeatDetector.templates()` | detector | `onset_p1`, `onset_ref`, `t_lift_samples`, `p3_method` y `n_beats` de cada paridad |
| `Beat.p1/p2/p3` | detector | solo diagnóstico; **no** se usan para las métricas (frágiles cuando tic y tac suenan distinto) |
| Configuración | usuario o base de calibres | BPH nominal y ángulo de levantamiento (LA); si el LA no se conoce, se usa 52° y se avisa |

Todos los instantes son índices absolutos de muestra en el reloj del ADC.

## 3. Requisitos de diseño (derivados de la fase 2)

### 3.1 Marcha

- **Modelo:** regresión robusta `t = a + P·k + c·(−1)^k` sobre `t_fine`, con índice entero `k`.
  Nunca mediana de intervalos: el "Periodo" de `detect_beats` llegó a sesgarse el equivalente a
  +15 s/d en el cuarzo.
- **Ponderación por paridad, obligatoria:**
  - cada paridad pesa 1/σ² de su propio residuo, con la σ estimada de forma robusta (MAD) y
    recortando atípicos a 5σ por paridad;
  - **por qué:** en el H-50 y el 4R36 el tac tiene 500–830 µs de jitter frente a 110–220 µs del
    tic. Sin ponderar, la marcha del H-50 nuevo pasa de +8.1 a +10.7 s/d.
- **Fórmula:** marcha = `(P_nominal / P − 1) · 86400` s/d (positivo = adelanta).
- **Incertidumbre estadística:** a partir de la covarianza de la regresión ponderada. Se
  reporta junto a la marcha.
- **Marcha por ventanas y tendencia:**
  - además del promedio de la ventana larga, dar la marcha por ventanas (por defecto 10 s,
    configurable) y la pendiente de esa marcha a lo largo de la toma;
  - avisar cuando la tendencia supera su incertidumbre;
  - **por qué:** en el 4R36 la marcha subió de −2 a +2 s/d dentro de 30 s (el reloj se estaba
    asentando).
- **Sin corrección del reloj del ADC:** es la fase 4. Se deja el punto de enganche (ppm) en la
  API y se avisa de que la marcha no está calibrada.

### 3.2 Error de batimiento

- **Se mide en los inicios de P1 de las plantillas:** el P1 de cada batimiento es
  `t_fine − (onset_ref − onset_p1)` de su paridad. Después se aplica la misma regresión
  ponderada por paridad, y el error de batimiento es `2·|c|` (la mitad de la diferencia entre
  los intervalos tic→tac y tac→tic, como en `fases-nucleo.md`).
- **Respaldo:** si alguna plantilla no tiene `onset_p1`, se usa el `c` de `t_fine` y se avisa
  del cambio de punto de medida.
- **Resolución:** se reporta con 0.1 ms; por debajo de ~0.05 ms se da como "< 0.1 ms".

### 3.3 Amplitud

- **Fórmula:** `A = LA / (2·sin(π·f·t_lift))`, con `f = BPH / 7200` y el t_lift de **las
  plantillas** (`ParityTemplate.t_lift_samples`, inicio de P1 → inicio de P3). Convención
  elegida por el usuario.
- **Se reportan las amplitudes de tic y tac y su media.** Si difieren más de 20° tras el
  selector de la fase 2, se avisa.
- **Validez:**
  - fuera de 90–400° no se da cifra;
  - si el LA es el valor por defecto, se marca la amplitud como orientativa;
  - ±1° de LA supone ≈ ±2 % de amplitud, y esa incertidumbre se declara.
- **No se usa la mediana de los t_lift por batimiento:** dio 250° en el 4R36 frente a
  272–275° de referencia.

### 3.4 Exclusión de batimientos y calidad

- **Se excluyen de las regresiones** los batimientos `SATURATED`, sin `t_fine` o con
  `TIMING_OUTLIER`. `AFTER_GAP` no se excluye, porque `k` ya contempla el hueco.
- **Recorte robusto por paridad:** 5σ (MAD).
- **Mínimo de batimientos por cálculo:** 10 por paridad, configurable. Por debajo no se da
  cifra.
- **Confianza:** combina el número de batimientos usados, la σ de cada paridad, la fracción
  excluida y el desacuerdo de amplitud entre tic y tac. Debe estar documentada y es una
  ordenación, no una probabilidad.

### 3.5 Arquitectura y entregables (reglas de `fases-nucleo.md`)

- **Módulo puro sin Qt:** `src/adv_tg/core/metrics.py`, con funciones pequeñas, type hints y
  unidades en los nombres (`rate_s_per_day`, `beat_error_ms`, `amplitude_deg`).
- **Modo incremental (ventana deslizante) y modo por lotes**, con resultados idénticos sobre los
  mismos datos.
- **Herramienta de consola:** `python -m adv_tg.tools.measure` (WAV/FLAC → marcha, error de
  batimiento, amplitud, ventanas y tendencia), con salida JSON y CSV, y gráfica opcional con el
  extra `plot`.
- **Tests headless en CI:** sintético con verdad exacta, más grabaciones reales que se saltan si
  no están.
- **Sin dependencias nuevas** sin preguntar. Los agentes no hacen commit, push ni checkout.

## 4. Criterios de aceptación

### 4.1 Sintético (`tests/synth.py`, verdad exacta)

| Caso | Marcha | Error de batimiento | Amplitud |
|---|---|---|---|
| Limpio, 18000 / 21600 / 28800 / 36000 BPH, 30 s, SNR 30 dB | ±0.2 s/d | ±0.03 ms | ±2 % |
| Con realismo (`REALISM`) | ±0.3 s/d | ±0.05 ms | ±3 % |
| Dos lóbulos en la caída de los impares (`odd_drop_lobe`) | ±0.3 s/d | ±0.05 ms | ±3 % |
| Rampa antes de la caída de los impares (`odd_pre_ramp`) | ±0.3 s/d | ±0.05 ms | ±3 % |
| Marcha −300 / 0 / +300 s/d | ±0.3 s/d | — | — |
| Error de batimiento 0 / 0.5 / 1.5 / 3 ms | — | ±0.05 ms | — |
| Amplitud 160 / 220 / 280 / 320° | — | — | ±3 % |
| Paridad con 4 veces más jitter que la otra | ±0.3 s/d **con** ponderación; el test debe mostrar que sin ponderar se sale | — | — |
| Marcha que cambia linealmente dentro de la toma | tendencia recuperada a ±20 % y aviso emitido | — | — |

Además, en sintético:

- **Estabilidad:** en ventanas de 30 s, la desviación de las cifras entre ventanas consecutivas
  es ≤ 3 veces la incertidumbre estadística declarada.
- **Equivalencia de modos:** el incremental y el de lotes dan el mismo resultado (±1e-9).
- **Incertidumbre:** la declarada cubre el error real en ≥ 90 % de 50 semillas.

### 4.2 Grabaciones reales frente a las referencias

Referencias: `docs/samples/tests.csv` (tg 0.8.0 y Watch Accuracy Meter, mismas condiciones) y,
para la toma original del H-50, `fases-nucleo.md`.

**Banda de aceptación = [mínimo, máximo] de las referencias ± tolerancia:**

| Magnitud | Tolerancia | Justificación |
|---|---|---|
| Marcha | ±1.5 s/d | Diferencia entre instrumentos (1 s/d) + error estadístico de 30 s + medidas en vivo en otro intervalo |
| Error de batimiento | ±0.15 ms | Diferencia entre instrumentos (0.1 ms) + resolución |
| Amplitud | ±12° | Diferencia entre instrumentos (9°) + LA ±1° (≈ ±6°) |

| Grabación | Referencia (s/d / ms / °) | Resultado esperado (prototipo de validación de la fase 2) |
|---|---|---|
| `miyota_9015.flac` (28800, LA 51°) | tg +8/0.1/295 · WAM +7/0.1/304 | +8.06 / 0.15 / 293° |
| `hamilton_h50new.flac` (21600, LA 50°) | tg +9/0.3/293 · WAM +9/0.3/290 | +8.08 / 0.36 / 297° |
| `hamilton_h50.flac` (21600, LA 50°) | tg +10/0.2/280 | +11.31 / 0.25 / 291° |
| `4r36.flac` (21600, LA 53°) | tg +4/0.3/275 · WAM +3/0.2/272 | −3.15 / 0.34 / 269° (marcha fuera de banda: la grabación tiene otra marcha, ver informe de la fase 2, 7.3) |
| `bulova_nm10.flac` (cuarzo) | deriva de la tarjeta +8.3 ppm según tg → −0.72 s/d | −0.71 s/d; solo marcha, sin error de batimiento ni amplitud |

Criterios:

- **La fase 3 no puede empeorar** ninguna cifra del prototipo de validación. Cualquier cambio
  respecto a esa columna se justifica.
- **El error de batimiento y la amplitud** deben quedar dentro de banda en los cuatro calibres
  mecánicos.
- **La marcha** debe quedar dentro de banda en todos salvo el 4R36. Para el 4R36 se exige:
  - que la herramienta avise de la tendencia de la marcha dentro de la toma;
  - que la última ventana sea coherente con las referencias, como comprobación, no como
    criterio estricto.
- **El Bulova** debe reproducir la deriva de la tarjeta a ±0.3 s/d.
- **Estabilidad en ventanas de 30 s** (criterio de `fases-nucleo.md`): las cifras de ventanas de
  30 s desplazadas 5 s no varían más de 2 s/d, 0.1 ms y 10° en las grabaciones limpias
  (Miyota y H-50 nuevo).

### 4.3 No regresión

- Los 288 tests actuales siguen pasando.
- `check_samples` da OK en las cuatro grabaciones mecánicas y `--self-test` sale con código 0.
- Ruff sin avisos (`ruff check` y `ruff format --check`, como en el CI).
- El rendimiento del detector con métricas no baja de ~90× el tiempo real a 48 kHz.

## 5. Mejoras recomendadas para la validación (no bloquean la fase 3)

- **Grabaciones más largas:** ≥ 2 min tras ≥ 1 min de reposo, con la posición, el estado de
  carga y la temperatura anotados.
- **Medir tg y WAM sobre la reproducción del mismo archivo**, para comparar exactamente el
  mismo intervalo (fase 6).
- **Grabar el Bulova en la misma sesión**, como referencia del reloj del ADC (fase 4).
- **Más posiciones** (CA, CB y verticales) para ejercitar los avisos de dispersión de amplitud
  y de marcha.

## 6. Resultados

Rama `feature/metrics` (sobre v0.3.0), macOS, Python 3.12.13, sin commit.

### 6.1 Entregables

| Archivo | Contenido |
|---|---|
| `src/adv_tg/core/metrics.py` | Módulo puro sin Qt (detalle debajo) |
| `src/adv_tg/tools/measure.py` | CLI `python -m adv_tg.tools.measure`: informe, `--json`, `--csv` (una fila por ventana), `--plot` (con el extra `plot`), `--quartz`, `--window`, `--adc-drift-ppm`, `--chunk` |
| `tests/test_metrics.py` | 37 tests unitarios sobre batimientos y plantillas construidos a mano |
| `tests/test_metrics_synthetic.py` | 27 tests de extremo a extremo (audio sintético → detector → métricas) |
| `tests/test_metrics_samples.py` | 19 tests sobre las grabaciones reales frente a `tests.csv` |
| `tests/test_measure_tool.py` | 8 tests de la herramienta CLI |
| `tests/synth.py` | Opciones nuevas: `odd_jitter_us` (una paridad más ruidosa) y `rate_drift_s_per_day_per_min` (deriva lineal de la marcha) |

Contenido de `metrics.py`:

- **Funciones:** `select_beats`, `fit_parity_weighted`, `rate_from_period`, `rate_trend`,
  `window_rates`, `beat_error_from_fit`, `amplitude_from_templates`, `compute_metrics` y
  `format_beat_error_ms`.
- **Clases:** `MetricsConfig`, `Metrics` y `MetricsTracker` (modo incremental).

### 6.2 Implementación frente a la especificación

| Requisito | Implementación | Estado |
|---|---|---|
| Exclusión de batimientos | `SATURATED` y `t_fine` NaN; `AFTER_GAP` se conserva; `p1/p2/p3` no se usan. `TIMING_OUTLIER` también se conserva tras la optimización 6.8 (1) | ✅ (modificado) |
| Mínimo por paridad | 10 por defecto (configurable); por debajo, NaN con aviso | ✅ |
| Recorte robusto | 5σ por paridad con σ = 1.4826·MAD, iterado hasta que el conjunto no cambia | ✅ |
| Marcha | `t = a + P·k + c·(−1)^k` ponderada con 1/σ² por paridad; incertidumbre de la covarianza `(XᵀWX)⁻¹`; además, la incertidumbre por bloques y la que se reporta (la mayor de las dos), ver 6.8 (2) | ✅ |
| Hook del ADC | `adc_drift_ppm` (> 0 = la tarjeta va rápida); con 0 se avisa de "marcha sin calibrar" | ✅ |
| Ventanas | Consecutivas, de 10 s por defecto, con la misma regresión; solapadas opcionales (`window_step_s`, `--window-step`) | ✅ |
| Tendencia | Término cuadrático en la regresión global; σ por *jackknife* de 6 bloques; aviso si supera 3σ | ✅ (con cambio de método, ver 6.5) |
| Error de batimiento | `t_p1 = t_fine − (onset_ref − onset_p1)` de cada paridad, misma regresión, 2·\|c\|; si falta P1, se usa `t_fine` y se avisa | ✅ |
| Formato | Resolución de 0.1 ms; por debajo de 0.05 ms, "< 0.1 ms" | ✅ |
| Amplitud | Solo con el t_lift de las plantillas; tic, tac y media; fuera de 90–400°, NaN | ✅ |
| Avisos de amplitud | ±1° de LA declarado; con LA por defecto (52°), "orientativa"; aviso si tic y tac difieren > 20° | ✅ |
| Confianza | Producto documentado de: número de batimientos, σ de cada paridad, fracción excluida y desacuerdo de amplitud | ✅ |
| Incremental frente a lotes | `MetricsTracker` aplica la misma función a los batimientos de la ventana deslizante; idéntico (±1e-9) | ✅ |

Añadido no pedido: `rate_sigma_block_s_per_day`, la incertidumbre de la marcha por el mismo
*jackknife* de bloques. En las grabaciones reales es 2–4 veces la de la covarianza, porque el
ruido temporal real no es blanco (sección 6.4).

### 6.3 Sintético (detector + métricas frente a la verdad exacta)

30 s, SNR 30 dB, marcha +5 s/d, error de batimiento 0.4 ms y 280° (salvo en los barridos).

| Caso | Δ marcha (s/d) | Δ error de batimiento (ms) | Δ amplitud | Tolerancia | |
|---|---|---|---|---|---|
| Limpio, 18000 | −0.007 | −0.005 | −0.15 % | ±0.2 / ±0.03 / ±2 % | ✅ |
| Limpio, 21600 | −0.000 | −0.024 | −0.29 % | ±0.2 / ±0.03 / ±2 % | ✅ |
| Limpio, 28800 | −0.000 | +0.007 | −0.27 % | ±0.2 / ±0.03 / ±2 % | ✅ |
| Limpio, 36000 | +0.004 | −0.002 | −0.35 % | ±0.2 / ±0.03 / ±2 % | ✅ |
| `REALISM`, 21600 / 28800 | +0.051 / +0.010 | +0.023 / −0.004 | −0.60 / −0.78 % | ±0.3 / ±0.05 / ±3 % | ✅ |
| Dos lóbulos, 21600 / 28800 | +0.018 / −0.006 | −0.002 / +0.004 | −0.15 / −0.27 % | ±0.3 / ±0.05 / ±3 % | ✅ |
| Rampa, 21600 / 28800 | +0.119 / +0.165 | +0.014 / +0.004 | +0.36 / +0.69 % | ±0.3 / ±0.05 / ±3 % | ✅ |
| Marcha −300 / 0 / +300 | +0.004 / −0.008 / +0.004 | — | — | ±0.3 | ✅ |
| Error de batimiento 0 / 0.5 / 1.5 / 3 | — | +0.004 / +0.001 / −0.002 / −0.001 | — | ±0.05 | ✅ |
| Amplitud 160 / 220 / 280 / 320° | — | — | −0.13 / −0.24 / −0.27 / −0.39 % | ±3 % | ✅ |

**Requisitos estadísticos:**

| Prueba | Resultado | Criterio | |
|---|---|---|---|
| Jitter 4× en una paridad (60 / 240 µs, 30 semillas, 21600) | Ponderado: 30 de 30 dentro de ±0.3 s/d. Sin ponderar: varias fuera y error cuadrático medio > 1.8× el ponderado (en la exploración con 100/400 µs: 0.32 frente a 0.127 s/d) | Ponderado dentro; sin ponderar falla | ✅ |
| Deriva lineal de 15 y 30 s/d por minuto (40 s) | Recuperada a < 20 % (exploración: 30 → 30.6 / 29.9; 10 → 10.5 / 9.9) con aviso | ±20 % y aviso | ✅ |
| Marcha constante | Sin aviso de tendencia en ningún caso sintético | — | ✅ |
| Ventanas de 30 s consecutivas (90 s, modo incremental) | Δ marcha y Δ error de batimiento ≤ 3σ | ≤ 3σ | ✅ |
| Incremental frente a lotes (salida real del detector) | Marcha, error de batimiento, amplitud y tendencia idénticos | ±1e-9 | ✅ |
| Cobertura de la incertidumbre (50 semillas, 20 s, jitter de 50 µs) | Error dentro de 1σ en el 70 % y de 2σ en el 94 %; sesgo −0.005 s/d | ≥ 90 % a 2σ | ✅ |

### 6.4 Grabaciones reales

Medido con `python -m adv_tg.tools.measure`, de una pasada y por bloques de 4096 muestras.

| Grabación | Marcha (s/d) | σ covarianza / bloques | Error de batimiento (ms) | Amplitud tic/tac → media | Tendencia (s/d por min) | Usados par/impar (excluidos) | Confianza |
|---|---|---|---|---|---|---|---|
| Miyota 9015 | +8.06 | 0.15 / 0.38 | 0.15 ± 0.03 | 293/294 → 293° (±6°) | +0.7 ± 12.1 | 113/114 (18) | 0.62 |
| H-50 nuevo | +8.10 | 0.18 / 0.50 | 0.35 ± 0.06 | 304/290 → 297° (±6°) | −2.8 ± 19.4 | 77/83 (16) | 0.27 |
| H-50 original | +11.46 | 0.11 / 0.13 | 0.27 ± 0.05 | 290/293 → 292° (±6°) | +3.4 ± 4.2 | 62/75 (44) | 0.44 |
| Seiko 4R36 | −2.98 | 0.16 / 0.62 | 0.20 ± 0.26 | 268/270 → 269° (±5°) | +8.1 ± 12.9 | 83/83 (15) | 0.32 |
| Bulova (cuarzo) | −0.71 | 0.00 / 0.00 | — | — | −0.0 ± 0.0 | 2316/2350 (50) | 0.56 |

*Cifras finales, tras las optimizaciones de 6.8. El error de batimiento se da con la incertidumbre
reportada (la mayor entre covarianza y bloques). Las cifras anteriores a las optimizaciones
están en 6.8.*

**Frente a la banda de referencia** (tg 0.8.0 y WAM ± 1.5 s/d / 0.15 ms / 12°):

| Grabación | Referencia | Marcha | Error de batimiento | Amplitud | ¿No peor que el prototipo de la fase 2? |
|---|---|---|---|---|---|
| Miyota 9015 | +7…+8 / 0.1 / 295…304° | ✅ +0.06 | ✅ +0.05 | ✅ −2° | ✅ (+8.06 / 0.15 / 293 → +8.06 / 0.15 / 293) |
| H-50 nuevo | +9 / 0.3 / 290…293° | ✅ −0.90 | ✅ +0.05 | ✅ +4° | ✅ (+8.08 / 0.36 / 297 → +8.10 / 0.35 / 297) |
| H-50 original | +10 / 0.2 / 280° (`fases-nucleo`) | ✅ +1.46 | ✅ +0.07 | ✅ +12° | ✅ dentro de 2σ (+11.31 / 0.25 / 291 → +11.46 / 0.27 / 292) |
| Seiko 4R36 | +3…+4 / 0.2…0.3 / 272…275° | ❌ −5.98 (fallo esperado documentado) | ✅ dentro | ✅ −3° | ✅ (−3.15 / 0.34 / 269 → −2.98 / 0.20 / 269) |
| Bulova | deriva de la tarjeta → −0.72 s/d | ✅ −0.71 (±0.3) | sin cifra ✅ | sin cifra ✅ | ✅ |

Notas:

- **H-50 original:** sus cifras se alejan algo del prototipo (+0.15 s/d en marcha) porque ahora
  se excluyen sus 29 batimientos saturados (el prototipo no excluía ninguno). Sigue dentro de
  banda y de 2σ.
- **Error de batimiento del 4R36:** 0.20 ± 0.26 ms. La incertidumbre por bloques muestra que en
  esta toma solo se conoce a ±0.26 ms (tac con 830 µs de jitter). Valores entre 0.2 y 0.6 ms
  son igual de compatibles; dentro de banda, pero no es una cifra precisa.
- **Marcha del 4R36 fuera de banda:**
  - Todos los estimadores coinciden en una marcha negativa para esta grabación: P1, P2, P3,
    `t_fine`, la autocorrelación de la envolvente y un cronocomparador independiente (sección
    7.3 del informe de la fase 2).
  - **Corrección respecto a la fase 2:** con la tendencia robusta, la deriva dentro de la toma
    **no es significativa** (+7.9 ± 10.9 s/d por minuto con `t_fine`; +4.3 ± 3.0 con P1, que
    es 1.4σ).
  - La explicación de que "el reloj se estaba asentando" no se sostiene estadísticamente. Lo
    más probable es que tg y WAM midieran en otro momento.
  - Por eso el aviso de tendencia **no salta** en el 4R36, y un estimador honesto no debe
    hacerlo. El requisito de "verificar el aviso de tendencia en el 4R36" no se cumple.
  - Queda como un test `xfail` estricto, que avisará si en el futuro pasa.
- **El aviso de tendencia no salta en ningún reloj estable.** Con la versión inicial (pendiente
  de las tasas por ventana) daba falsos avisos de −23 s/d por minuto en el Miyota y −39 en el
  H-50 nuevo.

**Estabilidad (modo incremental, el de la GUI):**

| Criterio | Miyota 9015 | H-50 nuevo | |
|---|---|---|---|
| Ventanas de 30 s cada 5 s | 1 ventana: la toma dura 30.6 s | 1 ventana: la toma dura 30.0 s | ⚠️ no verificable (test saltado) |
| Ventanas de 20 s cada 5 s (adaptación): Δ marcha | 1.09 s/d | 1.39 s/d | ✅ ≤ 2 |
| Δ amplitud | 7° | 1° | ✅ ≤ 10° |
| Δ error de batimiento | 0.07 ms | 0.18 ms | ⚠️ el H-50 supera 0.1 ms (el criterio es para 30 s; con 20 s se acota a 0.2 ms) |

Para verificar el criterio de 30 s hacen falta tomas de ≥ 45 s.

### 6.5 Desviaciones respecto a la especificación (justificadas)

1. **Método de la tendencia.**
   - **Especificado:** la pendiente de las tasas por ventana frente a su incertidumbre.
   - **Implementado:** término cuadrático de la regresión global, con incertidumbre por
     *jackknife* de bloques.
   - **Motivo:** con tomas de 30 s solo hay 2 ventanas de 10 s. La σ de cada ventana no recoge
     el ruido correlacionado de la señal real y daba falsos avisos en relojes estables.
   - En sintético, el método implementado recupera la deriva a < 6 % y no da falsos avisos. Las
     tasas por ventana se siguen calculando y mostrando.
2. **El aviso de tendencia del 4R36 no salta:** la deriva no es significativa (6.4).
3. **Estabilidad con ventanas de 30 s:** no verificable con las grabaciones actuales. Se añade
   una adaptación a 20 s.
4. ~~Rendimiento del Bulova por debajo de 90×~~: **resuelto** con la optimización 6.8 (3), que
   lo sube de 78–84× a ~100×.

### 6.6 No regresión y calidad

- **Tests:** 383 pasan (288 anteriores + 95 nuevos), 2 saltados y 1 `xfail` (los de 6.4).
  La suite tarda ~100 s; antes de la fase, 69 s.
- **Ruff:** `ruff check src/ tests/` y `ruff format --check src/ tests/` limpios (67 archivos).
  Ruff no depende de la plataforma; la ejecución del CI en Linux y Windows queda pendiente del
  push.
- **`check_samples` y `--self-test`:** código 0.

**Rendimiento de detector + métricas a 48 kHz:**

| Señal | Tiempo real | Coste de las métricas |
|---|---|---|
| Grabaciones mecánicas reales (tras 6.8) | 110–128× | 2–10 ms |
| Bulova (cuarzo, 300 s, tras 6.8) | 99–100× | ~20 ms |
| *Antes de 6.8: sintético `REALISM` de 60 s a 21600 / 28800 / 36000 BPH* | *114× / 106× / 97×* | *3–4 ms* |
| *Antes de 6.8: reales / Bulova* | *100–117× / 78×* | — |

### 6.7 Sugerencias de optimización (las de alcance de la fase 3 se aplicaron en 6.8)

1. **Recuperar los batimientos `TIMING_OUTLIER`:**
   - Esa marca se pone por el P3 de cada batimiento, y `t_fine` es independiente de él.
   - Sin excluirlos, las cifras no cambian más allá del ruido (H-50 original: +11.45 frente a
     +11.48 s/d) y se usan entre un 11 % y un 30 % más de batimientos (H-50 original: 137
     frente a 106).
   - El recorte robusto ya elimina los atípicos reales de `t_fine`.
   - Cambiar la especificación para excluir solo `SATURATED` y `t_fine` NaN.
2. **Reportar la incertidumbre por bloques como la principal en datos reales:** la de la
   covarianza subestima 2–4 veces cuando el ruido es correlacionado.
3. **Usar la confianza en la GUI (fase 5)** para ocultar o atenuar cifras. Hoy va de 0.23 (H-50
   nuevo, tac ruidoso) a 0.64 (Miyota).
4. **Grabaciones de validación más largas** (≥ 2 min tras reposo) para verificar la estabilidad
   a 30 s y la tendencia, y **medir con tg y WAM sobre la reproducción del mismo archivo**.
5. **Rendimiento con cuarzo:** perfilar el detector a 57600 BPH, en la plantilla y la mediana
   por batimiento, si el cuarzo va a usarse como referencia del ADC en la fase 4.
6. **Ventanas solapadas** (paso de la mitad de la ventana) para dibujar la marcha en la GUI, con
   la tendencia calculada aparte, como ahora.

### 6.8 Optimizaciones aplicadas

De las sugerencias de 6.7, estas entraban en el alcance de la fase 3:

| # | Optimización | Cambio | Efecto medido |
|---|---|---|---|
| 1 | Conservar los batimientos `TIMING_OUTLIER` | `select_beats` solo excluye `SATURATED` y `t_fine` NaN. Esa marca juzga el P3 de cada batimiento, no `t_fine`; el recorte robusto por paridad elimina los atípicos reales | Batimientos usados: H-50 nuevo 145 → 160, H-50 original 106 → 137, 4R36 149 → 166. Cifras sin cambios fuera del ruido |
| 2 | Incertidumbre realista | `rate_uncertainty_s_per_day` y `beat_error_uncertainty_ms` = la mayor entre la σ de covarianza y la σ por *jackknife* de bloques (nueva también para el error de batimiento). La CLI informa esa | Miyota: marcha ±0.38 en lugar de ±0.15. 4R36: error de batimiento ±0.26 en lugar de ±0.10 |
| 3 | Rendimiento con cuarzo | Detector: la mediana de la plantilla madura se recalcula cada `template_refresh_beats` batimientos de su paridad (2; el menor valor que cumple ≥ 90× con cuarzo) y se cachea la mediana del t_lift acumulado | Bulova 84× → **100×**; Miyota 99× → 111×; H-50 112× → ~128× |
| 6 | Ventanas solapadas | `MetricsConfig.window_step_s` y `--window-step` (por defecto, consecutivas). La tendencia no las usa | Para dibujar en la GUI (fase 5) |
| — | Test del log de la fase 1 | `test_child_logs_to_its_own_file` corta el log en bytes, no en caracteres. Arreglo pendiente desde el informe de Windows | Fallaba en local tras acumular caracteres con acento; ahora pasa |

**Fuera de alcance:**

- (3) usar la confianza para ocultar o atenuar cifras: es de la GUI, fase 5;
- (4) grabaciones más largas y tg/WAM sobre el mismo archivo: lo hace el usuario, fase 6.

**Probado y descartado: congelar el punto de referencia de `t_fine`.**

- **Idea:** en una caída ambigua (rampa), el inicio por nivel se mueve cuando la plantilla se
  recalcula y `t_fine` da pequeños saltos. Congelar el punto lo evita.
- **En sintético** reduce cinco veces el error de marcha de la rampa a 21600 BPH (error
  cuadrático medio de 0.144 a 0.030 s/d).
- **En las grabaciones reales** el **marco de la plantilla deriva** lentamente: al final de la
  toma del Miyota, el punto congelado y el inicio vivo difieren ~0.4 ms.
- Con el punto congelado, el error de batimiento del Miyota pasaba a 0.31 ms y el Bulova
  perdía la deriva de la tarjeta (−1.16 s/d, σ 1188 µs). El inicio vivo sigue al punto físico.
- **Conclusión:** se mantiene el inicio vivo. El precio es el ruido de marcha en caídas con
  rampa: 0.14 s/d de error cuadrático medio y hasta 0.30 s/d en el sintético de 21600 BPH,
  justo en la tolerancia. Queda como límite conocido.
- **Posible mejora futura:** anclar el marco de la plantilla al punto físico, por ejemplo
  recentrando los segmentos guardados en cada refresco.

**Ajustes de tests asociados (criterios explícitos, sin relajar tolerancias de la especificación):**

- **Asimetría de jitter:** pasa a 80/320 µs (sigue siendo 4×). Con 60/240 µs la parte "sin
  ponderar falla" dependía del azar (máximo 0.279 s/d, a un pelo de 0.3). Ahora la versión
  ponderada no falla ninguna de 30 semillas y la sin ponderar falla 5.
- **Rampa del detector:** el caso estricto usa rampas de nivel 0.4 y 0.5 (desacuerdo entre tic
  y tac de 30–74°, el selector actúa siempre).
  - El nivel 0.35 a 21600 BPH queda justo en el umbral de 20° (16–23° según el refresco) y
    pasa a un test propio con la cota documentada: amplitud media a ±3 %, que se cumple con
    +2.7 %.
