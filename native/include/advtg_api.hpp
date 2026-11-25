// Export macros and ABI version of the native engine (libadvtg_native).
//
// Every function callable from Python (ctypes) is `ADVTG_API` (extern "C", default
// visibility); everything else stays hidden (-fvisibility=hidden). C API functions NEVER let
// a C++ exception escape: they catch and return a negative error code / NaN.
#pragma once

#if defined(_WIN32)
#  define ADVTG_EXPORT __declspec(dllexport)
#else
#  define ADVTG_EXPORT __attribute__((visibility("default")))
#endif

#define ADVTG_API extern "C" ADVTG_EXPORT

// Bump when an exported signature or struct layout changes (checked by native_bridge.py).
#define ADVTG_ABI_VERSION 1
