// C API exports for NativeSampleStream, NativeAudioCapture, SIMD utilities,
// and NativeGraphRenderer (Steps 5 & 6).
#include <cstddef>
#include <cstdint>
#include <exception>

#include "advtg.h"  // public declarations: the compiler checks they match
#include "advtg_api.hpp"
#include "advtg_runtime.hpp"

// Mobile builds (Android / iOS) capture audio with the platform API (Oboe/AAudio,
// AVAudioEngine) and write the stream themselves: no PortAudio, see CMakeLists.txt.
#ifndef ADVTG_WITH_AUDIO_CAPTURE
#  define ADVTG_WITH_AUDIO_CAPTURE 1
#endif
#ifndef ADVTG_WITH_RENDERER
#  define ADVTG_WITH_RENDERER 1
#endif

using advtg::runtime::NativeAudioCapture;
using advtg::runtime::NativeCaptureStats;
using advtg::runtime::NativeGraphRenderer;
using advtg::runtime::NativeSampleStream;
using advtg::runtime::NativeStreamStats;

// ------------------------------------------------------------------ Clocks
ADVTG_API std::int64_t advtg_steady_now_ns() { return advtg::runtime::steady_now_ns(); }
ADVTG_API std::int64_t advtg_wall_now_ns() { return advtg::runtime::wall_now_ns(); }

// ------------------------------------------------------------------ SampleStream
ADVTG_API void* advtg_stream_create(std::size_t capacity, double sample_rate,
                                    std::size_t block_log_size, std::int64_t perf_offset_ns) {
    try {
        return new NativeSampleStream(capacity, sample_rate, block_log_size, perf_offset_ns);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_stream_destroy(void* h) {
    delete static_cast<NativeSampleStream*>(h);
}

ADVTG_API void advtg_stream_reset(void* h) {
    if (h) static_cast<NativeSampleStream*>(h)->reset();
}

ADVTG_API std::int64_t advtg_stream_total(const void* h) {
    return h ? static_cast<const NativeSampleStream*>(h)->total_samples() : 0;
}

ADVTG_API std::int64_t advtg_stream_oldest(const void* h) {
    return h ? static_cast<const NativeSampleStream*>(h)->oldest_available() : 0;
}

ADVTG_API void advtg_stream_write_f32(void* h, const float* data, std::size_t n,
                                      std::int64_t host_ns, int has_host_ns, std::int64_t adc_ns,
                                      std::int64_t wall_ns) {
    if (h) {
        static_cast<NativeSampleStream*>(h)->write_f32(data, n, host_ns, has_host_ns != 0, adc_ns,
                                                       wall_ns);
    }
}

ADVTG_API void advtg_stream_write_f64(void* h, const double* data, std::size_t n,
                                      std::int64_t host_ns, int has_host_ns, std::int64_t adc_ns,
                                      std::int64_t wall_ns) {
    if (h) {
        static_cast<NativeSampleStream*>(h)->write_f64(data, n, host_ns, has_host_ns != 0, adc_ns,
                                                       wall_ns);
    }
}

ADVTG_API int advtg_stream_read_f64(const void* h, std::int64_t start, std::int64_t stop,
                                    double* out) {
    return h ? static_cast<const NativeSampleStream*>(h)->read_f64(start, stop, out) : -1;
}

ADVTG_API int advtg_stream_read_f32(const void* h, std::int64_t start, std::int64_t stop,
                                    float* out) {
    return h ? static_cast<const NativeSampleStream*>(h)->read_f32(start, stop, out) : -1;
}

ADVTG_API std::size_t advtg_stream_clock_log(const void* h, std::int64_t last, std::int64_t* out_end,
                                             std::int64_t* out_ns, std::int64_t* out_adc,
                                             std::int64_t* out_wall, std::size_t out_cap) {
    return h ? static_cast<const NativeSampleStream*>(h)->clock_log(last, out_end, out_ns, out_adc,
                                                                    out_wall, out_cap)
             : 0;
}

ADVTG_API void advtg_stream_stats(const void* h, NativeStreamStats* out) {
    if (h && out) static_cast<const NativeSampleStream*>(h)->stats(out);
}

// ------------------------------------------------------------------ SIMD helpers & smoother
ADVTG_API void advtg_minmax_decimate_f64(const double* signal, std::size_t n, std::size_t bins,
                                         double* out_vals) {
    advtg::runtime::minmax_decimate_f64(signal, n, bins, out_vals);
}

ADVTG_API void advtg_channel_diff2_energy_f32(const float* block, std::size_t frames,
                                              std::size_t channels, double* accum) {
    advtg::runtime::channel_diff2_energy_f32(block, frames, channels, accum);
}

ADVTG_API void advtg_channel_diff2_energy_f64(const double* block, std::size_t frames,
                                              std::size_t channels, double* accum) {
    advtg::runtime::channel_diff2_energy_f64(block, frames, channels, accum);
}

ADVTG_API void advtg_smooth_median_f64(const double* raw, std::size_t n, int window, double* out) {
    advtg::runtime::smooth_median_f64(raw, n, window, out);
}

#if ADVTG_WITH_AUDIO_CAPTURE
// ------------------------------------------------------------------ AudioCapture
ADVTG_API void* advtg_cap_create(void* stream_h, int device, double sample_rate,
                                 std::size_t blocksize, int high_latency) {
    try {
        return new NativeAudioCapture(static_cast<NativeSampleStream*>(stream_h), device,
                                      sample_rate, blocksize, high_latency);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_cap_destroy(void* h) {
    delete static_cast<NativeAudioCapture*>(h);
}

ADVTG_API int advtg_cap_open(void* h, const char* pa_lib_path, int requested_channels) {
    if (!h) return -1;
    try {
        return static_cast<NativeAudioCapture*>(h)->open(pa_lib_path, requested_channels);
    } catch (...) {
        return -1;
    }
}

ADVTG_API int advtg_cap_start(void* h) {
    return h ? static_cast<NativeAudioCapture*>(h)->start() : -1;
}

ADVTG_API int advtg_cap_stop(void* h) {
    return h ? static_cast<NativeAudioCapture*>(h)->stop() : -1;
}

ADVTG_API void advtg_cap_close(void* h) {
    if (h) static_cast<NativeAudioCapture*>(h)->close();
}

ADVTG_API int advtg_cap_is_active(const void* h) {
    return (h && static_cast<const NativeAudioCapture*>(h)->is_active()) ? 1 : 0;
}

ADVTG_API void advtg_cap_stats(const void* h, NativeCaptureStats* out) {
    if (h && out) static_cast<const NativeAudioCapture*>(h)->stats(out);
}

ADVTG_API const char* advtg_cap_last_error(const void* h) {
    return h ? static_cast<const NativeAudioCapture*>(h)->last_error() : "";
}

#else

ADVTG_C_API int advtg_set_portaudio_library(const char*) { return ADVTG_ERR_UNAVAILABLE; }

ADVTG_C_API int32_t advtg_list_input_devices(advtg_audio_device_info* out, int32_t cap) {
    if (out && cap > 0) {
        std::memset(out, 0, sizeof *out);
        out->device_id = -1;
        out->max_input_channels = 1;
        out->default_samplerate = 48000.0;
        out->is_synthetic = 1;
        std::snprintf(out->name, sizeof out->name, "Synthetic Ground-Truth Generator");
    }
    return 1;
}

ADVTG_C_API int32_t advtg_audio_devices_text(char* buf, int32_t cap) {
    const char* msg = "(PortAudio not available: disabled in build)";
    const std::size_t len = std::strlen(msg);
    if (buf && cap > 0) {
        const std::size_t n = std::min<std::size_t>(len, static_cast<std::size_t>(cap - 1));
        std::memcpy(buf, msg, n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(len);
}

#endif  // ADVTG_WITH_AUDIO_CAPTURE

#if ADVTG_WITH_RENDERER
// ------------------------------------------------------------------ Multi-threaded GraphRenderer
ADVTG_API void* advtg_gr_create() {
    try {
        return new NativeGraphRenderer();
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_gr_destroy(void* h) {
    delete static_cast<NativeGraphRenderer*>(h);
}

ADVTG_API void advtg_gr_submit_tape(void* h, int w, int h_px, double x0, double x1, double y0,
                                    double y1, const double* xe, const double* ye, std::size_t ne,
                                    const double* xo, const double* yo, std::size_t no) {
    if (h) {
        static_cast<NativeGraphRenderer*>(h)->submit_tape(w, h_px, x0, x1, y0, y1, xe, ye, ne, xo,
                                                          yo, no);
    }
}

ADVTG_API void advtg_gr_submit_osc(void* h, int w, int h_px, double x0, double x1, double y0,
                                   double y1, const double* t, const double* wave,
                                   const double* env, std::size_t n) {
    if (h) {
        static_cast<NativeGraphRenderer*>(h)->submit_osc(w, h_px, x0, x1, y0, y1, t, wave, env, n);
    }
}

ADVTG_API void advtg_gr_submit_wave(void* h, int w, int h_px, double x0, double x1, double y0,
                                    double y1, const double* t, const double* wave, std::size_t n) {
    if (h) {
        static_cast<NativeGraphRenderer*>(h)->submit_wave(w, h_px, x0, x1, y0, y1, t, wave, n);
    }
}

ADVTG_API void advtg_gr_clear_slot(void* h, int slot) {
    if (h) static_cast<NativeGraphRenderer*>(h)->clear_slot(slot);
}

ADVTG_API void advtg_gr_flush(void* h) {
    if (h) static_cast<NativeGraphRenderer*>(h)->flush();
}

ADVTG_API const std::uint8_t* advtg_gr_acquire_frame(void* h, int slot, int* out_w, int* out_h,
                                                     int* out_stride, double* out_x0,
                                                     double* out_x1, double* out_y0,
                                                     double* out_y1, std::uint64_t* out_seq) {
    return h ? static_cast<NativeGraphRenderer*>(h)->acquire_frame(
                   slot, out_w, out_h, out_stride, out_x0, out_x1, out_y0, out_y1, out_seq)
             : nullptr;
}
#endif  // ADVTG_WITH_RENDERER
