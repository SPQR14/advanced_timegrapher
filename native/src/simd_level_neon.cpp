// arm64 NEON level (2 doubles per register; the FMA pipes are fed by the 4 independent
// accumulators of the kernels). Apple Silicon, Raspberry Pi 4/5, any AArch64.
#if defined(ADVTG_HAVE_NEON)
#  define ADVTG_LEVEL_NAME neon
#  define ADVTG_VW 2
#  include "simd_kernels.inl"
#endif
