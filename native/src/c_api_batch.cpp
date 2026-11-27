// Public C API for batch BeatDetector and batch Metrics (advtg.h).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <string>
#include <vector>

#include "advtg.h"
#include "advtg_detector.hpp"
#include "advtg_dsp.hpp"
#include "advtg_metrics.hpp"

using advtg::core::BeatDetector;
using advtg::core::NativeBeat;
using advtg::core::NativeDetectorConfig;
using advtg::core::NativeDetectorStats;
using advtg::core::NativeMetricsConfig;
using advtg::core::NativeMetricsResult;
using advtg::core::NativeTemplateHeader;
using advtg::core::NativeTemplateInput;
using advtg::core::NativeWindowRate;

struct advtg_detector {
    std::vector<double> sos;
    std::vector<double> zi;
    std::unique_ptr<BeatDetector> impl;
    std::vector<NativeBeat> last_beats;
};

namespace {

thread_local std::vector<NativeWindowRate> g_batch_windows;
thread_local std::vector<std::string> g_batch_warnings;
thread_local std::vector<advtg_warning> g_batch_warning_codes;

static_assert(sizeof(advtg_beat) == sizeof(NativeBeat));
static_assert(sizeof(advtg_detector_stats) == sizeof(NativeDetectorStats));
static_assert(sizeof(advtg_metrics) == sizeof(NativeMetricsResult));
static_assert(sizeof(advtg_window_rate) == sizeof(NativeWindowRate));

int32_t copy_warn_text(const std::vector<std::string>& lines, char* buf, int32_t cap) {
    std::string joined;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        if (i) joined.push_back('\n');
        joined += lines[i];
    }
    if (buf && cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(joined.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, joined.data(), n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(joined.size());
}

template <class T>
int64_t copy_items(const std::vector<T>& src, T* out, int64_t cap) {
    const auto n = static_cast<int64_t>(src.size());
    if (out && cap > 0) {
        const auto m = static_cast<std::size_t>(std::min(n, cap));
        if (m) std::memcpy(out, src.data(), m * sizeof(T));
    }
    return n;
}

}  // namespace

extern "C" {

ADVTG_C_API void advtg_detector_config_default(advtg_detector_config* cfg, double sample_rate,
                                               double bph) {
    if (!cfg) return;
    std::memset(cfg, 0, sizeof *cfg);
    cfg->sample_rate = sample_rate;
    cfg->bph = bph;
    cfg->lift_angle_deg = 52.0;
    cfg->quartz = 0;
    cfg->normalize = 0;
}

ADVTG_C_API advtg_detector* advtg_detector_create(const advtg_detector_config* cfg) {
    if (!cfg) return nullptr;
    if (!(std::isfinite(cfg->sample_rate) && cfg->sample_rate > 0.0)) return nullptr;
    if (!(std::isfinite(cfg->bph) && cfg->bph > 0.0)) return nullptr;
    const double lift =
        (std::isfinite(cfg->lift_angle_deg) && cfg->lift_angle_deg > 0.0)
            ? cfg->lift_angle_deg
            : 52.0;
    if (!(lift > 0.0 && lift < 90.0)) return nullptr;
    try {
        auto det = std::make_unique<advtg_detector>();
        NativeDetectorConfig dc{};
        dc.sample_rate = cfg->sample_rate;
        dc.bph = cfg->bph;
        dc.lift_angle_deg = lift;
        dc.quartz = cfg->quartz ? 1 : 0;
        dc.normalize = cfg->normalize ? 1 : 0;
        dc.band_lo_hz = 1000.0;
        dc.band_hi_hz = std::min(15000.0, 0.45 * cfg->sample_rate);
        det->sos = advtg::dsp::butter_bandpass_sos(dc.filter_order, dc.band_lo_hz, dc.band_hi_hz,
                                                   cfg->sample_rate);
        const int n_sec = static_cast<int>(det->sos.size() / 6);
        det->zi = advtg::dsp::sosfilt_zi(det->sos.data(), n_sec);
        dc.sos = det->sos.data();
        dc.zi = det->zi.data();
        dc.n_sec = n_sec;
        dc.padlen = advtg::dsp::sosfiltfilt_padlen(det->sos.data(), n_sec);
        det->impl = std::make_unique<BeatDetector>(dc);
        return det.release();
    } catch (...) {
        return nullptr;
    }
}

ADVTG_C_API void advtg_detector_destroy(advtg_detector* d) { delete d; }

ADVTG_C_API void advtg_detector_reset(advtg_detector* d) {
    if (!d) return;
    d->last_beats.clear();
    d->impl->reset();
}

ADVTG_C_API int64_t advtg_detector_process(advtg_detector* d, const double* chunk, size_t n,
                                           int64_t start_index, advtg_beat* out, int64_t cap) {
    if (!d || (!chunk && n > 0)) return ADVTG_ERR_INVALID;
    try {
        d->last_beats.clear();
        d->impl->process(chunk, n, start_index, d->last_beats);
        const auto m = static_cast<int64_t>(d->last_beats.size());
        if (out && cap > 0) {
            const auto k = static_cast<std::size_t>(std::min(m, cap));
            if (k) std::memcpy(out, d->last_beats.data(), k * sizeof(advtg_beat));
        }
        return m;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int64_t advtg_detector_flush(advtg_detector* d, advtg_beat* out, int64_t cap) {
    if (!d) return ADVTG_ERR_INVALID;
    try {
        d->last_beats.clear();
        d->impl->flush(d->last_beats);
        const auto m = static_cast<int64_t>(d->last_beats.size());
        if (out && cap > 0) {
            const auto k = static_cast<std::size_t>(std::min(m, cap));
            if (k) std::memcpy(out, d->last_beats.data(), k * sizeof(advtg_beat));
        }
        return m;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int64_t advtg_detector_last_beats(const advtg_detector* d, advtg_beat* out,
                                              int64_t cap) {
    if (!d) return ADVTG_ERR_INVALID;
    const auto m = static_cast<int64_t>(d->last_beats.size());
    if (out && cap > 0) {
        const auto k = static_cast<std::size_t>(std::min(m, cap));
        if (k) std::memcpy(out, d->last_beats.data(), k * sizeof(advtg_beat));
    }
    return m;
}

ADVTG_C_API int advtg_detector_stats_get(const advtg_detector* d, advtg_detector_stats* out) {
    if (!d || !out) return ADVTG_ERR_INVALID;
    const NativeDetectorStats st = d->impl->stats();
    std::memcpy(out, &st, sizeof *out);
    return ADVTG_OK;
}

ADVTG_C_API int32_t advtg_detector_warnings(const advtg_detector* d, char* buf, int32_t cap) {
    return d ? copy_warn_text(d->impl->warnings(), buf, cap) : ADVTG_ERR_INVALID;
}

ADVTG_C_API int32_t advtg_detector_warning_codes(const advtg_detector* d, advtg_warning* out,
                                                 int32_t cap) {
    if (!d) return ADVTG_ERR_INVALID;
    return static_cast<int32_t>(copy_items(d->impl->warning_codes(), out, cap));
}

// ------------------------------------------------------------------ batch metrics
ADVTG_C_API void advtg_metrics_config_default(advtg_metrics_config* cfg, double sample_rate,
                                              double bph) {
    if (!cfg) return;
    std::memset(cfg, 0, sizeof *cfg);
    cfg->sample_rate = sample_rate;
    cfg->bph = bph;
    cfg->lift_angle_deg = std::numeric_limits<double>::quiet_NaN();
    cfg->adc_drift_ppm = 0.0;
    cfg->adc_drift_sigma_ppm = 0.0;
    cfg->window_s = 10.0;
    cfg->window_step_s = 0.0;
    cfg->quartz = 0;
    cfg->has_lift_angle = 0;
    cfg->compute_windows = 1;
}

ADVTG_C_API int advtg_metrics_compute(const advtg_detector* det, const advtg_beat* beats,
                                      size_t n_beats, const advtg_metrics_config* cfg,
                                      advtg_metrics* out) {
    if (!cfg || !out || (!beats && n_beats > 0)) return ADVTG_ERR_INVALID;
    try {
        NativeMetricsConfig mc{};
        mc.sample_rate = cfg->sample_rate;
        mc.bph = cfg->bph;
        const bool has_la = cfg->has_lift_angle != 0 ||
                            (std::isfinite(cfg->lift_angle_deg) && cfg->lift_angle_deg > 0.0);
        mc.has_lift_angle = has_la ? 1 : 0;
        mc.lift_angle_deg = has_la ? cfg->lift_angle_deg : 52.0;
        mc.quartz = cfg->quartz ? 1 : 0;
        mc.adc_drift_ppm = cfg->adc_drift_ppm;
        mc.adc_drift_sigma_ppm = cfg->adc_drift_sigma_ppm;
        mc.window_s = cfg->window_s > 0.0 ? cfg->window_s : 10.0;
        mc.window_step_s = cfg->window_step_s;
        mc.compute_windows = cfg->compute_windows ? 1 : 0;

        NativeTemplateHeader hdr[2];
        std::vector<double> env[2];
        NativeTemplateInput tpl[2];
        if (det && det->impl) {
            det->impl->templates(hdr[0], env[0], hdr[1], env[1]);
            for (int p = 0; p < 2; ++p) {
                tpl[p] = NativeTemplateInput{};
                tpl[p].valid = hdr[p].valid;
                if (hdr[p].valid) {
                    tpl[p].onset_p1 = hdr[p].onset_p1;
                    tpl[p].onset_p3 = hdr[p].onset_p3;
                    tpl[p].onset_ref = hdr[p].onset_ref;
                    const std::size_t len = std::min<std::size_t>(
                        env[p].size(), static_cast<std::size_t>(hdr[p].length));
                    tpl[p].env = len ? env[p].data() : nullptr;
                    tpl[p].env_len = len;
                }
            }
        }

        std::vector<NativeBeat> nb(n_beats);
        for (size_t i = 0; i < n_beats; ++i) {
            nb[i].k = beats[i].k;
            nb[i].p1 = beats[i].p1;
            nb[i].p2 = beats[i].p2;
            nb[i].p3 = beats[i].p3;
            nb[i].peak_dbfs = beats[i].peak_dbfs;
            nb[i].snr_db = beats[i].snr_db;
            nb[i].residual_samples = beats[i].residual_samples;
            nb[i].confidence = beats[i].confidence;
            nb[i].t_fine = beats[i].t_fine;
            nb[i].t_p1 = beats[i].t_p1;
            nb[i].t_p1_tpl = beats[i].t_p1_tpl;
            nb[i].n_subevents = beats[i].n_subevents;
            nb[i].flags = beats[i].flags;
        }
        NativeMetricsResult res{};
        g_batch_windows.clear();
        g_batch_warnings.clear();
        g_batch_warning_codes.clear();
        advtg::core::compute_metrics_cpp(nb.data(), nb.size(), tpl[0], tpl[1], mc, res,
                                         g_batch_windows, g_batch_warnings,
                                         &g_batch_warning_codes);
        std::memcpy(static_cast<void*>(out), static_cast<const void*>(&res), sizeof *out);
        return ADVTG_OK;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int64_t advtg_metrics_last_windows(advtg_window_rate* out, int64_t cap) {
    const auto n = static_cast<int64_t>(g_batch_windows.size());
    if (out && cap > 0) {
        const auto m = static_cast<std::size_t>(std::min(n, cap));
        if (m) std::memcpy(out, g_batch_windows.data(), m * sizeof(advtg_window_rate));
    }
    return n;
}

ADVTG_C_API int32_t advtg_metrics_last_warnings(char* buf, int32_t cap) {
    return copy_warn_text(g_batch_warnings, buf, cap);
}

ADVTG_C_API int32_t advtg_metrics_last_warning_codes(advtg_warning* out, int32_t cap) {
    return static_cast<int32_t>(copy_items(g_batch_warning_codes, out, cap));
}

}  // extern "C"
