# Empaquetado y Distribución Multiplataforma (Beta)

Este directorio contiene las especificaciones y utilidades para construir los binarios (macOS `.dmg`/`.app`/`.zip`, Windows `.exe` setup/`.zip`, Linux `.tar.gz`/`.rpm`) y distribuirlos a través de GitHub Releases y GitHub Actions al crear una etiqueta (`v0.7.0-beta4` o similar).

| Archivo | Para qué |
|---|---|
| `launcher.py` | Script de entrada: invoca `multiprocessing.freeze_support()` antes de cualquier importación pesada |
| `adv-tg.spec` | Especificación de PyInstaller validada (carpeta única; `.app` en macOS con inyección de `Info.plist` e icono) |
| `linux/adv-tg.desktop` | Entrada de escritorio de Linux (menú de aplicaciones, Wayland app-id) |
| `linux/adv-tg.rpm.spec` | Especificación RPM para distribuciones basadas en RPM (Fedora, RHEL, openSUSE) |
| `linux/build_rpm.sh` | Script de empaquetado RPM a partir del bundle generado por PyInstaller |
| `macos/build_dmg.sh` | Script para generar imagen de disco DMG con enlace de arrastrar y soltar a `/Applications` |
| `macos/Info.plist` | Configuración del bundle de macOS, incluyendo **`NSMicrophoneUsageDescription`** e icono |
| `macos/adv-tg.icns` | Icono multi-resolución de macOS (16x16 hasta 1024x1024) |
| `windows/installer.iss` | Script de Inno Setup para crear el instalador `.exe` con instalación en aplicaciones, accesos directos y desinstalador |
| `windows/adv-tg.ico` | Icono estándar para ejecutables de Windows |

---

## Flujo de Construcción (Python puro)

El proyecto es **Python puro** (numpy, sin SciPy y sin extensiones C/C++), así que no hay
compilación nativa propia: el mismo código se instala con `pip` y se congela con PyInstaller.

```
GitHub Actions (.github/workflows/build.yml):
 1. [python-dist]   ──> python -m build: wheel universal (py3-none-any) + sdist
 2. [build]         ──> pip install . + PyInstaller en Linux, macOS (arm64) y Windows
 3. [Smoke test]    ──> --version y --smoke-test headless: arranca la GUI, comprueba que SciPy
                        no está cargado y mide una muestra (o ticks sintéticos) sin hardware
 4. [Package]       ──> Generación de instaladores nativos:
                        - macOS: .dmg de instalación (drag & drop a /Applications) y .zip
                        - Windows: instalador Inno Setup .exe (instalación en aplicaciones) y .zip
                        - Linux: paquete RPM y .tar.gz
 5. [release]       ──> Solo con etiqueta v*: publica los instaladores, paquetes, wheel y sdist
```

### Tabla de Artefactos de Release

| Artefacto | Plataforma y Arquitectura | Tipo |
|---|---|---|
| `adv-tg-macos-arm64.dmg` | macOS Apple Silicon (M1/M2/M3/M4) | Imagen DMG con arrastre directo a `/Applications` |
| `adv-tg-macos-arm64.zip` | macOS Apple Silicon (M1/M2/M3/M4) | Bundle `.app` con Info.plist de micrófono |
| `adv-tg-windows-x64-setup.exe` | Windows 10/11 x64 | Instalador estándar con accesos directos y desinstalador |
| `adv-tg-windows-x64.zip` | Windows 10/11 x64 | Carpeta standalone comprimida con `adv-tg.exe` |
| `adv-tg-linux-x86_64.tar.gz` | Linux x86_64 | Carpeta standalone con PortAudio integrado |
| `adv-tg-*-1.x86_64.rpm` | Linux x86_64 (Fedora, RHEL, openSUSE) | Paquete RPM instalable vía `dnf` / `zypper` |
| `advanced_timegrapher-*-py3-none-any.whl` | Cualquier plataforma (Python ≥ 3.11) | Wheel puro (sin compilador) |
| `advanced_timegrapher-*.tar.gz` | Código fuente universal | Source distribution (sdist) |

---

## Reglas de Empaquetado y Arquitectura

### 1. Permisos de Micrófono en macOS
- **Obligatoriedad:** Sin `NSMicrophoneUsageDescription` en el `Info.plist` del bundle `.app`, macOS bloquea el micrófono silenciosamente devolviendo únicamente silencio digital (muestras en cero).
- **Inyección:** `adv-tg.spec` lee e inyecta `packaging/macos/Info.plist` en la estructura `BUNDLE`, sincronizando además `CFBundleShortVersionString`, `CFBundleVersion` y `CFBundleIconFile="adv-tg.icns"`.
- **Firma:** En la beta se firma ad-hoc (manejado por PyInstaller), lo cual permite que macOS muestre el prompt de solicitud de permisos al usuario.

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

### 5. Dependencias Nativas y PortAudio en Linux
- **macOS y Windows:** Las ruedas oficiales de `sounddevice` ya incluyen las librerías dinámicas de PortAudio precompiladas.
- **Linux:** `sounddevice` en Linux depende de la librería del sistema `libportaudio.so.2`.
  - **Empaquetado:** `adv-tg.spec` inspecciona automáticamente `ldconfig` y los directorios de librerías del sistema del host de compilación; si encuentra `libportaudio.so.2`, la empaqueta dentro del paquete distribuible.
  - **Requisito de sistema:** Si un sistema no cuenta con la librería empaquetada o prefiere usar la del sistema, se requiere instalar `libportaudio2` (Debian/Ubuntu: `sudo apt install libportaudio2`, Fedora: `sudo dnf install portaudio`, Arch: `sudo pacman -S portaudio`).

### 6. Iconos Multiplataforma
- `adv-tg.icns`: Creado a partir del vector `adv-tg.svg` y vinculado tanto a `BUNDLE(icon=...)` como a `EXE(icon=...)` en macOS.
- `adv-tg.ico`: Generado con tamaños estándar (16, 24, 32, 48, 64, 128, 256) y vinculado a `EXE(icon=...)` para Windows.
- `adv-tg.svg`: Icono vectorial escalable instalado en `/usr/share/icons/hicolor/scalable/apps/adv-tg.svg` y `/usr/share/pixmaps/adv-tg.svg` en Linux.

### 7. Empaquetado RPM para Linux (Fedora, RHEL, openSUSE)
- **Estructura instalada:**
  - Binarios y runtime PyInstaller: `/opt/adv-tg/`
  - Enlace simbólico en el PATH: `/usr/bin/adv-tg` -> `/opt/adv-tg/adv-tg`
  - Acceso directo de escritorio: `/usr/share/applications/adv-tg.desktop`
  - Iconos del sistema: `/usr/share/icons/hicolor/scalable/apps/adv-tg.svg` y `/usr/share/pixmaps/adv-tg.svg`
- **Generación:**
  ```bash
  # Requiere el bundle previo de PyInstaller en dist/adv-tg y la herramienta rpmbuild:
  #   Fedora/RHEL: sudo dnf install rpm-build
  #   Debian/Ubuntu: sudo apt-get install rpm
  ./packaging/linux/build_rpm.sh
  ```
- **Instalación y prueba:**
  ```bash
  sudo dnf install ./dist/adv-tg-*.rpm       # Fedora / RHEL
  sudo zypper install ./dist/adv-tg-*.rpm    # openSUSE
  ```

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
