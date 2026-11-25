// x86_64 SSE4.2 level (2 doubles per register). Built with -msse4.2 -mpopcnt (no FMA).
#if defined(ADVTG_HAVE_SSE42)
#  define ADVTG_LEVEL_NAME sse42
#  define ADVTG_VW 2
#  include "simd_kernels.inl"
#endif
