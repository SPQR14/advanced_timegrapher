# Empaquetado y Distribución Multiplataforma (Beta)

Este directorio contiene las especificaciones y utilidades para construir los binarios de la beta (macOS `.app`/`.zip`, Windows `.zip`, Linux `.tar.gz`) y distribuirlos a través de GitHub Releases y GitHub Actions al crear una etiqueta (`v0.6.0-beta.1` o similar).

| Archivo | Para qué |
|---|---|
| `launcher.py` | Script de entrada: invoca `multiprocessing.freeze_support()` antes de cualquier importación pesada |
| `adv-tg.spec` | Especificación de PyInstaller validada (carpeta única; `.app` en macOS con inyección de `Info.plist` e icono) |
| `macos/Info.plist` | Configuración del bundle de macOS, incluyendo **`NSMicrophoneUsageDescription`** e icono |
| `macos/adv-tg.icns` | Icono multi-resolución de macOS (16x16 hasta 1024x1024) |
| `windows/adv-tg.ico` | Icono estándar para ejecutables de Windows |

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
