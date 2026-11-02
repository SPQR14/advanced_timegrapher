# Grabación de referencia del PPS de GPS

Fecha: 2026-11-01. Rama `experiment/pps_through_LAN`.

## 1. La grabación

`docs/samples/pps_48000Hz.flac`: PPS de un NEO-6M (con *fix*, comprobado con las tramas NMEA)
→ transistor → zumbador activo sin amortiguar → micrófono CM18 a 48 kHz, macOS, cable corto.
150 pulsos en 150 s, ráfagas de ~110 ms con pico de −55 dBFS, ruido casi todo por encima de
15 kHz, SNR del arranque 44 dB.

Es la única muestra con verdad exacta: un PPS es un "reloj" perfecto (error < 1 µs). Medido
con el motor de relojes como un cuarzo de 1 paso/s, su marcha es solo el error de la tarjeta.

Lleva sus propias etiquetas (`adc_calibration pps --tag`): `ADV_TG_ADC_DRIFT_*` = +7.443 ±
0.043 ppm (nivel 3, `pps`) y `ADV_TG_REFERENCE=gps-pps`. Por eso:

- medida en cualquier instalación, la marcha corregida es 0 s/d (la corrección viaja con el
  archivo, no depende de la tarjeta del usuario);
- el diálogo de calibración la analiza y **pide confirmación** (por defecto No) antes de
  guardarla: solo vale para la tarjeta y el ordenador que la grabaron;
- `adv-tg --smoke-test` la comprueba en cada paquete (`tools/reference_check.py`).

Grabaciones descartadas el mismo día: con el zumbador amortiguado con arcilla el arranque deja
de repetirse (ráfagas enteras desplazadas ~1 ciclo, 300 µs); a 44.1 kHz la misma tarjeta da el
mismo error (+7.39 ppm), así que se conserva solo la de 48 kHz.

## 2. Hallazgos y cambios

### 2.1 Detector PPS (`core/pps.py`)

A −55 dBFS el primer lóbulo queda a la altura del ruido de alta frecuencia de la tarjeta y el
arranque se adelantaba 0.2–0.5 ms en el 18 % de los pulsos (siempre tarde: sesgaba la
pendiente). Pasa-bajos FIR de 9 kHz (fase lineal, retardo compensado): 27 → 0 pulsos tardíos,
12 → 7.1 µs. La grabación anterior, más fuerte, no cambió (6.8 → 7.5 µs).

El estimador de ruido usaba los bloques de los últimos 2 s del **trozo** procesado: con un
trozo grande, el ruido del final del archivo decidía el disparo del principio. Ahora solo usa
bloques terminados antes de la muestra buscada (resultado idéntico con cualquier troceado).

### 2.2 Motor de relojes en modo cuarzo (`core/beat_detector.py`, paso 8b)

La plantilla de cuarzo alinea ±25 ms de envolvente suavizada, que sigue el centroide del sonido
y no su arranque: 190 µs por batimiento y escalones de ~300 µs cada vez que la plantilla se
renueva. En el PPS, **−0.435 ± 0.108 s/d en vez de −0.643** (1.9 σ).

Nuevo: si la mediana de la SNR de los 4 primeros batimientos de una paridad (medida durante el
calentamiento de la plantilla, cuando `t_fine` es NaN de todos modos) llega a 26 dB, cada
batimiento de esa paridad se mide por su propio arranque (pasa-bajos, línea base local, último
tramo de silencio, cruce del umbral: `dsp/onset.py`, el mismo código del detector PPS). Un
batimiento sin arranque limpio (< 20 dB) recibe `t_fine` más la mediana del desfase de los
recientes. La elección no cambia en toda la toma.

| Muestra | Antes | Ahora | Elección |
|---|---|---|---|
| PPS 48 kHz (verdad −0.643) | −0.435 ± 0.108 s/d, 188/197 µs | **−0.642 ± 0.005**, 8/7 µs | arranque (44 dB) |
| PPS 44.1 kHz (verdad −0.638) | −0.632 ± 0.097, 139/192 µs | −0.643 ± 0.002, 5/6 µs | arranque (44 dB) |
| Citizen H874 (1/s) | −0.499 ± 0.142, 961/838 µs | −0.521 ± 0.040, 267/233 µs | arranque (28–30 dB) |
| TMI VH31 (4/s) | −0.551 ± 0.221 | igual | plantilla (16–17 dB) |
| Bulova NM10, dos tomas (16/s) | −0.720 / −0.641 | igual | plantilla (10–17 dB) |
| Mecánicos (6 tomas) | — | idénticos | no aplica |

Sintético con precursor de nivel aleatorio (TMI a 30 dB, 8 semillas): error medio −0.0012 s/d
con arranque, +0.0001 con plantilla; rms 0.0098 frente a 0.0089; z rms 0.93 y 1.12. Sin sesgo.

Comprobación cruzada: la segunda toma del Bulova Precisionist mide −0.641 ± 0.016 s/d sin
corregir; el PPS predice −0.643 para la tarjeta. Corregido, el Bulova queda en +0.002 s/d.

### 2.3 Métricas (`core/metrics.py`)

- **Aviso de tendencia:** además de 3 σ exige `trend_min_s_per_day_per_min` = 0.1 s/d por
  minuto. Con 5 µs de precisión, el calentamiento de la tarjeta (0.016 s/d/min) salía
  significativo y avisaba "el reloj no está estable".
- **Ventanas de marcha:** a 3600 BPH una ventana de 10 s tiene 5 batimientos por paridad y
  hacen falta 10: el cuarzo de 1/s no tenía ninguna. `effective_window_s` la alarga a lo justo
  (25 s a 3600 BPH; nada cambia desde 14400 BPH).
- En vivo (30 s), la marcha del PPS oscila ±0.06 s/d: es la frecuencia de la tarjeta, que se
  mueve ~0.5 ppm en ese plazo mientras se calienta (±0.18 ppm/min medidos). Está por debajo
  de la resolución útil para relojes; no se ha modelado.

## 3. El error depende del ordenador

El mismo CM18 midió +22 ppm en Linux, +24–25 en Windows y +7.4 en macOS (44.1 y 48 kHz
iguales en macOS). Lo más probable es que el reloj del adaptador USB se derive del bus del
ordenador. Las calibraciones se guardan en cada máquina, así que basta con calibrar en cada
ordenador; no copiar el archivo de calibraciones de uno a otro.

## 4. Pendientes

- Batería de relojes reales del usuario con el modo cuarzo nuevo (sobre todo cuarzos de 1/s
  con buena SNR: es donde cambia la medida).
- Una entrada en la interfaz para medir la referencia sin terminal (hoy: `reference_check` y
  el autotest del paquete).
