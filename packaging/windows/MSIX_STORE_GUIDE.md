# Publicación en Microsoft Store (MSIX)

Esta guía resume el proceso para publicar **Advanced Timegrapher** en Microsoft Store usando el paquete `.msix` generado por GitHub Actions (sin necesidad de comprar un certificado de firma externo: Microsoft firma el paquete al aprobar la publicación, eliminando las alertas de SmartScreen).

---

## 1. Identidad registrada en Microsoft Partner Center

El producto **`Advanced Timegrapher`** ya está registrado en Microsoft Partner Center y sus valores públicos de identidad están configurados por defecto en `packaging/windows/build_msix.ps1` y `.github/workflows/build.yml`:

| Campo de Partner Center | Valor configurado |
| --- | --- |
| `Package/Identity/Name` | `AlbertoPico.AdvancedTimegrapher` |
| `Package/Identity/Publisher` | `CN=2B353A7B-439C-4198-A61A-63000DFBF72B` |
| `Package/Properties/PublisherDisplayName` | `Alberto Pico` |
| `Package Family Name (PFN)` | `AlbertoPico.AdvancedTimegrapher_p2ny92apt2ptc` |
| `Store ID` | `9NNSNDLJ3HV9` |

> No hace falta configurar variables ni secretos adicionales en GitHub Actions para compilar el `.msix` de la Store (opcionalmente se pueden sobrescribir con las variables de repositorio `MSIX_IDENTITY_NAME`, `MSIX_PUBLISHER` y `MSIX_PUBLISHER_DISPLAY_NAME`).

---

## 2. Obtener el paquete `.msix` sin firmar

Cada ejecución de `.github/workflows/build.yml` (en `main`, `release/*`, `v*` o `workflow_dispatch`):

1. Compila el bundle PyInstaller (`dist\adv-tg`).
2. Genera y prueba el instalador Inno Setup (`adv-tg-<versión>-windows-x64-setup.exe`, que sigue publicándose en GitHub Releases).
3. Genera el paquete `.msix` **sin firmar** (`adv-tg-<versión>-windows-x64.msix`), verifica su instalación real en Windows (`Add-AppxPackage` + `adv-tg.exe --smoke-test` con identidad MSIX) y lo sube como artefacto de workflow llamado **`store-windows-msix`**.

Descarga `store-windows-msix` desde la ejecución de Actions en GitHub y sube ese `.msix` directamente a Partner Center.

---

## 3. Comportamiento específico cuando corre como MSIX

La aplicación detecta en tiempo de ejecución si corre dentro de un paquete MSIX (`adv_tg.utils.msix.is_msix_packaged()`, mediante `kernel32.GetCurrentPackageFullName`) y aplica automáticamente estos ajustes para cumplir con las políticas de Microsoft Store (sin alterar Linux, macOS ni `setup.exe`):

- **Política 10.2.2 (actualizaciones gestionadas por la Store):** se desactiva la consulta automática y manual a GitHub Releases; Microsoft Store entrega las actualizaciones.
- **Política 10.8 (transacciones/donaciones externas):** se oculta el botón externo *"Buy me a coffee"* del diálogo *Acerca de*.
- **Carpetas virtualizadas (`LocalCache`):** *"Abrir carpeta de registros"* y *"Abrir carpeta de calibración"* resuelven la ruta física `%LOCALAPPDATA%\Packages\AlbertoPico.AdvancedTimegrapher_p2ny92apt2ptc\LocalCache\{Local,Roaming}\adv_tg` para que el Explorador de Windows abra los archivos reales.

---

## 4. Notas recomendadas para el formulario de Partner Center

### Capacidades restringidas (`runFullTrust`) y `microphone`
En **Submission options → Notes for certification**, incluye una explicación clara en inglés:

> *Advanced Timegrapher is an open-source (GPL-3.0-or-later) Win32 desktop acoustic timegrapher for mechanical and quartz watches, packaged with PyInstaller (Python + PyQt6 + PortAudio).*
> - **`runFullTrust`**: Required because the application is a native Win32 desktop executable that spawns isolated local worker processes (`capture_process` and `analysis_process`) for real-time audio acquisition and DSP analysis, and reads/writes user-chosen WAV/FLAC recordings and exported reports.
> - **`microphone`**: Required to capture the acoustic ticking sound of a watch (or an external GPS PPS reference buzzer for sound-card clock calibration) through the user's selected microphone/audio interface. Audio is processed 100% locally and never transmitted over the network.
> - **Testing note**: No physical watch is required to test the UI; the built-in *Simulated (MOCK)* input source or the bundled sample audio files allow verifying measurement and UI functionality immediately.

### Cumplimiento de licencia GPL-3.0-or-later
- En la descripción de la ficha de Store y en **Support / Website**, enlaza a <https://github.com/SPQR14/advanced_timegrapher> indicando que el código fuente completo está disponible bajo licencia **GPL-3.0-or-later**.
- El icono de 300×300 px para la ficha de Partner Center está listo en `packaging/windows/msix/StoreListing/AppIcon-300x300.png`.
