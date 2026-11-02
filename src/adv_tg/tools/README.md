# Herramientas de diagnóstico (`adv_tg.tools`)

## `capture_check`

Verifica que la capa de captura recibe el audio completo (ingesta ≥ 99 %, hueco máximo < 3
bloques, callback < 2 ms). `--isolated` usa la captura en proceso aparte (`ProcessAudioCapture`)
y añade `dropped_frames`, `backlog_max` y `parent_lag_ms` al resumen; `--fake-source` (con
`--isolated`) usa una fuente falsa en el proceso hijo, sin micrófono. Ver `docs/resumen_beta1.md`.

## `adc_calibration`

Calibraciones del reloj de la tarjeta (ppm ± σ) guardadas por dispositivo y tasa de muestreo en
`adc_calibration.json` de la carpeta de configuración del usuario (macOS:
`~/Library/Application Support/adv_tg`; se cambia con `ADV_TG_CONFIG_DIR`). Subcomandos `list`,
`set` (manual, nivel 3), `forget`, `show FILE` y `tag FILE --device D` (estampa la calibración
en una grabación antigua). `--device` acepta el nombre PortAudio o su índice.

`pps FILE [--device D --save] [--tag]`: error del reloj a partir de una grabación de un PPS de
GPS; `--save` lo guarda para el dispositivo y `--tag` lo estampa en la propia grabación, con la
marca `ADV_TG_REFERENCE=gps-pps` (grabación de referencia: el diálogo de calibración pide
confirmación antes de guardarla, porque solo vale para la tarjeta que la grabó).

Se aplica la calibración de menor incertidumbre después de sumar el envejecimiento del cristal
(1 ppm/año en cuadratura): una medida con PPS de la semana pasada gana a una estimación NTP de
hoy, pero no una de hace un año.

## `reference_check`

Comprueba el programa con la grabación de referencia `docs/samples/pps_48000Hz.flac` (PPS de
GPS con zumbador, CM18 a 48 kHz en macOS, etiquetada con `adc_calibration pps --tag`): el
detector PPS debe reproducir el error de la etiqueta (± 0.05 ppm) y el motor de relojes, como
cuarzo de 3600 BPH y corregido con ese error, marcar 0 s/d (± 0.02 o 4 σ) con ≤ 30 µs por
batimiento. Medido: +7.443 ± 0.043 ppm; +0.001 ± 0.006 s/d, 8 µs. Lo ejecuta también
`adv-tg --smoke-test` en cada paquete. Códigos de salida: 0 = pasa, 1 = falla, 2 = falta la
referencia.

## `gil_probe`

Banco de pruebas que mide, escenario por escenario (cada uno en un subproceso limpio), cuánto
audio llega con la GUI en distintos estados y cuánto espera el callback por el GIL.

```bash
python -m adv_tg.tools.gil_probe --all --device 1 --secs 20             # fases 1c (en proceso)
python -m adv_tg.tools.gil_probe --all --isolated --device 1 --secs 20  # fase 1d (aislado)
python -m adv_tg.tools.gil_probe --scenario gui_real --isolated --device 1
```

Escenarios: `main`, `thread`, `qthread`, `gui_idle`, `gui_mock`, `gui_real`,
`gui_real_nopaint`. `--all` ejecuta las variantes (i) 1024/low, (ii) 4096/high y (iii)
1024/low con `switchinterval` 0.5 ms (en el modo que indique `--isolated`) y siempre añade la
variante (iv) "1024/low aislado" con los cuatro escenarios de GUI. Las tablas se guardan en
`logs/gil_probe_<fecha>.md` junto con el JSON crudo.

Columnas clave: "Callbacks (hijo/stream)" se cuenta donde vive el stream (el proceso hijo en
modo aislado); "Bloques en padre" e "Ingesta padre %" salen del `SampleStream` del proceso de
la GUI; "Tramas perdidas" debe ser 0; "Retraso padre máx" muestra cuánto se atrasó el padre
(sin perder audio) mientras repintaba.

### La ventana debe estar visible

**No cubras ni minimices la ventana de la GUI durante la prueba.** Con la ventana tapada,
macOS deja de repintarla y la medición deja de reflejar el uso real. El probe muestra, levanta
y activa la ventana, espera a que esté en pantalla y la muestrea cada 250 ms
(`isVisible()` y `windowHandle().isExposed()`). La columna "Ventana visible" lo indica y una
fila con la ventana tapada se marca **NO REPRESENTATIVA** (los instantes tapados quedan en
`window_hidden_at_s` del JSON). `gui_real_nopaint` oculta la ventana a propósito.

### Corrección del hallazgo de la fase 1c

La primera ejecución completa (fase 1c, `logs/gil_probe_20261023_194426.md`) dio para
`gui_real` una ingesta del **97 %**: **subestimó la pérdida** porque la ventana de la GUI estaba
ocluida (sin repintado), algo que aquel probe no comprobaba. Con la ventana visible, el mismo
escenario dio **24.5 %** (medido por el usuario). La conclusión de la fase 1c se mantiene y se
refuerza: la pérdida la causa el GIL retenido durante el repintado, y la solución adoptada en
la fase 1d es capturar en un proceso aparte.
