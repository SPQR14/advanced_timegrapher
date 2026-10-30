# Advanced Timegrapher v0.8.0-beta: documentación técnica

Fecha: 30/10/2026. Versión del código: `0.8.0` (etiqueta `v0.8.0-beta`).

Documenta lo que cambia desde la v0.6.3: captura a 44.1 kHz y sin desbordamientos, la
normalización de audio por software, el modo cuarzo, la calibración con un reloj de
referencia y, sobre todo, el **análisis de grabaciones** (defectos cíclicos del tren de rodaje,
vector de características y reglas de diagnóstico), que es la base de la futura etapa de ML.
La arquitectura, las fases 0–5, la validación del núcleo y el marco teórico siguen en
[`documentation_v0.6.3-beta3.md`](documentation_v0.6.3-beta3.md) y
[`marco_teorico_y_matematico.md`](marco_teorico_y_matematico.md).

Las cifras marcadas **[medido]** se obtuvieron con el código de esta versión: en señales
sintéticas de verdad conocida (`tests/synth.py`) o con el hardware indicado.

---

## 1. Resumen

| Área | Cambio | Módulos |
|---|---|---|
| Captura | Opción de **44.1 kHz**; apertura con latencia `"high"` (adiós a los *overflows* en ALSA `hw:`) | `ui/main_window.py`, `ui/capture_worker.py` |
| Señal | **Normalizar audio**: ganancia por software para entradas que saturan sin remedio | `dsp/normalizer.py`, `core/beat_detector.py` |
| Cuarzo | Calibre, A/h y ángulo deshabilitados; 1 paso/s fijo; ya no se reactivan al acabar la sesión | `ui/main_window.py` |
| Calibración | Cálculo del ppm a partir de un reloj de referencia de marcha conocida | `ui/dialogs.py` |
| Análisis | Ventana **Analizar Grabación**: tren de rodaje (TKEO + Lomb-Scargle + FFT), vector de características por ventana de 60 s, reglas de diagnóstico, exportación JSON | `analysis/gear_train.py`, `analysis/features.py`, `ui/analysis_dialog.py` |
| Documentación | Corregida la cifra de la rueda de escape (no es 7.5 s; depende de los A/h) | `index.html`, `docs/` |

## 2. Historial de versiones

Continúa la tabla de la sección 3 de la documentación v0.6.3.

| Versión | Fecha | Contenido |
|---|---|---|
| v0.7.0 (etiquetas `v0.7.0-b4`, `v0.7.0.beta4`) | 28/10 | Error de batimiento y marcha coherentes, recuadro de A/h reales, marco teórico; instaladores DMG (macOS), Inno Setup (Windows) y RPM (Linux) |
| v0.8.0-beta | 30/10 | Este documento |

**Número de versión.** `pyproject.toml` y `src/adv_tg/__init__.py` dicen `0.8.0`, y la
etiqueta de git es `v0.8.0-beta` (mismo criterio que la v0.7.0). No se usa `0.8.0b0` (PEP 440)
a propósito: pip ignora las versiones preliminares si no se pasa `--pre`, y la instalación
desde el release del README (`pip install --find-links … advanced-timegrapher`) dejaría de
encontrarla. `tests/test_smoke.py` comprueba que ambos números coincidan; después de
cambiarlos hay que ejecutar `pip install -e .`.

## 3. Arquitectura: lo nuevo

```
src/adv_tg/
├── analysis/                 NUEVO. Análisis fuera de línea, sin Qt (entrada del futuro ML)
│   ├── gear_train.py         detección → series por batimiento → espectros → ruedas
│   └── features.py           vector de características, diferenciales posicionales, reglas
├── dsp/normalizer.py         NUEVO. Normalizador por bloques fijos (opcional)
├── core/beat_detector.py     opción `normalize` (normalizador antes del detector)
└── ui/analysis_dialog.py     NUEVO. Ventana "Analizar Grabación" (3 pestañas, QThread)
```

`analysis/` depende solo de `core/` y `dsp/`: se puede usar desde scripts, desde las
herramientas de consola o desde un futuro *pipeline* de entrenamiento sin la interfaz.

## 4. Captura

### 4.1 Frecuencia de muestreo de 44.1 kHz

Algunos micrófonos USB en Linux con PipeWire solo abren a la frecuencia a la que PipeWire
tiene el dispositivo, a menudo 44.1 kHz. La barra superior ofrece **44.1, 48 y 96 kHz** (48 por
defecto). El núcleo ya aceptaba cualquier frecuencia ≥ 8 kHz: la banda del detector es
`(1000, min(15000, 0.45 fs))` Hz, válida a 44.1 kHz, y `tests/test_beat_detector_accuracy.py`
ya cubría 44100 Hz. La forma de onda en pantalla usa 960 casillas sobre 0.6 s; a 44.1 kHz se
descartan las 540 muestras más antiguas (12 ms) para que las casillas sean enteras (solo
visualización).

Si PortAudio rechaza la frecuencia (`Invalid sample rate`, `PaErrorCode -9997`), la barra de
estado añade una pista traducida para elegir 44.1 o 48 kHz.

### 4.2 Desbordamientos de entrada (*overflows*)

**Causa.** El worker de la GUI abría el dispositivo con `latency="low"`. En un dispositivo ALSA
directo (`hw:X,Y`), PortAudio deja entonces un búfer del anfitrión de **un solo bloque**
(1024 muestras = 21.3 ms a 48 kHz): cualquier retraso del planificador lo desborda y se pierden
muestras. No era el procesado: el *callback* tarda como máximo 0.13 ms y no hay esperas del
GIL.

**[medido]** Adaptador USB CM108 (`hw:5,0`), Fedora 44 con PipeWire, 10 s por prueba:

| Latencia | Búfer real | 48 kHz | 44.1 kHz |
|---|---|---|---|
| `"low"` (antes) | 21.3 ms | 6 overflows, 96.9 % de ingesta | 82 overflows, **58 %** de ingesta |
| `"high"` (ahora) | 42.7 ms (2 bloques) | 0 overflows, 99.8 % | 0 overflows, 99.8 % |

Con `capture_check` (captura aislada en proceso hijo, 20 s): 100 % de ingesta y 0 avisos a 48 y
a 44.1 kHz. `PRIMARY_LATENCY` pasa a `"high"` en `ui/capture_worker.py`. La latencia no afecta
a la medida: los tiempos salen de las marcas del ADC; solo las gráficas se dibujan unos 20 ms
más tarde.

### 4.3 Dispositivos `hw:` y el volumen de PipeWire

Al abrir un `hw:X,Y` la aplicación salta PipeWire, así que el volumen de la fuente en PipeWire
no le afecta. Además, adaptadores como el CM108 tienen **control automático de ganancia por
hardware** y un volumen de captura mínimo de 0 dB (no atenúan): un reloj ruidoso satura aunque
el volumen del sistema esté al 1 %. El README indica cómo desactivarlo
(`amixer -c <tarjeta> sset 'Auto Gain Control' off`). Pendiente: ofrecer preferentemente los
dispositivos de PipeWire frente a los `hw:` (sección 11).

## 5. Normalización de audio (`dsp/normalizer.py`)

Para entradas cuya ganancia no se puede bajar. El detector marca `SATURATED` los batimientos
que llegan a fondo de escala (|x| ≥ 0.99) y las métricas los excluyen: con un Timex de cuarzo
todos saturaban y la marcha no aparecía nunca.

**Diseño.**

- **Bloques fijos** de 0.1 s anclados al índice absoluto de muestra (`m B … (m+1) B`), de modo
  que la salida no depende del troceado del audio; el detector se apoya en esa invariancia.
  Un bloque se emite al completarse: retrasa el análisis como mucho 0.1 s.
- **Seguidor de pico con retención**: ataque instantáneo, retención de 3 s y caída exponencial
  con constante de 10 s. Con batimientos más próximos que 3 s la ganancia es constante en
  régimen permanente: no modula la forma del sonido.
- **Sin sobrepasar el objetivo** (−6 dBFS): un bloque cuya ganancia baja usa la nueva ganancia
  entero; uno cuya ganancia sube hace una rampa lineal hasta ella. Toda muestra de salida
  cumple |y| ≤ 0.5.
- Ganancia máxima +24 dB; las rachas de ceros exactos siguen siendo ceros (el detector las
  toma por huecos de datos). Un salto hacia delante del índice cierra el bloque parcial.
- Cuenta las muestras de entrada recortadas (`normalizer_clipped_samples`).

Se activa con la casilla **Normalizar audio**, que viaja por `WatchConfig.normalize` →
`SessionConfig.normalize` → `BeatDetectorConfig.normalize`. Se lee una vez por sesión: la
casilla se bloquea durante la medida. Las grabaciones guardan el audio original y la etiqueta
`ADV_TG_NORMALIZE=1`. La barra de calidad sigue marcando la saturación de la entrada: es cierta.

**Limitación.** No deshace el recorte: las crestas siguen planas. El inicio de cada sonido,
que es lo que fija los tiempos, sobrevive; la forma no. Por eso es una opción, apagada por
defecto, y su ayuda dice que se use solo cuando la ganancia no se pueda ajustar.

**[medido]** Cuarzo sintético de 1 paso/s recortado al 30 % del pico, 90 s:

| | 44.1 kHz | 48 kHz |
|---|---|---|
| Sin normalizar | 90/90 batimientos `SATURATED`, marcha NaN | 90/90, marcha NaN |
| Normalizado | 0 saturados, marcha −0.6993 s/d | 0 saturados, −0.7002 s/d |

Verdad: −0.7 s/d. Resultado idéntico troceando en bloques de 4096, 1000 o de una vez. En una
señal no recortada, la normalización mueve los instantes `t_fine` como mucho 0.09 muestras
(2 µs): el detector es invariante a la escala, y solo los cambios de ganancia de los primeros
segundos tocan ligeramente la plantilla.

## 6. Modo cuarzo y calibración con reloj de referencia

**Modo cuarzo.** Al marcarlo se deshabilitan Calibre, A/h (fijo en 1 paso/s) y ángulo de
levantamiento (`_apply_quartz_lock`). Corrige un fallo: `_set_top_controls_enabled(True)`, al
acabar cada sesión, volvía a habilitar los tres aunque siguiera en cuarzo. Los combos y
*spinboxes* deshabilitados tienen ahora un estilo atenuado visible (antes solo cambiaba la
flecha). Consecuencia: ya no se pueden medir cuarzos de 2, 4, 8 o 16 pasos/s (TMI, Bulova
Precisionist); `QUARTZ_STEPS_PER_S` sigue en el código para recuperarlo.

**Reloj de referencia.** El diálogo de calibración tiene una sección para obtener el error del
reloj de la tarjeta con un cuarzo de marcha conocida. Una tarjeta que adelanta `p` ppm hace que
el reloj parezca atrasar `0.0864 p` s/d, así que

```
p = (marcha conocida − marcha medida sin corregir) / 0.0864
```

La marcha medida se rellena con la última lectura sin la corrección aplicada:
`mostrada − 0.0864 × ppm aplicado` (`MainWin._uncorrected_rate_spd`). "Usar este valor" copia
el resultado al formulario y deja una nota con ambas marchas.

## 7. Tren de rodaje (`analysis/gear_train.py`)

### 7.1 Física

Una rueda excéntrica, con un diente dañado o con un pivote doblado repite su efecto una vez
por vuelta. Dos ruedas giran lo bastante rápido para verse en minutos:

- **Rueda de escape**: avanza un diente por oscilación del volante, es decir, cada dos
  batimientos. Con `Z` dientes (15 en casi todos los calibres suizos y japoneses):

  ```
  T_escape = 2 · Z · 3600 / A/h      f_escape = f_beat / (2 Z)  (= f_beat / 30 con Z = 15)
  ```

  | A/h | 18 000 | 21 600 | 25 200 | 28 800 | 36 000 |
  |---|---|---|---|---|---|
  | T_escape (Z = 15) | 6 s | 5 s | 4.29 s | 3.75 s | 3 s |

  **Corrección:** el documento de origen, el marco teórico y la web daban "≈ 7.5 s". Eso solo
  vale para 14 400 A/h (o una rueda de 30 dientes); está corregido en `index.html`,
  `marco_teorico_y_matematico.md` y `documentation_v0.6.3-beta3.md`.
- **Cuarta rueda**: lleva el segundero, una vuelta por minuto (60 s).

### 7.2 Series por batimiento

`extract_series` detecta los batimientos con el `BeatDetector` del núcleo (una sola vez por
grabación) y construye:

1. **Residuo de tiempo** de `t_fine`, quitando `a + b k + c k² + d (−1)^k`: marcha media,
   deriva lenta con el estado de cuerda y error de batimiento. Antes se rechazan los valores
   atípicos (5 σ robustas, hasta 3 pasadas), pero **se guardan sus instantes**
   (`outlier_t_s`): son las excursiones bruscas que busca la regla de *rebanking*. Un rizado de
   fase de amplitud `A` y periodo `T` equivale a un rizado de marcha de
   `2π A / T × 86 400` s/d.
2. **Energía de la caída P3**: P3 es el diente de la rueda de escape cayendo sobre la paleta.
   Operador de Teager-Kaiser `Ψ[n] = x[n]² − x[n−1] x[n+1]` sobre la señal filtrada
   (Butterworth 1–15 kHz, fase cero) y sumado en `[t_fine − 1 ms, t_fine + 6 ms]`. Cada paridad
   se normaliza por su mediana (el tic y el tac suenan distinto) y se quita la tendencia.
3. **Datos por sub-impulso** (para las características): instantes P1/P2/P3, energía TKEO de
   cada sub-evento (`[P1, P2)`, `[P2, P3)`, `[P3, P3 + 6 ms)`), pico TKEO en
   `[P1 − 6 ms, P1 − 0.5 ms]` sobre la mediana de toda la grabación (dB) y amplitud de cada
   batimiento a partir de `P3 − P1`.

Se exigen al menos 64 batimientos con `t_fine` (`NotEnoughBeats`, mensaje traducido).

### 7.3 Espectros y decisión

- **Lomb-Scargle** normalizado (Scargle 1982) sobre los instantes reales: los batimientos
  perdidos dejan huecos sin interpolar. Implementación propia en NumPy (sin SciPy, como el
  resto del proyecto), por bloques de 512 frecuencias. Malla: de `1/duración` a
  `1/(4 periodos de batimiento)`, sobremuestreada ×5.
- **FFT** con ventana de Hann sobre la malla uniforme de batimientos (huecos interpolados
  linealmente), como contraste.
- Para cada rueda: el pico más alto dentro de una resolución en frecuencia del valor esperado,
  ajustado por mínimos cuadrados (amplitud y fase), y su **probabilidad de falsa alarma**
  (Horne & Baliunas 1986, con las frecuencias independientes de la ventana de búsqueda).
  **Presente** si FAP < 1 %.
- **Resoluble** si la grabación dura al menos 3 vueltas de la rueda. Para la cuarta rueda:
  **180 s** (`FOURTH_WHEEL_MIN_RECORDING_S`), evaluado sobre la **duración de la grabación**.
  En la GUI solo la prueba de 240 s lo cumple (y 240 s es lo máximo que se graba). Por debajo,
  la tabla dice "Graba ≥ 180 s" y la cabecera lo avisa.

### 7.4 Validación **[medido]**

Sintético con `phase_mod` (rizado de tiempo) y `level_mod` (volumen), jitter de 20 µs, SNR 30 dB:

| Caso | Rueda | Inyectado | Medido | FAP |
|---|---|---|---|---|
| 28 800 A/h, 200 s | escape (tiempo) | 40 µs a 3.75 s | 41.7 µs a 3.750 s (6.03 s/d) | < 1e-12 |
| | escape (energía) | volumen ±15 % | energía ±26.7 % a 3.750 s | < 1e-12 |
| | cuarta (tiempo) | 150 µs a 60 s | 137 µs a 61.9 s | < 1e-12 |
| 21 600 A/h, 200 s | escape (tiempo) | 40 µs a 5 s | 39.7 µs a 5.008 s | < 1e-12 |
| Sin defectos, 200 s | todas | — | ninguna presente | 0.14–0.82 |

La cuarta rueda sale un 8 % baja y desplazada porque 200 s contienen solo 3.3 vueltas y parte de
la sinusoide se va con el ajuste cuadrático de la deriva. La energía TKEO es proporcional al
cuadrado de la amplitud: una modulación de volumen del ±15 % aparece como ≈ ±30 % en energía.

**Rendimiento [medido].** 200 s de audio a 48 kHz: 6.6 s en total, de los que 5.1 s son la
detección (~40× tiempo real), 1.0 s el filtrado y 0.5 s los dos periodogramas. Por eso la
detección corre en un `QThread` y cambiar los dientes de la rueda solo recalcula espectros y
características.

## 8. Características y diagnóstico (`analysis/features.py`)

### 8.1 Vector de características

Un `FeatureVector` por ventana de 60 s (desde el primer batimiento; una cola de menos de 30 s se
une a la ventana anterior) y otro para la grabación completa:

| Grupo | Campos |
|---|---|
| Tren de rodaje | `escape_power`, `escape_amp_us` (Lomb-Scargle y ajuste en `f_beat/(2Z)` sobre `Δt_k`); `fourth_power`, `fourth_amp_us` (1/60 Hz) |
| Sub-impulsos | `d12_*` y `d23_*` (media ms, varianza ms², asimetría, exceso de curtosis) de `t_P2 − t_P1` y `t_P3 − t_P2`; `e1_e2`, `e3_e2` (medianas de `E_P1/E_P2` y `E_P3/E_P2`); `n_subevents` |
| Medida | `rate_spd`, `beat_error_ms` (`2|d|` del ajuste `a + b k + d (−1)^k`), `amplitude_deg` (mediana), `n_beats` |
| Anomalías | `spurious_fraction` (batimientos con un pico > 20 dB antes de P1), `timing_jumps` (excursiones bruscas en la ventana) |

**La cuarta rueda solo se da para la grabación completa y desde 180 s**: una ventana de 60 s
contiene una sola vuelta, indistinguible de la deriva de la marcha, y darla por ventana sería
ruido presentado como dato. `as_dict()` convierte NaN en `None` para JSON.

**Diferenciales posicionales** (`PositionalDeltas`), a partir de las posiciones guardadas con
"Guardar Posición": `ΔRate_H−V` y `ΔAmp_H−V` (media de DU/DD menos media de CU/CD/CL/CR) y la
dispersión del error de batimiento entre posiciones. Hace falta al menos una posición
horizontal y una vertical.

### 8.2 Reglas de diagnóstico

Reglas transparentes sobre la grabación completa; cada indicio lleva su evidencia numérica.

| Indicio | Condición | Nivel |
|---|---|---|
| Rebanking / knocking | amplitud > 310° **y** (picos antes de P1 > 5 % de los batimientos, excursiones bruscas de tiempo > 0) | las dos señales → probable; una → posible; la amplitud sola no basta |
| Falta de lubricación en las paletas | `E_P3/E_P2` > 10 (P2 más de 10 dB bajo P3), CV de `t_P2 − t_P1` > 5 % | ambas → probable; una → posible |
| Magnetismo / espiral descentrada | marcha > +30 s/d **y** dispersión del error de batimiento entre posiciones > 0.5 ms | probable; sin posiciones, desviación del error de batimiento entre ventanas > 0.2 ms → posible |
| Rueda de escape / cuarta rueda | componente presente (FAP < 1 %) en el tiempo o en la energía | probable (un indicio por rueda) |

**Los umbrales son provisionales** (constantes `*_THRESHOLD` en `features.py`). Traducen el
criterio relojero de cada defecto, pero no están calibrados con relojes etiquetados: esa
calibración es justo lo que debe aportar la etapa de ML. Mientras tanto, la interfaz los
presenta como **indicios** a comprobar en el banco, nunca como diagnósticos. El umbral de
+30 s/d de magnetismo está muy por encima del sesgo de una tarjeta sin calibrar (1–4 s/d).

### 8.3 Validación **[medido]**

| Caso sintético | Resultado |
|---|---|
| Limpio, 28 800 A/h | `t_P2 − t_P1` = 3.330 ms (verdad 3.330), desviación 0.008 ms; `E_P1/E_P2` 3.34, `E_P3/E_P2` 4.40; pico antes de P1: mediana 9.5 dB, percentil 95 de 11.3 dB, 0 % > 20 dB; **ningún indicio** |
| P1 débil (×0.3) | `E_P1/E_P2` 3.34 → 0.55; `E_P3/E_P2` sin cambio |
| Ruido impulsivo 20/s | 4.3 % de batimientos con pico > 20 dB antes de P1 |
| Amplitud 325°, ruido impulsivo, jitter de 200 µs en el tac | rebanking **probable** (o posible si los picos no llegan al 5 %) |
| Rueda de escape + cuarta rueda, 200 s | ambas **probables**; la amplitud de la rueda de escape en cada ventana de 60 s queda dentro del 20 % de los 40 µs inyectados |

`t_P3 − t_P2` es más ruidoso por batimiento (σ ≈ 0.3 ms) porque el inicio de P3 en un
batimiento suelto es menos estable que el de la plantilla; vale para estadísticos sobre muchos
batimientos.

## 9. Ventana "Analizar Grabación" (`ui/analysis_dialog.py`)

- **Botón** encima de "Grabar Sonido". No disponible durante una sesión.
- **Fuente**: la grabación de la última sesión (FLAC temporal del `MeasureStreamWorker`, sin
  quitársela, o el tren sintético del MOCK); si no hay, un WAV/FLAC elegido por el usuario. Las
  etiquetas `ADV_TG_BPH`, `ADV_TG_LIFT_ANGLE` y `ADV_TG_QUARTZ` mandan sobre la barra superior.
  Los cuarzos se rechazan con un mensaje: no hay escape.
- **Pestañas**:
  1. *Tren de rodaje*: residuo de tiempo (µs), energía P3 (%), periodograma con marcas en
     `T_escape` y 60 s (eje logarítmico en segundos), tabla por rueda y serie, ayuda.
  2. *Características*: una columna por ventana de 60 s y otra para la grabación completa.
  3. *Diagnóstico*: diferenciales H−V, indicios con nivel y evidencia, reglas y advertencia de
     umbrales provisionales.
- **Exportar características…** guarda un JSON por grabación, pensado como registro del dataset
  de ML:

  ```json
  {
    "source": "…", "sample_rate": 48000.0, "bph": 28800.0, "lift_angle_deg": 52.0,
    "duration_s": 240.0, "escape_teeth": 15,
    "windows": [ { "window_start_s": 2.1, "…": "FeatureVector.as_dict()" } ],
    "whole": { "…": "FeatureVector.as_dict()" },
    "positional": { "positions": ["DU", "CU"], "delta_rate_hv_spd": 6.0, "…": "…" },
    "gear_train": [ { "wheel": "escape_wheel", "series": "timing", "period_s": 3.75,
                      "amplitude": 4.1e-05, "fap": 0.0, "resolvable": true, "significant": true } ],
    "findings": [ { "code": "escape_wheel", "level": "probable", "evidence": { "…": 0.0 } } ]
  }
  ```

  Los valores no disponibles van como `null`; las amplitudes de tiempo de `gear_train`, en
  segundos.

## 10. Internacionalización

Todo texto nuevo está en inglés y en español (`i18n/translator.py`): casilla y ayuda de
normalización, pista de frecuencia no válida, sección del reloj de referencia, botón y ventana
de análisis (80 claves `an_*` por idioma). Un test comprueba que los dos diccionarios tengan las
mismas claves.

## 11. Pruebas

649 pruebas en verde (3 omitidas, 1 xfail), `ruff` sin avisos. Nuevas:

| Archivo | Qué cubre |
|---|---|
| `tests/test_normalizer.py` | sin sobrepasar el objetivo, invariancia al troceado, ganancia constante en régimen, ceros y huecos, cuarzo recortado medido solo con normalización a 44.1 y 48 kHz, tiempos sin cambios |
| `tests/test_gear_train.py` | periodo de la rueda de escape, TKEO, Lomb-Scargle con muestreo irregular, ruedas inyectadas, reloj limpio, cambio de dientes, `NotEnoughBeats`, ventana |
| `tests/test_features.py` | momentos, ventanas de 60 s, estadísticos frente a la verdad, relación de energías, condición de 180 s, diferenciales, lógica de cada regla, pestañas y JSON |
| `tests/test_gui_controls.py` | 44.1 kHz, bloqueo del cuarzo tras la sesión, casilla de normalización, textos en ambos idiomas, cálculo del reloj de referencia, sesión de cuarzo a 44.1 kHz, botón de análisis |

`tests/synth.py` admite `phase_mod` y `level_mod` (rizado periódico de tiempo y de volumen).

## 12. Pendientes

- **Calibrar los umbrales** con grabaciones reales de relojes con averías conocidas: es el
  primer trabajo de la etapa de ML. Ninguna regla está validada todavía en relojes reales.
- La cuarta rueda solo es resoluble con la prueba de 240 s; valorar grabar pruebas más largas
  en un formato más compacto o grabar solo el análisis.
- Ofrecer preferentemente los dispositivos de PipeWire frente a los `hw:` en Linux
  (respetan el volumen del sistema y no compiten por el dispositivo).
- Recuperar el modo cuarzo de 2–16 pasos/s si hace falta (quizá con detección automática).
- El panel de estadísticas sale aplastado con ventanas de 1360 × 840 o menos (anterior a esta
  versión).
- `src/adv_tg/ml/` sigue siendo un marcador de posición (`cml_core.md`).

## 13. Publicar la versión

1. Revisar y hacer commit de los cambios en `feature/ml_capabilities`; fusionar a `develop` y
   `main` como de costumbre.
2. `pip install -e .` y `pytest` (la prueba de humo comprueba la versión).
3. Etiquetar `v0.8.0-beta` y subir la etiqueta: `build.yml` genera los paquetes (el instalador
   de Windows y el RPM leen la versión de `pyproject.toml`).
