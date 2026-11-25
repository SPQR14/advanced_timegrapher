// Runtime-dispatched SIMD kernels.
//
// The same source (src/simd_kernels.inl) is compiled once per instruction-set level, each in
// its own translation unit with its own flags (x86_64: SSE4.2, AVX2+FMA, AVX-512F/DQ/BW/VL+FMA;
// arm64: NEON), and one level is chosen at first use from CPUID + XGETBV (the OS must save the
// YMM/ZMM registers). A single packaged binary therefore never raises SIGILL on an older CPU
// and still uses AVX-512 / FMA where they exist. `ADVTG_SIMD=<level>` or
// `set_simd_level("<level>")` force a level (tests compare every level against `generic`).
//
// Rounding: the levels differ only in the order/fusion of floating-point operations (dot
// products, FMA contraction): results agree to ~1e-15 relative, never bit for bit.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace advtg {

struct Kernels {
    const char* name;  // "generic", "sse42", "avx2", "avx512" or "neon"
    int width;         // doubles per vector register used by the level

    // sum_i a[i] * b[i]
    double (*dot)(const double* a, const double* b, std::size_t n);

    // Odd-antisymmetric FIR (Hilbert): out[i] = sum_t c[t] * (yp[i + half - d[t]] -
    // yp[i + half + d[t]]), i in [0, n). `yp` is the signal zero-padded by `half` samples on
    // both sides (yp[0 .. n + 2 half)); d[t] in [1, half] (the taps of the odd lags only).
    void (*hilbert_fold)(const double* yp, std::size_t n, std::size_t half, const double* c,
                         const std::int32_t* d, std::size_t nt, double* out);

    // e[i] = sqrt(y[i]^2 + h[i]^2)
    void (*hypot_env)(const double* y, const double* h, std::size_t n, double* e);

    // Median across m segments for every sample: segment j starts at base + j * stride and has
    // `len` samples; out[i] = median_j seg_j[i] (bit-identical to np.median(..., axis=0):
    // even m -> 0.5 * (a + b)). Values must be NaN-free.
    void (*median_segments)(const double* base, std::size_t stride, std::size_t m,
                            std::size_t len, double* out);
};

const Kernels& kernels_generic();
#if defined(ADVTG_HAVE_SSE42)
const Kernels& kernels_sse42();
#endif
#if defined(ADVTG_HAVE_AVX2)
const Kernels& kernels_avx2();
#endif
#if defined(ADVTG_HAVE_AVX512)
const Kernels& kernels_avx512();
#endif
#if defined(ADVTG_HAVE_NEON)
const Kernels& kernels_neon();
#endif

// Active kernels (best supported level, or the forced one).
const Kernels& kernels();
// Kernels of a level by name, or nullptr if it is not compiled in or this CPU lacks it.
const Kernels* kernels_by_name(const char* name);
// Force a level ("auto" restores the best one). False if unavailable.
bool set_simd_level(const char* name);
const char* simd_level_name();
// Human-readable CPU summary, e.g. "x86_64 SSE4.2 AVX2 FMA AVX512F+DQ+BW+VL".
std::string cpu_feature_string();

}  // namespace advtg
