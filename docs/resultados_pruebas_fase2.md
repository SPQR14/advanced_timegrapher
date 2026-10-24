# Resultados de las pruebas de la fase 2 (macOS, grabaciones reales)

Fecha: 24 de octubre de 2026. Rama `feature/detection` (commit `18d6d2f`).
Valida lo que quedó pendiente en `docs/resultados_fase2_win.md`, sección "Qué no se pudo verificar".

## Resumen

- **Todas las pruebas descritas pasan.** `check_samples` sale con código 0: los dos Hamilton y el
  Miyota dan **OK** y el Bulova da **INFO**. `test_beat_detector_samples.py` pasa 4 de 4 (con la
  nueva grabación del H-50) y la suite completa del CI pasa 252 de 252, sin fallos ni saltos.
- **El detector cumple los criterios estructurales** en señal real: conteo, alternancia tic/tac,
  P3 ≥ 95 % e invarianza al troceado.
- **La precisión temporal por batimiento en señal real es mucho peor que en sintético:**
  σ del residuo de 290–630 µs, frente a ~5 µs. Un estimador por correlación cruzada baja a ~100 µs
  en el Miyota, así que hay margen de mejora en el detector.
- **Miyota 9015:** la medida es coherente entre tres estimadores independientes:
  marcha +8.1 a +8.2 s/d y error de batimiento < 0.1 ms.
- **Hamilton H-50:** pasa el veredicto, pero sus magnitudes de medida no son fiables:
  - 12 P3 mal asignados;
  - t_lift bimodal;
  - discrepancia de ~3 s/d entre estimadores;
  - saturación en la grabación.
- **Nueva grabación del H-50** (`hamilton_h50new.flac`, pico −9.4 dBFS, sin saturación; sección
  2.6): resuelve los fallos de **detección** (P1 del 63 % al 97 %, P3 atípicos de 12 a 2, t_lift
  ya no bimodal), pero no los de **medida**: la marcha sigue variando ~5 s/d según el estimador
  y el error de batimiento, entre 0.1 y 1.7 ms. El ruido de fondo, 15 dB mejor, explica más
  mejora que la ausencia de saturación.
- **Referencia de tg para la toma nueva del H-50** (sección 2.7): +7 s/d, 0.3 ms, 272°.
  - **Marcha:** el detector sobre P3 la reproduce (+7.67 ± 0.54 s/d).
  - **Amplitud:** sale ~30° alta (303°), porque el t_lift es ~1 ms corto (P1 débil detectado
    tarde).
  - **Error de batimiento:** sale 5 veces más alto (1.5 ms), en P1, P2 y P3 por igual.
  - En el Miyota las tres magnitudes coinciden con tg.
- **Normalizar el audio no arregla nada** (sección 3). El detector es exactamente invariante a
  la ganancia. Limitar o comprimir los picos sesga la medida igual que la saturación, y un AGC
  la empeora. La solución está en la adquisición, no en el post-proceso.
- **El campo "Periodo" de `detect_beats` no sirve como estimador de marcha.** Da −0.017 % en el
  cuarzo (equivale a +15 s/d), cuando la regresión da −0.4 s/d.

**Conclusión (actualizada en las secciones 6 y 7):** contrastado con tg 0.8.0 y Watch Accuracy
Meter (`docs/samples/tests.csv`), el detector queda dentro de banda en error de batimiento y
amplitud en todos los calibres, y en marcha en todos salvo el Seiko 4R36, cuya grabación tiene
otra marcha que la medida en vivo (sección 7.3). **Antes de la iteración 7:** el error de batimiento y la amplitud del H-50 se
han corregido con una **alineación fina por plantillas de tic y tac** en el detector
(`Beat.t_fine`, `BeatDetector.templates()`). Con ella las tres grabaciones con referencia de
tg quedan a ≤ 1.4 s/d en marcha y ≤ 0.25 ms en error de batimiento, y la amplitud del H-50 baja
de +31° a +25° (Miyota, +2°). El Bulova reproduce la deriva de la tarjeta que mide tg
(−0.69 frente a −0.72 s/d).

## Entorno

| | |
|---|---|
| Máquina | macOS (Darwin 27.0.0), Apple Silicon |
| Python | 3.12.13 (`.venv`) |
| soundfile / libsndfile | 0.14.0 / 1.2.2 (rueda con libsndfile incluida, sin instalar nada del sistema) |
| PyQt6 / Qt | 6.11.0 / 6.11.2 |
| matplotlib | instalada en el venv solo para `--plot` (sigue siendo opcional) |

El venv no tenía `soundfile` (dependencia nueva de la fase 2). Se reinstaló con
`pip install -e .[dev]`, igual que hace el CI.

### Grabaciones (`docs/samples/`)

| Archivo | Formato | fs | Duración | Pico | Muestras saturadas | Calibre (tabla interna) |
|---|---|---|---|---|---|---|
| `hamilton_h50.flac` | FLAC PCM_16, mono | 48 kHz | 30.52 s | 0.0 dBFS | 183 (49 rachas) | 21600 BPH, LA 50° |
| `hamilton_h50new.flac` | FLAC PCM_16, mono | 48 kHz | 30.01 s | −9.4 dBFS | 0 | 21600 BPH, LA 50° |
| `miyota_9015.flac` | FLAC PCM_16, mono | 48 kHz | 30.61 s | 0.0 dBFS | 1 | 28800 BPH, LA 51° |
| `bulova_nm10.flac` | FLAC PCM_16, mono | 48 kHz | 299.99 s | −3.5 dBFS | 0 | cuarzo, 16 pasos/s (57600 "BPH"), informativo |

No consta la posición, el estado de carga ni la temperatura de las grabaciones. Por eso, las
marchas de este informe sirven para validar el software, no para diagnosticar los relojes.

## 1. Pruebas pedidas en `resultados_fase2_win.md`

### 1.1 `python -m adv_tg.tools.check_samples` (código de salida 0)

Tabla guardada en `logs/check_samples_20261024_110630.md`:

| Archivo | Esperados | Encontrados | Perdidos | Rechazados | P1/P2/P3 % | t_lift med | Amplitud implícita | σ residuo | Tic/tac | Invariante | Tiempo real | Veredicto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bulova_nm10.flac | 4799.3 | 4738 | 62 | 134 | 81/22/84 | 3.18 ms | — | 354.4 µs | n/d | sí | 151× | **INFO** |
| hamilton_h50.flac | 182.3 | 181 | 2 | 289 | 63/75/100 | 8.42 ms | 315° | 289.2 µs | sí | sí | 174× | **OK** |
| hamilton_h50new.flac | 179.5 | 180 | 0 | 497 | 97/97/100 | 8.77 ms | 303° | 388.2 µs | sí | sí | 160× | **OK** |
| miyota_9015.flac | 244.1 | 245 | 0 | 359 | 98/85/100 | 6.84 ms | 297° | 630.6 µs | n/d | sí | 148× | **OK** |

- **Grabación nueva:** la fila de `hamilton_h50new.flac` sale de la segunda ejecución,
  `logs/check_samples_20261024_113147.md`. Las demás filas no cambiaron.
- **Enganche:** 2.25 s en todos, igual que en sintético.
- **Tic/tac "n/d" en el Miyota:** el error de batimiento es ~0, así que no hay alternancia que
  comprobar. No es un fallo.
- **Rechazados:** salen más candidatos rechazados que batimientos (en sintético salían 0). Son
  sub-eventos y ecos de la señal real, y las defensas contra clics los descartan como deben.

### 1.2 `pytest tests/test_beat_detector_samples.py -v`

```
test_real_mechanical_recording[hamilton_h50.flac]                PASSED
test_real_mechanical_recording[hamilton_h50new.flac]             PASSED
test_real_mechanical_recording[miyota_9015.flac]                 PASSED
test_real_quartz_recording_is_informational[bulova_nm10.flac]    PASSED
4 passed in 9.27s
```

### 1.3 Lint y suite completa (mismos comandos que el CI)

```
ruff check src/ tests/            -> All checks passed!
ruff format --check src/ tests/   -> 60 files already formatted
pytest -v --cov=adv_tg            -> 252 passed in 68.19s (cobertura total 74 %)
```

- **Comparación con Windows** (244 passed, 4 skipped, 1 failed): los tests que se saltaban por
  falta de grabaciones ahora se ejecutan y pasan.
- **`test_child_logs_to_its_own_file` pasa en esta máquina.** El log local no tenía caracteres
  multibyte en la zona leída. El fallo en Windows sigue siendo un defecto latente del test
  (mide bytes y corta caracteres) y el arreglo propuesto sigue pendiente.

| Módulo | Cobertura |
|---|---|
| `beat_detector` | 99 % |
| `beat_types` | 96 % |
| `detect_beats` | 95 % |
| `audio_files` | 89 % |
| `check_samples` | 89 % |

### 1.4 `check_samples --self-test` (código de salida 0)

Resultado idéntico al de Windows:

| Archivo | Encontrados / esperados | t_lift | σ residuo | Veredicto |
|---|---|---|---|---|
| selftest_bulova_nm10.flac | 320 / 319.6 | — | 4.0 µs | INFO |
| selftest_hamilton_c07_h50.flac | 119 / 119.0 | n/d | 4.8 µs | OK |
| selftest_miyota_9015.wav | 160 / 159.1 | 7.52 ms | 4.8 µs | OK |

## 2. Análisis adicional de las grabaciones reales

Los criterios de `check_samples` validan la **detección**. Para saber si los instantes sirven
para **medir** en la fase 3, se contrastó la salida del detector con dos estimadores
independientes:

- **Regresión offline** sobre los instantes P3 y P1 del detector, con el modelo
  `t = a + P·k + c·(−1)^k`, ajuste robusto (recorte a 5 MAD) y marcha por ventanas de 10 s.
- **Correlación cruzada de envolventes:** cada batimiento se alinea con una plantilla mediana
  (banda de 1–15 kHz, Hilbert, interpolación parabólica sub-muestra). No usa umbrales ni
  etiquetado de sub-eventos.
- **Cronocomparador de referencia** externo (`timegrapher.py`, que toma el umbral de inicio y
  mide el t_lift del primer al último sonido). Es más tosco y solo sirve como tercera opinión.

### 2.1 Miyota 9015: coherente

| Estimador | Marcha (s/d) | Error de batimiento (ms) | σ residuo (µs) |
|---|---|---|---|
| adv_tg, regresión sobre P3 | +8.05 ± 0.31 | 0.03 | 499 |
| adv_tg, regresión sobre P1 | +8.11 ± 0.23 | 0.05 | 342 |
| Correlación cruzada | +8.19 | 0.08 | **96** |
| `timegrapher.py` (referencia) | +8.19 (σ ventanas 0.62) | 0.47 | 233 |

```
Calibre / BPH: Miyota 9015 / 28800   Posición: desconocida   Carga: desconocida   LA: 51° (tabla)
Marcha:   +8.1 s/d  (±0.3 s/d estadístico; ventanas de 10 s: +10.3 / +10.4 / +7.5 s/d)
Amplitud: ~297° (implícita, t_lift mediano 6.84 ms; p5–p95 6.07–7.60 ms)
Error de batimiento: < 0.1 ms
```

- **Marcha:** los cuatro estimadores coinciden en ±0.15 s/d.
- **Error de batimiento:** tres de cuatro dan < 0.1 ms. El 0.47 ms de `timegrapher.py` es un
  artefacto de su umbral sobre un inicio ruidoso.
- **Amplitud:** ~297° es físicamente sano.
  - Un error de ±1° en el LA cambia la amplitud un ~2 %. El LA del 9015 se publica como 51–52°.
  - Los 157° de `timegrapher.py` no son creíbles: su t_lift "primer a último sonido" (12.9 ms)
    incluye la cola de reverberación.
- **Jitter:** el σ del residuo de adv_tg (340–500 µs) es 3.5–5 veces el de la correlación cruzada
  (96 µs) sobre la misma señal. El ruido no está en el reloj, está en la estimación del inicio.
  - La traza de residuos es ruido blanco de ±1 ms, sin ondulación periódica atribuible al tren
    de rodaje.
  - El t_lift por batimiento oscila ±0.75 ms, lo que equivale a ±30° de amplitud aparente. Eso no
    es físico de batimiento a batimiento: la amplitud debe promediarse.

### 2.2 Hamilton H-50: veredicto OK, medida no fiable

| Estimador | Marcha (s/d) | Error de batimiento (ms) | σ residuo (µs) |
|---|---|---|---|
| adv_tg, regresión sobre P3 (sin 12 atípicos) | +7.42 ± 0.46 | 0.90 | 617 |
| adv_tg, regresión sobre P1 (63 % de batimientos) | +1.19 ± 0.71 | 0.88 | 867 |
| Correlación cruzada | +10.08 | 0.63 | 244 |
| `timegrapher.py` (referencia) | +10.71 (σ ventanas 0.95) | 0.13 | 189 |

Problemas observados:

1. **12 de 181 P3 mal asignados** (6.6 %). Su residuo es de +5 a +11 ms: el detector toma como P3
   un sub-evento posterior.
   - El ajuste robusto los recorta, pero el detector los entrega sin marca.
   - El criterio "P3 ≥ 95 %" no los detecta porque cuenta presencia, no corrección.
2. **El t_lift es bimodal.** Hay un grupo en ~9.0 ms (≈295°, plausible) durante los primeros
   ~12 s y otro en 6.5–8 ms (≈350–390°, no plausible para un reloj quieto) a partir de ~22 s.
   - Entre 15 y 22 s no se resuelve P1.
   - La hipótesis más probable es confusión de P1 con P2 (P2 etiquetado como P1), el límite ya
     descrito en "P1 débil y amplitud baja".
   - No se descarta un cambio real de posición durante la grabación. Hace falta saber cómo se
     grabó.
3. **La marcha discrepa unos 3 s/d entre estimadores.** P3 da +7.4 s/d; la envolvente y la
   referencia dan +10.1 a +10.7 s/d.
   - La deriva relativa P1–P3 a lo largo de los 30 s (~2 ms) cuadra con el salto de t_lift del
     punto 2.
   - Los estimadores que dependen del etiquetado heredan ese error.
4. **Saturación en la grabación:** 49 rachas de muestras a fondo de escala (0.012 % de las
   muestras). Distorsiona el inicio de los sub-eventos más fuertes. Hay que regrabar con menos
   ganancia.

Con estos datos no se puede dar una medida del H-50. El rango plausible es de +7 a +11 s/d, el
error de batimiento de 0.6 a 0.9 ms y la amplitud de ~295° si el grupo de 9 ms es el correcto.

### 2.3 Bulova (cuarzo): sirve como referencia del reloj del ADC

| Estimador | Marcha (s/d) |
|---|---|
| adv_tg, regresión sobre P3 | −0.45 ± 0.02 |
| adv_tg, regresión sobre P1 | −0.28 ± 0.02 |
| `timegrapher.py` (referencia) | −0.78 |

- Un cuarzo debería medir ~0 s/d. El resultado acota el error del reloj de la tarjeta de sonido
  en **≲ 5 ppm** (≈ 0.4 s/d) para esa cadena de grabación.
  - Si las tres grabaciones se hicieron con el mismo equipo, el sesgo sistemático de marcha por
    el ADC es despreciable frente al resto de fuentes.
  - Si no, no aplica.
- **Detección:** 62 batimientos perdidos (1.3 %), P3 en el 84 %, σ del residuo de 354 µs
  (1.1 ms en la regresión).
  - Las marchas por ventana de 10 s oscilan entre −18 y +13 s/d. Es esperable: un paso a paso
    de cuarzo no tiene la estructura P1/P2/P3 de un escape.
  - Es informativo y cumple su función.

### 2.4 El "Periodo" de `detect_beats` está sesgado

| Archivo | "Periodo observado" frente al nominal | Marcha equivalente | Marcha por regresión |
|---|---|---|---|
| bulova_nm10 | −0.017 % | +15 s/d | −0.3 a −0.45 s/d |
| miyota_9015 | −0.020 % | +17 s/d | +8.1 s/d |
| hamilton_h50 | −0.011 % | +9.6 s/d | +7 a +11 s/d |

- El periodo que imprime `detect_beats` (mediana de intervalos consecutivos) **no es un
  estimador de marcha**. En el cuarzo se desvía en el equivalente a ~15 s/d.
- El test solo exige ±1 % del nominal, así que pasa. No se debe reutilizar en la fase 3.
- Se recomienda etiquetarlo como orientativo o cambiarlo por la pendiente de una regresión.

### 2.5 Comparación con los valores de referencia de tg

Valores de `docs/fases-nucleo.md`, medidos con tg y el CM18 sobre las mismas grabaciones. La
comparación formal es de la fase 6; aquí sirve para ver si los instantes de la fase 2 llevarán
a métricas correctas en la fase 3.

| Grabación | Magnitud | tg | adv_tg (P3) | adv_tg (P1) | Correlación cruzada |
|---|---|---|---|---|---|
| Miyota 9015 | Marcha | +9 s/d | +8.05 | +8.11 | +8.19 |
| | Error de batimiento | 0.0 ms | 0.03 | 0.05 | 0.08 |
| | Amplitud | 291° | 297° (implícita) | | |
| Hamilton H-50 | Marcha | +10 s/d | **+7.42** | **+1.19** | +10.08 |
| | Error de batimiento | 0.2 ms | **0.90** | **0.88** | 0.63 |
| | Amplitud | 280° | 315° (implícita, mediana de un t_lift bimodal) | | |
| Bulova NM10 | Deriva | +8.3 ppm (reloj de la tarjeta) | −0.45 s/d ≈ −5 ppm | −0.28 s/d ≈ −3 ppm | — |

- **Miyota:** todo coincide con tg dentro de ~1 s/d, 0.1 ms y 6°. Es la tolerancia esperable
  entre ventanas distintas y un LA de 51° frente a 52°.
- **Hamilton:** la correlación cruzada reproduce tg (+10.1 frente a +10 s/d), pero los
  instantes P3 y P1 del detector no.
  - Con P3 la marcha se va 2.6 s/d; con P1, casi 9 s/d.
  - El error de batimiento sale entre 3 y 4.5 veces más alto.
  - Esto apunta a que el problema del H-50 está en el **etiquetado de sub-eventos del
    detector** y que la fase 3 lo heredaría. La sección 2.6 matiza el papel de la grabación.
- **Bulova:** tg ve la tarjeta +8.3 ppm rápida, y eso hace que el cuarzo parezca atrasar
  ~0.7 s/d. adv_tg mide −0.3 a −0.45 s/d: el mismo signo y el mismo orden, con ~4 ppm de
  diferencia. La medida fina de la deriva es la fase 4.

### 2.6 Nueva grabación del H-50, sin saturación (`hamilton_h50new.flac`)

Grabación de 30.01 s con pico de −9.4 dBFS y ninguna muestra a fondo de escala. Es otra sesión:
los valores de tg de la sección 2.5 corresponden a la grabación original y **no sirven como
referencia para esta**. No consta la posición ni el estado de carga.

**Detección**: mejora clara.

| | Original (`hamilton_h50`) | Nueva (`hamilton_h50new`) |
|---|---|---|
| Pico / muestras saturadas | 0.0 dBFS / 183 | −9.4 dBFS / 0 |
| Ruido de fondo (p20 de la envolvente RMS de 5 ms, banda 1–15 kHz) | −38 dBFS | **−53 dBFS** |
| SNR de envolvente (p99,9 / mediana) | ≈ 28 dB | **≈ 36 dB** |
| Encontrados / esperados, perdidos | 181 / 182.3, 2 | 180 / 179.5, **0** |
| P1 / P2 / P3 | 63 / 75 / 100 % | **97 / 97 / 100 %** |
| P3 atípicos (residuo de +5 a +11 ms) | 12 | **2** |
| t_lift mediana / IQR | 8.42 / 1.49 ms (bimodal) | 8.77 / 0.99 ms (unimodal) |
| t_lift tic / tac | 8.96 / 7.83 ms | 8.83 / 8.27 ms |
| Tendencia del t_lift en 30 s | −1.99 ms | −0.87 ms |
| Amplitud implícita (mediana) | 315° | 303° |
| Veredicto `check_samples` | OK | OK |

**Medida**: sigue sin ser coherente.

| Estimador | Marcha (s/d) | Error de batimiento (ms) | σ residuo (µs) |
|---|---|---|---|
| adv_tg, regresión sobre P3 (2 atípicos fuera) | +7.67 ± 0.54 | 1.49 | 714 |
| adv_tg, regresión sobre P1 | +5.22 ± 0.90 | 1.69 | 1149 |
| Correlación cruzada | +10.18 | 0.68 | 666 |
| `timegrapher.py` (referencia; descarta el 11 % como atípicos) | +7.78 (σ ventanas 0.53) | 0.11 | 294 |

Por mitades de la grabación:

| Mitad | Marcha P3 | Marcha P1 | BE P3 | BE P1 | σres P3 |
|---|---|---|---|---|---|
| 0–15 s | +6.13 s/d | +5.34 s/d | 1.19 ms | 0.88 ms | 801 µs |
| 15–30 s | +7.99 s/d | +4.96 s/d | 1.78 ms | 2.76 ms | 574 µs |

Interpretación:

- **La mejora de la detección es real y se debe sobre todo al SNR.** El ruido de fondo de la
  original era 15 dB peor; en la original, P1 en la primera mitad daba −27 s/d con
  σ = 2.3 ms, que es justo la confusión P1/P2.
  - El experimento de la sección 3 lo confirma: saturar de forma artificial la grabación nueva
    hasta 123 muestras (una cantidad parecida a las 183 de la original) **no** reproduce los
    12 atípicos ni la caída de P1.
  - La saturación sí añade un sesgo propio (~0.8 s/d), pero no fue la causa principal.
- **La medida sigue sin converger.**
  - La regresión sobre P3 da +7.7 y sobre P1, +5.2 s/d. La diferencia es estable entre mitades
    y corresponde a la deriva del t_lift (−0.87 ms en 30 s ≈ 2.5 s/d).
  - Una amplitud que sube ~30° en 30 s no es esperable en un reloj quieto. Hay dos opciones: el
    reloj se estaba asentando (recién dado cuerda o recién colocado) o el detector desplaza el
    instante de P1 o P3 de forma gradual. Con una sola grabación no se pueden separar.
- **El error de batimiento no es fiable en el H-50:** va de 0.11 a 1.7 ms según el estimador.
  - El t_lift difiere entre tic y tac en 0.56 ms. Eso hace que el error de batimiento medido con
    P1 y con P3 difiera por construcción. Puede ser real (asimetría del escape) o del detector.
  - El σ del residuo, 570–800 µs, es comparable al propio error de batimiento: con 90 pares
    tic/tac la incertidumbre de `c` es del orden de 0.1 ms, así que las diferencias de 1 ms son
    sistemáticas, no ruido.
- **Resolución estadística:** con σ ≈ 700 µs, la marcha en 30 s tiene ±0.5 s/d y en ventanas de
  10 s, ±2.8 s/d. Las ventanas de 10 s sobre P3 (+1.0 / +7.5 s/d) caen dentro de ~2σ.
- **Para cerrar el H-50 hace falta una referencia de esta misma grabación:** pasar
  `hamilton_h50new.flac` por tg, que es lo que se hizo con la original. Sin eso solo se puede
  decir que los estimadores discrepan, no cuál acierta.

### 2.7 Comparación con tg de la toma nueva del H-50

tg sobre `hamilton_h50new.flac`: **+7 s/d, error de batimiento 0.3 ms, amplitud 272°** (LA 50°).

| Magnitud | tg | adv_tg P3 | adv_tg P1 | adv_tg P2 | adv_tg (P1+P3)/2 | Correlación cruzada | `timegrapher.py` |
|---|---|---|---|---|---|---|---|
| Marcha (s/d) | +7 | **+7.67 ± 0.54** | +5.22 | +4.59 | +6.47 | +10.18 | +7.78 |
| Error de batimiento (ms) | 0.3 | 1.49 | 1.69 | 1.51 | 1.59 | 0.68 | 0.11 |
| Amplitud (°) | 272 | 303 (implícita, t_lift 8.77 ms) | | | | | 338 |

**Marcha.** La regresión sobre P3 coincide con tg: +0.67 s/d, ~1.2σ.

- P1 y P2 se quedan cortos (−1.8 y −2.4 s/d). Eso indica que la deriva del t_lift vista en 2.6
  es del instante de P1 (y P2), no un cambio real de amplitud. Si la amplitud hubiera cambiado,
  P3 también se habría desviado de tg.
- La correlación cruzada, que acertó con la toma original (+10.1 frente a +10), aquí falla
  (+10.2 frente a +7). Alinear la envolvente completa mezcla sub-eventos y hereda la deriva de
  P1, así que tampoco sirve como árbitro.
- **Conclusión:** P3 es el instante correcto para la marcha, como ya establece el diseño.

**Amplitud.** Para 272° con LA 50° a 21600 BPH, el t_lift debería ser **9.77 ms**; el detector
mide **8.77 ms**.

- La diferencia es de ~1.0 ms, que da ~30° (11 %) de amplitud de más.
- En el Miyota la misma comparación da 6.98 ms esperados frente a 6.84 ms medidos (0.14 ms, 6°):
  el sesgo es propio del P1 débil del H-50.
- La causa más probable es que el inicio de P1 se toma al 40 % de su propio pico: en un P1 débil
  y de subida lenta, ese cruce llega tarde. Un desplazamiento progresivo de ese cruce explicaría
  también la deriva de P1.

**Error de batimiento.** Este es el fallo principal que queda.

- El detector da ~1.5 ms con cualquier sub-evento (P1, P2, P3 o la media de P1 y P3), frente a
  0.3 ms de tg. En la toma original pasaba lo mismo: 0.9 frente a 0.2 ms.
- Que coincida en todos los sub-eventos descarta un sub-evento mal asignado. Todo el tic queda
  desplazado ~0.75 ms respecto al tac.
- El t_lift por paridad (tic 8.83 ms, tac 8.27 ms) y el P3−P2 (4.88 y 4.54 ms) muestran que tic
  y tac tienen formas distintas. Un criterio de inicio relativo al pico de cada sub-evento
  reacciona distinto a cada forma.
- No es una diferencia de definición: con la misma fórmula (`2·|c|`, la mitad de la diferencia
  entre intervalos), el Miyota coincide con tg (0.03 frente a 0.0 ms).
- La causa exacta no está determinada.

**Resumen frente a tg:**

| | Marcha | Error de batimiento | Amplitud |
|---|---|---|---|
| Miyota 9015 | ✅ (−0.9 s/d) | ✅ | ✅ (+6°) |
| H-50, toma nueva | ✅ (+0.7 s/d) | ❌ (×5) | ⚠️ (+31°, t_lift −1 ms) |
| H-50, toma original (SNR bajo) | ❌ (−2.6 s/d) | ❌ (×4.5) | ⚠️ (+35°) |

## 3. Normalización de audio para suprimir picos

Se pidió evaluar si conviene una función de normalización que suprima picos como los de la
grabación original del H-50.

### 3.1 Qué puede y qué no puede hacer una normalización

- **La saturación ocurre antes del software**, en el preamplificador o el ADC. Las muestras que
  llegan a ±1.0 ya perdieron la forma de onda real y ninguna ganancia aplicada después la
  recupera.
  - Reducir la ganancia en software sobre una grabación saturada solo escala una forma ya
    recortada.
  - La única corrección posterior posible es **reconstruir** las muestras recortadas (de-clipping),
    que es otra cosa: una estimación, no un dato.
- Las variantes posibles y su efecto en la medida:

| Variante | Qué hace | Efecto sobre la medida |
|---|---|---|
| Normalización por pico (ganancia global) | Multiplica todo por una constante | Ninguno: el detector es invariante a la ganancia (umbral al 40 % del pico propio, SNR como cociente) |
| Limitador o compresor (supresión de picos) | Reduce de forma no lineal la parte alta | Achata los sub-eventos fuertes, cambia la relación P1/P2/P3 y desplaza los cruces del 40 %. Equivale a saturar |
| AGC (ganancia variable en el tiempo) | Iguala el nivel con una constante de tiempo | Modula la envolvente dentro del batimiento si es rápido, o entre batimientos si es lento. Distorsiona el t_lift y los atípicos |
| De-clipping (reconstrucción) | Interpola las muestras recortadas a partir de su entorno | Puede recuperar en parte la forma del pico. El resultado es una estimación y su error se mete en el instante de P3 |

### 3.2 Experimento sobre la grabación limpia del H-50

Se partió de `hamilton_h50new.flac` (sin saturación) y se le aplicó cada variante. Así se mide
el efecto aislado, con la misma señal y el mismo reloj. Detector a 21600 BPH y LA 50°; la marcha
y el error de batimiento salen de la regresión robusta sobre P3.

| Caso | Muestras ≥ 0.999 | Batimientos | P1 % | t_lift med / IQR (ms) | P3 atípicos | Marcha P3 (s/d) | BE P3 (ms) | σres P3 (µs) |
|---|---|---|---|---|---|---|---|---|
| Original (pico −9.4 dBFS) | 0 | 180 | 97 | 8.77 / 0.99 | 2 | +7.67 | 1.49 | 714 |
| −12 dB (×0.25) | 0 | 180 | 97 | 8.77 / 0.99 | 2 | +7.67 | 1.49 | 714 |
| Normalizado por pico a −1 dBFS | 0 | 180 | 97 | 8.77 / 0.99 | 2 | +7.67 | 1.49 | 714 |
| +12 dB, saturación dura | 123 | 180 | 97 | 8.57 / 0.89 | 2 | **+8.44** | 1.38 | 771 |
| +18 dB, saturación dura | 4138 | 180 | 96 | 8.73 / 0.91 | 2 | **+8.51** | 1.16 | 826 |
| +12 dB, limitador suave tanh (umbral −6 dBFS) | 0 | 180 | 97 | 8.59 / 0.85 | 2 | **+8.42** | 1.40 | 772 |
| AGC lento (ataque instantáneo, liberación 2 s) | 0 | 180 | 94 | 8.92 / **1.98** | **5** | **+9.07** | 1.23 | 842 |
| AGC rápido (ataque instantáneo, liberación 5 ms) | 0 | **125** | 69 | 8.87 / 0.41 | — | n/d | n/d | n/d |

Lectura:

1. **Normalizar por pico no cambia nada:** los resultados son idénticos bit a bit en todas las
   magnitudes. No ayuda ni estorba a la medida; solo sirve para visualizar o para guardar con
   más resolución. La GUI solo baja la ganancia al guardar cuando el pico supera 1.0
   (`audio_io._normalize_samples`), para no recortar al convertir a PCM; no sube el nivel.
   - A −9 dBFS en PCM_16 el ruido de cuantización (≈ −98 dBFS) queda 45 dB por debajo del ruido
     acústico (−53 dBFS), así que tampoco hay ganancia de resolución.
2. **Suprimir picos con un limitador es equivalente a saturar.** El limitador suave y la
   saturación dura dan prácticamente el mismo sesgo: +0.75 s/d de marcha y −0.2 ms de t_lift
   (≈ +7° de amplitud aparente) sobre la misma señal. Esto es un error sistemático, no ruido,
   porque la comparación es pareada.
   - Un limitador no arregla el problema de la grabación original: lo **introduce** en
     grabaciones que estaban bien.
3. **El AGC es lo más dañino.**
   - El lento dobla la dispersión del t_lift, multiplica por 2.5 los P3 atípicos y desplaza la
     marcha +1.4 s/d.
   - El rápido aplana la envolvente dentro del batimiento: se pierde el 31 % de los batimientos
     y la regresión no converge.
4. **La saturación de la grabación original no fue la causa principal de sus fallos:**
   - con 123 muestras saturadas de forma artificial, el detector sigue resolviendo P1 en el 97 %;
   - el factor dominante fue el ruido de fondo (sección 2.6).

### 3.3 Implicaciones y recomendación

- **No conviene incluir normalización, limitación ni AGC en la ruta de medida.**
  - La normalización por pico es inocua pero inútil para el detector.
  - Las otras tres sesgan marcha, t_lift y amplitud de forma sistemática, sin que se note en los
    criterios de detección: todos los casos sin AGC rápido pasarían `check_samples`.
  - Sería un error silencioso, el peor tipo para un instrumento de medida.
- **Donde sí tiene sentido:**
  - **Ruta de visualización** (forma de onda en la GUI, fase 5): normalizar o aplicar AGC solo a
    lo que se dibuja, nunca a lo que se mide. Coincide con la idea de "SNR antes de AGC" de
    `fases-nucleo.md`, que asume que el AGC va después del punto de medida.
  - **Guardado de grabaciones:** normalizar por pico es aceptable, pero conviene guardar en el
    comentario del archivo la ganancia aplicada y el pico original, para no ocultar que la toma
    original saturaba.
- **Lo que sí ayuda contra los picos, por orden de coste:**
  1. **Prevención en la adquisición:** apuntar a picos de −12 a −6 dBFS. `capture_check` ya
     avisa a ≥ −1 dBFS. La barra de calidad de la GUI (fase 5) debería mostrar las muestras
     saturadas en vivo y pedir bajar la ganancia.
  2. **Marcar en vez de corregir:** añadir un indicador de saturación por batimiento en
     `BeatFlags` (alguna muestra ≥ 0.999 dentro de la ventana del batimiento) y que la fase 3
     excluya esos batimientos de marcha, error de batimiento y amplitud, o los pondere a la
     baja. Es barato, no inventa datos y deja trazabilidad.
  3. **De-clipping offline, opcional:** solo como herramienta separada (`tools/`), con aviso
     explícito, para recuperar grabaciones antiguas. Las rachas de la original son cortas
     (183 muestras en 49 rachas, ~3.7 muestras de media), así que una interpolación local
     podría funcionar, pero su efecto sobre P3 hay que validarlo con el mismo experimento
     pareado (saturar una grabación limpia, reconstruir y comparar). Encaja en la fase 6.
- **Coste de no hacer nada:** bajo. Con saturación leve el sesgo medido es de ~0.8 s/d, del
  orden de la incertidumbre estadística en 30 s. El indicador por batimiento (punto 2) lo
  convierte en un aviso en lugar de un sesgo oculto.

## 4. Recomendaciones antes de la fase 3

Primero los bloqueantes; el resto, de menor a mayor coste.

**Estado tras la sección 6:**

- 1 (error de batimiento) y 2 (inicio del P1 débil): **resueltos** con las plantillas por
  paridad.
- 8 (precisión temporal): **resuelto**. El σ por paridad baja a 110–220 µs en las paridades
  limpias.
- 6 (P3 mal asignados): **mitigado**. `t_fine` no depende del P3 de cada batimiento.
- 4 y 9: pasan a la fase 3 (sección 6.6).
- 5, 7 y 10 siguen abiertos.


1. **Error de batimiento en el H-50 (bloqueante):** averiguar por qué tic y tac quedan
   desfasados ~0.75 ms en todos los sub-eventos, cuando tg mide 0.3 ms. Hay que reproducirlo en
   sintético con tic y tac de formas distintas (envolventes asimétricas) y verdad conocida.
2. **Inicio del P1 débil (bloqueante para la amplitud):** el t_lift del H-50 sale ~1 ms corto y
   P1 deriva. Probar un umbral de inicio relativo al ruido local, o un ajuste de la pendiente de
   subida, en lugar del 40 % del pico propio. Validar con el sintético de P1 débil y con
   `hamilton_h50new.flac` frente a tg (272°).
3. **En las próximas tomas:** anotar posición, estado de carga y temperatura y, si se puede,
   grabar el cuarzo en la misma sesión como referencia del ADC.
4. **Calcular la marcha de la fase 3 solo por regresión robusta** con término tic/tac
   (`a + P·k + c·(−1)^k`), nunca con la mediana de intervalos. Marcar como orientativo el
   "Periodo" de `detect_beats`.
5. **Endurecer `check_samples` para que un OK implique calidad de medida, no solo de detección:**
   - umbral de σ del residuo;
   - fracción de atípicos de P3 a más de N·σ;
   - prueba de bimodalidad o dispersión del t_lift;
   - coincidencia de la marcha entre P1 y P3, que delata la deriva de P1.

   Con los criterios actuales, el H-50 original sale OK con 12 P3 mal asignados.
6. **Detector, P3 mal asignados:** rechazar o marcar un P3 cuyo residuo supere unos pocos ms
   cuando el modelo está maduro, en lugar de aceptarlo.
7. **Detector, confusión P1/P2 en el C07/H-50:** añadir `hamilton_h50.flac` (SNR bajo) y
   `hamilton_h50new.flac` como casos de regresión una vez resuelto. Los indicadores son la
   estabilidad del t_lift y la coincidencia de la marcha entre P1 y P3.
8. **Precisión temporal:** estudiar refinar el instante de anclaje por correlación con una
   plantilla adaptativa por sub-evento. En el Miyota bajaría el jitter de ~400 µs a ~100 µs, lo
   que mejora directamente la resolución de la marcha por ventanas cortas y del error de
   batimiento.
9. **Indicador de saturación por batimiento:** ya existe (`BeatFlags.SATURATED`, |x| ≥ 0.99
   en el batimiento). Falta que la fase 3 excluya o pondere a la baja esos batimientos, en
   lugar de normalizar (sección 3.3).
10. **Test de log de la fase 1:** aplicar el arreglo de una línea propuesto en el informe de
   Windows, aunque aquí haya pasado.

## 5. Pendiente de verificar

- **CI de Linux** (ubuntu + xvfb): no se ejecutó aquí.
- **Runner de macOS del CI:** las pruebas se hicieron en la Mac local con los mismos comandos,
  no en el runner.
- **Diagnóstico de los relojes:** fuera del alcance. Faltan posiciones y estado de carga.

## 6. Cierre de la fase 2: alineación fina con plantillas por paridad

Implementado a partir del commit `4f1451a`, sin commit todavía.

### 6.1 Causa del error de batimiento del H-50

Se apilaron las envolventes de todos los tic y de todos los tac sobre una rejilla común
`a + P·k`, sin término de paridad:

- En el **tac** del H-50, la caída (P3) suena en **dos lóbulos y el segundo es el más
  fuerte**. El detector toma como ancla el sub-evento más fuerte, así que P3 sale ~2 ms tarde
  (+1.0 ms frente a −1.1 ms del tic).
- P1 se busca desde ese ancla a una distancia igual al t_lift acumulado y cae en un pico
  intermedio (−7.1 ms) en lugar del inicio real (−10.1 ms).
- **En las envolventes medias, tic y tac empiezan a la vez** (P1 en −10.0 y −10.1 ms), lo que
  cuadra con los 0.3 ms de tg.
- Que el error se viera igual con P1, P2 y P3 se explica porque toda la estructura del tac
  estaba desplazada.

Se descartaron dos alternativas:

- **Unir lóbulos sobre los candidatos discretos:** los valles entre lóbulos cambian demasiado
  de un batimiento a otro.
- **Umbrales fijos sobre la envolvente de cada batimiento:** lo que arregla el H-50 rompe el
  Miyota, cuyo tac tiene P1 y P2 tan fuertes como P3. Sería sobreajustar.

### 6.2 Solución

El concepto de trabajar sobre una forma de onda promediada es el que usa tg; la implementación
es propia y no se ha portado código de tg.

- **Una plantilla por paridad de `k`:** el detector mantiene la mediana de los últimos 24
  batimientos alineados de cada paridad.
- **Alineación:** cada batimiento se alinea con la plantilla de su paridad por correlación
  cruzada, solo sobre el estallido de la caída. Se usa una envolvente suavizada a 1 ms para que
  los lóbulos se fundan y la correlación no salte entre ellos.
- **Inicios sobre la plantilla limpia:**
  - **P3:** primer punto que alcanza el 40 % del máximo en los 2.5 ms anteriores (primer
    lóbulo); desde ahí, cruce hacia atrás del 20 %.
  - **P1:** primera subida sobre el ruido de la plantilla dentro de la ventana física.
- **`Beat.t_fine`:** el inicio de P3 de la plantilla, llevado a cada batimiento. Es el mismo
  punto físico en tic y en tac.
- **`BeatDetector.templates()`:** devuelve las dos plantillas (`ParityTemplate`) con
  `onset_p1`, `onset_p3` y `t_lift_samples`.
- **Siembra:** la plantilla se crea con la predicción de un modelo de fase maduro, nunca con el
  ancla de un batimiento suelto. Durante el arranque se aceptan todos los batimientos.
- **Propiedades:** las plantillas sobreviven a los re-enganches, y el resultado sigue siendo
  invariante al troceado (verificado con `t_fine` incluido).
- **Rendimiento:** baja de ~150× a ~95–120× el tiempo real.
- **Configuración:** 11 parámetros nuevos `template_*`, documentados en `BeatDetectorConfig`.

**Convención de amplitud (decisión del usuario):** t_lift = inicio de P1 → inicio de P3,
medido en las plantillas. tg usa un heurístico propio (suavizado de máximo decreciente con
umbrales escalonados), que no se ha imitado.

### 6.3 Resultados en sintético

Opción nueva del generador: `odd_drop_lobe`, un segundo lóbulo de la caída en los `k` impares,
1.5 ms después y con un nivel aleatorio de 0.6–1.6 veces el primero.

| Caso (30 s, SNR 30 dB) | Error de batimiento real | Con P3 por batimiento | Con `t_fine` | σ `t_fine` |
|---|---|---|---|---|
| Limpio, 28800 | 0.5 ms | 0.500 | 0.501 | 8 µs |
| Dos lóbulos, 28800 | 0.5 ms | **0.942** | 0.494 | 12 µs |
| Dos lóbulos, 21600 | 0.3 ms | **1.084** | 0.296 | 12 µs |
| Dos lóbulos + realismo, 21600 | 0.3 ms | **1.197** | 0.303 | 27 µs |

- El t_lift de las plantillas queda a 1–3 muestras de la verdad, que es menos del 1 % de
  amplitud a 18000, 21600 y 28800 BPH.
- Con un P1 muy débil (10 %), la plantilla devuelve NaN en P1 en vez de un valor erróneo.

### 6.4 Resultados en las grabaciones reales

La marcha y el error de batimiento se calculan con `a + P·k + c·(−1)^k` sobre `t_fine`, cada
paridad ponderada por 1/σ² de su residuo. El error de batimiento se toma en los inicios de P1
de las plantillas. Es el cálculo que se propone para la fase 3; aquí está hecho con un script
de validación.

| Grabación | tg (s/d / ms / °) | Marcha | Error de batimiento | Amplitud | σ tic / tac | `t_fine` |
|---|---|---|---|---|---|---|
| H-50 nuevo | +7 / 0.3 / 272 | +8.08 ± 0.18 | 0.36 | 297° | 146 / 517 µs | 164 / 180 |
| H-50 original | +10 / 0.2 / 280 | +11.31 ± 0.12 | 0.25 | 291° | 111 / 234 µs | 166 / 181 |
| Miyota 9015 | +9 / 0.0 / 291 | +8.06 ± 0.15 | 0.15 | 293° | 216 / 225 µs | 229 / 245 |
| Bulova (cuarzo) | −0.72 (tarjeta +8.3 ppm) | **−0.69** | — | — | 229 / 330 µs | 4690 / 4738 |
| Seiko 4R36 (sin referencia) | ? | −2.97 ± 0.25 | 0.34 | 304° | 134 / 826 µs | 166 / 181 |

Antes de este cambio, con P3 por batimiento:

| Grabación | Marcha | Error de batimiento | Amplitud |
|---|---|---|---|
| H-50 nuevo | +7.67 | **1.49** | 303° |
| H-50 original | +7.42 | **0.90** | 315° |
| Miyota | +8.05 | 0.03 | 297° |

Interpretación:

- **Error de batimiento:** en los dos H-50 pasa de ×5 a ≤ 0.2 ms de diferencia con tg. En el
  Miyota sube de 0.03 a 0.15 ms (tg 0.0), que sigue dentro de la resolución.
- **Marcha:**
  - Las tres quedan a ±1.4 s/d de tg y con el mismo signo de error en los dos H-50.
  - La diferencia restante es del orden de la variación entre ventanas de 10 s (±1–3 s/d) y de
    lo que puede separar a dos programas que no miden sobre el mismo intervalo.
  - El **Bulova** reproduce casi exacto la deriva de la tarjeta que mide tg (+8.3 ppm). Es la
    mejor validación de que `t_fine` mide bien la marcha.
- **Amplitud:** Miyota a +2° de tg y H-50 original a +11°. El H-50 nuevo queda a +25° (9 %).
  Con la convención elegida, ese desvío es una diferencia de definición con tg, no un fallo de
  detección (sección 2.7).
- **El tac del H-50 y del 4R36 es intrínsecamente más ruidoso** (σ de 500–800 µs frente a
  110–220 µs del tic). Por eso la fase 3 **debe ponderar por paridad**; sin ponderar, el tac
  arrastra la marcha del H-50 nuevo hasta +10.7 s/d.
- **Seiko 4R36:** grabación nueva, fuera de muestra (no se ajustó nada con ella). Destapó dos
  fallos de la siembra de plantillas, ya corregidos:
  - una plantilla anclada en P2;
  - una plantilla bloqueada durante el arranque.

  Su tac tiene una rampa de ~3 ms antes del frente de la caída, y el inicio de P3 cae al
  principio de la rampa. **Hace falta la referencia de tg del 4R36** para validarlo. Tampoco
  está en la tabla de calibres de `check_samples` (21600 BPH, LA 53°).

### 6.5 Herramientas y tests

- **`detect_beats`:**
  - imprime la cobertura de `t_fine`, el σ por paridad, el desfase de P1 entre tic y tac y el
    t_lift y la amplitud de las plantillas (como comprobaciones, no medidas);
  - el CSV tiene una columna `t_fine`;
  - el "Periodo" aparece marcado como orientativo.
- **`check_samples`:**
  - tres columnas nuevas;
  - dos criterios de OK nuevos: `t_fine` en ≥ 80 % de los batimientos y t_lift de las
    plantillas dentro del rango físico.
- **Tests:**
  - `tests/test_beat_detector_templates.py`, 24 tests: sintético con y sin dos lóbulos, marcha,
    error de batimiento, amplitud por plantillas, P1 débil, copias y validación de
    configuración;
  - la invarianza al troceado compara también `t_fine`;
  - el test de grabaciones reales exige un desfase de P1 entre tic y tac < 0.5 ms (antes,
    1.5 ms en el H-50).
- **Suite completa:** 279 de 279 pasan. Ruff sin avisos. `check_samples` y `--self-test` salen
  con código 0.

### 6.6 Pendiente para la fase 3

1. Calcular la marcha con la regresión **ponderada por paridad** sobre `t_fine`.
2. Calcular el error de batimiento con los inicios de P1 de las plantillas, cuando existan, y
   si no con `t_fine`.
3. Calcular la amplitud con el t_lift de las plantillas.
4. Excluir o ponderar los batimientos `SATURATED`.

## 7. Iteración con la matriz de referencia (tg 0.8.0 y Watch Accuracy Meter) y el Seiko 4R36

### 7.1 Referencias y condiciones

`docs/samples/tests.csv`:

| Calibre | tg 0.8.0 (s/d / ms / °) | Watch Accuracy Meter (s/d / ms / °) |
|---|---|---|
| Miyota 9015 | +8 / 0.1 / 295 | +7 / 0.1 / 304 |
| Hamilton H-50 | +9 / 0.3 / 293 | +9 / 0.3 / 290 |
| Seiko 4R36 | +4 / 0.3 / 275 | +3 / 0.2 / 272 |

- **Condiciones:** mismo micrófono, misma habitación, esfera arriba y cuerda completa.
- **Watch Accuracy Meter (WAM):** según el usuario, coincide casi siempre con los Weishi 1000 y
  1900 (9 de cada 10 casos).
- **Asignación de las filas:** el H-50 se compara con `hamilton_h50new.flac`, la toma más
  reciente. `hamilton_h50.flac` mantiene su referencia de tg de `fases-nucleo.md`
  (+10 / 0.2 / 280).
- **Ninguna referencia procede de analizar los mismos archivos:** son medidas en vivo, así que
  pueden no cubrir el mismo intervalo de tiempo que la grabación.
- **Incertidumbre de la propia referencia:**
  - tg y WAM difieren hasta 1 s/d, 0.1 ms y 9°;
  - para el H-50 nuevo, tg dio +7 / 0.3 / 272 en una sesión anterior frente a
    +9 / 0.3 / 293 en esta, lo que supone 20° de diferencia entre sesiones del mismo
    instrumento.

### 7.2 Criterios de aceptación y de optimización

**Banda de aceptación:** el intervalo entre tg y WAM, ampliado con estas tolerancias:

| Magnitud | Tolerancia | Justificación |
|---|---|---|
| Marcha | ±1.5 s/d | Diferencia entre instrumentos (1 s/d) + error estadístico de 30 s (±0.2 s/d) + intervalos de medida distintos |
| Error de batimiento | ±0.15 ms | Diferencia entre instrumentos (0.1 ms) + resolución |
| Amplitud | ±12° | Diferencia entre instrumentos (9°) + LA ±1° (≈ ±2 %, ±6°) |

**Reglas para cambiar el algoritmo:**

1. Todo cambio responde a un **mecanismo físico observado en la señal** (lóbulos, rampa,
   frente de impacto, igualdad de amplitud entre tic y tac), no al ajuste de un número de
   referencia.
2. El fenómeno se **reproduce antes en sintético con verdad exacta**: el método anterior debe
   fallar ahí y el nuevo, acertar. Esos casos quedan como tests.
3. **Sin parámetros por calibre:** los mismos valores por defecto para todos los archivos.
4. **No regresión:**
   - las demás grabaciones siguen dentro de banda;
   - la exactitud en sintético no empeora;
   - se mantiene la invarianza al troceado;
   - el rendimiento no baja de ~90× el tiempo real.

### 7.3 Seiko 4R36: lo que encontró la prueba fuera de muestra

Tres problemas en total. Los dos primeros ya se corrigieron en la sección 6.4.

1. **Plantilla de los impares anclada en P2:** en el tac del 4R36, P2 suena tan fuerte como P3
   y la plantilla se sembró sobre el ancla de un batimiento suelto. Corregido: se siembra con
   la predicción del modelo maduro.
2. **Plantilla de los pares bloqueada durante 30 batimientos:** su primer batimiento era
   ruidoso y los siguientes no alcanzaban la correlación mínima. Corregido: durante el
   calentamiento se aceptan todos.
3. **Amplitud del tac de 341° frente a 267° del tic:**
   - **Causa:** una **rampa lenta de ~3 ms antes del frente de la caída**, y el inicio por
     nivel cae al principio de la rampa. Es detectable sin referencias, porque el volante no
     puede tener amplitudes distintas en tic y tac.
   - **Corrección (iteración 7):**
     - **Segunda definición del inicio de P3 en la plantilla, el "frente":** el pie de la
       tangente en el punto de máxima pendiente del primer tramo abrupto (≥ 50 % de la
       pendiente máxima). Es una técnica de detección de llegadas: el sonido de un impacto
       empieza con un frente abrupto y una rampa lenta no lo es. La búsqueda se limita al 40 %
       del t_lift antes del máximo, para dejar fuera P2.
     - **Selector por coherencia física:** se usa el inicio por nivel en las dos paridades
       salvo que sus amplitudes discrepen más de 20°. En ese caso se elige la combinación
       (nivel o frente en cada paridad) cuyas amplitudes coinciden mejor.
     - El frente por sí solo **no** sirve para todos los calibres: en el Miyota, cuyo tac sube
       de forma gradual, daría 332°. Por eso es un respaldo y no un sustituto.
     - `t_fine` sigue marcando el inicio por nivel (`ParityTemplate.onset_ref`), así que la
       marcha y el error de batimiento no cambian con la elección; solo cambia el t_lift.
   - **Sintético** (nueva opción `odd_pre_ramp` del generador):
     - solo con el inicio por nivel, el t_lift de los impares se equivoca en 27–69 muestras
       (hasta 363° frente a 290°);
     - con el selector, todas las combinaciones quedan a 1–4 muestras (< 1 %).
     - **Límite:** una rampa corta a 21600 BPH crea solo 16° de desacuerdo, por debajo del
       umbral. Queda con +7° (2.6 %) de error, dentro de tolerancia.

**Marcha del 4R36: −3.15 s/d frente a +3/+4 de las referencias.** No es un error de detección.

- **Todos los estimadores coinciden en una marcha negativa para esta grabación:**

| Estimador | Marcha (s/d) |
|---|---|
| P1 por paridad (σ 38 / 67 µs, P1 sin ambigüedad) | −5.04 / −5.21 (±0.05–0.07) |
| P2 por paridad | −4.99 / −4.38 |
| P3 por paridad | −3.56 / −3.99 |
| `t_fine`, paridad limpia | −3.24 ± 0.16 |
| Autocorrelación de la envolvente (sin detector) | −4.0 a −5.4 |
| Cronocomparador de la habilidad `relojeria-dsp` (independiente) | −4.98 |

- ~~La marcha sube dentro de la propia toma~~ **(corregido en la fase 3):** las ventanas de
  10 s parecían mostrar una subida (con P1, de −2.1 a +2.3 s/d), pero con un estimador robusto
  (término cuadrático e incertidumbre por *jackknife* de bloques) la tendencia **no es
  significativa**: +4.3 ± 3.0 s/d por minuto con P1 y +7.9 ± 10.9 con `t_fine`. Las ventanas de
  10 s tienen ±2–3 s/d de ruido real (`docs/fase3_resultados.md`, 6.4).
- **Interpretación:** lo sólido es que esta grabación tiene marcha negativa en toda su
  duración. Lo más probable es que tg y WAM midieran el reloj en otro momento.
- **No es la tarjeta:** el mismo equipo da la marcha correcta en el Miyota y el H-50, y el
  Bulova reproduce la deriva de +8.3 ppm.
- **Para comparar la marcha del 4R36** hace falta una grabación más larga, de ≥ 2 min y tras
  ≥ 1 min de reposo, o medir con tg y WAM sobre la reproducción del mismo archivo.

### 7.4 Resultados finales frente a la banda de referencia

Método:

- **Marcha:** regresión robusta `a + P·k + c·(−1)^k` sobre `t_fine`, cada paridad ponderada
  por 1/σ².
- **Error de batimiento:** en los P1 de las plantillas.
- **Amplitud:** t_lift de las plantillas (inicio de P1 → inicio de P3), media de tic y tac.

| Grabación | Referencia | Marcha (s/d) | Δ banda | Error de batimiento (ms) | Δ banda | Amplitud | Δ banda | P3 por | σ par/impar (µs) |
|---|---|---|---|---|---|---|---|---|---|
| Miyota 9015 | tg +8/0.1/295 · WAM +7/0.1/304 | +8.06 ± 0.15 | +0.06 ✅ | 0.15 | +0.05 ✅ | 293° | −2 ✅ | nivel/nivel | 216 / 225 |
| H-50 nuevo | tg +9/0.3/293 · WAM +9/0.3/290 | +8.08 ± 0.17 | −0.92 ✅ | 0.36 | +0.06 ✅ | 297° | +4 ✅ | nivel/nivel | 146 / 517 |
| H-50 original | tg +10/0.2/280 (`fases-nucleo`) | +11.31 ± 0.12 | +1.31 ✅ | 0.25 | +0.05 ✅ | 291° | +11 ✅ | nivel/nivel | 111 / 234 |
| Seiko 4R36 | tg +4/0.3/275 · WAM +3/0.2/272 | −3.15 ± 0.16 | −6.15 ⚠️ (7.3) | 0.34 | +0.04 ✅ | 269° | −3 ✅ | frente/frente | 132 / 832 |
| Bulova (cuarzo) | tarjeta +8.3 ppm según tg → −0.72 s/d | −0.71 | +0.01 ✅ | — | — | — | — | — | 243 / 384 |

**Evolución de las magnitudes del detector a lo largo de las iteraciones:**

| | Fase 2 inicial (P3 por batimiento) | Plantillas (sección 6) | Iteración 7 |
|---|---|---|---|
| Error de batimiento del H-50 nuevo | 1.49 ms ❌ | 0.36 ✅ | 0.36 ✅ |
| Error de batimiento del H-50 original | 0.90 ms ❌ | 0.25 ✅ | 0.25 ✅ |
| Amplitud del 4R36 | 250° (mediana por batimiento) ❌ | 304° ❌ | **269°** ✅ |
| Marcha del Bulova | −0.45 s/d | −0.69 | −0.71 ✅ |
| Jitter de la paridad limpia | 500–700 µs | 110–220 µs | 110–220 µs |

**Estado:**

- `check_samples`: OK en las 4 grabaciones mecánicas e INFO en el cuarzo, con código 0.
- `--self-test`: código 0.
- Tests: 288 de 288, incluidos 8 nuevos de la rampa y el selector, y el test de grabaciones
  reales sobre el 4R36.
- Ruff sin avisos.

### 7.5 Límites conocidos que pasan a la fase 3

- **Jitter de la paridad ambigua:** el tac del H-50 y del 4R36 tiene 500–830 µs, frente a
  110–220 µs de la limpia. **La fase 3 debe ponderar por paridad**; sin ponderar, la marcha del
  H-50 nuevo se iría a +10.7 s/d.
- **Calentamiento:** ~6 batimientos hasta que el modelo de fase madura, más 6 por paridad
  (~3 s a 21600 BPH), sin `t_fine`.
- **Umbral del selector (20°):** desacuerdos menores no se corrigen. En el peor caso
  sintético, +7° (2.6 %).
- **La marcha cambia dentro de una toma** (4R36). La fase 3 debe dar la marcha por ventanas y
  su tendencia, no solo un promedio, y avisar cuando la tendencia supera la incertidumbre.
- **Comparación con las referencias:** las referencias son medidas en vivo. Para la fase 6
  conviene medir con tg y WAM sobre la reproducción del mismo archivo.

## Reproducción

```
pip install -e .[dev]
ruff check src/ tests/ && ruff format --check src/ tests/
python -m adv_tg.tools.check_samples
python -m adv_tg.tools.check_samples --self-test
pytest tests/test_beat_detector_samples.py -v
pytest -v --cov=adv_tg
python -m adv_tg.tools.detect_beats docs/samples/<archivo>.flac --bph <BPH> --lift-angle <LA> --json r.json --plot r.png
```

Los scripts del análisis adicional (regresión offline, correlación cruzada, análisis por
mitades y experimento de ganancia y saturación) no forman parte del repositorio. Se pueden añadir como herramienta si se quieren mantener.
