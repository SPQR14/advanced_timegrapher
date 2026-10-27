# Beta 2 (`v0.6.1.beta.2`): empaquetado en Python puro

Reparto:

- **Parte A — Gemini:** verificar y empaquetar en local, y corregir lo que falle. **No hace
  commits, push, etiquetas ni releases.**
- **Parte B — Alberto:** commits, push, etiqueta y publicación del release.

Leer también `packaging/README.md` (reglas del `.app`, micrófono en macOS, `freeze_support`,
rutas de datos) y `GEMINI.md` (reglas de arquitectura y empaquetado).

## Contexto: por qué fallaban los builds

| Fallo en GitHub | Causa | Estado |
|---|---|---|
| `Build Wheels / ubuntu-latest` | wheel C++ (cibuildwheel + manylinux) | **Eliminado**: ya no hay C++ |
| `Build App …` saltado, sin binarios en el release | dependía del job de wheels | Ahora solo depende de sí mismo |
| `Build & Package Release` en cola 8 h+ | runner `macos-13` retirado por GitHub | Quitado de la matriz |
| `CI / windows 3.12`: `test_worker_reports_child_death` | carrera en `audio/process_capture.py` | **Corregido** |

Cambios en la copia de trabajo (rama `release/v0.6.1.beta.2`, sin commit):

- **Fuera C++:** borrados `src/adv_tg/dsp/native_dsp.cpp`, `setup.py`,
  `.github/workflows/wheels.yml` y `tests/test_native_dsp.py`; fuera `pybind11` y
  `[tool.cibuildwheel]` de `pyproject.toml` y `adv_tg_dsp_native` de `packaging/adv-tg.spec`.
- **Fuera SciPy en todo el proyecto, también en tests** (ni extra `fallback` ni en `dev`).
  `adv_tg/dsp/engine.py` es numpy puro y vectorizado:
  - `sosfilt`/`sosfiltfilt`: biquads por bloques de 64 muestras en espacio de estados
    (productos matriciales + recurrencia escalar entre bloques); igual a la recursión muestra
    a muestra a ~1e-13.
  - Envolvente: Hilbert FIR por convolución directa (FFT si el kernel es largo) y media móvil
    con sumas acumuladas.
  - `find_local_maxima`: equivalente vectorizado de `find_peaks` (regla de mesetas).
- `tests/test_dsp.py` (nuevo): compara el motor con recursiones de referencia ingenuas.
- `main.py --smoke-test`: ya no exige la extensión C++; sigue exigiendo que SciPy no esté
  cargado y que una medición sin hardware dé una marcha finita.
- Workflows: `ci.yml` (tests en 3 SO × Python 3.11/3.12) y `build.yml` (wheel puro + sdist;
  PyInstaller en Linux x86_64 [tarball y paquete RPM], macOS arm64 y Windows x64; release solo con etiqueta `v*`).

Verificado por Claude en macOS arm64 (2026-10-27): 569 tests en verde, ruff limpio, wheel
`py3-none-any` sin binarios, `.app` de PyInstaller pasa `--smoke-test`, análisis a
105–122× tiempo real (igual que con el C++).

> [!IMPORTANT]
> No reintroducir C++, Cython, pybind11, cibuildwheel, numba ni SciPy (ni como dependencia
> opcional ni en tests). Si hace falta velocidad, optimizar en numpy (vectorizar, cachear
> núcleos). No tocar `src/adv_tg/core/` ni `src/adv_tg/dsp/` sin hablarlo: cambian cifras
> validadas.

---

## Parte A — Gemini: verificación y empaquetado local

### A1. Entorno limpio y tests

Desde la raíz del repo:

```bash
python3.12 -m venv /tmp/advtg-venv && source /tmp/advtg-venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]" ruff pyinstaller build
python -c "import importlib.util,sys; sys.exit('ERROR: scipy instalado' if importlib.util.find_spec('scipy') else 0)"

ruff check src/ tests/
ruff format --check src/ tests/
QT_QPA_PLATFORM=offscreen pytest -q          # en Linux: xvfb-run -a pytest -q
```

Esperado: `569 passed, 2 skipped, 1 xfailed` (aprox.), ruff sin avisos.

### A2. Wheel y sdist

```bash
python -m build
ls dist/*-py3-none-any.whl                   # debe existir: el wheel es universal
unzip -l dist/*.whl | grep -E "\.(so|pyd|dylib|cpp)$" && echo "ERROR: binarios en el wheel"
```

### A3. Aplicación con PyInstaller

```bash
pyinstaller packaging/adv-tg.spec --noconfirm
find dist -iname "*scipy*" | grep -q . && echo "ERROR: SciPy en el bundle"
QT_QPA_PLATFORM=offscreen "./dist/Advanced Timegrapher.app/Contents/MacOS/adv-tg" --version
QT_QPA_PLATFORM=offscreen "./dist/Advanced Timegrapher.app/Contents/MacOS/adv-tg" --smoke-test; echo "exit=$?"
```

El smoke test debe salir con código 0 y registrar `Smoke test passed: application initialized
and DSP verified successfully` (logs en `~/Library/Logs/adv_tg/adv_tg.log`).

Abrir también la app a mano una vez (`open "dist/Advanced Timegrapher.app"`): debe pedir
permiso de micrófono y medir con el reloj o con una muestra de `docs/samples/`.

### A3.bis. Empaquetado RPM para Linux (Fedora, RHEL, openSUSE)

Para generar el instalador RPM a partir del bundle generado por PyInstaller (`dist/adv-tg`):

1. **Archivos de configuración:**
   - `packaging/linux/adv-tg.desktop`: entrada Freedesktop para el lanzador de aplicaciones y Wayland `app-id`.
   - `packaging/linux/adv-tg.rpm.spec`: spec de RPM configurado con `AutoReqProv: no` y `%define debug_package %{nil}` (evita dependencias erróneas sobre librerías internas de PyInstaller) y dependencias de sistema explícitas (`portaudio`, `alsa-lib`, `libxkbcommon`).
   - `packaging/linux/build_rpm.sh`: script que crea el árbol `rpmbuild`, ejecuta la compilación y copia el archivo a `dist/`.

2. **Estructura de instalación en el sistema destino:**
   - `/opt/adv-tg/`: contiene el ejecutable y librerías completas de PyInstaller.
   - `/usr/bin/adv-tg`: enlace simbólico a `/opt/adv-tg/adv-tg`.
   - `/usr/share/applications/adv-tg.desktop`: integración en menús de escritorio (GNOME, KDE, etc.).
   - `/usr/share/icons/hicolor/scalable/apps/adv-tg.svg` y `/usr/share/pixmaps/adv-tg.svg`: icono oficial.

3. **Compilación local del RPM:**
   ```bash
   # En entornos basados en RPM (Fedora, RHEL, AlmaLinux, Rocky Linux):
   sudo dnf install -y rpm-build
   ./packaging/linux/build_rpm.sh

   # O en distribuciones Debian/Ubuntu con herramienta rpm:
   sudo apt-get install -y rpm
   ./packaging/linux/build_rpm.sh
   ```

4. **Inspección y verificación del paquete:**
   ```bash
   rpm -qip dist/adv-tg-*.rpm          # metadatos, versión y licencia
   rpm -qlp dist/adv-tg-*.rpm          # lista de ficheros instalados
   rpm -qp --requires dist/adv-tg-*.rpm # verificación de dependencias de sistema
   ```

5. **Prueba de instalación en máquina o contenedor RPM:**
   ```bash
   # En Fedora / RHEL:
   sudo dnf install ./dist/adv-tg-*.rpm
   # En openSUSE:
   sudo zypper install ./dist/adv-tg-*.rpm

   # Smoke test del binario instalado en el sistema:
   adv-tg --version
   QT_QPA_PLATFORM=offscreen adv-tg --smoke-test
   ```

### A4. Revisión de los workflows (sin ejecutarlos)

Revisar `.github/workflows/ci.yml` y `build.yml`. Reglas:

- Runners solo `ubuntu-latest`, `macos-latest`, `windows-latest` (sin `macos-13`).
- Nada de `cibuildwheel`, `wheels.yml`, `setup.py` ni `needs: wheels`.
- Linux instala `libportaudio2`, `xvfb` y `rpm` (`sudo apt-get install -y ... rpm`); ejecuta tests con `xvfb-run -a`; macOS/Windows con
  `QT_QPA_PLATFORM=offscreen`.
- Linux construye tanto el tarball `.tar.gz` como el paquete `.rpm` (`./packaging/linux/build_rpm.sh`) y sube ambos artefactos.
- Si un binario falla al importar un módulo en un proceso hijo: añadirlo a `hiddenimports`
  en `packaging/adv-tg.spec`.

### A5. Limpieza y entrega

```bash
rm -rf build dist                            # incluye un build/ viejo con el .so del C++
git status --short                           # no debe haber .so/.pyd/.cpp ni build/dist
```

Entregar a Alberto un resumen corto: resultado de A1–A4, archivos que hayas cambiado (si
alguno) y el porqué. **No hacer commit.**

---

## Parte B — Alberto: commits, etiqueta y publicación

### B1. Commit y push de la rama

```bash
git status                                   # revisar que solo están los cambios esperados
git add -A
git commit -m "revert(dsp): back to pure Python (numpy, no SciPy, no C++); fix CI/build workflows"
git push origin release/v0.6.1.beta.2
```

Esperar a que **CI/CD Pipeline** y **Build & Package Release** de la rama terminen en verde
(~10–15 min). El build de la rama genera los binarios pero no publica nada.

```bash
gh run list --branch release/v0.6.1.beta.2 -L 5
gh run watch <run-id>                        # si falla: gh run view <run-id> --log-failed
```

**No etiquetar hasta que esté todo en verde.**

### B2. Rehacer el release `v0.6.1.beta.2`

La etiqueta `v0.6.1.beta.2` y su release ya existen, pero **vacíos** (sin binarios) y apuntando
al commit viejo con C++ (`2240139`). Hay además dos runs en cola desde hace horas. Pasos:

```bash
# 1. Cancelar los runs atascados en cola
gh run list -L 20 --json databaseId,status --jq '.[] | select(.status=="queued" or .status=="in_progress") | .databaseId' \
  | xargs -n1 gh run cancel

# 2. Borrar el release y la etiqueta (remota y local)
gh release delete v0.6.1.beta.2 --yes --cleanup-tag
git tag -d v0.6.1.beta.2 2>/dev/null || true

# 3. Etiquetar el commit en verde y subir la etiqueta (dispara build.yml con publicación)
git tag -a v0.6.1.beta.2 -m "Beta 2: pure Python DSP (numpy), binaries for Linux, macOS arm64, Windows"
git push origin v0.6.1.beta.2
```

> [!NOTE]
> Si prefieres no reescribir una etiqueta ya publicada: sube la versión (p. ej. `0.6.2`) en
> `pyproject.toml` **y** `src/adv_tg/__init__.py` (deben coincidir), commit, y etiqueta
> `v0.6.2.beta.3`. El release se marca como *prerelease* solo si la etiqueta contiene `beta`
> o `rc`.

### B3. Comprobar el release

```bash
gh run list --workflow build.yml -L 3        # el run de la etiqueta, en verde (incl. Publish Release)
gh release view v0.6.1.beta.2
```

Debe contener exactamente:

- `adv-tg-linux-x86_64.tar.gz`
- `adv-tg-0.6.1-1.x86_64.rpm` (paquete nativo RPM para Fedora, RHEL, openSUSE)
- `adv-tg-macos-arm64.zip`
- `adv-tg-windows-x64.zip`
- `advanced_timegrapher-0.6.1-py3-none-any.whl`
- `advanced_timegrapher-0.6.1.tar.gz`

Y debe aparecer como *Pre-release*. Opcional: descargar el zip de macOS y ejecutar
`--smoke-test`.

### B4. Merge

Cuando el release esté bien: PR de `release/v0.6.1.beta.2` a `main` (y a `develop` si lo usas).

## Criterios de terminado

1. CI verde en Linux, macOS y Windows (Python 3.11 y 3.12).
2. `Build & Package Release` de la etiqueta en verde, incluido `Publish Release`.
3. Release con los 6 artefactos de la lista (incluyendo el paquete `.rpm` para Linux), marcado como prerelease, ninguno con `scipy`.
4. En el repo no hay `.cpp`, `setup.py`, `wheels.yml`, `.so`/`.pyd`, y
   `git grep -nE "import scipy|from scipy" -- src tests` sale vacío.
