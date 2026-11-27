# C++ tests (Catch2 v3). The golden files and the recordings are read from the source tree.
find_package(Catch2 3 QUIET)
if(NOT Catch2_FOUND)
  include(FetchContent)
  FetchContent_Declare(Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.16.1
    GIT_SHALLOW TRUE)
  FetchContent_MakeAvailable(Catch2)
  list(APPEND CMAKE_MODULE_PATH "${catch2_SOURCE_DIR}/extras")
endif()
include(Catch)

# libsndfile: CMake package (vcpkg, recent distributions) or pkg-config.
find_package(SndFile CONFIG QUIET)
if(TARGET SndFile::sndfile)
  set(ADVTG_SNDFILE SndFile::sndfile)
else()
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(SNDFILE REQUIRED IMPORTED_TARGET sndfile)
  set(ADVTG_SNDFILE PkgConfig::SNDFILE)
endif()

add_executable(advtg_cpp_tests test_golden_live.cpp)
set_target_properties(advtg_cpp_tests PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
target_compile_definitions(advtg_cpp_tests PRIVATE ADVTG_SOURCE_DIR="${PROJECT_SOURCE_DIR}")
target_link_libraries(advtg_cpp_tests PRIVATE Catch2::Catch2WithMain advtg_native ${ADVTG_SNDFILE})
if(MSVC)
  target_compile_options(advtg_cpp_tests PRIVATE /utf-8)
endif()
catch_discover_tests(advtg_cpp_tests)
