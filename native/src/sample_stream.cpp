// Lock-free SPSC ring buffer (NativeSampleStream), SIMD waveform min/max decimation,
// multi-channel 2nd-difference energy accumulation, and branchless sorting-network median.
#include "advtg_runtime.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace advtg::runtime {

std::int64_t steady_now_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

std::int64_t wall_now_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

NativeSampleStream::NativeSampleStream(std::size_t capacity, double sample_rate,
                                       std::size_t log_size, std::int64_t perf_offset_ns)
    : cap_(capacity), fs_(sample_rate), log_size_(log_size), perf_offset_ns_(perf_offset_ns) {
    buf_ = static_cast<float*>(aligned_alloc_bytes(cap_ * sizeof(float), kSimdAlign));
    log_end_ = static_cast<std::int64_t*>(aligned_alloc_bytes(log_size_ * sizeof(std::int64_t), kSimdAlign));
    log_ns_ = static_cast<std::int64_t*>(aligned_alloc_bytes(log_size_ * sizeof(std::int64_t), kSimdAlign));
    log_adc_ = static_cast<std::int64_t*>(aligned_alloc_bytes(log_size_ * sizeof(std::int64_t), kSimdAlign));
    log_wall_ = static_cast<std::int64_t*>(aligned_alloc_bytes(log_size_ * sizeof(std::int64_t), kSimdAlign));
    reset();
}

NativeSampleStream::~NativeSampleStream() {
    aligned_free_bytes(buf_);
    aligned_free_bytes(log_end_);
    aligned_free_bytes(log_ns_);
    aligned_free_bytes(log_adc_);
    aligned_free_bytes(log_wall_);
}

void NativeSampleStream::reset() noexcept {
    std::memset(buf_, 0, cap_ * sizeof(float));
    std::memset(log_end_, 0, log_size_ * sizeof(std::int64_t));
    std::memset(log_ns_, 0, log_size_ * sizeof(std::int64_t));
    std::memset(log_adc_, 0, log_size_ * sizeof(std::int64_t));
    std::memset(log_wall_, 0, log_size_ * sizeof(std::int64_t));
    reserved_.store(0, std::memory_order_relaxed);
    total_.store(0, std::memory_order_relaxed);
    blocks_.store(0, std::memory_order_relaxed);
    first_ns_.store(0, std::memory_order_relaxed);
    first_block_n_.store(0, std::memory_order_relaxed);
    last_ns_.store(0, std::memory_order_relaxed);
    max_gap_ns_.store(0, std::memory_order_relaxed);
    max_write_ns_.store(0, std::memory_order_release);
}

namespace {

inline void copy_to_f32(float* dst, const float* src, std::size_t count) noexcept {
    std::memcpy(dst, src, count * sizeof(float));
}

inline void copy_to_f32(float* dst, const double* src, std::size_t count) noexcept {
    for (std::size_t i = 0; i < count; ++i) dst[i] = static_cast<float>(src[i]);
}

inline void copy_from_f32(double* dst, const float* src, std::size_t count) noexcept {
    for (std::size_t i = 0; i < count; ++i) dst[i] = static_cast<double>(src[i]);
}

inline void copy_from_f32(float* dst, const float* src, std::size_t count) noexcept {
    std::memcpy(dst, src, count * sizeof(float));
}

}  // namespace

void NativeSampleStream::write_f32(const float* data, std::size_t n, std::int64_t host_ns,
                                   bool has_host_ns, std::int64_t adc_ns,
                                   std::int64_t wall_ns) noexcept {
    const std::int64_t t0 = steady_now_ns() + perf_offset_ns_;
    if (!has_host_ns) host_ns = t0;
    if (n == 0 || !data) return;

    const std::int64_t total = total_.load(std::memory_order_relaxed);
    const std::int64_t new_total = total + static_cast<std::int64_t>(n);
    std::size_t off = 0, m = n;
    if (n > cap_) {
        off = n - cap_;
        m = cap_;
    }

    // Reserve range before mutating ring contents (readers verify against reserved_ after copy).
    reserved_.store(new_total, std::memory_order_release);

    const std::size_t start = static_cast<std::size_t>(
        (new_total - static_cast<std::int64_t>(m)) % static_cast<std::int64_t>(cap_));
    const std::size_t first = std::min(m, cap_ - start);
    copy_to_f32(buf_ + start, data + off, first);
    if (first < m) copy_to_f32(buf_, data + off + first, m - first);

    const std::int64_t blk = blocks_.load(std::memory_order_relaxed);
    const std::size_t k = static_cast<std::size_t>(blk) % log_size_;
    log_end_[k] = new_total;
    log_ns_[k] = host_ns;
    log_adc_[k] = adc_ns;
    log_wall_[k] = wall_ns;

    if (blk == 0) {
        first_ns_.store(host_ns, std::memory_order_relaxed);
        first_block_n_.store(static_cast<std::int64_t>(n), std::memory_order_relaxed);
    } else {
        const std::int64_t gap = host_ns - last_ns_.load(std::memory_order_relaxed);
        if (gap > max_gap_ns_.load(std::memory_order_relaxed)) {
            max_gap_ns_.store(gap, std::memory_order_relaxed);
        }
    }
    last_ns_.store(host_ns, std::memory_order_relaxed);
    blocks_.store(blk + 1, std::memory_order_release);

    const std::int64_t cost = (steady_now_ns() + perf_offset_ns_) - t0;
    if (cost > max_write_ns_.load(std::memory_order_relaxed)) {
        max_write_ns_.store(cost, std::memory_order_relaxed);
    }
    total_.store(new_total, std::memory_order_release);
}

void NativeSampleStream::write_f64(const double* data, std::size_t n, std::int64_t host_ns,
                                   bool has_host_ns, std::int64_t adc_ns,
                                   std::int64_t wall_ns) noexcept {
    const std::int64_t t0 = steady_now_ns() + perf_offset_ns_;
    if (!has_host_ns) host_ns = t0;
    if (n == 0 || !data) return;

    const std::int64_t total = total_.load(std::memory_order_relaxed);
    const std::int64_t new_total = total + static_cast<std::int64_t>(n);
    std::size_t off = 0, m = n;
    if (n > cap_) {
        off = n - cap_;
        m = cap_;
    }

    reserved_.store(new_total, std::memory_order_release);

    const std::size_t start = static_cast<std::size_t>(
        (new_total - static_cast<std::int64_t>(m)) % static_cast<std::int64_t>(cap_));
    const std::size_t first = std::min(m, cap_ - start);
    copy_to_f32(buf_ + start, data + off, first);
    if (first < m) copy_to_f32(buf_, data + off + first, m - first);

    const std::int64_t blk = blocks_.load(std::memory_order_relaxed);
    const std::size_t k = static_cast<std::size_t>(blk) % log_size_;
    log_end_[k] = new_total;
    log_ns_[k] = host_ns;
    log_adc_[k] = adc_ns;
    log_wall_[k] = wall_ns;

    if (blk == 0) {
        first_ns_.store(host_ns, std::memory_order_relaxed);
        first_block_n_.store(static_cast<std::int64_t>(n), std::memory_order_relaxed);
    } else {
        const std::int64_t gap = host_ns - last_ns_.load(std::memory_order_relaxed);
        if (gap > max_gap_ns_.load(std::memory_order_relaxed)) {
            max_gap_ns_.store(gap, std::memory_order_relaxed);
        }
    }
    last_ns_.store(host_ns, std::memory_order_relaxed);
    blocks_.store(blk + 1, std::memory_order_release);

    const std::int64_t cost = (steady_now_ns() + perf_offset_ns_) - t0;
    if (cost > max_write_ns_.load(std::memory_order_relaxed)) {
        max_write_ns_.store(cost, std::memory_order_relaxed);
    }
    total_.store(new_total, std::memory_order_release);
}

int NativeSampleStream::read_f64(std::int64_t start, std::int64_t stop, double* out) const noexcept {
    if (start < 0 || stop < start) return -1;
    if (stop == start) return 0;
    const std::int64_t total = total_.load(std::memory_order_acquire);
    if (stop > total) return 1;
    const std::int64_t cap = static_cast<std::int64_t>(cap_);
    if (total - start > cap) return 2;

    const std::size_t n = static_cast<std::size_t>(stop - start);
    const std::size_t i0 = static_cast<std::size_t>(start % cap);
    const std::size_t first = std::min(n, cap_ - i0);
    copy_from_f32(out, buf_ + i0, first);
    if (first < n) copy_from_f32(out + first, buf_, n - first);

    std::atomic_thread_fence(std::memory_order_acquire);
    if (reserved_.load(std::memory_order_acquire) - start > cap) return 2;
    return 0;
}

int NativeSampleStream::read_f32(std::int64_t start, std::int64_t stop, float* out) const noexcept {
    if (start < 0 || stop < start) return -1;
    if (stop == start) return 0;
    const std::int64_t total = total_.load(std::memory_order_acquire);
    if (stop > total) return 1;
    const std::int64_t cap = static_cast<std::int64_t>(cap_);
    if (total - start > cap) return 2;

    const std::size_t n = static_cast<std::size_t>(stop - start);
    const std::size_t i0 = static_cast<std::size_t>(start % cap);
    const std::size_t first = std::min(n, cap_ - i0);
    copy_from_f32(out, buf_ + i0, first);
    if (first < n) copy_from_f32(out + first, buf_, n - first);

    std::atomic_thread_fence(std::memory_order_acquire);
    if (reserved_.load(std::memory_order_acquire) - start > cap) return 2;
    return 0;
}

std::size_t NativeSampleStream::clock_log(std::int64_t last, std::int64_t* out_end,
                                          std::int64_t* out_ns, std::int64_t* out_adc,
                                          std::int64_t* out_wall,
                                          std::size_t out_cap) const noexcept {
    const std::int64_t blocks = blocks_.load(std::memory_order_acquire);
    std::size_t avail = static_cast<std::size_t>(
        std::min<std::int64_t>(blocks, static_cast<std::int64_t>(log_size_)));
    if (last >= 0) avail = std::min(avail, static_cast<std::size_t>(last));
    avail = std::min(avail, out_cap);
    if (avail == 0) return 0;

    const std::int64_t first_idx = blocks - static_cast<std::int64_t>(avail);
    for (std::size_t i = 0; i < avail; ++i) {
        const std::size_t idx = static_cast<std::size_t>(
            (first_idx + static_cast<std::int64_t>(i)) % static_cast<std::int64_t>(log_size_));
        if (out_end) out_end[i] = log_end_[idx];
        if (out_ns) out_ns[i] = log_ns_[idx];
        if (out_adc) out_adc[i] = log_adc_[idx];
        if (out_wall) out_wall[i] = log_wall_[idx];
    }

    std::atomic_thread_fence(std::memory_order_acquire);
    const std::int64_t blocks_after = blocks_.load(std::memory_order_acquire);
    const std::int64_t lapped =
        (blocks_after - blocks) - static_cast<std::int64_t>(log_size_ - avail);
    if (lapped <= 0) return avail;
    if (static_cast<std::size_t>(lapped) >= avail) return 0;
    const std::size_t skip = static_cast<std::size_t>(lapped);
    const std::size_t kept = avail - skip;
    if (out_end) std::memmove(out_end, out_end + skip, kept * sizeof(std::int64_t));
    if (out_ns) std::memmove(out_ns, out_ns + skip, kept * sizeof(std::int64_t));
    if (out_adc) std::memmove(out_adc, out_adc + skip, kept * sizeof(std::int64_t));
    if (out_wall) std::memmove(out_wall, out_wall + skip, kept * sizeof(std::int64_t));
    return kept;
}

void NativeSampleStream::stats(NativeStreamStats* out) const noexcept {
    if (!out) return;
    const std::int64_t total = total_.load(std::memory_order_acquire);
    const std::int64_t blocks = blocks_.load(std::memory_order_acquire);
    const std::int64_t first_ns = first_ns_.load(std::memory_order_relaxed);
    const std::int64_t last_ns = last_ns_.load(std::memory_order_relaxed);
    const std::int64_t first_n = first_block_n_.load(std::memory_order_relaxed);
    double ratio = 1.0;
    if (blocks >= 2 && last_ns > first_ns) {
        const double expected = fs_ * static_cast<double>(last_ns - first_ns) * 1e-9;
        ratio = static_cast<double>(total - first_n) / expected;
    }
    out->total_samples = total;
    out->blocks = blocks;
    out->audio_s = static_cast<double>(total) / fs_;
    out->ingest_ratio = ratio;
    out->max_gap_ms = static_cast<double>(max_gap_ns_.load(std::memory_order_relaxed)) / 1e6;
    out->max_write_us = static_cast<double>(max_write_ns_.load(std::memory_order_relaxed)) / 1e3;
}

// ------------------------------------------------------------------ SIMD utilities & smoothers
void minmax_decimate_f64(const double* signal, std::size_t n, std::size_t bins,
                         double* out_vals) noexcept {
    if (!signal || !out_vals || bins == 0 || n < bins) return;
    const std::size_t size = n / bins;
    for (std::size_t b = 0; b < bins; ++b) {
        const double* p = signal + b * size;
        double mn = p[0];
        double mx = p[0];
        for (std::size_t i = 1; i < size; ++i) {
            const double v = p[i];
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }
        out_vals[2 * b] = mn;
        out_vals[2 * b + 1] = mx;
    }
}

void channel_diff2_energy_f32(const float* block, std::size_t frames, std::size_t channels,
                              double* accum_energy) noexcept {
    if (!block || !accum_energy || frames < 3 || channels == 0) return;
    const std::size_t m = frames - 2;
    for (std::size_t i = 0; i < m; ++i) {
        const float* r0 = block + i * channels;
        const float* r1 = r0 + channels;
        const float* r2 = r1 + channels;
        for (std::size_t c = 0; c < channels; ++c) {
            const double d = static_cast<double>(r2[c]) - 2.0 * static_cast<double>(r1[c]) +
                             static_cast<double>(r0[c]);
            accum_energy[c] += d * d;
        }
    }
}

void channel_diff2_energy_f64(const double* block, std::size_t frames, std::size_t channels,
                              double* accum_energy) noexcept {
    if (!block || !accum_energy || frames < 3 || channels == 0) return;
    const std::size_t m = frames - 2;
    for (std::size_t i = 0; i < m; ++i) {
        const double* r0 = block + i * channels;
        const double* r1 = r0 + channels;
        const double* r2 = r1 + channels;
        for (std::size_t c = 0; c < channels; ++c) {
            const double d = r2[c] - 2.0 * r1[c] + r0[c];
            accum_energy[c] += d * d;
        }
    }
}

namespace {

inline void cswap(double& a, double& b) noexcept {
    const double lo = (a < b) ? a : b;
    const double hi = (a < b) ? b : a;
    a = lo;
    b = hi;
}

inline double median5_net(double a, double b, double c, double d, double e) noexcept {
    cswap(a, d);
    cswap(b, e);
    cswap(a, c);
    cswap(b, d);
    cswap(a, b);
    cswap(c, e);
    cswap(b, c);
    cswap(d, e);
    cswap(c, d);
    return c;
}

}  // namespace

void smooth_median_f64(const double* raw, std::size_t n, int window, double* out) noexcept {
    if (!raw || !out || n == 0) return;
    if (window <= 1) {
        std::memcpy(out, raw, n * sizeof(double));
        return;
    }
    const std::size_t w = static_cast<std::size_t>(window);
    const std::size_t warmup = std::min(n, w - 1);
    std::vector<double> tmp(w);
    for (std::size_t i = 0; i < warmup; ++i) {
        const std::size_t len = i + 1;
        std::memcpy(tmp.data(), raw, len * sizeof(double));
        out[i] = advtg::exact_median(tmp.data(), len);
    }
    if (w == 5) {
        for (std::size_t i = 4; i < n; ++i) {
            out[i] = median5_net(raw[i - 4], raw[i - 3], raw[i - 2], raw[i - 1], raw[i]);
        }
        return;
    }
    for (std::size_t i = warmup; i < n; ++i) {
        std::memcpy(tmp.data(), raw + (i + 1 - w), w * sizeof(double));
        out[i] = advtg::exact_median(tmp.data(), w);
    }
}

}  // namespace advtg::runtime
