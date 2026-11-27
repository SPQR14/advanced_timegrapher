# Plan de migración completa a C++ (Advanced Timegrapher 2.x → 3.0)

Estado de partida: **v2.0.1**. La medición (DSP, detector, métricas, reloj del ADC, sesión en
vivo) ya corre en el motor C++20 (`native/`, API C en `advtg.h`). El resto de la aplicación
sigue en Python: interfaz PyQt6 + pyqtgraph, captura y análisis en procesos hijos, E/S de
audio, informes, calibración, herramientas de línea de comandos y empaquetado con
PyInstaller.

Objetivo: **una aplicación C++20 + Qt 6 sin intérprete de Python**, con las mismas cifras
validadas, los mismos cinco instalables y la misma verificación en CI. La migración se hace
por fases, y **al final de cada fase se puede publicar**.

---

## 1. Qué se espera ganar (y qué no)

| Aspecto | Hoy (v2.0.1) | Tras la migración | Fuente |
|---|---|---|---|
| Velocidad de la medición | Ya nativa: 4.4× en vivo, 2.0× por lotes frente a numpy | Igual: **la medición no se acelera más** | medido (SER9) |
| Arquitectura en vivo | 3 procesos (GUI, captura, análisis) por el GIL; snapshots serializados con pickle por tubería | 1 proceso con hilos; snapshots por memoria compartida sin copias | `audio/analysis_process.py` |
| Latencia de la GUI | Bloqueos de hasta 60 ms con el análisis en el proceso de la GUI (por eso se separó) | Sin GIL ni IPC: hilos independientes | `analysis_process.py` |
| Arranque, memoria, tamaño del instalador | Python + numpy + PyQt6 + pyqtgraph + PyOpenGL empaquetados | Solo Qt 6 + el motor | **medir en la fase 0** |
| Futuras apps móviles | El motor ya es reutilizable | Además, la lógica de la app (sesión de prueba, posiciones, informes) | — |

La ganancia de rendimiento de la medición **ya está conseguida**. Lo que aporta el resto de la
migración es simplicidad (un proceso, sin GIL ni pickle), respuesta de la GUI, arranque,
tamaño del instalador y una sola base de código. La fase 0 mide la línea base para comprobarlo
con números.

## 2. Inventario (líneas no vacías de Python, v2.0.1)

| Área | Líneas | Destino |
|---|---:|---|
| `ui/` (ventana principal 4 066 líneas, diálogos, workers, gráficas) | 7 082 | Qt 6 Widgets C++ — fase 3 |
| `core/` (envoltorios del motor + referencia numpy) | 6 664 | Ya en C++; queda la referencia numpy — fase 6 |
| `tools/` (reference_check, check_samples, measure, detect_beats…) | 3 001 | CLI C++ `adv-tg-cli` — fase 4 |
| `utils/` (archivos de audio y etiquetas, informes, calibración, actualización, BD de calibres, MSIX) | 1 954 | Biblioteca C++ — fase 1 |
| `audio/` (captura, procesos hijo, repetición de archivos) | 1 899 | Runtime C++ con hilos — fase 2 |
| `dsp/` (envoltorios + referencia numpy) | 1 442 | Ya en C++ — fase 6 |
| `native_bridge.py` | 1 088 | Desaparece en la fase 6 |
| `i18n/` (traductor) | 816 | Qt Linguist o tabla propia — fase 3 |
| `analysis/` (tren de rodaje, diagnóstico) | 784 | C++ — fase 1 |
| `mock/` (generador sintético) | 491 | Tests C++ — fase 1 |
| `main.py` (arranque, smoke test) | 302 | `main.cpp` — fase 3 |

Dependencias que se sustituyen:

| Python | C++ |
|---|---|
| PyQt6 | Qt 6 (Widgets, PrintSupport, Network) |
| pyqtgraph + PyOpenGL | QCustomPlot (GPL-3.0, compatible) o gráficas propias con QPainter |
| sounddevice | PortAudio directo (`audio_capture.cpp` ya existe) |
| soundfile | libsndfile (LGPL-2.1, enlace dinámico) |
| numpy | Ya sustituido por el motor |
| certifi | Almacén de certificados del sistema vía Qt Network |
| zipfile (`utils/report.py`) | `miniz` (MIT, enlace estático / vcpkg) |
| json (`adc_calibration.json`, API GitHub) | `nlohmann/json` (MIT, solo cabeceras, sin dependencia de Qt para reutilización móvil) |
| logging y rutas (`utils/logger_setup.py`, `config_dir()`) | Helpers C++ multiplataforma con las rutas de `GEMINI.md` (`~/Library/Logs/adv_tg`, `%LOCALAPPDATA%\adv_tg\logs`, `~/.local/state/adv_tg/logs` y `config_dir()`); assets estáticos (`watch_db.csv`, iconos) en Qt Resource System (`.qrc`) |
| pytest | Catch2 o GoogleTest + ctest |
| PyInstaller | CMake + windeployqt / macdeployqt / linuxdeploy + CPack |

## 3. Principios

1. **Las cifras validadas no cambian.** Antes de quitar ninguna referencia Python se
   congelan "archivos dorados": latidos, métricas y snapshots de cada grabación de
   `docs/samples/`, generados con v2.0.1. Todo el código C++ nuevo debe reproducirlos con la
   tolerancia que fija hoy el test de paridad.
2. **Patrón estrangulador:** cada fase sustituye una capa por debajo de la aplicación Python,
   que sigue funcionando y publicándose hasta que la GUI C++ alcanza la paridad de funciones.
3. **Cada fase termina publicable**, con el CI de las tres plataformas en verde, sin cambiar el
   conjunto de instalables y sin regresiones en `--smoke-test` ni en la referencia GPS PPS.
4. **Misma política numérica:** sin `-ffast-math`; `/fp:precise`; dispatch SIMD en ejecución.
5. **Licencias:** el proyecto es GPL-3.0. Qt (LGPL-3/GPL-3), QCustomPlot (GPL-3), PortAudio
   (MIT) y libsndfile (LGPL-2.1) son compatibles.

## 4. Fases

### Fase 0 — Cimientos y línea base (sin cambios para el usuario)

- **Medir la línea base de v2.0.1** en las tres plataformas: tiempo de arranque, RAM en
  medición, tamaño de cada instalador y tiempos de frame de la GUI. Para esto último hay
  que añadir una medición: hoy solo `tools/gil_probe.py` mide las esperas del GIL.
- **Archivos dorados:** script que exporta, por cada grabación de referencia, latidos y
  métricas por lotes y la secuencia de snapshots en vivo. Se versionan en `tests/golden/`.
- **Esqueleto C++:**
  - árbol `app/` (aplicación) junto a `native/` (motor);
  - CMake de nivel superior;
  - Catch2 + ctest en CI;
  - Qt 6 en los runners (Linux EL9, macOS, Windows MSVC).
- **Decisiones cerradas** (sección 6).
- **Salida:** CI compila un "hola mundo" Qt C++ en las tres plataformas y en contenedor
  `almalinux:9` (`glibc 2.34`, job `cpp-app-el9`), y ctest ejecuta las comparaciones con
  los archivos dorados contra el motor actual.

### Fase 1 — Completar la biblioteca C++ por debajo de la GUI (versión 2.1)

Todo lo que la GUI llama y aún es Python pasa a C++ detrás de la API C, manteniendo esta
capa **sin dependencia de Qt** (en `native/` o en una biblioteca intermedia `core_app/`) para
reutilizarla en futuras apps móviles:

- E/S de audio con libsndfile, incluidas las etiquetas Vorbis/WAV `ADV_TG_*` que usan la
  calibración y la repetición (`utils/audio_files.py`).
- Calibración del ADC con GPS PPS (`utils/adc_calibration.py`, `tools/adc_calibration.py`) y
  lectura/escritura atómica de `adc_calibration.json` con `nlohmann/json`.
- Diagnóstico y rasgos (`analysis/features.py`) y el análisis completo del tren de rodaje
  (hoy solo sus núcleos son nativos).
- Base de calibres (CSV), informes `.zip` (`utils/report.py` con `miniz`: `report.txt`,
  logs, `adc_calibration.json` y grabación `.flac`), exportación CSV y utilidades de
  seguridad (`utils/security.py`: saneado de CSV y nombres de archivo).
- Logger y rutas escribibles multiplataforma conforme a `GEMINI.md` (`~/Library/Logs/adv_tg`,
  `%LOCALAPPDATA%\adv_tg\logs`, `~/.local/state/adv_tg/logs` y `config_dir()`).
- Revisar `advtg_session_metric_warnings` y `advtg_session_detector_warnings` en `advtg.h`
  (hoy cadenas fijas en español) hacia códigos localizables para mantener la paridad bilingüe
  EN/ES en la GUI.
- Generador sintético (`mock/`) para tests C++.
- **Salida:** la app Python usa estas funciones nativas; archivos dorados (incluidos batch y
  PPS en `tests/cpp/`) y suite Python en verde; se publica como 2.1.

### Fase 2 — Runtime en vivo en C++: un proceso, sin GIL (versión 2.2)

- `portaudio` pasa a ser dependencia explícita del build y empaquetado C++ (`vcpkg.json` en
  Windows, Homebrew en macOS, `libportaudio2` en Linux), al dejar de depender de `sounddevice`.
- Un objeto `LiveRuntime` C++ con hilos sustituye a `capture_process`, `analysis_process`,
  `process_capture`, `capture_worker`, `measure_worker`, `pps_worker` y `file_replay`:
  - hilo de captura: PortAudio y escritura en el `SampleStream` sin bloqueos (ya existe);
  - hilo de análisis: `advtg_session_update` 4–8 veces por segundo;
  - entrega del snapshot a la GUI por cola o doble búfer, sin serializar;
  - modo repetición de archivo y fuente sintética.
- La GUI Python lo consume desde un solo hilo.
- **Salida:**
  - desaparecen los procesos hijo y el pickle;
  - se compara la latencia de la GUI con la línea base de la fase 0;
  - los snapshots siguen idénticos a los dorados;
  - se publica como 2.2.

### Fase 3 — GUI C++ con Qt 6 (versión 3.0-beta)

Se construye en paralelo a la GUI Python; se publica como beta junto a la 2.x estable.

- **Ventana principal** (128 métodos en `main_window.py`): parámetros de la prueba, cifras,
  calidad de señal, telemetría, posiciones y estadísticas globales, grabación, análisis,
  exportación, informes.
- **Gráficas:** cinta de papel, osciloscopio con marcadores P1/P2/P3 y forma de onda en vivo,
  con zoom, centrado, exportación e impresión (`QPrinter`). QCustomPlot o QPainter propio.
  Hay que verificar con escalado 1.0×, 1.25×, 1.5× y 2.0× (la lección del fallo de la 2.0.0).
  Se retira `native/src/graph_renderer.cpp` (`ADVTG_WITH_RENDERER`, desactivado por defecto en
  `native_plots.py`).
- **Diálogos** (`dialogs.py`, `analysis_dialog.py`), aviso de actualización
  (`QNetworkAccessManager` + `nlohmann/json`), política MSIX, traducciones EN/ES y assets
  estáticos (`watch_db.csv`, iconos) embebidos vía Qt Resource System (`.qrc`).
- **Hoja de estilo:** los QSS actuales sirven tal cual en Qt C++.
- **Salida:**
  - lista de paridad de funciones completa, revisada con capturas lado a lado en las tres
    plataformas;
  - `--smoke-test` equivalente en C++;
  - prueba de uso real con micrófono en cada sistema.

### Fase 4 — Herramientas de línea de comandos (con la 3.0)

- Un ejecutable `adv-tg-cli` con subcomandos: `reference-check` (lo usa el smoke test),
  `check-samples`, `measure`, `detect-beats`, `capture-check`, `adc-calibration`.
- `gil_probe` se retira: sin GIL no tiene sentido.
- Las gráficas de diagnóstico que hoy hace matplotlib (`detect_beats --plot`) se exportan a
  CSV o SVG.

### Fase 5 — Empaquetado C++ (3.0)

- **Linux:** compilación en AlmaLinux 9 (glibc 2.34); Qt 6 empaquetado con linuxdeploy en el
  AppImage; deb y rpm con CPack o los scripts actuales; las mismas verificaciones en Debian 12,
  Ubuntu 22.04/24.04, Rocky 9 y Fedora.
- **macOS:** macdeployqt; mínimo macOS 11; DMG.
- **Windows:** windeployqt; los binarios oficiales de Qt 6 (`aqtinstall`) y `vcpkg`
  (`x64-windows`) enlazan con el CRT dinámico (`/MD`), por lo que Inno Setup y MSIX
  distribuirán el VC++ Redistributable (o sus DLLs). `advtg_native` conserva `/MT` por defecto
  en 2.x para `ctypes` (`ADVTG_STATIC_MSVC_RUNTIME=ON`) y en 3.0 se compila todo en `/MD`
  (`-DADVTG_STATIC_MSVC_RUNTIME=OFF`).
- `check_native_deps.py` se amplía a todo el bundle.
- Los mismos cinco instalables con los mismos nombres, `SHA256SUMS.txt` y firma GPG.

### Fase 6 — Retirar Python (3.0 estable)

- La GUI C++ pasa a ser la única que se publica; la 2.x queda congelada.
- La referencia numpy se mueve a `tests/reference/` (o a un repositorio de investigación) y
  deja de estar en el paquete. Sigue siendo la referencia metrológica legible de los
  algoritmos.
- `native_bridge.py` y la API C quedan para cuadernos de investigación en Python, sin formar
  parte de la app.
- **Salida:** CI sin Python en la ruta de release (salvo scripts de soporte), suite ctest y
  archivos dorados como garantía.

## 5. Calendario de versiones

| Versión | Contenido | Para el usuario |
|---|---|---|
| 2.1 | Fase 1: biblioteca C++ completa bajo la GUI Python | Igual, más rápido en E/S y calibración |
| 2.2 | Fase 2: runtime en vivo C++ (un proceso) | GUI más fluida, menos memoria |
| 3.0-beta | Fases 3–5: app C++ en paralelo a la 2.2 | Beta opcional |
| 3.0 | Fase 6: solo C++ | Instalador más pequeño, arranque más rápido |

## 6. Decisiones (tomadas el 2026-11-25)

1. **GUI: Qt 6 Widgets.** Porte directo de la interfaz actual, con los mismos widgets y QSS.
   QML se reconsiderará solo para una futura app móvil, sin tocar el motor.
2. **Gráficas: QCustomPlot** (GPL-3, un solo archivo, con impresión vía `Qt6::PrintSupport` y
   exportación). Frente a Qt 6.11 requiere validar un PoC antes de la fase 3 (parche
   `QT_VERSION_CHECK(6, 2, 0)` para `moc` en QCustomPlot 2.1.1, `/bigobj` en MSVC y
   supresión de avisos de deprecación en su unidad de compilación); si no compensa, se usa
   `QPainter` propio (las tres gráficas solo dibujan unos miles de puntos).
3. **Dependencias:**
   - Qt 6.11.3 oficial instalado con `aqtinstall` (fijado al commit `4c75985` de GitHub con el
     PR #1048 para el formato de carpetas Windows de Qt ≥ 6.11 hasta que salga `> 3.3.0` en PyPI);
   - vcpkg en modo manifiesto (`vcpkg.json`, baseline vcpkg 2026.10.16, commit `9eec002`)
     para `libsndfile`, `catch2`, `portaudio`, `nlohmann-json` y `miniz` en Windows;
   - paquetes del sistema en Linux y macOS;
   - Catch2 v3.16.1 por FetchContent cuando el sistema no trae Catch2 3.
4. **Paquete Python:** se congela en 2.x como biblioteca de investigación (wheel puro con la
   referencia numpy); desde la 3.0 no se publica como aplicación.

## 6b. Estado de la fase 0

| Entregable | Dónde | Estado |
|---|---|---|
| Archivos dorados de v2.0.1 (10 relojes: lotes + en vivo; 2 PPS) | `tests/golden/`, `scripts/make_golden.py` | Generados y deterministas (`--check`) |
| Test Python contra los dorados (motor nativo) | `tests/test_golden.py` | 23 tests en verde |
| Test C++ contra los dorados, a través de `advtg.h` y libsndfile | `tests/cpp/` | 10 relojes en vivo + 10 por lotes + 2 PPS + pruebas unitarias de fase 1 (8 casos, 41 491 comprobaciones en verde) |
| Línea base y su contrato `ADV_TG_BENCH` | `scripts/baseline.py`, `adv_tg/tools/bench.py` | Medida en Linux; `build.yml` mide los binarios empaquetados de las 3 plataformas |
| Esqueleto C++ (CMake raíz, `app/` Qt 6 Widgets, Catch2) | `CMakeLists.txt`, `app/`, `vcpkg.json` | Compila sin avisos; `--version`, `--smoke-test` y `ADV_TG_BENCH=startup` |
| CI de la app C++ en las 3 plataformas + AlmaLinux 9 | `ci.yml`, jobs `cpp-app` y `cpp-app-el9` | En verde: Ubuntu, macOS, Windows MSVC+vcpkg y AlmaLinux 9 glibc 2.34 |

Hallazgo de la fase 0: en los Hamilton H-50 (P1 débil, Notch activo), la referencia numpy
elige otro candidato de frente de desbloqueo (`t_p1`, hasta 36 muestras) en algunos latidos.
Eso mueve las cifras en vivo hasta 0.006 s/d y 1° en algunas actualizaciones; las cifras
finales por lotes coinciden dentro de su incertidumbre. Por eso los archivos dorados son las
salidas del **motor nativo** de v2.0.1 (detalle en `tests/golden/protocol.py`).

### Línea base v2.0.1 (Linux, Ryzen AI 9 HX 370, desde el código, `QT_QPA_PLATFORM=offscreen`)

| Medida | Valor |
|---|---|
| Arranque hasta mostrar la ventana, tiempo total del proceso (mediana de 5) | 344 ms |
| Arranque medido dentro de la app (desde `main()`) | 251 ms |
| RAM máxima en medición en vivo (`nh34.flac`, 30 s, árbol de procesos) | 222 MB |
| Retraso de un temporizador de 16 ms en la GUI: p50 / p99 / máximo | 0.08 / 1.4 / 5.5 ms |
| Tamaño de los instalables de v2.0.0 | Windows 140 MB, DMG 146 MB, deb 153 MB, rpm 157 MB, AppImage 169 MB |

Los binarios empaquetados de cada plataforma se miden en cada ejecución de `build.yml`
(artefactos `baseline-linux`, `baseline-macos` y `baseline-windows`).

## 6c. Estado de la fase 1

| Entregable | Dónde | Estado |
|---|---|---|
| API C99 de aplicación ampliada (ABI `ADVTG_ABI_VERSION == 1` aditiva) | `native/include/advtg.h` | Pasa `gcc -std=c99 -Wall -Wextra -pedantic -Werror -fsyntax-only` |
| E/S de audio WAV/FLAC y etiquetas atómicas (`libsndfile` por `dlopen`/`LoadLibraryW`) | `native/src/audio_files.cpp`, `adv_tg/utils/audio_files.py` | Autocontenida (`DT_NEEDED` cero); guarda y lee PCM/FLOAT y Vorbis/WAV |
| Detector/calibrador GPS PPS y detector/métricas por lotes | `native/src/pps.cpp`, `c_api_batch.cpp`, `adv_tg/core/pps.py` | 10 relojes por lotes y 2 PPS dentro de tolerancias `NATIVE` en C++ |
| Persistencia JSON de calibraciones (`nlohmann/json` v3.11.3 MIT) | `native/src/calibration_store.cpp`, `adv_tg/utils/adc_calibration.py`, `adc_clock.py` | Historial 50/dispositivo, envejecimiento, etiquetas `pps` (`method = 4`) |
| Tren de rodaje, QR Detrend, Bluestein FFT, rasgos y diagnóstico | `native/src/gear_train.cpp`, `features.cpp`, `adv_tg/analysis/` | Conectado desde Python con respaldo `ADV_TG_DISABLE_NATIVE=1` |
| Informe `.zip` (`miniz` v3.0.2 MIT, `-DMINIZ_NO_STDIO`), CSV, seguridad, DB, logger, avisos ES/EN | `native/src/{report,csv_export,security,watch_db,paths_log,warnings,mock_generator}.cpp` | Conectado con `native_bridge.py`; dispositivos PortAudio vía `dlopen` |
| Compilación estática móvil sin audio de escritorio (`ADVTG_STATIC=ON`) | `native/CMakeLists.txt` (`ADVTG_WITH_AUDIO_CAPTURE=OFF`, `AUDIO_IO=OFF`) | Compila `libadvtg_native.a` y `advtg_replay` sin avisos |
| Pruebas C++ (dorados live + lotes + PPS + librería fase 1) | `tests/cpp/` | 8 casos de Catch2 en verde (41 491 comprobaciones) |
| Pruebas de paridad Python nativo vs Python puro | `tests/test_native_phase1.py` | Compara salidas nativas frente a `ADV_TG_DISABLE_NATIVE=1` |

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| La ventana principal (4 066 líneas) tiene comportamiento sutil acumulado en v0.x–v2.0 | Lista de paridad de funciones a partir de sus 128 métodos y de `tests/test_gui_*`; GUI C++ en beta paralela |
| Diferencias numéricas al portar la E/S, la calibración o el diagnóstico | Archivos dorados con tolerancia fija; la referencia numpy sigue en los tests |
| Gráficas con escalado HiDPI | Verificación con capturas a 1.0×, 1.25×, 1.5× y 2.0× en CI (`QT_SCALE_FACTOR`) |
| Qt en AlmaLinux 9 (CI de Linux) | Comprobar en la fase 0 que el Qt de aqtinstall arranca en EL9 y en las cinco distros de verificación |
| Más barrera de entrada para colaborar (C++ frente a Python) | Documentación de arquitectura; API C y Python de investigación para experimentar |
| Firma de código y notarización (macOS, Store) | No cambia respecto a hoy: el mismo proceso sobre otro ejecutable |

## 8. Criterios de aceptación de la 3.0

- [ ] Archivos dorados: mismos latidos y métricas dentro de la tolerancia en las 12
      grabaciones versionadas de `docs/samples/`.
- [ ] Referencia GPS PPS (`reference-check`) superada en el smoke test de cada instalable.
- [ ] Lista de paridad de funciones de la GUI completa en las tres plataformas.
- [ ] Gráficas correctas a 1.0×, 1.25×, 1.5× y 2.0×.
- [ ] Arranque, RAM y tamaño de instalador iguales o mejores que la línea base de la fase 0.
- [ ] Los cinco instalables verificados en sistemas limpios, como hoy.
