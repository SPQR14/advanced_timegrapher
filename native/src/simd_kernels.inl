// SIMD kernels, compiled once per level (see advtg_simd.hpp). Include only from
// simd_level_<name>.cpp after defining ADVTG_LEVEL_NAME (a bare token) and ADVTG_VW (doubles
// per vector: 1 = scalar). Written with GCC/Clang vector extensions, so the same code maps to
// SSE2/AVX2/AVX-512/NEON; other compilers (MSVC) get the scalar path, which they vectorise
// with the /arch flags of the translation unit.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "advtg_simd.hpp"

#ifndef ADVTG_LEVEL_NAME
#  error "define ADVTG_LEVEL_NAME before including simd_kernels.inl"
#endif
#ifndef ADVTG_VW
#  define ADVTG_VW 1
#endif

#define ADVTG_CAT2(a, b) a##b
#define ADVTG_CAT(a, b) ADVTG_CAT2(a, b)
#define ADVTG_STR2(a) #a
#define ADVTG_STR(a) ADVTG_STR2(a)

namespace advtg {
namespace ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME) {

constexpr int VW = ADVTG_VW;

#if ADVTG_VW > 1 && (defined(__GNUC__) || defined(__clang__))
#  define ADVTG_VEC 1
typedef double vd __attribute__((vector_size(ADVTG_VW * 8)));
typedef double vdu __attribute__((vector_size(ADVTG_VW * 8), aligned(8), may_alias));

static inline vd loadu(const double* p) { return *reinterpret_cast<const vdu*>(p); }
static inline void storeu(double* p, vd v) { *reinterpret_cast<vdu*>(p) = v; }
static inline vd splat(double x) {
    vd v;
    for (int i = 0; i < VW; ++i) v[i] = x;
    return v;
}
static inline double hsum(vd v) {
    double s = 0.0;
    for (int i = 0; i < VW; ++i) s += v[i];
    return s;
}
static inline vd vmin(vd a, vd b) { return a < b ? a : b; }
static inline vd vmax(vd a, vd b) { return a < b ? b : a; }
#endif

// ------------------------------------------------------------------ dot
static double k_dot(const double* a, const double* b, std::size_t n) {
    std::size_t i = 0;
#ifdef ADVTG_VEC
    vd a0 = {}, a1 = {}, a2 = {}, a3 = {};
    for (; i + 4 * VW <= n; i += 4 * VW) {
        a0 += loadu(a + i) * loadu(b + i);
        a1 += loadu(a + i + VW) * loadu(b + i + VW);
        a2 += loadu(a + i + 2 * VW) * loadu(b + i + 2 * VW);
        a3 += loadu(a + i + 3 * VW) * loadu(b + i + 3 * VW);
    }
    for (; i + VW <= n; i += VW) a0 += loadu(a + i) * loadu(b + i);
    double r = hsum((a0 + a1) + (a2 + a3));
#else
    double s0 = 0.0, s1 = 0.0, s2 = 0.0, s3 = 0.0;
    for (; i + 4 <= n; i += 4) {
        s0 += a[i] * b[i];
        s1 += a[i + 1] * b[i + 1];
        s2 += a[i + 2] * b[i + 2];
        s3 += a[i + 3] * b[i + 3];
    }
    double r = (s0 + s1) + (s2 + s3);
#endif
    for (; i < n; ++i) r += a[i] * b[i];
    return r;
}

// ------------------------------------------------------------------ Hilbert (folded FIR)
static void k_hilbert_fold(const double* yp, std::size_t n, std::size_t half, const double* c,
                           const std::int32_t* d, std::size_t nt, double* out) {
    std::size_t i = 0;
#ifdef ADVTG_VEC
    for (; i + 2 * VW <= n; i += 2 * VW) {
        vd acc0 = {}, acc1 = {};
        const double* base = yp + i + half;
        for (std::size_t t = 0; t < nt; ++t) {
            const vd ct = splat(c[t]);
            const std::ptrdiff_t dt = d[t];
            acc0 += ct * (loadu(base - dt) - loadu(base + dt));
            acc1 += ct * (loadu(base + VW - dt) - loadu(base + VW + dt));
        }
        storeu(out + i, acc0);
        storeu(out + i + VW, acc1);
    }
    for (; i + VW <= n; i += VW) {
        vd acc = {};
        const double* base = yp + i + half;
        for (std::size_t t = 0; t < nt; ++t) {
            const std::ptrdiff_t dt = d[t];
            acc += splat(c[t]) * (loadu(base - dt) - loadu(base + dt));
        }
        storeu(out + i, acc);
    }
#endif
    for (; i < n; ++i) {
        double acc = 0.0;
        const double* base = yp + i + half;
        for (std::size_t t = 0; t < nt; ++t) {
            const std::ptrdiff_t dt = d[t];
            acc += c[t] * (base[-dt] - base[dt]);
        }
        out[i] = acc;
    }
}

// ------------------------------------------------------------------ envelope magnitude
static void k_hypot_env(const double* y, const double* h, std::size_t n, double* e) {
    for (std::size_t i = 0; i < n; ++i) e[i] = std::sqrt(y[i] * y[i] + h[i] * h[i]);
}

// ------------------------------------------------------------------ median across segments
constexpr std::size_t kMaxNetwork = 64;

static void median_scalar(const double* base, std::size_t stride, std::size_t m, std::size_t i,
                          double* out) {
    double v[kMaxNetwork];
    for (std::size_t j = 0; j < m; ++j) v[j] = base[j * stride + i];
    const std::size_t h = m >> 1;
    std::nth_element(v, v + h, v + m);
    if (m & 1) {
        out[i] = v[h];
    } else {
        const double lo = *std::max_element(v, v + h);
        out[i] = 0.5 * (lo + v[h]);
    }
}

static void k_median_segments(const double* base, std::size_t stride, std::size_t m,
                              std::size_t len, double* out) {
    if (m == 0) return;
    std::size_t i = 0;
#ifdef ADVTG_VEC
    if (m <= kMaxNetwork) {
        const std::size_t h = m >> 1;
        for (; i + VW <= len; i += VW) {
            vd v[kMaxNetwork];
            for (std::size_t j = 0; j < m; ++j) v[j] = loadu(base + j * stride + i);
            // insertion network (data independent): m (m - 1) / 2 compare-exchanges
            for (std::size_t a = 1; a < m; ++a)
                for (std::size_t b = a; b > 0; --b) {
                    const vd lo = vmin(v[b - 1], v[b]);
                    const vd hi = vmax(v[b - 1], v[b]);
                    v[b - 1] = lo;
                    v[b] = hi;
                }
            vd r = (m & 1) ? v[h] : (splat(0.5) * (v[h - 1] + v[h]));
            storeu(out + i, r);
        }
    }
#endif
    if (m <= kMaxNetwork) {
        for (; i < len; ++i) median_scalar(base, stride, m, i, out);
    } else {  // more segments than the network handles: generic selection per sample
        thread_local std::vector<double> tmp;
        tmp.resize(m);
        const std::size_t h = m >> 1;
        for (; i < len; ++i) {
            for (std::size_t j = 0; j < m; ++j) tmp[j] = base[j * stride + i];
            std::nth_element(tmp.begin(), tmp.begin() + h, tmp.end());
            if (m & 1) {
                out[i] = tmp[h];
            } else {
                const double lo = *std::max_element(tmp.begin(), tmp.begin() + h);
                out[i] = 0.5 * (lo + tmp[h]);
            }
        }
    }
}

}  // namespace ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME)

const Kernels& ADVTG_CAT(kernels_, ADVTG_LEVEL_NAME)() {
    static const Kernels k = {
        ADVTG_STR(ADVTG_LEVEL_NAME),
        ADVTG_VW,
        &ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME)::k_dot,
        &ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME)::k_hilbert_fold,
        &ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME)::k_hypot_env,
        &ADVTG_CAT(lvl_, ADVTG_LEVEL_NAME)::k_median_segments,
    };
    return k;
}

}  // namespace advtg
