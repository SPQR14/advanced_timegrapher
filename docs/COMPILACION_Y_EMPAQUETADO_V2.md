# Advanced Timegrapher v2.0 — Arquitectura C++20 SIMD y Guía de Compilación / Empaquetado

Advanced Timegrapher v2.0 migra el núcleo crítico de procesamiento, análisis estadístico, captura de audio y rasterización de gráficos a una biblioteca nativa compartida en **C++20 (`advtg_native`)**, cargada en tiempo de ejecución mediante `ctypes` desde `adv_tg.native_bridge`. Si la biblioteca no está presente o se define `ADV_TG_DISABLE_NATIVE=1`, la aplicación conserva el respaldo completo en Python/NumPy puro (sin SciPy).

---

## 1. Arquitectura Nativa v2.0 (C++20, Bit-Level y SIMD Runtime Dispatch)

### 1.1 Módulos migrados a C++20 (`native/`)
| Subsistema | Archivos C++20 | Módulo Python enlazado | Responsabilidad clave |
|---|---|---|---|
| **Base SIMD y Bits** | `advtg_bits.hpp`, `advtg_simd.hpp`, `simd_dispatch.cpp`, `simd_level_*.cpp`, `simd_kernels.inl` | `adv_tg.native_bridge` | Detección CPUID + XGETBV (`generic`, `sse42`, `avx2`, `avx512`, `neon`), buffers alineados a 64 B, relleno de línea de caché de 128 B, biyección orden-preservante IEEE-754 `binary64 <-> uint64` y mediana radix exacta |
| **Motor DSP e IIR** | `fft.cpp`, `dsp_engine.cpp`, `onset.cpp`, `interference.cpp`, `normalizer.cpp`, `c_api_dsp.cpp` | `adv_tg.dsp.{engine,onset,interference,normalizer}` | RFFT/IRFFT Radix-2 Stockham, `sosfilt` / `sosfiltfilt`, convolución Hilbert SIMD, detección de candidatos de celda, filtro Notch adaptativo de interferencia y normalizador con headroom constante |
| **Detector de Batimientos y PPS** | `beat_detector.cpp`, `pps.cpp`, `c_api_detector.cpp` | `adv_tg.core.{beat_detector,pps}` | Detector incremental de tic/tac, bloqueo de fase, plantillas por paridad y detector acústico GPS PPS |
| **Métricas, Reloj ADC y Lomb-Scargle** | `metrics.cpp`, `adc_clock.cpp`, `gear_train.cpp`, `c_api_metrics.cpp` | `adv_tg.core.{metrics,adc_clock}`, `adv_tg.analysis.gear_train` | Regresión robusta ponderada por paridad (lineal y cuadrática), jackknife por bloques, envolvente inferior Theil-Sen, Teager-Kaiser y periodograma Lomb-Scargle multihilo con SIMD |
| **Ring Buffer Lock-Free y Captura sin GIL** | `sample_stream.cpp`, `audio_capture.cpp`, `c_api_runtime.cpp` | `adv_tg.core.sample_stream`, `adv_tg.audio.{capture,channel_select}`, `adv_tg.mock.generator` | Ring buffer SPSC lock-free con atómicos separados cada 128 B (`alignas(128)`), callback C puro sobre PortAudio cargada dinámicamente (`dlopen`/`LoadLibraryW`) sin adquisición de GIL, selector de canal y decimación min/max |
| **Rasterizador Gráfico Multihilo** | `graph_renderer.cpp`, `sample_stream.cpp` (`smooth_median_f64`) | `adv_tg.ui.{native_plots,main_window}` | Dos hilos de CPU dedicados (`RenderWorker-Tape` y `RenderWorker-WaveOsc`), triple buffer `ARGB32_Premultiplied` alineado a 64 B, mezcla alfa SWAR por píxel y red de ordenamiento sin saltos de 5 elementos |

### 1.1b Sesión de medición nativa y API C pública (base para móvil)
- `native/src/session.cpp` + `filters.cpp` portan `core/session.py` (detector + métricas en ventana + reloj del ADC en vivo + medianas mostradas + cinta + osciloscopio) y el diseño Butterworth: la medición completa corre en C++ sin Python.
- API **C99** pública en `native/include/advtg.h` (`advtg_session_*`, `advtg_stream_*`), pensada para JNI (Android) y Swift (iOS). `MeasurementSession` usa esta sesión nativa por defecto; `PyMeasurementSession` queda como referencia y respaldo.
- Paridad verificada **bit a bit** (tolerancia 0) contra la sesión Python en `tests/test_native_session.py`.
- Guía de integración móvil y opciones de CMake (`ADVTG_WITH_AUDIO_CAPTURE`, `ADVTG_STATIC`, `ADVTG_BUILD_EXAMPLES`): [`MOTOR_NATIVO_MOVIL.md`](MOTOR_NATIVO_MOVIL.md).

### 1.2 Política de Precisión Numérica IEEE-754
- **Nunca `-ffast-math`**: `NaN` actúa como centinela físico de sub-evento no resuelto y las sumas compensadas de Neumaier requieren orden estricto IEEE-754.
- **GCC / Clang**: `-std=c++20 -O3 -fno-fast-math -fno-math-errno -fno-trapping-math -ffp-contract=fast -funroll-loops` + LTO (`INTERPROCEDURAL_OPTIMIZATION ON`).
- **MSVC (Windows x64)**: `/O2 /Ob3 /Oi /Ot /Gw /Gy /fp:precise /GL /LTCG` (precisión estricta con contracción FMA donde aplique).
- **Dispatch seguro en un solo binario (`--target x86_64_universal`)**: el núcleo compila en `-march=x86-64-v2`, mientras `simd_level_sse42.cpp`, `simd_level_avx2.cpp` y `simd_level_avx512.cpp` se compilan por unidad de traducción con sus flags ISA propios y se seleccionan dinámicamente en runtime previa verificación de bits OSXSAVE/ZMM (`XGETBV`). Se puede forzar un nivel específico en pruebas con `ADVTG_SIMD=generic|sse42|avx2|avx512|neon`.

---

## 2. Resultados Comparativos de Rendimiento (`docs/samples/*.flac`)

Medido en x86_64 con dispatch activo `[avx512] x86_64 SSE4.2 AVX2 FMA BMI2 AVX512F+DQ+BW+VL` comparando el motor C++20 v2.0.0 frente al mismo código con `ADV_TG_DISABLE_NATIVE=1` (respaldo Python/NumPy v1.1.1) usando chunks de 4096 muestras + cálculo completo de métricas:

- **Periodograma Lomb-Scargle (`720 x 2000`)**: Python `22.12 ms` -> **C++20 `0.58 ms`** (**`37.8x`** más rápido).
- **Rasterizador Gráfico CPU Multihilo (`NativeGraphRenderer`, 2 hilos, Paper Tape 1200×400 + Osciloscopio 600×300 + Forma de Onda 600×300 simultáneos)**: **`6067.7 frames compuestos/s`** (`0.16 ms/frame`).

| Grabación FLAC | Tipo | Duración (s) | Python v1.1 (s) | C++20 v2.0 (s) | Aceleración | Factor Tiempo Real (C++20) | Pulsos |
|---|---|---:|---:|---:|---:|---:|---:|
| `4r36.flac` | Mecánico | 30.0 | 0.365 | 0.183 | **2.0x** | **164.4x** | 181 |
| `PPS_48k_Fedora45.flac` | GPS PPS | 461.1 | 0.636 | 0.199 | **3.2x** | **2322.3x** | 461 |
| `SeagullST1901.flac` | Mecánico | 30.0 | 0.314 | 0.139 | **2.3x** | **215.6x** | 180 |
| `boluva_nm10_new.flac` | Cuarzo | 300.0 | 3.921 | 1.707 | **2.3x** | **175.8x** | 4729 |
| `bulova_nm10.flac` | Cuarzo | 300.0 | 3.892 | 1.628 | **2.4x** | **184.3x** | 4798 |
| `citizen_H874.flac` | Cuarzo | 300.0 | 1.912 | 1.186 | **1.6x** | **252.9x** | 300 |
| `hamilton_h50.flac` | Mecánico (Notch activo) | 30.5 | 0.370 | 0.394 | **0.9x** | **77.4x** | 183 |
| `hamilton_h50new.flac` | Mecánico (Notch activo) | 30.0 | 0.342 | 0.388 | **0.9x** | **77.3x** | 180 |
| `miyota_9015.flac` | Mecánico | 30.6 | 0.327 | 0.134 | **2.4x** | **228.1x** | 245 |
| `nh34.flac` | Mecánico | 129.7 | 1.242 | 0.554 | **2.2x** | **234.0x** | 778 |
| `pps_44100Hz.flac` | GPS PPS | 224.6 | 0.305 | 0.091 | **3.4x** | **2468.0x** | 224 |
| `pps_48000Hz.flac` | GPS PPS | 150.0 | 0.219 | 0.067 | **3.2x** | **2222.9x** | 150 |
| `tmi_vh31.flac` | Cuarzo | 300.0 | 2.911 | 1.377 | **2.1x** | **217.9x** | 1200 |
| **TOTAL (13 grabaciones)** | — | **2316.7** | **16.756** | **8.048** | **2.1x** | **287.8x** | — |

---

## 3. Instrucciones de Compilación Nativa y Empaquetado por Plataforma

El script `scripts/build_native.py` automatiza CMake + Ninja/MSVC (con respaldo directo `--direct` para `g++`/`clang++`) y deposita automáticamente el binario compilado en `src/adv_tg/_native/`, donde es detectado tanto en desarrollo como al congelar con `packaging/adv-tg.spec`.

Targets disponibles en `--target`:
`auto` | `native` | `zen5` | `x86_64_universal` | `apple_silicon` | `rpi4` | `rpi5` | `arm64_universal`

---

### 3.1 Raspberry Pi 4 / 5 (`ARM64 / aarch64` Linux)

Compila NEON nativo ajustado específicamente a Cortex-A72 (`rpi4`) o Cortex-A76 (`rpi5`), o genérico `-march=armv8-a` (`arm64_universal`) para distribuir `.deb` / `.rpm` / `.AppImage` compatibles entre ambas placas.

#### Dependencias del sistema (Raspberry Pi OS Bookworm / Debian 12+ / Ubuntu ARM64)
```bash
sudo apt-get update
sudo apt-get install -y \
  git cmake ninja-build g++ python3-venv python3-dev \
  libportaudio2 portaudio19-dev libasound2-dev \
  libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
  libxcb-keysyms1 libxcb-shape0 dpkg-dev rpm desktop-file-utils
```

#### Compilar el motor C++20 y verificar
```bash
cd ~/advanced_timegrapher
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller ".[dev]"

# Para Raspberry Pi 5 (Cortex-A76 + NEON):
python scripts/build_native.py --clean --target rpi5
# (Para Raspberry Pi 4 usar: --target rpi4 ; para binario ARM64 portátil: --target arm64_universal)

python -c "from adv_tg import native_bridge; print(native_bridge.native_arch_info())"
QT_QPA_PLATFORM=offscreen pytest -q
```

#### Empaquetar binarios ARM64 en Raspberry Pi
```bash
pyinstaller packaging/adv-tg.spec --noconfirm
QT_QPA_PLATFORM=offscreen ./dist/adv-tg/adv-tg --version
QT_QPA_PLATFORM=offscreen ./dist/adv-tg/adv-tg --smoke-test

# Construir instaladores Linux ARM64 (.deb y/o .AppImage):
./packaging/linux/build_deb.sh 2.0.0          # -> dist/adv-tg_2.0.0_arm64.deb
./packaging/linux/build_appimage.sh 2.0.0     # -> dist/adv-tg-2.0.0-linux-aarch64.AppImage
```

---

### 3.2 Mac con Apple Silicon (`M1 / M2 / M3 / M4` — `arm64` macOS)

Compila con Apple Clang apuntando a `-mcpu=apple-m1` con NEON y LTO, conservando compatibilidad en toda la familia Apple Silicon M1–M4 y líneas de caché de 128 B (`kCacheLine = 128`).

#### Dependencias en macOS
```bash
xcode-select --install
brew install cmake ninja portaudio python@3.12 create-dmg
```

#### Compilar el motor C++20 y verificar
```bash
cd ~/advanced_timegrapher
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller ".[dev]"

python scripts/build_native.py --clean --target apple_silicon
python -c "from adv_tg import native_bridge; print(native_bridge.native_arch_info())"
QT_QPA_PLATFORM=offscreen pytest -q
```

#### Empaquetar `.app` y `.dmg` en Apple Silicon
```bash
pyinstaller packaging/adv-tg.spec --noconfirm
QT_QPA_PLATFORM=offscreen "./dist/Advanced Timegrapher.app/Contents/MacOS/adv-tg" --version
QT_QPA_PLATFORM=offscreen "./dist/Advanced Timegrapher.app/Contents/MacOS/adv-tg" --smoke-test

./packaging/macos/build_dmg.sh \
  "dist/Advanced Timegrapher.app" \
  "dist/adv-tg-2.0.0-macos-arm64.dmg"
```

---

### 3.3 Beelink SER9 Dual-Boot — Partición Linux (`x86_64` AMD Zen 5 / Intel)

El Beelink SER9 (AMD Ryzen AI 9 HX 370 / arquitectura **Zen 5**) soporta AVX-512 completo (F, DQ, BW, VL, FMA, BMI2) en ciclo único sin penalización de frecuencia:
- Usa `--target zen5` (o `--target native` con GCC/Clang) para exprimir `-march=znver5 -mtune=znver5` en tu instalación local. Con MSVC solo `zen5` activa `/arch:AVX512` en todo el núcleo; `native` usa el núcleo portable con selección en ejecución.
- Usa `--target x86_64_universal` si vas a redistribuir el `.rpm`, `.deb` o `.AppImage` a otros equipos AMD/Intel sin AVX-512 (el binario selecciona AVX-512, AVX2+FMA, SSE4.2 o genérico en runtime por CPUID+XGETBV sin provocar `SIGILL`).

#### Dependencias en Linux SER9 (Fedora / RHEL / AlmaLinux o Ubuntu / Debian)
*Fedora:*
```bash
sudo dnf install -y \
  gcc-c++ cmake ninja-build git python3.12 python3.12-devel \
  portaudio-devel alsa-lib-devel mesa-libGL mesa-libEGL \
  rpm-build dpkg desktop-file-utils
```
*Ubuntu / Debian:*
```bash
sudo apt-get update
sudo apt-get install -y \
  g++ cmake ninja-build git python3.12-venv python3.12-dev \
  libportaudio2 portaudio19-dev libasound2-dev libgl1 libegl1 \
  libxkbcommon-x11-0 libxcb-cursor0 dpkg-dev rpm desktop-file-utils
```

#### Compilar, probar y empaquetar en Linux SER9
```bash
cd ~/Desarrollo/advanced_timegrapher
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller ".[dev]"

# Máximo rendimiento local en AMD Zen 5 (SER9):
python scripts/build_native.py --clean --target zen5
# (Para paquete distribuible a CPUs sin AVX-512: --target x86_64_universal)

python -c "from adv_tg import native_bridge; print(native_bridge.native_arch_info())"
QT_QPA_PLATFORM=offscreen pytest -q

pyinstaller packaging/adv-tg.spec --noconfirm
QT_QPA_PLATFORM=offscreen ./dist/adv-tg/adv-tg --version
QT_QPA_PLATFORM=offscreen ./dist/adv-tg/adv-tg --smoke-test

# Empaquetado nativo Fedora / Debian / AppImage:
./packaging/linux/build_rpm.sh 2.0.0          # -> dist/adv-tg-2.0.0-1.x86_64.rpm
./packaging/linux/build_deb.sh 2.0.0          # -> dist/adv-tg_2.0.0_amd64.deb
./packaging/linux/build_appimage.sh 2.0.0     # -> dist/adv-tg-2.0.0-linux-x86_64.AppImage
```

---

### 3.4 Beelink SER9 Dual-Boot — Partición Windows 11 (`x64` MSVC)

#### Prerrequisitos en Windows 11
1. Instalar **Visual Studio 2022 Build Tools** (o VS 2022 Community) marcando la carga **Desarrollo para el escritorio con C++** (incluye compilador `cl.exe` x64 y `CMake`).
2. Instalar **Python 3.12** (x64) y **Git**.
3. Opcional para instalador/Store: **Inno Setup 6** (`choco install innosetup -y`) y **Windows SDK** (`makeappx.exe` para MSIX).

#### Compilar en PowerShell (x64 Native Tools Command Prompt / Developer PowerShell)
```powershell
cd C:\Ruta\a\advanced_timegrapher
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install pyinstaller ".[dev]"

# Compilación máxima precisión (/fp:precise) + AVX-512 para SER9:
python scripts\build_native.py --clean --target zen5
# (Para distribuir instalador a cualquier PC Windows x64 Intel/AMD usar: --target x86_64_universal)

python -c "from adv_tg import native_bridge; print(native_bridge.native_arch_info())"
$env:QT_QPA_PLATFORM = "offscreen"
pytest -q
```

#### Empaquetar `.exe`, Instalador Inno Setup y `.msix`
```powershell
pyinstaller packaging\adv-tg.spec --noconfirm

# Smoke test del binario congelado:
$p = Start-Process -FilePath "dist\adv-tg\adv-tg.exe" -ArgumentList "--smoke-test" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Smoke test falló ($($p.ExitCode))" }

# 1) Instalador Windows (setup.exe):
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DMyAppVersion=2.0.0 packaging\windows\installer.iss
# Resultado: dist\adv-tg-2.0.0-windows-x64-setup.exe

# 2) Paquete Microsoft Store (.msix sin firmar, listo para Partner Center):
& packaging\windows\build_msix.ps1 `
  -Version 2.0.0 `
  -IdentityName "AlbertoPico.AdvancedTimegrapher" `
  -Publisher "CN=2B353A7B-439C-4198-A61A-63000DFBF72B" `
  -PublisherDisplayName "Alberto Pico" `
  -Verify
# Resultado: dist\adv-tg-2.0.0-windows-x64.msix
```
