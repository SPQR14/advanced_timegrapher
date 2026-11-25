// Bit-level and memory utilities shared by every module of the native engine.
//
//  * Aligned buffers (64 B: AVX-512 / NEON friendly) and 128 B cache-line padding (Apple
//    Silicon lines are 128 B; 128 also covers the 64 B lines of x86_64 / ARM64 Linux).
//  * IEEE-754 binary64 <-> uint64 *order-preserving* bijection: comparing the integer keys is
//    comparing the doubles (-0.0 == +0.0), which allows radix histograms over the bit pattern.
//  * Exact median / MAD (bit-identical to numpy.median) with a radix selection for big arrays.
//  * Neumaier compensated sum.
//
// This code must NEVER be compiled with -ffast-math / -ffinite-math-only /
// -fassociative-math: NaN is a physical sentinel ("sub-event not found") and the compensated
// sums rely on the exact order of floating-point operations.
#pragma once

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <new>
#include <utility>
#include <vector>

#if defined(_MSC_VER)
#  include <malloc.h>
#endif

namespace advtg {

inline constexpr std::size_t kCacheLine = 128;  // padding unit against false sharing
inline constexpr std::size_t kSimdAlign = 64;   // alignment of numeric buffers
inline constexpr double kTiny = 1e-12;
inline constexpr double kMadToSigma = 1.4826;
inline constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
inline constexpr double kInf = std::numeric_limits<double>::infinity();

inline bool is_nan(double x) { return x != x; }
inline bool is_finite(double x) { return std::isfinite(x); }

// ---------------------------------------------------------------- aligned memory
inline void* aligned_alloc_bytes(std::size_t bytes, std::size_t align = kSimdAlign) {
    if (bytes == 0) bytes = align;
#if defined(_MSC_VER)
    void* p = _aligned_malloc(bytes, align);
#else
    void* p = nullptr;
    if (posix_memalign(&p, align, bytes) != 0) p = nullptr;
#endif
    if (!p) throw std::bad_alloc();
    return p;
}

inline void aligned_free_bytes(void* p) noexcept {
#if defined(_MSC_VER)
    _aligned_free(p);
#else
    std::free(p);
#endif
}

// Uninitialised, 64-byte aligned array of trivially copyable T (reused in hot loops: grow
// only, never shrink; `resize` does NOT preserve contents unless `keep` is true).
template <class T>
class AlignedBuffer {
public:
    AlignedBuffer() = default;
    explicit AlignedBuffer(std::size_t n) { resize(n); }
    AlignedBuffer(const AlignedBuffer& o) { assign(o.data_, o.size_); }
    AlignedBuffer& operator=(const AlignedBuffer& o) {
        if (this != &o) assign(o.data_, o.size_);
        return *this;
    }
    AlignedBuffer(AlignedBuffer&& o) noexcept
        : data_(std::exchange(o.data_, nullptr)),
          size_(std::exchange(o.size_, 0)),
          cap_(std::exchange(o.cap_, 0)) {}
    AlignedBuffer& operator=(AlignedBuffer&& o) noexcept {
        if (this != &o) {
            aligned_free_bytes(data_);
            data_ = std::exchange(o.data_, nullptr);
            size_ = std::exchange(o.size_, 0);
            cap_ = std::exchange(o.cap_, 0);
        }
        return *this;
    }
    ~AlignedBuffer() { aligned_free_bytes(data_); }

    void reserve(std::size_t n, bool keep = true) {
        if (n <= cap_) return;
        std::size_t new_cap = std::max<std::size_t>(n, cap_ + cap_ / 2);
        T* p = static_cast<T*>(aligned_alloc_bytes(new_cap * sizeof(T)));
        if (keep && size_) std::memcpy(p, data_, size_ * sizeof(T));
        aligned_free_bytes(data_);
        data_ = p;
        cap_ = new_cap;
    }
    void resize(std::size_t n, bool keep = false) {
        reserve(n, keep);
        size_ = n;
    }
    void assign(const T* src, std::size_t n) {
        resize(n, false);
        if (n) std::memcpy(data_, src, n * sizeof(T));
    }
    void append(const T* src, std::size_t n) {
        reserve(size_ + n, true);
        if (n) std::memcpy(data_ + size_, src, n * sizeof(T));
        size_ += n;
    }
    void zero() { if (size_) std::memset(data_, 0, size_ * sizeof(T)); }
    void clear() { size_ = 0; }

    T* data() { return data_; }
    const T* data() const { return data_; }
    std::size_t size() const { return size_; }
    std::size_t capacity() const { return cap_; }
    T& operator[](std::size_t i) { return data_[i]; }
    const T& operator[](std::size_t i) const { return data_[i]; }
    T* begin() { return data_; }
    T* end() { return data_ + size_; }
    const T* begin() const { return data_; }
    const T* end() const { return data_ + size_; }

private:
    T* data_ = nullptr;
    std::size_t size_ = 0;
    std::size_t cap_ = 0;
};

// Pads a value to its own cache line(s): counters written by different threads.
template <class T>
struct alignas(kCacheLine) Padded {
    T value{};
};

// ---------------------------------------------------------------- bit tricks
inline constexpr bool is_pow2(std::uint64_t x) { return x && !(x & (x - 1)); }
inline constexpr std::uint64_t next_pow2(std::uint64_t x) { return x <= 1 ? 1 : std::bit_ceil(x); }
inline constexpr int ilog2(std::uint64_t x) { return 63 - std::countl_zero(x); }

// Order-preserving bijection between finite/infinite doubles and uint64: a < b  <=>
// key(a) < key(b); -0.0 and +0.0 share the key of +0.0. NaN is not supported.
inline std::uint64_t f64_key(double x) {
    const std::uint64_t u = std::bit_cast<std::uint64_t>(x + 0.0);  // -0.0 + 0.0 = +0.0
    return (u >> 63) ? ~u : (u | (1ULL << 63));
}
inline double key_f64(std::uint64_t k) {
    const std::uint64_t u = (k >> 63) ? (k & ~(1ULL << 63)) : ~k;
    return std::bit_cast<double>(u);
}

// ---------------------------------------------------------------- order statistics
namespace detail {

inline double select_small(double* v, std::size_t n, std::size_t k) {
    if (n <= 32) {  // insertion sort: no allocation, fastest for the tiny arrays of the fits
        for (std::size_t i = 1; i < n; ++i) {
            const double x = v[i];
            std::size_t j = i;
            while (j > 0 && v[j - 1] > x) {
                v[j] = v[j - 1];
                --j;
            }
            v[j] = x;
        }
        return v[k];
    }
    std::nth_element(v, v + k, v + n);
    return v[k];
}

// k-th smallest (0-based) of src[0..n). Does NOT corrupt `src`; when n <= 1024 works on a
// tiny stack buffer, otherwise narrows into a thread_local scratch buffer so `src` keeps all
// its elements (safe to call exact_median_abs_dev right after exact_median on the same array).
inline double select_kth(const double* src, std::size_t n, std::size_t k) {
    if (n <= 1024) {
        double buf[1024];
        std::memcpy(buf, src, n * sizeof(double));
        return select_small(buf, n, k);
    }
    thread_local AlignedBuffer<double> work;
    const double* cur = src;
    std::size_t m = n;
    while (m > 1024) {
        std::uint64_t kmin = ~0ULL, kmax = 0;
        for (std::size_t i = 0; i < m; ++i) {
            const std::uint64_t key = f64_key(cur[i]);
            kmin = key < kmin ? key : kmin;
            kmax = key > kmax ? key : kmax;
        }
        const std::uint64_t diff = kmin ^ kmax;
        if (diff == 0) return key_f64(kmin);  // all equal
        const int nbits = 64 - std::countl_zero(diff);
        const int shift = nbits > 11 ? nbits - 11 : 0;
        std::uint32_t hist[2048] = {};
        for (std::size_t i = 0; i < m; ++i) ++hist[(f64_key(cur[i]) >> shift) & 2047U];
        std::size_t cum = 0;
        std::uint32_t b = 0;
        for (; b < 2048; ++b) {
            if (cum + hist[b] > k) break;
            cum += hist[b];
        }
        k -= cum;
        if (cur == src) {
            work.resize(hist[b]);
            std::size_t w = 0;
            for (std::size_t i = 0; i < m; ++i)
                if (((f64_key(src[i]) >> shift) & 2047U) == b) work[w++] = src[i];
            cur = work.data();
            m = w;
        } else {
            std::size_t w = 0;
            for (std::size_t i = 0; i < m; ++i)
                if (((f64_key(work[i]) >> shift) & 2047U) == b) work[w++] = work[i];
            m = w;
        }
    }
    return cur == src ? select_kth(src, m, k) : select_small(work.data(), m, k);
}

}  // namespace detail

// Median of v[0..n) (NaN-free), bit-identical to float(np.median(v)). May reorder v[0..n)
// (always a valid permutation; never loses elements). Returns NaN for n == 0.
inline double exact_median(double* v, std::size_t n) {
    if (n == 0) return kNaN;
    const std::size_t h = n >> 1;
    if (n <= 1024) {
        std::nth_element(v, v + h, v + n);
        const double hi = v[h];
        if (n & 1) return hi;
        const double lo = *std::max_element(v, v + h);
        return 0.5 * (lo + hi);
    }
    if (n & 1) return detail::select_kth(v, n, h);
    const double hi = detail::select_kth(v, n, h);
    const double lo = detail::select_kth(v, n, h - 1);
    return 0.5 * (lo + hi);
}

// Median of |v[i] - center|: REORDERS v (it is overwritten with the deviations).
inline double exact_median_abs_dev(double* v, std::size_t n, double center) {
    for (std::size_t i = 0; i < n; ++i) v[i] = std::fabs(v[i] - center);
    return exact_median(v, n);
}

// ---------------------------------------------------------------- compensated sums
struct NeumaierSum {
    double sum = 0.0;
    double comp = 0.0;
    void add(double x) {
        const double t = sum + x;
        comp += (std::fabs(sum) >= std::fabs(x)) ? (sum - t) + x : (x - t) + sum;
        sum = t;
    }
    double value() const { return sum + comp; }
};

inline double neumaier_sum(const double* x, std::size_t n) {
    NeumaierSum s;
    for (std::size_t i = 0; i < n; ++i) s.add(x[i]);
    return s.value();
}

}  // namespace advtg
