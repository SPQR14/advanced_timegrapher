# Fase 3: métricas (criterios y resultados)

Fecha de creación: 24 de octubre de 2026. Rama `feature/detection`.

Este documento fija los criterios con los que se diseña y se acepta la fase 3 (marcha, error de
batimiento y amplitud). La sección "Resultados" se completará al terminar la fase.
Antecedentes: `docs/resultados_pruebas_fase2.md`, secciones 6 y 7.

**Estado:** pendiente de inicio. El usuario confirmará el commit de la fase 2 antes de
empezar.

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

*(Pendiente: se completará al terminar la fase 3, con la misma estructura que la sección 4:
tablas de sintético, grabaciones frente a la banda, estabilidad, no regresión y límites
conocidos.)*
