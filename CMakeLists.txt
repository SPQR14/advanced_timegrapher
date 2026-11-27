cmake_minimum_required(VERSION 3.21)
# Advanced Timegrapher, C++ application (migration plan: docs/PLAN_MIGRACION_CPP.md).
#
#   cmake -S . -B build/app -DCMAKE_BUILD_TYPE=Release [-DCMAKE_PREFIX_PATH=<Qt 6>]
#   cmake --build build/app
#   ctest --test-dir build/app --output-on-failure
#
# native/  measurement engine (also built alone by scripts/build_native.py for the 2.x app)
# app/     Qt 6 Widgets application (phase 0: skeleton)
# tests/cpp/  Catch2 tests, including the golden files of v2.0.1 (tests/golden/)
#
# Dependencies: Qt 6 (Widgets), libsndfile, Catch2 v3. Windows: vcpkg manifest (vcpkg.json)
# through -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake; Linux / macOS: the
# system packages (apt / dnf / brew). Catch2 is downloaded (FetchContent) when not installed.
project(advtg_app VERSION 3.0.0 LANGUAGES C CXX)

option(ADVTG_BUILD_APP "Build the Qt application" ON)
option(ADVTG_BUILD_TESTS "Build the C++ tests (Catch2)" ON)

# Every executable and DLL in one directory: Windows finds advtg_native.dll next to the exe.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

add_subdirectory(native)

if(ADVTG_BUILD_APP)
  add_subdirectory(app)
endif()

if(ADVTG_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests/cpp)
endif()
