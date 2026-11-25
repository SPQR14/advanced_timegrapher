// C API exports for advtg::core::metrics, adc_clock, and advtg::analysis::gear_train.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <string>
#include <vector>

#include "advtg_api.hpp"
#include "advtg_detector.hpp"
#include "advtg_metrics.hpp"

using advtg::core::NativeAdcDrift;
using advtg::core::NativeBeat;
using advtg::core::NativeMetricsConfig;
using advtg::core::NativeMetricsResult;
using advtg::core::NativeParityFit;
using advtg::core::NativeTemplateInput;
using advtg::core::NativeWindowRate;

ADVTG_API int advtg_fit_parity_weighted(const double* k, const double* t, std::size_t n,
                                        double outlier_sigma, int max_iterations,
                                        int min_per_parity, int weighted, int quadratic,
                                        NativeParityFit* out) {
    if (!out) return -1;
    try {
        *out = advtg::core::fit_parity_weighted_cpp(k, t, n, outlier_sigma, max_iterations,
                                                    min_per_parity, weighted != 0, quadratic != 0);
        return out->valid ? 0 : 1;
    } catch (...) {
        return -1;
    }
}

namespace {
thread_local std::vector<NativeWindowRate> g_metrics_wins;
thread_local std::string g_metrics_warns;
}  // namespace

ADVTG_API int advtg_compute_metrics(const NativeBeat* beats, std::size_t n_beats,
                                    const NativeTemplateInput* tpl0,
                                    const NativeTemplateInput* tpl1,
                                    const NativeMetricsConfig* cfg, NativeMetricsResult* out,
                                    std::int64_t* n_wins_out, std::int32_t* warn_bytes_out) {
    if (!cfg || !out || !tpl0 || !tpl1) return -1;
    try {
        std::vector<std::string> warns;
        advtg::core::compute_metrics_cpp(beats, n_beats, *tpl0, *tpl1, *cfg, *out, g_metrics_wins,
                                         warns);
        g_metrics_warns.clear();
        for (std::size_t i = 0; i < warns.size(); ++i) {
            if (i) g_metrics_warns.push_back('\n');
            g_metrics_warns += warns[i];
        }
        if (n_wins_out) *n_wins_out = static_cast<std::int64_t>(g_metrics_wins.size());
        if (warn_bytes_out) *warn_bytes_out = static_cast<std::int32_t>(g_metrics_warns.size());
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::int64_t advtg_take_metrics_extra(NativeWindowRate* wins_out, std::size_t cap_wins,
                                                char* warn_buf, std::int32_t cap_warn) {
    const std::size_t nw = std::min(cap_wins, g_metrics_wins.size());
    if (nw && wins_out) std::memcpy(wins_out, g_metrics_wins.data(), nw * sizeof(NativeWindowRate));
    if (warn_buf && cap_warn > 0) {
        const std::size_t nch =
            std::min<std::size_t>(g_metrics_warns.size(), static_cast<std::size_t>(cap_warn - 1));
        if (nch) std::memcpy(warn_buf, g_metrics_warns.data(), nch);
        warn_buf[nch] = '\0';
    }
    g_metrics_wins.clear();
    g_metrics_warns.clear();
    return static_cast<std::int64_t>(nw);
}

ADVTG_API std::size_t advtg_lower_envelope(const std::int64_t* end_indices,
                                           const std::int64_t* host_ns, std::size_t n,
                                           double sample_rate, double bin_s, std::int64_t* out_n,
                                           std::int64_t* out_t) {
    try {
        return advtg::core::lower_envelope_cpp(end_indices, host_ns, n, sample_rate, bin_s, out_n,
                                               out_t);
    } catch (...) {
        return 0;
    }
}

ADVTG_API int advtg_fit_envelope(const std::int64_t* end_indices, const std::int64_t* host_ns,
                                 std::size_t n, double sample_rate, int host_level,
                                 double host_sigma_ppm, double jump_ms, double outlier_sigma,
                                 int min_points, int min_segment, double min_span_s,
                                 NativeAdcDrift* out) {
    if (!out) return -1;
    try {
        *out = advtg::core::fit_envelope_cpp(end_indices, host_ns, n, sample_rate, host_level,
                                             host_sigma_ppm, jump_ms, outlier_sigma, min_points,
                                             min_segment, min_span_s);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API int advtg_drift_from_ticks(const double* ticks, std::size_t n, double sample_rate,
                                     double nominal_period_s, double reference_ppm,
                                     double reference_sigma_ppm, double outlier_sigma,
                                     int min_ticks, NativeAdcDrift* out) {
    if (!out) return -1;
    try {
        *out = advtg::core::drift_from_ticks_cpp(ticks, n, sample_rate, nominal_period_s,
                                                 reference_ppm, reference_sigma_ppm, outlier_sigma,
                                                 min_ticks);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::size_t advtg_detect_pps_edges(const double* samples, std::size_t n,
                                             double sample_rate, std::int64_t start_index,
                                             int has_threshold, double threshold, int polarity,
                                             double min_interval_s, double* out_edges) {
    try {
        return advtg::core::detect_pps_edges_cpp(samples, n, sample_rate, start_index,
                                                 has_threshold, threshold, polarity, min_interval_s,
                                                 out_edges);
    } catch (...) {
        return 0;
    }
}

ADVTG_API void advtg_teager_kaiser(const double* x, std::size_t n, double* out) {
    try {
        advtg::analysis::teager_kaiser_cpp(x, n, out);
    } catch (...) {
    }
}

ADVTG_API void advtg_lomb_scargle(const double* t, const double* y, std::size_t n_t,
                                  const double* freqs, std::size_t n_f, double* out) {
    try {
        advtg::analysis::lomb_scargle_cpp(t, y, n_t, freqs, n_f, out);
    } catch (...) {
    }
}
