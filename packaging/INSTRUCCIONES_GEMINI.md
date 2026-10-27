# Instrucciones de empaquetado: extensión C++ precompilada con GitHub Actions

Encargo para el agente que haga el empaquetado (Gemini). Leer también `packaging/README.md`
(reglas del `.app`, micrófono en macOS, `freeze_support`, rutas de datos) y la última sección
de `docs/resumen_beta1.md` (núcleo DSP sin SciPy).

## Contexto

- El análisis usa una extensión C++ (`src/adv_tg/dsp/native_dsp.cpp` → módulo
  `adv_tg.dsp.adv_tg_dsp_native`, compilada por `setup.py` con pybind11). Contiene la envolvente,
  la búsqueda de sub-eventos, `sosfilt` y `sosfiltfilt`.
- **SciPy ya no es dependencia de la aplicación** (solo de desarrollo: referencia en los tests).
  Sin la extensión, `adv_tg.dsp.engine` recurre a SciPy si está instalado y, si no, lanza
  `DspBackendError`. Por eso la extensión debe estar en todo lo que se distribuya.
- Hoy, instalar desde el código exige un compilador de C++. El objetivo es que **nadie** lo
  necesite: ni el usuario de la app (ya lo cumple: PyInstaller incluye el binario) ni quien
  instala con `pip`.

## Decisión: no guardar binarios en el repositorio

**No** añadir `.so`/`.pyd` a git (`.gitignore` ya los excluye). Motivos: uno por versión de Python ×
sistema × arquitectura; en macOS Intel y Apple Silicon comparten nombre
(`.cpython-312-darwin.so`); Linux necesita *manylinux* para ser portable; un binario olvidado
quedaría desactualizado frente al `.cpp` sin que nadie lo note; y no se pueden revisar en un PR.
En su lugar: **wheels precompilados con cibuildwheel en GitHub Actions**, publicados en cada
release.

## Tareas

### 1. Compilación opcional y huella del código fuente (`setup.py`, C++, `engine.py`)

1. **Extensión opcional:** pasar `optional=True` a `Pybind11Extension` para que
   `pip install` no falle sin compilador. En ese caso la app solo funcionará con SciPy
   instalado: añadir el extra `fallback = ["scipy>=1.11"]` en `[project.optional-dependencies]`
   y explicarlo en el mensaje de `DspBackendError` (`pip install "advanced-timegrapher[fallback]"`).
2. **Huella del código:** que `setup.py` calcule el SHA-256 de `native_dsp.cpp` y lo escriba en un
   encabezado generado (p. ej. `src/adv_tg/dsp/native_dsp_source_hash.h`, en `.gitignore`), que
   `native_dsp.cpp` incluya y exporte como `SOURCE_SHA256`. Un encabezado generado es más
   portable que pasar la cadena con `-D`/`/D` (las comillas se escapan distinto en MSVC).
3. En `adv_tg.dsp.engine`: si existe `native_dsp.cpp` junto al módulo (instalación desde el
   código) y su hash no coincide con `SOURCE_SHA256`, registrar un aviso claro ("recompila:
   pip install -e ."). En wheels y en la app el `.cpp` no está: no comprobar nada.
4. Test en `tests/test_native_dsp.py`: si la extensión está cargada y el `.cpp` existe, el hash
   debe coincidir (falla si alguien cambia el `.cpp` sin recompilar).

### 2. Wheels con cibuildwheel (workflow nuevo `.github/workflows/wheels.yml`)

- Disparadores: etiquetas `v*` y `workflow_dispatch`.
- Configuración en `pyproject.toml` (`[tool.cibuildwheel]`), orientativa:

  ```toml
  [tool.cibuildwheel]
  build = "cp311-* cp312-* cp313-*"
  skip = "*-musllinux_* pp*"
  test-requires = ["pytest", "scipy"]
  test-command = "pytest {project}/tests/test_native_dsp.py -q"

  [tool.cibuildwheel.macos]
  archs = ["arm64", "x86_64"]
  environment = { MACOSX_DEPLOYMENT_TARGET = "11.0" }

  [tool.cibuildwheel.linux]
  archs = ["x86_64"]   # aarch64 opcional: runner ubuntu-24.04-arm o QEMU (lento)

  [tool.cibuildwheel.windows]
  archs = ["AMD64"]
  ```

- Matriz de jobs: `ubuntu-latest`, `windows-latest` y `macos-14` (Apple Silicon; también genera
  x86_64). Usar la acción `pypa/cibuildwheel` en su versión estable actual.
- Un job aparte con `python -m build --sdist` para el paquete fuente.
- El test de cada wheel (`test_native_dsp.py`) debe comprobar que la extensión **se carga**
  (`is_native_dsp_available()`) y que coincide con SciPy: si el wheel saliera sin extensión, el
  release no debe publicarse.
- Publicar los `.whl` y el `.tar.gz` en el mismo GitHub Release que las apps (job `release` de
  `build.yml`, o uno propio con `softprops/action-gh-release` y `needs` correctos). PyPI queda
  para más adelante (requiere cuenta y *trusted publishing*).

### 3. Apps con PyInstaller (`.github/workflows/build.yml`)

1. **Construir la app desde el wheel**, no desde `pip install -e .`: descargar el artefacto del
   job de wheels (`needs: wheels`) e instalarlo con `pip install <wheel>`, de modo que la app y el
   wheel publicado lleven exactamente el mismo binario. El `.spec` hoy busca el `.so`/`.pyd` en
   `src/adv_tg/dsp/`: cambiarlo para que PyInstaller lo recoja del paquete instalado
   (`collect_dynamic_libs("adv_tg")` o simplemente el `hiddenimport` ya declarado
   `adv_tg.dsp.adv_tg_dsp_native`, que PyInstaller resuelve desde `site-packages`).
2. **macOS Intel:** hoy solo se construye en `macos-latest` (Apple Silicon), así que los Mac Intel
   se quedan sin app. Añadir un job en un runner Intel (`macos-13` o el que GitHub ofrezca) y
   publicar `adv-tg-macos-arm64.zip` y `adv-tg-macos-x86_64.zip`. Una app *universal2* no es
   viable: numpy no publica wheels universal2. Si no hubiera runner Intel, decirlo en las notas
   del release y en el README.
3. **SciPy fuera del paquete:** el `.spec` ya lo excluye; comprobar en el workflow que no aparece
   (`dist/**/scipy` no debe existir) y que el tamaño baja frente a la beta 1.
4. **Prueba de humo más estricta:** que `--smoke-test` (en `adv_tg.main`) además de abrir la
   ventana:
   - falle si `adv_tg.dsp.is_native_dsp_available()` es False;
   - falle si `"scipy"` está en `sys.modules`;
   - ejecute una medida corta sin hardware: reproducir un sintético o
     `docs/samples/miyota_9015.flac` con `MeasurementSession` (no hace falta la GUI) y comprobar
     que sale una marcha finita.
5. Mantener lo que ya funciona: `Info.plist` con `NSMicrophoneUsageDescription`, iconos,
   `freeze_support`, PortAudio dentro del paquete de Linux.

### 4. CI (`.github/workflows/ci.yml`)

- Sigue compilando con `pip install -e .[dev]` (los runners tienen compilador): así los tests
  cubren el código C++ actual.
- Añadir un job sin compilador ni extensión (p. ej. `ADV_TG_SKIP_NATIVE=1` en `setup.py`, o borrar
  el `.so` tras instalar) con `pip install -e .[dev]` (incluye SciPy) para probar el respaldo.

### 5. Documentación

- `README.md` (inglés y español), opción "desde el código fuente": primero instalar el wheel del
  release (`pip install <url del .whl>` o `pip install --find-links <url del release>
  advanced-timegrapher`) sin compilador; el compilador solo para desarrollar desde git.
- `packaging/README.md`: describir el flujo wheels → app y la tabla de artefactos del release.
- Notas del release: lista de archivos por sistema y arquitectura.

## Criterios de aceptación

1. Una etiqueta `v*` publica: apps de macOS (arm64 y, si hay runner, x86_64), Windows y Linux;
   wheels cp311–cp313 de los tres sistemas; el sdist.
2. Todos los wheels pasan `tests/test_native_dsp.py` con la extensión cargada.
3. Cada app pasa la prueba de humo estricta (extensión cargada, sin SciPy, medida corta).
4. `pip install <wheel>` en una máquina sin compilador permite ejecutar `adv-tg`.
5. `pip install -e .` sin compilador termina sin error y la app indica cómo resolverlo.
6. Ningún `.so`/`.pyd` en git; un cambio en `native_dsp.cpp` sin recompilar hace fallar el test
   de la huella.
7. La suite completa (`pytest`) y `ruff` siguen pasando.
