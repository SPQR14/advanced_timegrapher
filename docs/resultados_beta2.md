# Beta 2: escalones de la marcha y cinta de papel

Respuesta a `INSTRUCCIONES_CLAUDE.md` (Gemini, 26/10/2026). Caso: Seiko 4R36 con el CM18,
prueba de 120 s; la cinta bajaba en escalones de −1 a −8.4 s/d y el error de batimiento marcaba
1.43 ms (tg/WAM: 0.2–0.3 ms en la toma de referencia). La grabación de esa sesión no se guardó;
el análisis usa la toma de 70 s del CM18 del mismo reloj (`4r36_live70`) y las 5 de `docs/samples`.

## 1. Causa A (saltos de lóbulo del punto de referencia): confirmada

- El punto de `t_fine` de una paridad (`onset_ref`, inicio de P3 medido en la plantilla) salta
  entre lóbulos de la caída al refrescarse la plantilla: en el 4R36 el tac salta **±0.9–1.7 ms**
  varias veces por minuto (33 saltos > 0.04 ms en 30 s; 62 en la toma de 70 s). En Miyota y H-50
  los saltos son < 0.25 ms.
- Cada salto es un escalón en la serie de `t_fine`; dentro de la ventana de 30 s sesga la marcha
  ~1.5 δ/T (0.9 ms → unos 4 s/d) y el error de batimiento.
- Con una referencia constante por paridad (sin seguir la deriva) la dispersión de la marcha
  en tramos de 10 s baja donde hay saltos (CM18 70 s: 1.11 → 0.42 s/d; Miyota 1.45 → 0.83) pero
  sube donde el marco deriva (H-50 0.58 → 1.37): hay que seguir la deriva y bloquear solo los
  saltos.
- Además, la escalera de la captura también tenía otras dos causas: la ventana de 30 s se llena
  durante el primer medio minuto (la marcha se va desplazando mientras tanto) y el ruido propio
  del 4R36 (tac con 580 µs de dispersión).

## 2. Correcciones probadas en el detector (no adoptadas)

Métricas: lote (cifras finales) frente a tg/WAM, dispersión de la marcha en tramos de 10 s, y en
vivo el máximo del error de batimiento sin mediana.

| Variante | σ tramos 10 s (media 5 tomas) | Error de batimiento en lote | En vivo |
|---|---|---|---|
| Original | 1.48 s/d | Miyota 0.15, H-50 0.27, 4R36 0.20 (tg 0.1 / 0.3 / 0.3) | picos 15.6 ms (Miyota), 2.8 (H-50) |
| Histéresis P1 + P3 (0.12 ms, desde 6 batimientos) | 1.21 | 0.23 / **0.06** / 0.34 | picos ≤ 0.9 ms |
| Histéresis P1 + P3 (P3 con plantilla llena) | — | 0.20 / **0.06** / 0.24 | rango marcha 4R36 4.5 → 8.5 s/d |
| Histéresis solo P1 (desde 6 batimientos) | 1.48 | **idéntico** | picos Miyota 0.39, H-50 0.38; 4R36 sin cambio (1.8) |
| Histéresis solo P1 (plantilla llena) | — | H-50 **0.82**, 301° | — |
| Tolerancias 0.25–0.6 ms | 1.41–1.46 | H-50 1.29–1.92, 310–324° | — |
| Ordenada por tramo en la regresión (cortes > 0.12 ms) | 1.58 → 1.40 | — | 4R36 ref. peor (2.48 → 2.74) |

- La histéresis (seguir la deriva por correlación normalizada entre plantillas consecutivas y
  bloquear saltos > 0.12 ms salvo que persistan 12 refrescos) estabiliza el vivo, pero **mueve
  las cifras validadas** de forma que depende de cuándo se engancha (qué lóbulo queda fijado):
  el H-50 se aleja de tg/WAM. Con 5 grabaciones no es validable: **se revirtió** el detector.
- Hallazgo útil para la fase 6: la correlación entre plantillas debe ser **normalizada**. La
  correlación simple (la de `_anchor_frame`) da −9 muestras comparando una plantilla consigo
  misma por la cola en pendiente de la envolvente; ese sesgo pudo contribuir a que el anclaje
  se descartara en mecánicos.
- El sintético con dos lóbulos de nivel aleatorio (`odd_drop_lobe`) **no reproduce** el problema
  (error < 0.01 s/d): la lógica del primer lóbulo funciona con lóbulos limpios. Hace falta un
  sintético con lóbulos de forma variable o, mejor, grabaciones reales.

## 3. Adoptado

- **Mediana de 20 actualizaciones (5 s)** para el error de batimiento mostrado (las demás cifras
  siguen con 7): picos máximos 1.89 → 1.72 ms (CM18 70 s), 1.85 → 1.14 (H-50). Solo
  presentación; las cifras en lote no cambian.
- **Causa B (cinta de papel): corregida.** La gráfica superior restaba y sumaba la mitad del
  error de batimiento (ms) a la marcha (s/d). Ahora es una cinta auténtica:
  - un punto por batimiento, `-(t_fine − t0 − (k − k0)·P_nom·(1 + ppm·1e−6))` en ms
    (`SessionSnapshot.tape_*` → `BeatTelemetry.tape_*`); un reloj que adelanta sube;
  - tic y tac en hileras separadas (medidas en P3; su separación es el desfase tic/tac en la
    caída, no la tarjeta del error de batimiento, medido en P1);
  - pendiente = marcha (1 s/d = 1/86.4 ms por segundo); límites COSC como pendientes;
  - test: sintético +6 s/d → pendiente 6/86.4 ms/s (±5 %), hileras a 0.5 ms (±0.05);
  - en el MOCK se integra la marcha simulada;
  - la exportación CSV usa los puntos guardados (las curvas pueden estar diezmadas).
- En la cinta del 4R36 se ven ahora los saltos de lóbulo del tac como escalones de ~1.5 ms
  (antes ocultos dentro de la cifra de marcha).

## 4. Pendiente (fase 6)

1. Grabaciones de 2–5 min del 4R36 y de otros calibres **con la cifra de tg/WAM medida a la vez**
   (los reportes de la beta). Con ellas, validar una estabilización del punto P3 (histéresis con
   correlación normalizada o anclaje con correlación normalizada).
2. Sintético con lóbulos de forma cambiante que reproduzca los saltos.
3. Decidir si la cinta debe proyectarse a P1 (separación = error de batimiento) cuando P1 sea
   estable.

## 5. Incidencia de versión

El commit `8394ccb` ("fix(ci)…", subido a `origin/release/v0.6.1.beta.2`) incluyó una versión
**experimental** del detector (histéresis de P1 con plantilla llena: H-50 0.82 ms / 301°) mientras
se evaluaba. La copia de trabajo la revierte (`beat_detector.py` igual que antes de la
histéresis); hay que hacer commit de esa reversión antes de publicar la rama.
