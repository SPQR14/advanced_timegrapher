// Core C API: version, CPU / SIMD information and the primitives exposed for tests.
// Module APIs live in their own c_api_<module>.cpp files.
#include <cstring>
#include <exception>
#include <string>

#include "advtg.h"  // public declarations: the compiler checks they match
#include "advtg_api.hpp"
#include "advtg_bits.hpp"
#include "advtg_simd.hpp"

#ifndef ADVTG_VERSION_STRING
#  define ADVTG_VERSION_STRING "2.1.0"
#endif

ADVTG_API int advtg_abi_version() { return ADVTG_ABI_VERSION; }

ADVTG_API const char* advtg_version() { return ADVTG_VERSION_STRING; }

ADVTG_API const char* advtg_simd_name() { return advtg::simd_level_name(); }

// CPU summary into `buf` (NUL-terminated, truncated to `cap`); returns the full length.
ADVTG_API int advtg_cpu_features(char* buf, int cap) {
    const std::string s = advtg::cpu_feature_string();
    if (buf && cap > 0) {
        const int n = static_cast<int>(s.size()) < cap - 1 ? static_cast<int>(s.size()) : cap - 1;
        std::memcpy(buf, s.data(), static_cast<std::size_t>(n));
        buf[n] = '\0';
    }
    return static_cast<int>(s.size());
}

// Force a SIMD level ("auto", "generic", "sse42", "avx2", "avx512", "neon"); 1 = done.
ADVTG_API int advtg_set_simd(const char* name) { return advtg::set_simd_level(name) ? 1 : 0; }

// Is this level compiled in AND supported by this CPU?
ADVTG_API int advtg_simd_available(const char* name) {
    return advtg::kernels_by_name(name) != nullptr ? 1 : 0;
}

// ---- primitives exposed for the parity tests ------------------------------------------
ADVTG_API std::uint64_t advtg_f64_key(double x) { return advtg::f64_key(x); }
ADVTG_API double advtg_key_f64(std::uint64_t k) { return advtg::key_f64(k); }

// Median of v[0..n) (REORDERS v); NaN for n == 0.
ADVTG_API double advtg_median_f64(double* v, std::size_t n) {
    try {
        return advtg::exact_median(v, n);
    } catch (...) {
        return advtg::kNaN;
    }
}

ADVTG_API double advtg_dot_f64(const double* a, const double* b, std::size_t n) {
    return advtg::kernels().dot(a, b, n);
}

ADVTG_API void advtg_median_segments_f64(const double* base, std::size_t stride, std::size_t m,
                                         std::size_t len, double* out) {
    try {
        advtg::kernels().median_segments(base, stride, m, len, out);
    } catch (...) {
    }
}

ADVTG_API void advtg_hilbert_fold_f64(const double* yp, std::size_t n, std::size_t half,
                                      const double* c, const std::int32_t* d, std::size_t nt,
                                      double* out) {
    advtg::kernels().hilbert_fold(yp, n, half, c, d, nt, out);
}
