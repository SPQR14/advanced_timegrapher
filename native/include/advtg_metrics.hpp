// Native C++20 Metrics (core/metrics.py), ADC Clock (core/adc_clock.py), and
// Multi-threaded SIMD Gear-Train Lomb-Scargle / Teager-Kaiser (analysis/gear_train.py).
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_detector.hpp"

namespace advtg::core {

struct NativeParityFit {
    double a = kNaN;
    double period = kNaN;
    double c = kNaN;
    double period_sigma = kNaN;
    double c_sigma = kNaN;
    double k_ref = kNaN;
    double sigma_even = kNaN;
    double sigma_odd = kNaN;
    double q = kNaN;
    double q_sigma = kNaN;
    std::int64_t n_even = 0;
    std::int64_t n_odd = 0;
    std::int64_t n_trimmed = 0;
    std::int32_t valid = 0;
    std::int32_t _pad = 0;
};

struct NativeWindowRate {
    double start_s = 0.0;
    double end_s = 0.0;
    double center_s = 0.0;
    double rate_s_per_day = kNaN;
    double rate_sigma_s_per_day = kNaN;
    double rate_uncertainty_s_per_day = kNaN;
    std::int64_t n_beats = 0;
};

struct NativeMetricsConfig {
    double sample_rate = 48000.0;
    double bph = 21600.0;
    double lift_angle_deg = 52.0;  // used when has_lift_angle == 1 else 52.0
    double outlier_sigma = 5.0;
    double window_s = 10.0;
    double window_step_s = 0.0;  // <= 0 -> window_s
    double trend_sigma = 3.0;
    double trend_min_s_per_day_per_min = 0.1;
    double trend_min_change_s_per_day = 7.0;
    double amp_lo_deg = 90.0;
    double amp_hi_deg = 400.0;
    double amplitude_mismatch_deg = 20.0;
    double drop_sharpness_ratio = 2.0;
    double adc_drift_ppm = 0.0;
    double adc_drift_sigma_ppm = 0.0;
    double be_switch_ratio = 1.0 / 1.6;
    double p1_keep_ratio = 0.9;
    double p1_min_share = 0.8;
    std::int32_t has_lift_angle = 0;
    std::int32_t quartz = 0;
    std::int32_t min_beats_per_parity = 10;
    std::int32_t max_iterations = 10;
    std::int32_t trend_blocks = 6;
    std::int32_t p1_timing = 1;
    std::int32_t prev_p0_p1 = 0;  // previous_points[0] == "p1"
    std::int32_t prev_p1_p1 = 0;  // previous_points[1] == "p1"
    std::int32_t compute_windows = 1;
    std::int32_t _pad = 0;
};

struct NativeMetricsResult {
    std::int64_t n_beats = 0;
    std::int64_t n_used_even = 0;
    std::int64_t n_used_odd = 0;
    std::int64_t n_excluded = 0;
    double excluded_fraction = 0.0;
    double rate_s_per_day = kNaN;
    double rate_sigma_s_per_day = kNaN;
    double rate_sigma_block_s_per_day = kNaN;
    double rate_uncertainty_s_per_day = kNaN;
    double rate_clock_sigma_s_per_day = 0.0;
    double beat_error_ms = kNaN;
    double beat_error_sigma_ms = kNaN;
    double beat_error_sigma_block_ms = kNaN;
    double beat_error_uncertainty_ms = kNaN;
    double beat_error_p3_ms = kNaN;
    double amplitude_deg = kNaN;
    double amplitude_tic_deg = kNaN;
    double amplitude_tac_deg = kNaN;
    double amplitude_la_uncertainty_deg = kNaN;
    double lift_angle_deg = 52.0;
    double sigma_even_us = kNaN;
    double sigma_odd_us = kNaN;
    double pair_sigma_even_us = kNaN;
    double pair_sigma_odd_us = kNaN;
    double rate_trend_s_per_day_per_min = kNaN;
    double rate_trend_sigma_s_per_day_per_min = kNaN;
    double window_sigma_factor = 1.0;
    double span_s = kNaN;
    double confidence = 0.0;
    std::int32_t rate_calibrated = 0;
    std::int32_t rate_point_0_p1 = 0;   // 0="t_fine", 1="p1"
    std::int32_t rate_point_1_p1 = 0;
    std::int32_t beat_error_point = 0;  // 0="—", 1="p1", 2="p1_beat", 3="p1_fine", 4="t_fine"
    std::int32_t amplitude_parity = 0;  // 0="both", 1="tic", 2="tac"
    std::int32_t amplitude_indicative = 0;
    std::int32_t rate_trend_flag = 0;
    std::int32_t _pad = 0;
};

struct NativeTemplateInput {
    std::int32_t valid = 0;
    std::int32_t _pad = 0;
    double onset_p1 = kNaN;
    double onset_p3 = kNaN;
    double onset_ref = kNaN;
    const double* env = nullptr;
    std::size_t env_len = 0;
};

NativeParityFit fit_parity_weighted_cpp(const double* k, const double* t, std::size_t n,
                                        double outlier_sigma, int max_iterations,
                                        int min_per_parity, bool weighted, bool quadratic);

void compute_metrics_cpp(const NativeBeat* beats, std::size_t n_beats,
                         const NativeTemplateInput& tpl0, const NativeTemplateInput& tpl1,
                         const NativeMetricsConfig& cfg, NativeMetricsResult& out,
                         std::vector<NativeWindowRate>& windows_out,
                         std::vector<std::string>& warnings_out);

// ------------------------------------------------------------------ ADC clock (core/adc_clock.py)
struct NativeAdcDrift {
    double ppm = 0.0;
    double sigma_ppm = 0.0;
    double systematic_ppm = 30.0;
    double span_s = 0.0;
    double residual_us = kNaN;
    std::int64_t n_points = 0;
    std::int64_t n_segments = 0;
    std::int32_t level = 0;     // 0=UNCALIBRATED, 1=HOST_RAW, 2=HOST_DISCIPLINED, 3=EXTERNAL
    std::int32_t method = 0;    // 0="none", 1="host-envelope", 2="reference", 3="manual"
    std::int32_t note_kind = 0; // 0=none, 1=few_points, 2=too_many_jumps, 3=short_span, 4=too_many_outliers, 5=multi_seg
    std::int32_t _pad = 0;
};

std::size_t lower_envelope_cpp(const std::int64_t* end_indices, const std::int64_t* host_ns,
                               std::size_t n, double sample_rate, double bin_s,
                               std::int64_t* out_n, std::int64_t* out_t);

NativeAdcDrift fit_envelope_cpp(const std::int64_t* end_indices, const std::int64_t* host_ns,
                                std::size_t n, double sample_rate, int host_level,
                                double host_sigma_ppm, double jump_ms, double outlier_sigma,
                                int min_points, int min_segment, double min_span_s);

NativeAdcDrift drift_from_ticks_cpp(const double* ticks, std::size_t n, double sample_rate,
                                    double nominal_period_s, double reference_ppm,
                                    double reference_sigma_ppm, double outlier_sigma,
                                    int min_ticks);

std::size_t detect_pps_edges_cpp(const double* samples, std::size_t n, double sample_rate,
                                 std::int64_t start_index, int has_threshold, double threshold,
                                 int polarity, double min_interval_s, double* out_edges);

}  // namespace advtg::core

namespace advtg::analysis {

void teager_kaiser_cpp(const double* x, std::size_t n, double* out);
void lomb_scargle_cpp(const double* t, const double* y, std::size_t n_t, const double* freqs,
                      std::size_t n_f, double* out);

}  // namespace advtg::analysis
