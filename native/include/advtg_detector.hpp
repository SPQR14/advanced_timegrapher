// Native BeatDetector and PpsDetector declarations (C++20 counterparts of core/beat_detector.py
// and core/pps.py). Exact sample-grid chunk invariance, float64 precision, SIMD median templates.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "advtg_bits.hpp"

namespace advtg::core {

enum BeatFlags : std::uint32_t {
    BEAT_NONE = 0U,
    BEAT_P1_MISSING = 1U,
    BEAT_P2_MISSING = 2U,
    BEAT_P3_MISSING = 4U,
    BEAT_WEAK = 8U,
    BEAT_SATURATED = 16U,
    BEAT_TIMING_OUTLIER = 32U,
    BEAT_AFTER_GAP = 64U,
};

struct NativeBeat {
    std::int64_t k = 0;
    double p1 = kNaN;
    double p2 = kNaN;
    double p3 = kNaN;
    double peak_dbfs = -240.0;
    double snr_db = 0.0;
    double residual_samples = 0.0;
    double confidence = 0.0;
    double t_fine = kNaN;
    double t_p1 = kNaN;
    double t_p1_tpl = kNaN;
    std::int32_t n_subevents = 0;
    std::uint32_t flags = 0;
};

struct NativeDetectorConfig {
    const double* sos = nullptr;
    const double* zi = nullptr;
    std::int64_t padlen = 0;

    double sample_rate = 48000.0;
    double bph = 21600.0;
    double lift_angle_deg = 52.0;
    double amp_lo_deg = 90.0;
    double amp_hi_deg = 400.0;
    double band_lo_hz = 1000.0;
    double band_hi_hz = 15000.0;
    double envelope_smooth_ms = 0.15;
    double grid_step_s = 0.25;
    double noise_window_s = 1.0;
    double threshold_hi = 6.0;
    double threshold_lo = 3.0;
    double ripple_merge_ms = 0.3;
    double min_subevent_spacing_ms = 1.0;
    double split_ratio = 2.0;
    double onset_fraction = 0.4;
    double subevent_rel_threshold = 0.05;
    double min_anchor_snr_db = 12.0;
    double min_subevent_snr_db = 12.0;
    double weak_snr_db = 20.0;
    double phase_tolerance = 0.1;
    double period_search = 0.06;
    double acquisition_s = 2.0;
    double acquisition_min_fraction = 0.6;
    double outlier_sigma = 5.0;
    double outlier_min_ms = 0.3;
    double lift_margin = 0.05;
    double lift_track_tolerance = 0.15;
    double typical_amp_lo_deg = 200.0;
    double typical_amp_hi_deg = 330.0;
    double saturation_level = 0.99;
    double dropout_min_ms = 10.0;
    double period_warn = 0.01;
    double post_group_ms = 6.0;
    double template_search_ms = 1.5;
    double template_post_ms = 6.0;
    double template_p3_fraction = 0.2;
    double template_lobe_fraction = 0.4;
    double template_burst_ms = 2.5;
    double template_p1_fraction = 0.04;
    double template_p1_min_ms = 0.1;
    double template_min_corr = 0.5;
    double template_align_smooth_ms = 1.0;
    double template_front_fraction = 0.5;
    double template_front_lift_fraction = 0.4;
    double template_amp_mismatch_deg = 20.0;
    double template_refresh_min_s = 0.5;
    double template_reanchor_corr = 0.8;
    double quartz_template_ms = 25.0;
    double quartz_outlier_min_ms = 1.0;
    double quartz_onset_snr_db = 26.0;
    double quartz_onset_beat_snr_db = 20.0;
    double quartz_onset_sigma = 6.0;
    double quartz_onset_lowpass_hz = 9000.0;
    double interference_ms = 40.0;
    double interference_excess_db = 12.0;
    double interference_max_db = 40.0;
    double interference_history_s = 2.0;
    double interference_percentile = 25.0;
    double p1_onset_sigma = 6.0;
    double p1_onset_fraction = 0.4;
    double p1_onset_back_ms = 5.0;
    double p1_onset_fwd_ms = 3.0;
    double p1_onset_base_ms = 5.0;
    double p1_onset_min_ms = 0.1;
    double p1_onset_peak_ms = 0.5;

    std::int32_t n_sec = 0;
    std::int32_t filter_order = 4;
    std::int32_t model_beats = 16;
    std::int32_t max_missed = 8;
    std::int32_t template_beats = 24;
    std::int32_t template_min_beats = 6;
    std::int32_t template_refresh_beats = 2;
    std::int32_t template_anchor_mode = -1;  // -1 = None (auto), 0 = False, 1 = True
    std::int32_t quartz = 0;
    std::int32_t quartz_reseed_beats = 3;
    std::int32_t quartz_onset = 1;
    std::int32_t interference_filter = 1;
    std::int32_t p1_onset = 1;
    std::int32_t normalize = 0;
};

struct NativeDetectorStats {
    std::int64_t samples_processed = 0;
    std::int64_t beats_emitted = 0;
    std::int64_t missed_beats = 0;
    std::int64_t gap_beats = 0;
    std::int64_t gaps = 0;
    std::int64_t rejected_events = 0;
    std::int64_t relocks = 0;
    std::int64_t normalizer_clipped_samples = 0;
    std::int64_t expected_index = 0;
    std::int64_t norm_next_index = 0;
    double lock_sample = 0.0;
    double lock_time_s = 0.0;
    double p1_fraction = 0.0;
    double p2_fraction = 0.0;
    double p3_fraction = 0.0;
    double period_samples = kNaN;
    double nominal_period_samples = 0.0;
    double interference_db = 0.0;
    double interference_hz = kNaN;
    double normalizer_gain_db = 0.0;
    std::int32_t has_lock_sample = 0;
    std::int32_t locked = 0;
    std::int32_t has_normalizer = 0;
    std::int32_t started = 0;
    std::int32_t finished = 0;
    std::int32_t has_norm_next = 0;
};

struct NativeTemplateHeader {
    double onset_p1 = kNaN;
    double onset_p3 = kNaN;
    double onset_ref = kNaN;
    std::int32_t valid = 0;
    std::int32_t parity = 0;
    std::int32_t n_beats = 0;
    std::int32_t length = 0;
    std::int32_t p3_is_frente = 0;  // 0 = "nivel", 1 = "frente"
    std::int32_t has_anchor = 0;
};

class BeatDetector {
public:
    explicit BeatDetector(const NativeDetectorConfig& cfg);
    ~BeatDetector();
    BeatDetector(const BeatDetector&) = delete;
    BeatDetector& operator=(const BeatDetector&) = delete;

    void reset();
    void process(const double* chunk, std::size_t n, std::int64_t start_index,
                 std::vector<NativeBeat>& out);
    void flush(std::vector<NativeBeat>& out);

    NativeDetectorStats stats() const;
    const std::vector<std::string>& warnings() const;
    void templates(NativeTemplateHeader& hdr0, std::vector<double>& env0,
                   NativeTemplateHeader& hdr1, std::vector<double>& env1) const;
    void quartz_timing(std::int32_t& q0, std::int32_t& q1) const;
    std::int64_t stream_start_hint() const;  // next index expected from raw or normalizer

private:
    struct Impl;
    Impl* impl_;
};

// ------------------------------------------------------------------ PPS detector (core/pps.py)
struct NativePpsPulse {
    double index = 0.0;
    double snr_db = 0.0;
    double peak = 0.0;
};

class PpsDetector {
public:
    PpsDetector(double sample_rate, double threshold_sigma = 8.0);
    ~PpsDetector();
    PpsDetector(const PpsDetector&) = delete;
    PpsDetector& operator=(const PpsDetector&) = delete;

    void reset(std::int64_t start_index);
    void process(const double* chunk, std::size_t n, std::int64_t start_index,
                 std::vector<NativePpsPulse>& out);
    std::int64_t rejected() const;

private:
    struct Impl;
    Impl* impl_;
};

}  // namespace advtg::core
