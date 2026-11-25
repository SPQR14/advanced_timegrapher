// x86_64 AVX2 + FMA level (4 doubles per register). Built with -mavx2 -mfma -mbmi -mbmi2.
#if defined(ADVTG_HAVE_AVX2)
#  define ADVTG_LEVEL_NAME avx2
#  define ADVTG_VW 4
#  include "simd_kernels.inl"
#endif
