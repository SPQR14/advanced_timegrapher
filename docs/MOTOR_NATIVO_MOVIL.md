# Motor nativo para aplicaciones móviles (Android / iOS)

Desde v2.0 la medición completa de Advanced Timegrapher corre en C++20 sin Python:
detector de batimientos, métricas en ventana deslizante, deriva del reloj del ADC en vivo,
medianas de las cifras mostradas, cinta de papel y forma de onda del osciloscopio. Todo se
expone con una API **C99 pura** en [`native/include/advtg.h`](../native/include/advtg.h),
consumible desde JNI (Android), Swift/Objective-C (iOS) o cualquier FFI.

La aplicación de escritorio usa esa misma sesión nativa (`MeasurementSession` →
`NativeMeasurementSession`), así que el motor que se distribuye en móvil es el que se prueba a
diario en escritorio.

## 1. Qué está verificado

| Verificación | Resultado |
|---|---|
| Sesión C++ vs sesión Python (`PyMeasurementSession`), snapshot a snapshot, **tolerancia 0** | 0 diferencias en ~1400 actualizaciones sobre 9 tomas reales (mecánicos, Notch activo, cuarzo 1/4/16 Hz) y 3 sintéticas — `tests/test_native_session.py` |
| Diseño Butterworth C++ vs port numpy de SciPy | ≤ 3·10⁻¹⁶ relativo (1–2 ulp); Python usa el diseño nativo cuando está cargado |
| Ejemplo C99 (`native/examples/advtg_replay.c`) enlazado a la librería **estática sin PortAudio ni rasterizador** | Mismas cifras que Python (Miyota 9015: +8.274 s/d, BE 0.041 ms, 300.0°, 241 latidos) |
| `advtg.h` compilado como C99 `-pedantic` | Sin avisos; tamaños de struct = ctypes = C++ (`static_assert` de offsets en `c_api_session.cpp`) |

**Pendiente de verificar (sin toolchain en la máquina de desarrollo):** compilación con
Android NDK y con el SDK de iOS, y la ruta SIMD NEON (`simd_level_neon.cpp`) en hardware
ARM64 real. El código no usa nada específico de escritorio fuera de `audio_capture.cpp`
(PortAudio vía `dlopen`), que queda excluido en móvil.

## 2. Modelo de uso

```
hilo de audio (Oboe / AVAudioEngine)          hilo de trabajo (4 veces/s)
  └─ advtg_stream_write_f32(stream, ...)  ─►   advtg_session_update(s, &snap)
        ring buffer SPSC sin bloqueos            snap.display.rate_s_per_day ...
                                                 advtg_session_tape(...), _waveform(...)
```

```c
advtg_session_config cfg;
advtg_session_config_default(&cfg, 48000.0, 28800.0);  // fs, BPH
cfg.lift_angle_deg = 52.0;                             // NaN = desconocido (amplitud orientativa)
advtg_session* s = advtg_session_create(&cfg, NULL);   // NULL: la sesión crea su propio stream
void* stream = advtg_session_stream(s);

// callback de audio: mono float32 en [-1, 1]; wall_ns = reloj de pared (NTP) del bloque
advtg_stream_write_f32(stream, pcm, frames, 0, 0, 0, advtg_wall_now_ns());

// hilo de trabajo, cada 0.25 s
advtg_snapshot snap;
if (advtg_session_update(s, &snap) == 0) {
    // snap.display: marcha (s/d), error de batimiento (ms), amplitud (°), confianza
    // snap.metrics: cifras crudas con incertidumbres; snap.drift: deriva del ADC aplicada
}
advtg_session_destroy(s);
```

Reglas:

- **Un solo productor** escribe el stream y **un solo hilo** llama a `advtg_session_update` y a
  los accesores `advtg_session_*` (devuelven datos de la última actualización).
- Ninguna función deja escapar excepciones C++: los errores son códigos negativos y
  `advtg_session_last_error()` da el mensaje.
- Instantes en muestras absolutas (desde el inicio del stream); `NaN` = no resuelto.
- Reloj en vivo: con `wall_ns > 0` la deriva del ADC se estima contra el reloj de pared
  (disciplinado por NTP en un teléfono: nivel 2, ±1 ppm). Ajusta
  `cfg.wall_clock_sigma_ppm` / `wall_clock_disciplined` si la plataforma no lo garantiza.
- Calibración guardada (p. ej. GPS PPS): `cfg.has_calibration = 1` y `cfg.calibration`.
- Captura: pide 48 kHz mono, sin procesado de voz (Android `VoicePerformance`/`Unprocessed`,
  iOS `AVAudioSession.Mode.measurement`): AGC, supresión de ruido y AEC deforman los pulsos.

## 3. Opciones de CMake

| Opción | Escritorio | Móvil | Efecto |
|---|---|---|---|
| `ADVTG_WITH_AUDIO_CAPTURE` | ON | OFF (automático en Android/iOS) | Captura PortAudio (`advtg_cap_*`) |
| `ADVTG_WITH_RENDERER` | ON | ON (opcional) | Rasterizador CPU de las gráficas Qt (`advtg_gr_*`) |
| `ADVTG_STATIC` | OFF | ON en iOS | Librería estática en lugar de compartida |
| `ADVTG_BUILD_EXAMPLES` | OFF | — | Compila `advtg_replay` |
| `ADVTG_LTO` | ON | ON | Optimización en enlace |

En Android se añade `-Wl,-z,max-page-size=16384` (páginas de 16 KB, requisito de Google Play
para Android 15+). La política de coma flotante es la misma que en escritorio: nunca
`-ffast-math` (NaN es centinela físico); `session.cpp` y `filters.cpp` se compilan con
`-ffp-contract=off` para reproducir numpy expresión a expresión.

Prueba en el host de la configuración móvil (estática, sin captura):

```bash
cmake -S native -B build/mobile_host -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DADVTG_WITH_AUDIO_CAPTURE=OFF -DADVTG_STATIC=ON -DADVTG_BUILD_EXAMPLES=ON
cmake --build build/mobile_host
./build/mobile_host/advtg_replay toma.wav 28800 51
```

## 4. Android (NDK r26+)

`app/build.gradle.kts`:

```kotlin
android {
    defaultConfig {
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }  // x86_64: emulador
        externalNativeBuild { cmake { arguments += listOf("-DADVTG_WITH_RENDERER=OFF") } }
    }
    externalNativeBuild { cmake { path = file("src/main/cpp/CMakeLists.txt") } }
}
```

`src/main/cpp/CMakeLists.txt` (motor como subproyecto + puente JNI):

```cmake
cmake_minimum_required(VERSION 3.22)
project(timegrapher_jni CXX)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../../../native advtg)  # ruta al repo
add_library(timegrapher_jni SHARED jni_bridge.cpp)
target_link_libraries(timegrapher_jni PRIVATE advtg_native)
```

Puente JNI mínimo (`jni_bridge.cpp`):

```cpp
#include <jni.h>
#include "advtg.h"

extern "C" JNIEXPORT jlong JNICALL
Java_app_timegrapher_Engine_create(JNIEnv*, jclass, jdouble fs, jdouble bph, jdouble lift) {
    advtg_session_config cfg;
    advtg_session_config_default(&cfg, fs, bph);
    cfg.lift_angle_deg = lift;
    return reinterpret_cast<jlong>(advtg_session_create(&cfg, nullptr));
}

// Llamar desde el callback de Oboe (hilo de audio). Sin JNI en el callback real: lo ideal es
// que el callback de Oboe en C++ llame directamente a advtg_stream_write_f32.
extern "C" JNIEXPORT void JNICALL
Java_app_timegrapher_Engine_write(JNIEnv* env, jclass, jlong h, jfloatArray pcm, jint n) {
    jfloat* p = env->GetFloatArrayElements(pcm, nullptr);
    auto* s = reinterpret_cast<advtg_session*>(h);
    advtg_stream_write_f32(advtg_session_stream(s), p, n, 0, 0, 0, advtg_wall_now_ns());
    env->ReleaseFloatArrayElements(pcm, p, JNI_ABORT);
}

extern "C" JNIEXPORT jdoubleArray JNICALL
Java_app_timegrapher_Engine_update(JNIEnv* env, jclass, jlong h) {
    advtg_snapshot snap;
    if (advtg_session_update(reinterpret_cast<advtg_session*>(h), &snap) != 0) return nullptr;
    const jdouble v[] = {snap.display.rate_s_per_day, snap.display.beat_error_ms,
                         snap.display.amplitude_deg, snap.display.confidence,
                         static_cast<jdouble>(snap.locked)};
    jdoubleArray out = env->NewDoubleArray(5);
    env->SetDoubleArrayRegion(out, 0, 5, v);
    return out;
}

extern "C" JNIEXPORT void JNICALL
Java_app_timegrapher_Engine_destroy(JNIEnv*, jclass, jlong h) {
    advtg_session_destroy(reinterpret_cast<advtg_session*>(h));
}
```

`armeabi-v7a` (ARM 32 bits) compila en modo genérico (sin NEON); no se recomienda.

## 5. iOS (Xcode 15+, CMake 3.22+)

Librería estática por arquitectura y XCFramework:

```bash
cmake -S native -B build/ios -G Xcode -DCMAKE_SYSTEM_NAME=iOS \
  -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=15.0 -DADVTG_STATIC=ON
cmake --build build/ios --config Release
cmake -S native -B build/ios-sim -G Xcode -DCMAKE_SYSTEM_NAME=iOS \
  -DCMAKE_OSX_SYSROOT=iphonesimulator -DCMAKE_OSX_ARCHITECTURES=arm64 -DADVTG_STATIC=ON
cmake --build build/ios-sim --config Release
xcodebuild -create-xcframework \
  -library build/ios/Release-iphoneos/libadvtg_native.a -headers native/include/advtg.h \
  -library build/ios-sim/Release-iphonesimulator/libadvtg_native.a -headers native/include/advtg.h \
  -output build/AdvtgNative.xcframework
```

Con `-headers native/include` el resto de cabeceras C++ también se copiaría; basta `advtg.h`
(en un directorio propio con un `module.modulemap`: `module AdvtgNative { header "advtg.h"
export * }`). Enlaza además `libc++` (Xcode lo hace al haber C++ en el target).

Swift:

```swift
import AdvtgNative

var cfg = advtg_session_config()
advtg_session_config_default(&cfg, 48_000, 28_800)
cfg.lift_angle_deg = 52
let session = advtg_session_create(&cfg, nil)!
let stream = advtg_session_stream(session)

// AVAudioEngine input tap (formato float32 mono 48 kHz)
input.installTap(onBus: 0, bufferSize: 960, format: format) { buf, _ in
    advtg_stream_write_f32(stream, buf.floatChannelData![0], Int(buf.frameLength),
                           0, 0, 0, advtg_wall_now_ns())
}

// temporizador de 0.25 s en una cola serie propia
var snap = advtg_snapshot()
if advtg_session_update(session, &snap) == 0 {
    print(snap.display.rate_s_per_day, snap.display.beat_error_ms, snap.display.amplitude_deg)
}
```

## 6. Correspondencia con Python

| Python (`adv_tg.core`) | C (`advtg.h`) |
|---|---|
| `SessionConfig` | `advtg_session_config` + `advtg_session_config_default` |
| `MeasurementSession.update(stream)` | `advtg_session_update(s, &snap)` |
| `SessionSnapshot.display` | `advtg_snapshot.display` |
| `SessionSnapshot.metrics` (`Metrics`) | `advtg_snapshot.metrics` + `advtg_session_metric_windows/_warnings` |
| `waveform`, `tape_*` | `advtg_session_waveform`, `advtg_session_tape` |
| `last_beat`, beats de la ventana | `advtg_snapshot.last_beat`, `advtg_session_window_beats` |
| `drift`, `drift_source`, `live_drift` | `advtg_snapshot.drift`, `drift_source` (+`clock_field`), `live_drift` |
| `detector_stats`, `detector_warnings` | `advtg_session_detector_stats`, `advtg_session_detector_warnings` |
| `SampleStream.write` | `advtg_stream_write_f32` |

Los avisos (`*_warnings`) son textos en español como en la aplicación de escritorio; los
códigos numéricos (`note_kind`, `drift_source`, `beat_error_point`, flags de `advtg_beat`)
permiten traducirlos en la app.
