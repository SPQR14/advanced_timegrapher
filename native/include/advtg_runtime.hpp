// Native C++20 runtime: lock-free SPSC SampleStream, GIL-free PortAudio capture,
// SIMD waveform decimation & channel selector, sorting-network smoother, and
// multi-threaded CPU graph rasterizer (Steps 5 & 6).
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include "advtg_bits.hpp"

namespace advtg::runtime {

// ------------------------------------------------------------------ Stream stats (48 B)
struct NativeStreamStats {
    std::int64_t total_samples = 0;
    std::int64_t blocks = 0;
    double audio_s = 0.0;
    double ingest_ratio = 1.0;
    double max_gap_ms = 0.0;
    double max_write_us = 0.0;
};

// ------------------------------------------------------------------ Capture stats (184 B)
inline constexpr int kGilHistBuckets = 12;  // len(GIL_WAIT_EDGES_MS) + 1 = 11 + 1

struct NativeCaptureStats {
    std::int64_t callbacks = 0;
    std::int64_t status_flags = 0;
    std::int64_t callback_errors = 0;
    std::int64_t gil_wait_over_block = 0;
    std::int64_t gil_wait_invalid = 0;
    std::int64_t gil_n = 0;
    std::int64_t gil_hist[kGilHistBuckets] = {};
    double actual_sample_rate = 0.0;
    double max_callback_us = 0.0;
    double gil_wait_sum_us = 0.0;
    double gil_wait_max_us = 0.0;
    std::int32_t active = 0;
    std::int32_t channels = 1;
};

// Monotonic steady clock in nanoseconds (matching std::chrono::steady_clock).
std::int64_t steady_now_ns() noexcept;
std::int64_t wall_now_ns() noexcept;

// ------------------------------------------------------------------ Lock-free SPSC SampleStream
class NativeSampleStream {
public:
    NativeSampleStream(std::size_t capacity, double sample_rate, std::size_t log_size,
                       std::int64_t perf_offset_ns = 0);
    ~NativeSampleStream();

    NativeSampleStream(const NativeSampleStream&) = delete;
    NativeSampleStream& operator=(const NativeSampleStream&) = delete;

    std::size_t capacity() const noexcept { return cap_; }
    double sample_rate() const noexcept { return fs_; }
    std::size_t log_size() const noexcept { return log_size_; }
    std::int64_t perf_offset_ns() const noexcept { return perf_offset_ns_; }

    std::int64_t total_samples() const noexcept { return total_.load(std::memory_order_acquire); }
    std::int64_t oldest_available() const noexcept {
        const std::int64_t tot = total_.load(std::memory_order_acquire);
        const std::int64_t cap = static_cast<std::int64_t>(cap_);
        return tot > cap ? (tot - cap) : 0;
    }

    void reset() noexcept;

    // Single-producer writes (host_ns < 0 -> measure steady_now_ns() + perf_offset_ns_).
    void write_f32(const float* data, std::size_t n, std::int64_t host_ns, bool has_host_ns,
                   std::int64_t adc_ns, std::int64_t wall_ns) noexcept;
    void write_f64(const double* data, std::size_t n, std::int64_t host_ns, bool has_host_ns,
                   std::int64_t adc_ns, std::int64_t wall_ns) noexcept;

    // Thread-safe readers: 0=ok, 1=SamplesNotYetAvailable, 2=SamplesOverwritten, -1=invalid arg
    int read_f64(std::int64_t start, std::int64_t stop, double* out) const noexcept;
    int read_f32(std::int64_t start, std::int64_t stop, float* out) const noexcept;

    // Returns number of kept entries written to out buffers.
    std::size_t clock_log(std::int64_t last, std::int64_t* out_end, std::int64_t* out_ns,
                          std::int64_t* out_adc, std::int64_t* out_wall,
                          std::size_t out_cap) const noexcept;

    void stats(NativeStreamStats* out) const noexcept;

private:
    std::size_t cap_ = 0;
    double fs_ = 48000.0;
    std::size_t log_size_ = 8192;
    std::int64_t perf_offset_ns_ = 0;

    float* buf_ = nullptr;
    std::int64_t* log_end_ = nullptr;
    std::int64_t* log_ns_ = nullptr;
    std::int64_t* log_adc_ = nullptr;
    std::int64_t* log_wall_ = nullptr;

    // Separate producer reservation and published head onto independent 128 B cache lines
    // to prevent false sharing between the audio thread and DSP/GUI reader threads.
    alignas(kCacheLine) std::atomic<std::int64_t> reserved_{0};
    alignas(kCacheLine) std::atomic<std::int64_t> total_{0};
    alignas(kCacheLine) std::atomic<std::int64_t> blocks_{0};
    std::atomic<std::int64_t> first_ns_{0};
    std::atomic<std::int64_t> first_block_n_{0};
    std::atomic<std::int64_t> last_ns_{0};
    std::atomic<std::int64_t> max_gap_ns_{0};
    std::atomic<std::int64_t> max_write_ns_{0};
};

// ------------------------------------------------------------------ SIMD utilities & smoothers
void minmax_decimate_f64(const double* signal, std::size_t n, std::size_t bins,
                         double* out_vals) noexcept;

void channel_diff2_energy_f32(const float* block, std::size_t frames, std::size_t channels,
                              double* accum_energy) noexcept;
void channel_diff2_energy_f64(const double* block, std::size_t frames, std::size_t channels,
                              double* accum_energy) noexcept;

void smooth_median_f64(const double* raw, std::size_t n, int window, double* out) noexcept;

// ------------------------------------------------------------------ GIL-free PortAudio C Capture
class NativeAudioCapture {
public:
    NativeAudioCapture(NativeSampleStream* stream, int device, double sample_rate,
                       std::size_t blocksize, int high_latency);
    ~NativeAudioCapture();

    NativeAudioCapture(const NativeAudioCapture&) = delete;
    NativeAudioCapture& operator=(const NativeAudioCapture&) = delete;

    // 0=ok, <0=error (error_message() holds human-readable reason)
    int open(const char* pa_lib_path, int requested_channels);
    int start();
    int stop();
    void close();

    bool is_active() const noexcept;
    void stats(NativeCaptureStats* out) const noexcept;
    const char* last_error() const noexcept { return last_error_.c_str(); }

    // Called from PortAudio C callback thread (public for static trampoline).
    int on_pa_callback(const float* input, unsigned long frames, const void* time_info,
                       unsigned long status_flags) noexcept;

private:
    struct PaApi;
    PaApi* pa_ = nullptr;
    void* pa_stream_ = nullptr;
    bool pa_initialized_ = false;
    std::string last_error_;

    NativeSampleStream* stream_ = nullptr;
    int device_ = -1;
    double sample_rate_ = 48000.0;
    std::size_t blocksize_ = 0;
    int high_latency_ = 1;
    int channels_ = 1;
    double actual_fs_ = 0.0;

    // Channel selector state (exact mirror of adv_tg.audio.channel_select.ChannelSelector)
    std::size_t ch_window_ = 12000;
    double ch_first_ratio_ = 1.9952623149688795;   // 10^(3/10)
    double ch_switch_ratio_ = 10.0;                // 10^(10/10)
    int ch_confirm_ = 2;
    int ch_selected_ = 0;
    int ch_candidate_ = -1;
    int ch_streak_ = 0;
    bool ch_decided_ = false;
    std::size_t ch_count_ = 0;
    double* ch_energy_ = nullptr;
    float* mono_buf_ = nullptr;
    std::size_t mono_cap_ = 0;

    alignas(kCacheLine) std::atomic<std::int64_t> callbacks_{0};
    std::atomic<std::int64_t> status_flags_{0};
    std::atomic<std::int64_t> callback_errors_{0};
    std::atomic<std::int64_t> max_callback_ns_{0};
    std::atomic<std::int64_t> gil_n_{0};
    std::atomic<std::int64_t> gil_over_block_{0};
    std::atomic<std::int64_t> gil_invalid_{0};
    std::atomic<std::int64_t> gil_hist_[kGilHistBuckets]{};
    // Microseconds scaled by 1e3 (nanosecond resolution sum/max as lock-free int64 atomics)
    std::atomic<std::int64_t> gil_sum_ns_{0};
    std::atomic<std::int64_t> gil_max_ns_{0};
};

// ------------------------------------------------------------------ Multi-threaded CPU Plot Rasterizer
// Slot indices: 0=paper tape, 1=oscilloscope, 2=live waveform.
class NativeGraphRenderer {
public:
    NativeGraphRenderer();
    ~NativeGraphRenderer();

    NativeGraphRenderer(const NativeGraphRenderer&) = delete;
    NativeGraphRenderer& operator=(const NativeGraphRenderer&) = delete;

    void submit_tape(int w, int h, double x0, double x1, double y0, double y1, const double* xe,
                     const double* ye, std::size_t ne, const double* xo, const double* yo,
                     std::size_t no);

    void submit_osc(int w, int h, double x0, double x1, double y0, double y1, const double* t,
                    const double* wave, const double* env, std::size_t n);

    void submit_wave(int w, int h, double x0, double x1, double y0, double y1, const double* t,
                     const double* wave, std::size_t n);

    void clear_slot(int slot);
    void flush();

    // Lock-free frame read (valid until next acquire_frame call on the same thread/slot).
    const std::uint8_t* acquire_frame(int slot, int* out_w, int* out_h, int* out_stride,
                                      double* out_x0, double* out_x1, double* out_y0,
                                      double* out_y1, std::uint64_t* out_seq);

private:
    struct Impl;
    Impl* impl_ = nullptr;
};

}  // namespace advtg::runtime
