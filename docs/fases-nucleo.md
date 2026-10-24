# Advanced Timegrapher: fases de reconstrucción del núcleo

Documento de referencia. Estado al 24/10/2026: **v0.2.0**. Fases 0 y 1 (con 1b y 1d) terminadas; fase 2 (detector) en validación con las grabaciones reales (ver `docs/resultados_pruebas_fase2.md`). Las métricas (fase 3) aún no existen.

## Decisión de base

Implementación limpia en el repo `advanced_timegrapher` (paquete `adv_tg`), sin basarse en el código de tg (GPLv2-only, incompatible con la GPLv3 del proyecto y con PyQt6). tg solo se usa más adelante como referencia externa de comparación.

## Reglas de trabajo (aplican a todas las fases)

- Una fase por vez, un solo objetivo. Al terminar, el agente se detiene y reporta.
- Los agentes de código **no** hacen commit, push, stash, reset ni checkout. Git es solo lectura; los commits los hace el usuario.
- Sin dependencias nuevas sin preguntar.
- Los tiempos se expresan en **índice absoluto de muestra** (reloj del ADC), nunca en ventanas.
- Prohibido loguear, bloquear o asignar memoria dentro del callback de audio.
- Cada fase entrega: módulo, herramienta de consola sin GUI (`python -m adv_tg.tools.*`) y tests headless que corren en CI (los que necesitan hardware o grabaciones se saltan si no están).
- Primero que funcione de punta a punta; validación contra tg y optimizaciones vienen después.
- Los cambios en la GUI son mínimos y se marcan con `# TODO(capa-5)` si dependen del motor real.

## Material de referencia

- Grabaciones reales en `./docs/samples` (raíz del repo): calibres **c07** (Hamilton H-50), **9015** (Miyota) y **nm10** (Bulova, cuarzo).
- Valores de referencia de tg (solo para la fase 6):
  - Hamilton H-50: 21600 BPH, ángulo de levantamiento 50°, +10 s/d, error de batimiento 0.2 ms, amplitud 280°.
  - Miyota 9015: 28800 BPH, 51°, +9 s/d, 0.0 ms, 291°.
  - Bulova NM10 (cuarzo, 16 Hz): con el CM18 se mide ≈ +8.3 ppm (deriva del reloj de la tarjeta de sonido).
- Micrófono: CM18 USB piezo.

## Fases

| Fase | Contenido | Quién | Criterio de "listo" |
|---|---|---|---|
| **0. Esqueleto** | Renombrado a `adv_tg`, entorno, Debugger, modo MOCK, CI. **Completada (v0.1.0).** | Agente + usuario | GUI arranca, MOCK mueve todos los widgets. |
| **1. Captura** | `core/sample_stream.py` (buffer circular sin bloqueos con índice absoluto y registro de bloques), `audio/capture.py` (envoltorio de sounddevice), `tools/capture_check.py` (CLI) y tests. **Completada.** | Agente; el usuario corre `capture_check` con el CM18 | En 30 s: ingesta ≥ 99 %, hueco máximo < 3 bloques, callback < 2 ms. Resuelve la duda del 12.7 %. |
| **1b. Primera integración real** | `CaptureStreamWorker` que implementa `BaseStreamWorker`: conecta el CM18 con `SampleStream` y alimenta solo la etiqueta de audio recibido, nivel/saturación de la barra de calidad y la onda cruda. No mide. **Completada** (con la captura en proceso aparte, 1d). | Agente | Audio real a ≥ 99 % en la GUI, plots a ~30 FPS. |
| **2. Detector de batimientos** | Detección incremental con una sola definición de P1/P3 y ventana de búsqueda acotada físicamente. Generador sintético en `tests/` con verdad conocida. **En validación.** | Agente; el usuario aporta grabaciones | Error < 1 muestra en sintético. En grabaciones reales el conteo coincide con duración × BPH / 3600 y no hay falsos. |
| **3. Métricas** | Marcha, error de batimiento y amplitud sobre la lista de batimientos, ventana larga, rechazo de atípicos, confianza real. | Agente | Sintético dentro de tolerancia y cifras estables en ventana de 30 s. |
| **4. Reloj** | Contador de muestras frente al reloj monotónico del host más NTP; niveles de confianza; ppm ± σ; calibración manual opcional. | Agente | Un ppm inyectado en sintético se recupera. El Bulova da ≈ +8 ppm con el CM18. |
| **5. Integración completa** | Worker real que une las capas 1 a 4. MOCK se queda como herramienta de pruebas. Barra de calidad (nivel/saturación, SNR antes de AGC, fiabilidad de detección), autoescala del papel, ocultar cifras con confianza baja. | Agente | ≥ 3 actualizaciones/s, plots a ~30 FPS, etiquetas de deriva y calidad funcionando. |
| **6. Validación** | Comparación contra tg con las tres grabaciones; optimización; ganchos para ML. | Usuario + Claude | Diferencias documentadas y dentro de tolerancia. |

## Fórmulas y convenciones del dominio

- f = BPH / 7200; amplitud A = LA / (2·sin(π·f·t_lift)), con LA el ángulo de levantamiento.
- Marcha (s/d) = (P_nominal / P_medido − 1) · 86400. 1 ppm = 0.0864 s/d.
- Error de batimiento = mitad de la diferencia entre los intervalos tic y tac.
- P1 puede ser débil (p. ej. H-50): buscar en una ventana acotada por la física.
- Deriva del ADC: contador de muestras frente a reloj monotónico crudo del host, con NTP para el nivel de confianza más alto.

## Pendientes que caen en fases posteriores

- Barra de calidad de señal: nivel/saturación, SNR antes de AGC, fiabilidad de detección (fase 5).
- Eje del papel de marcha con autoescala (fase 5).
- No mostrar cifras cuando la confianza es baja (fase 5).
- Aviso de saturación de entrada (ya en `capture_check`, fase 1).
- Recuperar empaquetado/distribución para Windows (el arranque ya aísla las DLL de Qt).
