# Empaquetado y Distribución Multiplataforma

Este directorio contiene las especificaciones y utilidades para construir **un instalable por plataforma** y publicarlos en GitHub Releases al crear una etiqueta `v<versión>` (`.github/workflows/build.yml`).

| Archivo | Para qué |
|---|---|
| `launcher.py` | Script de entrada: invoca `multiprocessing.freeze_support()` antes de cualquier importación pesada |
| `adv-tg.spec` | Especificación de PyInstaller validada (carpeta única; `.app` en macOS con inyección de `Info.plist` e icono) |
| `rthook_portaudio.py` | Hook de arranque (Linux): hace que `sounddevice` cargue el PortAudio empaquetado |
| `linux/adv-tg.desktop` | Entrada de escritorio de Linux (menú de aplicaciones, Wayland app-id) |
| `linux/adv-tg.rpm.spec`, `linux/build_rpm.sh` | Paquete RPM (Fedora, RHEL/Alma/Rocky, openSUSE) |
| `linux/build_deb.sh` | Paquete Debian (Debian, Ubuntu y derivadas) |
| `linux/build_appimage.sh` | AppImage (cualquier distribución); `appimagetool` y el runtime se descargan en versiones fijadas y verificadas por SHA-256 |
| `macos/build_dmg.sh` | Imagen de disco DMG con enlace de arrastrar y soltar a `/Applications` |
| `macos/Info.plist` | Configuración del bundle de macOS, incluyendo **`NSMicrophoneUsageDescription`** e icono |
| `macos/adv-tg.icns` | Icono multi-resolución de macOS (16x16 hasta 1024x1024) |
| `windows/installer.iss` | Instalador de Windows (Inno Setup 6): asistente, acceso en el menú Inicio, desinstalador y actualización |
| `windows/adv-tg.ico` | Icono estándar para ejecutables de Windows |
| `windows/build_msix.ps1` | Empaquetado MSIX sin firmar para Microsoft Store (`makepri` + `makeappx`) y verificación opcional `-Verify` con instalación real (`Add-AppxPackage`) |
| `windows/msix/AppxManifest.xml.in` | Manifiesto MSIX (`runFullTrust`, `microphone`, `AppExecutionAlias`) parametrizado para Partner Center |
| `windows/msix/Assets/*`, `windows/generate_msix_assets.py` | Iconos PNG multi-escala/unplated para MSIX y `StoreListing/AppIcon-300x300.png` generados desde `adv-tg.svg` |
| `windows/MSIX_STORE_GUIDE.md` | Guía paso a paso para alta gratuita en Microsoft Partner Center y envío a Microsoft Store |

---

## Flujo de Construcción (arquitectura híbrida, v2.0)

La aplicación es Python/Qt y la medición corre en el **motor nativo C++20**
(`libadvtg_native.so` / `.dylib` / `advtg_native.dll`, código en `native/`), que cada job
compila en su plataforma con `scripts/build_native.py` antes de PyInstaller; `adv-tg.spec` lo
copia a `adv_tg/_native/` dentro del bundle y `adv_tg.native_bridge` lo carga con `ctypes`.
Sin la librería (o con `ADV_TG_DISABLE_NATIVE=1`) todo funciona igual en numpy puro: es la
referencia de los tests de paridad y lo que instala el wheel `py3-none-any`.

Cómo se garantiza que los binarios siguen siendo autocontenidos:

- `scripts/check_native_deps.py` rechaza la librería si necesita algo que un sistema limpio
  no tiene: en Linux solo glibc ≤ 2.34, libstdc++ y libgcc_s (declaradas por el deb/rpm); en
  macOS solo `/usr/lib` y `/System` con mínimo macOS 11.0 (`MACOSX_DEPLOYMENT_TARGET`); en
  Windows nada de `MSVCP140`/`VCRUNTIME140` ni runtimes de MinGW (MSVC con runtime estático,
  `/MT`). Se ejecuta sobre la librería recién compilada y sobre la del bundle.
- `--smoke-test` de un binario congelado falla si el motor nativo o su API de sesión no
  cargan (un binario que cayese en silencio al modo numpy pasaría desapercibido si no).
- Un solo binario por plataforma para todas las CPU: x86_64 compila el núcleo para
  x86-64-v2 y elige SSE4.2 / AVX2 / AVX-512 en ejecución (CPUID + XGETBV); macOS usa NEON.

```
GitHub Actions (.github/workflows/build.yml):
 1. [meta]          ──> versión (src/adv_tg/__init__.py == pyproject.toml == etiqueta v<versión>)
 2. [python-dist]   ──> python -m build: solo comprueba que el wheel sigue siendo py3-none-any
 3. [linux]         ──> contenedor AlmaLinux 9 (glibc 2.34): PyInstaller, smoke test,
                        motor C++ con GCC 11 + check_native_deps, rpm + deb + AppImage
 4. [linux-verify]  ──> instala el deb en Debian 12 y Ubuntu 22.04/24.04 y el rpm en Rocky 9 y
                        Fedora (dependencias resueltas por apt/dnf) y ejecuta el smoke test de
                        la aplicación instalada y del AppImage, en offscreen y sobre X11 (Xvfb)
 5. [macos]         ──> PyInstaller, smoke test, DMG; smoke test de la app sacada del DMG
 6. [windows]       ──> PyInstaller, smoke test, instalador Inno Setup; lo instala en silencio, smoke test
                        de la aplicación instalada y desinstalación
 7. [release]       ──> solo con etiqueta v*: comprueba que hay exactamente los cinco
                        instalables y los publica con SHA256SUMS.txt
```

### Tabla de Artefactos de Release

| Artefacto | Plataforma | Instalación |
|---|---|---|
| `adv-tg-<versión>-linux-x86_64.AppImage` | Linux x86_64, cualquier distribución | `chmod +x` y ejecutar |
| `adv-tg-<versión>-1.x86_64.rpm` | Fedora, RHEL/Alma/Rocky 9+, openSUSE | `sudo dnf install ./adv-tg-*.rpm` |
| `adv-tg_<versión>_amd64.deb` | Debian 12+, Ubuntu 22.04+ y derivadas | `sudo apt install ./adv-tg_*.deb` |
| `adv-tg-<versión>-macos-arm64.dmg` | macOS Apple Silicon | Arrastrar a `/Applications` |
| `adv-tg-<versión>-windows-x64-setup.exe` | Windows 10/11 x64 | Doble clic (o `/VERYSILENT`) |

Ya no se publican el `.tar.gz`, los `.zip` ni el wheel/sdist:
eran el mismo programa repetido en otro envoltorio.

Todos son autocontenidos: llevan Python, Qt, numpy, PortAudio, libsndfile y el motor C++20. En Linux requieren
glibc ≥ 2.34 (la del sistema de compilación, Enterprise Linux 9).

---

## Reglas de Empaquetado y Arquitectura

### 1. Permisos de Micrófono en macOS
- **Obligatoriedad:** Sin `NSMicrophoneUsageDescription` en el `Info.plist` del bundle `.app`, macOS bloquea el micrófono silenciosamente devolviendo únicamente silencio digital (muestras en cero).
- **Inyección:** `adv-tg.spec` lee e inyecta `packaging/macos/Info.plist` en la estructura `BUNDLE`, sincronizando además `CFBundleShortVersionString`, `CFBundleVersion` y `CFBundleIconFile="adv-tg.icns"`.
- **Firma:** Se firma ad-hoc (manejado por PyInstaller), lo cual permite que macOS muestre el prompt de solicitud de permisos al usuario.

### 2. Procesos Hijo Multiprocessing (`freeze_support`)
- La captura de audio (`adv_tg.audio.capture_process`) y el análisis DSP (`adv_tg.audio.analysis_process`) se ejecutan en procesos separados utilizando el método `spawn`.
- En binarios congelados por PyInstaller, cada proceso hijo relanza el ejecutable base. Para evitar bucles infinitos de apertura de ventanas, **`multiprocessing.freeze_support()`** se ejecuta al principio de `packaging/launcher.py` y en `adv_tg.main.main`.
- Los módulos dinámicos requeridos por los procesos hijo están declarados en `hiddenimports` en `adv-tg.spec`.

### 3. Rutas de Lectura de Archivos de Datos (`sys._MEIPASS`)
- Los assets de UI (`adv_tg/ui/assets/*`) y la base de datos de calibres (`adv_tg/utils/db/watch_db.csv`) se empaquetan en el binario.
- Los módulos de la aplicación (`src/adv_tg/ui/main_window.py` y `src/adv_tg/utils/watch_db.py`) resuelven sus rutas verificando `getattr(sys, "frozen", False)` y consultando `sys._MEIPASS` (y en macOS, el directorio `Contents/Resources` del `.app`). En modo desarrollo o pip, utilizan `__file__`.

### 4. Rutas de Escritura y Registros (Logs)
El paquete puede estar instalado en directorios de solo lectura (como `/Applications` o `C:\Program Files`). Por ello, nada se escribe junto al ejecutable:
- **macOS:** `~/Library/Logs/adv_tg`
- **Windows:** `%LOCALAPPDATA%\adv_tg\logs` (ej. `C:\Users\<Usuario>\AppData\Local\adv_tg\logs`)
- **Linux:** `~/.local/state/adv_tg/logs` (respetando `$XDG_STATE_HOME`)
- **Configuración y calibración:** Gestionado por `adv_tg.utils.adc_calibration.config_dir` (`~/Library/Application Support/adv_tg`, `%APPDATA%\adv_tg`, `~/.config/adv_tg`).

### 5. Dependencias Nativas en Linux
- **PortAudio va dentro del paquete.** `adv-tg.spec` empaqueta `libportaudio.so.2` del sistema de
  compilación y `rthook_portaudio.py` hace que `sounddevice` la cargue: `sounddevice` busca
  PortAudio con `ctypes.util.find_library`, que en Linux solo mira el sistema, así que sin el
  hook la librería empaquetada no se usaba y la aplicación arrancaba sin entrada de audio. El
  smoke test falla ahora si PortAudio no carga en un binario empaquetado.
- **Librerías que se toman del sistema** (sección 5 de `adv-tg.spec`): glibc, libstdc++/libgcc,
  ALSA, glib, fontconfig/freetype, X11/xcb, Wayland y OpenGL/EGL. Empaquetar copias antiguas
  rompe los controladores gráficos del sistema, los plugins de ALSA (PipeWire/PulseAudio) o el
  tema GTK de Qt. El rpm y el deb las declaran como dependencias; el AppImage cuenta con ellas,
  como cualquier AppImage.
- **Sistema de compilación:** Enterprise Linux 9. Un bundle hecho en una distribución más nueva
  no arranca en las anteriores (glibc).

### 6. Iconos Multiplataforma
- `adv-tg.icns`: Creado a partir del vector `adv-tg.svg` y vinculado tanto a `BUNDLE(icon=...)` como a `EXE(icon=...)` en macOS.
- `adv-tg.ico`: Generado con tamaños estándar (16, 24, 32, 48, 64, 128, 256) y vinculado a `EXE(icon=...)` para Windows.
- `adv-tg.svg`: Icono vectorial escalable instalado en `/usr/share/icons/hicolor/scalable/apps/adv-tg.svg` y `/usr/share/pixmaps/adv-tg.svg` en Linux.

### 7. Paquetes de Linux (rpm, deb, AppImage)
- **Estructura instalada (rpm y deb):** `/opt/adv-tg/` (bundle), `/usr/bin/adv-tg` (enlace),
  `/usr/share/applications/adv-tg.desktop` e icono en `hicolor/scalable/apps` y `pixmaps`.
- **Generación** (a partir de `dist/adv-tg`, en un sistema con glibc antigua; ver punto 5):
  ```bash
  ./packaging/linux/build_rpm.sh        # necesita rpmbuild
  ./packaging/linux/build_deb.sh        # necesita dpkg-deb
  ./packaging/linux/build_appimage.sh   # descarga appimagetool (versión fijada)
  ```
- El RPM se construye sin enlaces `/usr/lib/.build-id` (chocaban con los paquetes del sistema
  que traen las mismas librerías) y sin etiqueta de distribución.

### 8. Instalador de Windows (Inno Setup) y MSIX (Microsoft Store)
- **Inno Setup (GitHub Releases):** `packaging\windows\installer.iss` empaqueta `dist\adv-tg` completo:
  `iscc /DMyAppVersion=<versión> packaging\windows\installer.iss` genera
  `dist\adv-tg-<versión>-windows-x64-setup.exe`.
  Asistente en inglés y español; instala para el usuario actual sin permisos de administrador
  (o en `Program Files` si se elige "para todos los usuarios"), con acceso en el menú Inicio y
  desinstalador. Una versión nueva se instala sobre la anterior. **No cambiar nunca el
  `AppId`** de `installer.iss`. Instalación desatendida: `/VERYSILENT /NORESTART`.
- **MSIX (Microsoft Store):** `packaging\windows\build_msix.ps1` empaqueta el mismo `dist\adv-tg`
  en un `.msix` **sin firmar** (`dist\adv-tg-<versión>-windows-x64.msix`), subido en GitHub
  Actions como artefacto `store-windows-msix` (separado de `release-*` porque un `.msix` sin
  firmar está pensado para que Microsoft Store lo firme al certificarlo).
  Lleva configurada por defecto la identidad de Partner Center (`AlbertoPico.AdvancedTimegrapher`,
  Store ID `9NNSNDLJ3HV9`; ver `packaging/windows/MSIX_STORE_GUIDE.md`).
  En tiempo de ejecución, `adv_tg.utils.msix.is_msix_packaged()` detecta la identidad MSIX para
  ocultar el botón externo de donación (política 10.8), desactivar el aviso de GitHub Releases
  (política 10.2.2) y abrir las rutas físicas `LocalCache` en el Explorador de Windows.

### 9. Aviso de versión nueva y certificados TLS
- `adv_tg.utils.update_check` consulta la API pública de GitHub (releases del repositorio) y la
  aplicación solo avisa y enlaza a la página del release; no descarga ni instala nada. Los
  releases marcados como *pre-release* o borrador se ignoran.
- El OpenSSL de un binario empaquetado busca los certificados raíz donde los guarda el sistema
  de *compilación* (el bundle de Linux se construye en AlmaLinux y se ejecuta en Debian), así que
  se usa `certifi`, cuyo `cacert.pem` se incluye en el bundle (`adv-tg.spec`). El smoke test de
  un binario empaquetado falla si no se carga ningún certificado.
- `ADV_TG_NO_UPDATE_CHECK=1` desactiva la consulta automática (instalaciones gestionadas).

---

## Verificación y Smoke Test

Para asegurar que un binario construido funciona correctamente sin colgarse en el bucle de eventos de la GUI (`app.exec()`), el ejecutable soporta los siguientes argumentos de línea de comandos:

- `./adv-tg --version`: Imprime la versión empaquetada y termina con código 0.
- `./adv-tg --smoke-test` (o con la variable `ADV_TG_SMOKE_TEST=1`): Inicializa el framework Qt, comprueba la carga de traducciones, valida la base de datos de calibres, inicializa la ventana principal y los motores de audio/DSP, y sale de inmediato con código de retorno `0`.
- En entornos CI/Headless, puede ejecutarse con `QT_QPA_PLATFORM=offscreen` (o `xvfb-run` en Linux).

---

## Construcción Local

Para compilar el binario localmente:

```bash
# 1. Asegúrate de tener las dependencias instaladas
pip install pyinstaller
pip install -e .

# 2. Compilar con PyInstaller
pyinstaller packaging/adv-tg.spec --noconfirm

# 3. Verificar el artefacto resultante
# En Linux / Windows:
./dist/adv-tg/adv-tg --smoke-test
# En macOS:
"./dist/Advanced Timegrapher.app/Contents/MacOS/adv-tg" --smoke-test
```
