// x86_64 AVX-512 (F/DQ/BW/VL) + FMA level (8 doubles per register).
#if defined(ADVTG_HAVE_AVX512)
#  define ADVTG_LEVEL_NAME avx512
#  define ADVTG_VW 8
#  include "simd_kernels.inl"
#endif
