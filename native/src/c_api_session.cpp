// C API of the native MeasurementSession and of the filter design (advtg.h).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <exception>
#include <limits>
#include <string>

#include "advtg.h"
#include "advtg_dsp.hpp"
#include "advtg_session.hpp"

using advtg::session::MeasurementSession;

// ------------------------------------------------------------------ layout checks (ABI)
#define ADVTG_SAME_FIELD(C, CPP, f) static_assert(offsetof(C, f) == offsetof(CPP, f), #C "." #f)
static_assert(sizeof(advtg_beat) == sizeof(advtg::core::NativeBeat));
ADVTG_SAME_FIELD(advtg_beat, advtg::core::NativeBeat, k);
ADVTG_SAME_FIELD(advtg_beat, advtg::core::NativeBeat, p3);
ADVTG_SAME_FIELD(advtg_beat, advtg::core::NativeBeat, t_p1_tpl);
ADVTG_SAME_FIELD(advtg_beat, advtg::core::NativeBeat, n_subevents);
ADVTG_SAME_FIELD(advtg_beat, advtg::core::NativeBeat, flags);
static_assert(sizeof(advtg_metrics) == sizeof(advtg::core::NativeMetricsResult));
ADVTG_SAME_FIELD(advtg_metrics, advtg::core::NativeMetricsResult, excluded_fraction);
ADVTG_SAME_FIELD(advtg_metrics, advtg::core::NativeMetricsResult, beat_error_p3_ms);
ADVTG_SAME_FIELD(advtg_metrics, advtg::core::NativeMetricsResult, confidence);
ADVTG_SAME_FIELD(advtg_metrics, advtg::core::NativeMetricsResult, rate_calibrated);
ADVTG_SAME_FIELD(advtg_metrics, advtg::core::NativeMetricsResult, rate_trend_flag);
static_assert(sizeof(advtg_adc_drift) == sizeof(advtg::core::NativeAdcDrift));
ADVTG_SAME_FIELD(advtg_adc_drift, advtg::core::NativeAdcDrift, residual_us);
ADVTG_SAME_FIELD(advtg_adc_drift, advtg::core::NativeAdcDrift, n_segments);
ADVTG_SAME_FIELD(advtg_adc_drift, advtg::core::NativeAdcDrift, note_kind);
static_assert(sizeof(advtg_window_rate) == sizeof(advtg::core::NativeWindowRate));
ADVTG_SAME_FIELD(advtg_window_rate, advtg::core::NativeWindowRate, n_beats);
static_assert(sizeof(advtg_detector_stats) == sizeof(advtg::core::NativeDetectorStats));
ADVTG_SAME_FIELD(advtg_detector_stats, advtg::core::NativeDetectorStats, norm_next_index);
ADVTG_SAME_FIELD(advtg_detector_stats, advtg::core::NativeDetectorStats, normalizer_gain_db);
ADVTG_SAME_FIELD(advtg_detector_stats, advtg::core::NativeDetectorStats, has_norm_next);
#undef ADVTG_SAME_FIELD

struct advtg_session {
    MeasurementSession impl;
    std::string error;
    advtg_session(const advtg_session_config& c, advtg::runtime::NativeSampleStream* s)
        : impl(c, s) {}
};

namespace {
thread_local std::string g_error;

void set_error(advtg_session* s, const char* what) {
    g_error = what ? what : "unknown error";
    if (s) s->error = g_error;
}

std::int32_t copy_text(const std::vector<std::string>& lines, char* buf, std::int32_t cap) {
    std::string joined;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        if (i) joined.push_back('\n');
        joined += lines[i];
    }
    if (buf && cap > 0) {
        const std::size_t n = std::min<std::size_t>(joined.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, joined.data(), n);
        buf[n] = '\0';
    }
    return static_cast<std::int32_t>(joined.size());
}

template <class C, class T>
std::int64_t copy_items(const C& src, T* out, std::int64_t cap) {
    const auto n = static_cast<std::int64_t>(src.size());
    if (out && cap > 0) {
        const auto m = std::min(n, cap);
        std::copy_n(src.begin(), m, out);
    }
    return n;
}
}  // namespace

extern "C" {

ADVTG_C_API void advtg_session_config_default(advtg_session_config* cfg, double sample_rate,
                                              double bph) {
    if (!cfg) return;
    std::memset(cfg, 0, sizeof *cfg);
    cfg->sample_rate = sample_rate;
    cfg->bph = bph;
    cfg->lift_angle_deg = std::numeric_limits<double>::quiet_NaN();
    cfg->span_s = 30.0;
    cfg->window_s = 10.0;
    cfg->display_pre_ms = 3.0;
    cfg->display_post_ms = 10.0;
#if defined(_WIN32)
    cfg->wall_clock_sigma_ppm = 2.0;  // W32Time is coarse by default (adc_clock.host_clock)
#else
    cfg->wall_clock_sigma_ppm = 1.0;
#endif
    cfg->mono_clock_sigma_ppm = 30.0;
    cfg->calibration.systematic_ppm = 30.0;
    cfg->calibration.residual_us = std::numeric_limits<double>::quiet_NaN();
    cfg->live_clock = 1;
    cfg->wall_clock_disciplined = 1;
    cfg->mono_clock_disciplined = 0;
    cfg->stream_capacity = 0;
}

ADVTG_C_API advtg_session* advtg_session_create(const advtg_session_config* cfg, void* stream) {
    if (!cfg) {
        set_error(nullptr, "config is NULL");
        return nullptr;
    }
    try {
        return new advtg_session(*cfg, static_cast<advtg::runtime::NativeSampleStream*>(stream));
    } catch (const std::exception& e) {
        set_error(nullptr, e.what());
    } catch (...) {
        set_error(nullptr, "unknown error");
    }
    return nullptr;
}

ADVTG_C_API void advtg_session_destroy(advtg_session* s) { delete s; }

ADVTG_C_API void* advtg_session_stream(advtg_session* s) { return s ? s->impl.stream() : nullptr; }

ADVTG_C_API int advtg_session_update(advtg_session* s, advtg_snapshot* out) {
    if (!s || !out) return -1;
    try {
        s->impl.update(*out);
        return 0;
    } catch (const std::exception& e) {
        set_error(s, e.what());
    } catch (...) {
        set_error(s, "unknown error");
    }
    return -2;
}

ADVTG_C_API int64_t advtg_session_waveform(const advtg_session* s, double* out, int64_t cap) {
    return s ? copy_items(s->impl.waveform(), out, cap) : -1;
}

ADVTG_C_API int64_t advtg_session_tape(const advtg_session* s, double* time_s, double* offset_ms,
                                       int8_t* parity, int64_t cap) {
    if (!s) return -1;
    const auto n = static_cast<std::int64_t>(s->impl.tape_time_s().size());
    if (cap > 0) {
        const auto m = static_cast<std::size_t>(std::min(n, cap));
        if (time_s) std::copy_n(s->impl.tape_time_s().begin(), m, time_s);
        if (offset_ms) std::copy_n(s->impl.tape_offset_ms().begin(), m, offset_ms);
        if (parity) std::copy_n(s->impl.tape_parity().begin(), m, parity);
    }
    return n;
}

ADVTG_C_API int64_t advtg_session_new_beats(const advtg_session* s, advtg_beat* out, int64_t cap) {
    if (!s) return -1;
    const auto& v = s->impl.new_beats();
    const auto n = static_cast<std::int64_t>(v.size());
    if (out && cap > 0) std::memcpy(out, v.data(), static_cast<std::size_t>(std::min(n, cap)) * sizeof(advtg_beat));
    return n;
}

ADVTG_C_API int64_t advtg_session_window_beats(const advtg_session* s, advtg_beat* out,
                                               int64_t cap) {
    if (!s) return -1;
    const auto& v = s->impl.window_beats();
    const auto n = static_cast<std::int64_t>(v.size());
    if (out && cap > 0) {
        const auto m = std::min(n, cap);
        for (std::int64_t i = 0; i < m; ++i)
            std::memcpy(out + i, &v[static_cast<std::size_t>(i)], sizeof(advtg_beat));
    }
    return n;
}

ADVTG_C_API int64_t advtg_session_metric_windows(const advtg_session* s, advtg_window_rate* out,
                                                 int64_t cap) {
    if (!s) return -1;
    const auto& v = s->impl.metric_windows();
    const auto n = static_cast<std::int64_t>(v.size());
    if (out && cap > 0)
        std::memcpy(out, v.data(), static_cast<std::size_t>(std::min(n, cap)) * sizeof(advtg_window_rate));
    return n;
}

ADVTG_C_API int32_t advtg_session_metric_warnings(const advtg_session* s, char* buf, int32_t cap) {
    return s ? copy_text(s->impl.metric_warnings(), buf, cap) : -1;
}

ADVTG_C_API int32_t advtg_session_detector_warnings(const advtg_session* s, char* buf,
                                                    int32_t cap) {
    return s ? copy_text(s->impl.detector_warnings(), buf, cap) : -1;
}

ADVTG_C_API int32_t advtg_session_metric_warning_codes(const advtg_session* s,
                                                       advtg_warning* out, int32_t cap) {
    return s ? static_cast<int32_t>(copy_items(s->impl.metric_warning_codes(), out, cap)) : -1;
}

ADVTG_C_API int32_t advtg_session_detector_warning_codes(const advtg_session* s,
                                                         advtg_warning* out, int32_t cap) {
    return s ? static_cast<int32_t>(copy_items(s->impl.detector_warning_codes(), out, cap)) : -1;
}

ADVTG_C_API int advtg_session_detector_stats(const advtg_session* s, advtg_detector_stats* out) {
    if (!s || !out) return -1;
    const auto st = s->impl.detector_stats();
    std::memcpy(out, &st, sizeof *out);
    return 0;
}

ADVTG_C_API void advtg_session_display_window(const advtg_session* s, int64_t* pre, int64_t* len) {
    if (!s) return;
    if (pre) *pre = s->impl.display_pre();
    if (len) *len = s->impl.display_len();
}

ADVTG_C_API const char* advtg_session_last_error(const advtg_session* s) {
    return s ? s->error.c_str() : g_error.c_str();
}

// ------------------------------------------------------------------ filter design
ADVTG_C_API int advtg_butter_bandpass_sos(int order, double lo_hz, double hi_hz, double fs,
                                          double* sos, int cap) {
    try {
        const auto v = advtg::dsp::butter_bandpass_sos(order, lo_hz, hi_hz, fs);
        if (!sos || cap < static_cast<int>(v.size())) return -2;
        std::copy(v.begin(), v.end(), sos);
        return static_cast<int>(v.size() / 6);
    } catch (...) {
        return -1;
    }
}

ADVTG_C_API int advtg_sosfilt_zi(const double* sos, int n_sec, double* zi) {
    if (!sos || !zi || n_sec < 1) return -1;
    try {
        const auto v = advtg::dsp::sosfilt_zi(sos, n_sec);
        std::copy(v.begin(), v.end(), zi);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_C_API int64_t advtg_sosfiltfilt_padlen(const double* sos, int n_sec) {
    return (sos && n_sec > 0) ? advtg::dsp::sosfiltfilt_padlen(sos, n_sec) : -1;
}

}  // extern "C"
