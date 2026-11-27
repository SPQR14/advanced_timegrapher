cmake_minimum_required(VERSION 3.20)
project(advtg_native VERSION 2.0.0 LANGUAGES CXX)

# Native engine of Advanced Timegrapher 2.0: DSP, beat detector, metrics, audio capture,
# session worker and graph renderer, as ONE shared library with a C ABI (loaded by ctypes).
#
#   cmake -S native -B build/native -G Ninja -DADVTG_TARGET_ARCH=native
#   cmake --build build/native
#
# Use scripts/build_native.py: it also copies the library to src/adv_tg/_native/.
#
# Floating point policy (read before touching a flag): NO -ffast-math / -ffinite-math-only /
# -fassociative-math. NaN is a physical sentinel and the compensated sums need the exact
# order of operations. FMA contraction IS allowed (-ffp-contract=fast): one rounding instead
# of two, never less accurate.

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(ADVTG_TARGET_ARCH "auto" CACHE STRING
    "auto | native | zen5 | x86_64_universal | apple_silicon | rpi4 | rpi5 | arm64_universal")
set(ADVTG_SOURCES "" CACHE STRING
    "Optional ';'-separated subset of src/*.cpp for partial builds (c_api, simd and dispatch are always built)")
set(ADVTG_LIB_SUFFIX "" CACHE STRING "Suffix of the library name (partial builds)")
option(ADVTG_LTO "Link-time optimisation" ON)

# Mobile (Android NDK / iOS): the app captures audio with the platform API (Oboe / AAudio,
# AVAudioEngine) and writes it into the stream itself, so PortAudio capture is left out.
set(_advtg_mobile OFF)
if(ANDROID OR IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS")
  set(_advtg_mobile ON)
endif()
if(_advtg_mobile)
  set(_advtg_capture_default OFF)
else()
  set(_advtg_capture_default ON)
endif()
option(ADVTG_WITH_AUDIO_CAPTURE "PortAudio capture (desktop)" ${_advtg_capture_default})
option(ADVTG_WITH_RENDERER "CPU graph rasterizer (desktop Qt plots)" ON)
option(ADVTG_STATIC "Static library (iOS / embedding) instead of a shared one" OFF)
option(ADVTG_BUILD_EXAMPLES "Build the plain-C example (examples/advtg_replay.c)" OFF)
option(ADVTG_STATIC_MSVC_RUNTIME "Static MSVC CRT (/MT) for self-contained Python ctypes DLL" ON)

string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _proc)
if(CMAKE_OSX_ARCHITECTURES MATCHES "arm64")
  set(_proc "arm64")
endif()
set(ADVTG_X86 OFF)
set(ADVTG_ARM64 OFF)
if(_proc MATCHES "x86_64|amd64")
  set(ADVTG_X86 ON)
elseif(_proc MATCHES "arm64|aarch64")
  set(ADVTG_ARM64 ON)
endif()

# ---------------------------------------------------------------- sources
file(GLOB ALL_SRC CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
set(LEVEL_SRC "")
set(CORE_SRC "")
foreach(f ${ALL_SRC})
  get_filename_component(_n "${f}" NAME)
  if(_n STREQUAL "audio_capture.cpp" AND NOT ADVTG_WITH_AUDIO_CAPTURE)
    continue()
  elseif(_n STREQUAL "graph_renderer.cpp" AND NOT ADVTG_WITH_RENDERER)
    continue()
  elseif(_n MATCHES "^simd_level_")
    list(APPEND LEVEL_SRC "${f}")
  elseif(ADVTG_SOURCES STREQUAL "" OR _n STREQUAL "c_api.cpp" OR _n STREQUAL "simd_dispatch.cpp")
    list(APPEND CORE_SRC "${f}")
  endif()
endforeach()
if(NOT ADVTG_SOURCES STREQUAL "")
  set(CORE_SRC "")
  foreach(f ${ALL_SRC})
    get_filename_component(_n "${f}" NAME)
    if(_n MATCHES "^simd_level_")
      continue()
    endif()
    if(_n STREQUAL "c_api.cpp" OR _n STREQUAL "simd_dispatch.cpp")
      list(APPEND CORE_SRC "${f}")
    else()
      foreach(want ${ADVTG_SOURCES})
        if(_n STREQUAL "${want}")
          list(APPEND CORE_SRC "${f}")
        endif()
      endforeach()
    endif()
  endforeach()
endif()

set(_libname "advtg_native${ADVTG_LIB_SUFFIX}")
if(ADVTG_STATIC)
  add_library(${_libname} STATIC ${CORE_SRC} ${LEVEL_SRC})
  target_compile_definitions(${_libname} PUBLIC ADVTG_STATIC)
else()
  add_library(${_libname} SHARED ${CORE_SRC} ${LEVEL_SRC})
endif()
# advtg.h is the public C header (JNI / Swift / ctypes); the rest stays private.
target_include_directories(${_libname}
  PUBLIC "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
  PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_definitions(${_libname} PRIVATE ADVTG_BUILDING ADVTG_VERSION_STRING="${PROJECT_VERSION}"
  ADVTG_WITH_AUDIO_CAPTURE=$<BOOL:${ADVTG_WITH_AUDIO_CAPTURE}>
  ADVTG_WITH_RENDERER=$<BOOL:${ADVTG_WITH_RENDERER}>)
set_target_properties(${_libname} PROPERTIES PUBLIC_HEADER "${CMAKE_CURRENT_SOURCE_DIR}/include/advtg.h")
set_target_properties(${_libname} PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON
  POSITION_INDEPENDENT_CODE ON)
if(WIN32)
  set_target_properties(${_libname} PROPERTIES PREFIX "")
endif()
if(MSVC AND ADVTG_STATIC_MSVC_RUNTIME)
  # Static C/C++ runtime (/MT): the DLL must not need MSVCP140.dll / VCRUNTIME140.dll, which a
  # clean Windows does not have and which ctypes would not find next to it in the bundle.
  set_property(TARGET ${_libname} PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
elseif(WIN32 AND NOT MSVC)
  message(WARNING "advtg_native on Windows should be built with MSVC: MinGW would make the DLL "
                  "depend on libstdc++-6.dll / libgcc_s / libwinpthread (not self-contained)")
endif()
find_package(Threads REQUIRED)
target_link_libraries(${_libname} PRIVATE Threads::Threads ${CMAKE_DL_LIBS})

# ---------------------------------------------------------------- SIMD levels compiled in
if(ADVTG_X86)
  target_compile_definitions(${_libname} PRIVATE ADVTG_HAVE_SSE42 ADVTG_HAVE_AVX2 ADVTG_HAVE_AVX512)
elseif(ADVTG_ARM64)
  target_compile_definitions(${_libname} PRIVATE ADVTG_HAVE_NEON)
endif()

# ---------------------------------------------------------------- compiler flags
if(MSVC)
  target_compile_options(${_libname} PRIVATE /O2 /Ob3 /Oi /Ot /Gw /Gy /fp:precise /EHsc /utf-8 /permissive-)
  target_compile_definitions(${_libname} PRIVATE NDEBUG _CRT_SECURE_NO_WARNINGS NOMINMAX)
  if(ADVTG_LTO)
    target_compile_options(${_libname} PRIVATE /GL)
    target_link_options(${_libname} PRIVATE /LTCG)
  endif()
  if(ADVTG_X86)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/simd_level_avx2.cpp" PROPERTIES COMPILE_OPTIONS "/arch:AVX2")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/simd_level_avx512.cpp" PROPERTIES COMPILE_OPTIONS "/arch:AVX512")
  endif()
  # zen5 on MSVC: AVX-512 for the whole core. MSVC has no "-march=native", so "native" keeps
  # the portable core + run-time dispatch (an /arch:AVX512 core would crash on CPUs without
  # AVX-512, e.g. the AMD EPYC Zen 3 CI runners).
  if(ADVTG_TARGET_ARCH STREQUAL "zen5")
    target_compile_options(${_libname} PRIVATE /arch:AVX512)
  endif()
else()
  include(CheckCXXCompilerFlag)
  target_compile_options(${_libname} PRIVATE
    -O3 -fno-math-errno -fno-trapping-math -ffp-contract=fast -funroll-loops -fno-fast-math
    -Wall -Wextra -Wno-unused-parameter)
  target_compile_definitions(${_libname} PRIVATE NDEBUG)

  # --- architecture of the core translation units
  set(_arch_flags "")
  if(ADVTG_X86)
    if(ADVTG_TARGET_ARCH STREQUAL "native")
      set(_arch_flags -march=native -mtune=native)
    elseif(ADVTG_TARGET_ARCH STREQUAL "zen5")
      check_cxx_compiler_flag("-march=znver5" _has_znver5)
      check_cxx_compiler_flag("-march=znver4" _has_znver4)
      if(_has_znver5)
        set(_arch_flags -march=znver5 -mtune=znver5)
      elseif(_has_znver4)
        set(_arch_flags -march=znver4 -mtune=znver4)
      else()
        set(_arch_flags -march=native -mtune=native)
      endif()
    else()  # auto / x86_64_universal: every x86_64 CPU since 2009 (SSE4.2); SIMD via dispatch
      check_cxx_compiler_flag("-march=x86-64-v2" _has_v2)
      if(_has_v2)
        set(_arch_flags -march=x86-64-v2 -mtune=generic)
      else()
        set(_arch_flags -msse4.2 -mpopcnt -mtune=generic)
      endif()
    endif()
  elseif(ADVTG_ARM64)
    if(ADVTG_TARGET_ARCH STREQUAL "native")
      check_cxx_compiler_flag("-mcpu=native" _has_mcpu_native)
      if(_has_mcpu_native)
        set(_arch_flags -mcpu=native)
      endif()
    elseif(ADVTG_TARGET_ARCH STREQUAL "rpi4")
      set(_arch_flags -mcpu=cortex-a72 -mtune=cortex-a72)
    elseif(ADVTG_TARGET_ARCH STREQUAL "rpi5")
      set(_arch_flags -mcpu=cortex-a76 -mtune=cortex-a76)
    elseif(ANDROID OR CMAKE_SYSTEM_NAME STREQUAL "iOS")
      # every arm64 phone: ARMv8-A with NEON (iOS: the toolchain default, apple-a7+)
      if(ANDROID)
        set(_arch_flags -march=armv8-a)
      endif()
    elseif(ADVTG_TARGET_ARCH STREQUAL "apple_silicon" OR (ADVTG_TARGET_ARCH STREQUAL "auto" AND APPLE))
      set(_arch_flags -mcpu=apple-m1)
    else()  # arm64_universal / auto on Linux
      set(_arch_flags -march=armv8-a)
    endif()
  endif()
  target_compile_options(${_libname} PRIVATE ${_arch_flags})

  # --- one translation unit per x86_64 level, with its own instruction set
  if(ADVTG_X86)
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/simd_level_sse42.cpp" PROPERTIES COMPILE_OPTIONS "-msse4.2;-mpopcnt")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/simd_level_avx2.cpp" PROPERTIES COMPILE_OPTIONS "-mavx2;-mfma;-mbmi;-mbmi2;-mf16c")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/simd_level_avx512.cpp" PROPERTIES COMPILE_OPTIONS "-mavx512f;-mavx512dq;-mavx512bw;-mavx512vl;-mavx2;-mfma;-mbmi2")
  endif()

  # The session and the filter design reproduce numpy expression by expression (parity tests
  # against the Python session): no FMA contraction there. They are not hot paths.
  set_source_files_properties(
    "${CMAKE_CURRENT_SOURCE_DIR}/src/session.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/src/filters.cpp"
    PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")

  if(ADVTG_LTO)
    include(CheckIPOSupported)
    check_ipo_supported(RESULT _ipo OUTPUT _ipo_msg LANGUAGES CXX)
    if(_ipo)
      set_property(TARGET ${_libname} PROPERTY INTERPROCEDURAL_OPTIMIZATION ON)
    endif()
  endif()
  if(UNIX AND NOT APPLE AND NOT ADVTG_STATIC)
    target_link_options(${_libname} PRIVATE -Wl,--as-needed -Wl,-z,relro -Wl,-z,now)
  endif()
  if(ANDROID AND NOT ADVTG_STATIC)
    # Android 15+ devices with 16 KB pages (Google Play requirement for new apps)
    target_link_options(${_libname} PRIVATE -Wl,-z,max-page-size=16384)
  endif()
endif()

if(ADVTG_BUILD_EXAMPLES)
  enable_language(C)
  add_executable(advtg_replay "${CMAKE_CURRENT_SOURCE_DIR}/examples/advtg_replay.c")
  set_target_properties(advtg_replay PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
  target_link_libraries(advtg_replay PRIVATE ${_libname})
  if(UNIX AND NOT APPLE)
    target_link_libraries(advtg_replay PRIVATE m)
    set_target_properties(advtg_replay PROPERTIES BUILD_RPATH "$<TARGET_FILE_DIR:${_libname}>")
  endif()
endif()

message(STATUS "advtg_native: arch=${ADVTG_TARGET_ARCH} x86=${ADVTG_X86} arm64=${ADVTG_ARM64} core=${_arch_flags} "
               "capture=${ADVTG_WITH_AUDIO_CAPTURE} renderer=${ADVTG_WITH_RENDERER} static=${ADVTG_STATIC}")
