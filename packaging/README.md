# Empaquetado (beta)

Punto de partida para construir los binarios de la beta (macOS `.app`/`.dmg`, Windows `.zip`,
Linux AppImage o `.tar.gz`) y publicarlos en GitHub Releases. **El `.spec` no se ha probado
todavía.** Decisiones del proyecto: los tres sistemas; publicación en GitHub Releases desde
GitHub Actions al crear una etiqueta (`v0.6.0-beta.1`); sin firma de código en la beta.

| Archivo | Para qué |
|---|---|
| `launcher.py` | Script de entrada: llama a `multiprocessing.freeze_support()` antes de importar nada |
| `adv-tg.spec` | Spec de PyInstaller (carpeta única; `.app` en macOS con el `Info.plist`) |
| `macos/Info.plist` | Claves del `Info.plist`, incluida **`NSMicrophoneUsageDescription`** |

## Lo que hay que respetar

1. **Micrófono en macOS.** Sin `NSMicrophoneUsageDescription` en el `Info.plist` del `.app`,
   macOS deniega el micrófono sin avisar y la aplicación solo recibe silencio digital. La
   aplicación detecta ese silencio y explica dónde dar el permiso, pero la clave es obligatoria.
   PyInstaller firma el `.app` ad hoc; debería bastar para que macOS muestre el diálogo de
   permiso (sin verificar: comprobarlo en un Mac que no haya dado nunca el permiso).
2. **Procesos hijo ("spawn").** La captura (`adv_tg.audio.capture_process`) y el análisis
   (`adv_tg.audio.analysis_process`) corren en procesos aparte. Con el binario congelado, cada
   hijo vuelve a ejecutar el script de entrada: por eso `freeze_support()` va primero en
   `launcher.py` (y también en `adv_tg.main.main`). Comprobar en el binario que el log
   `adv_tg_capture.log` y `adv_tg_analysis.log` aparecen al medir.
3. **Archivos de datos.** `adv_tg/ui/assets/*` (iconos SVG) y `adv_tg/utils/db/watch_db.csv`
   (calibres) van dentro del paquete (`collect_data_files`).
4. **Carpetas escribibles.** El paquete puede ser de solo lectura: los registros van a
   `LoggerSetup.LOG_DIR` (`~/Library/Logs/adv_tg`, `%LOCALAPPDATA%\adv_tg\logs`,
   `~/.local/state/adv_tg/logs`) y los ajustes y calibraciones a `config_dir()`
   (`~/Library/Application Support/adv_tg`, `%APPDATA%\adv_tg`, `~/.config/adv_tg`). Nada se
   escribe junto al ejecutable.
5. **Bibliotecas nativas.**
   - `sounddevice` trae PortAudio en sus ruedas de macOS y Windows; **en Linux no**: el
     AppImage debe incluir `libportaudio.so.2` o el README debe pedir `libportaudio2`.
   - `soundfile` trae libsndfile en sus ruedas (también en Linux).
   - PyQt6 + pyqtgraph: los hooks de `pyinstaller-hooks-contrib` cubren Qt; comprobar el
     plugin de plataforma (`cocoa`, `windows`, `xcb`) y el de iconos SVG (`qsvg`).
   - Windows: `adv_tg/__init__.py` aísla las DLL de Qt del paquete frente a otras instalaciones
     (MiKTeX, Anaconda); funciona con PyInstaller.
6. **Iconos.** Solo hay `adv-tg.svg` y `adv-tg.jpg` en `src/adv_tg/ui/assets/`: hay que generar
   `adv-tg.icns` (macOS) y `adv-tg.ico` (Windows) y pasarlos a `EXE(icon=...)`/`BUNDLE(icon=...)`.
7. **Sin firma.** Las instrucciones para abrir la aplicación están en el `README.md` de la raíz
   (inglés y español).

## Prueba mínima de cada binario

1. Abre y muestra la ventana; **Acerca de** enseña la versión correcta.
2. Con un micrófono: **INICIAR** → la barra de calidad muestra nivel y, con un reloj, aparecen
   cifras; en macOS se pide el permiso del micrófono la primera vez.
3. Sin micrófono: `ADV_TG_REPLAY_FILE=<toma.flac>` reproduce una grabación (por ejemplo
   `docs/samples/miyota_9015.flac`, 28800 A/h, 51°).
4. **Reportar** crea el `.zip` con los registros.
