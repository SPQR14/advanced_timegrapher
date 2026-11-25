// Native MeasurementSession (core/session.py): BeatDetector + sliding metrics + live ADC clock
// over one NativeSampleStream, with no Python anywhere. This is the layer a mobile app calls
// (through the C API of advtg.h); the desktop app checks it against the Python session.
#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <string>
#include <vector>

#include "advtg.h"
#include "advtg_detector.hpp"
#include "advtg_metrics.hpp"
#include "advtg_runtime.hpp"

namespace advtg::session {

// Live lower envelope of the block stamps (core/adc_clock.py ClockDriftTracker).
class ClockDriftTracker {
public:
    ClockDriftTracker(double sample_rate, int host_level, double host_sigma_ppm,
                      double bin_s = 1.0);
    void reset();
    void update(const std::int64_t* end_indices, const std::int64_t* stamps, std::size_t n);
    core::NativeAdcDrift estimate() const;
    std::size_t points() const { return n_.size(); }

private:
    double fs_;
    std::int64_t bin_;
    int host_level_;
    double host_sigma_ppm_;
    std::vector<std::int64_t> n_, t_;
    std::int64_t last_end_ = -1;
    std::int64_t cur_bin_ = -1;
    std::int64_t cur_n_ = 0;
    std::int64_t cur_t_ = 0;
    double cur_v_ = 0.0;
    std::int64_t bins_seen_ = 0;
};

class MeasurementSession {
public:
    // stream == nullptr: the session owns a stream of cfg.stream_capacity samples.
    MeasurementSession(const advtg_session_config& cfg, runtime::NativeSampleStream* stream);
    ~MeasurementSession();
    MeasurementSession(const MeasurementSession&) = delete;
    MeasurementSession& operator=(const MeasurementSession&) = delete;

    void update(advtg_snapshot& out);

    runtime::NativeSampleStream* stream() const { return stream_; }
    const std::vector<double>& waveform() const { return wave_; }
    const std::vector<double>& tape_time_s() const { return tape_t_; }
    const std::vector<double>& tape_offset_ms() const { return tape_off_ms_; }
    const std::vector<std::int8_t>& tape_parity() const { return tape_par_; }
    const std::vector<core::NativeBeat>& new_beats() const { return new_beats_; }
    const std::deque<core::NativeBeat>& window_beats() const { return beats_; }
    const std::vector<core::NativeWindowRate>& metric_windows() const { return windows_; }
    const std::vector<std::string>& metric_warnings() const { return metric_warnings_; }
    const std::vector<std::string>& detector_warnings() const { return detector_->warnings(); }
    core::NativeDetectorStats detector_stats() const { return detector_->stats(); }
    std::int64_t display_pre() const { return display_pre_; }
    std::int64_t display_len() const { return display_len_; }

private:
    void advance_detector();
    void update_clock(double audio_s);
    void apply_drift();
    void read_templates();
    void compute_metrics(bool compute_windows, core::NativeMetricsResult& m);
    void waveform_of(const core::NativeBeat& b, double& start);
    void display(const core::NativeMetricsResult& m, advtg_display& d);
    double tape_instant(const core::NativeBeat& b) const;
    void tape_follow(const core::NativeMetricsResult& m);
    void tape(const std::vector<core::NativeBeat>& beats);
    void tape_shift(const core::NativeMetricsResult& m, double out[2]);
    double timing_confidence(const core::NativeMetricsResult& m) const;
    double beat_period_nominal_samples() const { return 3600.0 / cfg_.bph * fs_; }

    advtg_session_config cfg_;
    double fs_;
    double lift_;            // effective lift angle
    bool has_lift_;
    double window_s_;
    std::unique_ptr<runtime::NativeSampleStream> own_stream_;
    runtime::NativeSampleStream* stream_ = nullptr;

    // detector and its band-pass (also used for the display waveform)
    std::vector<double> det_sos_, det_zi_;
    std::unique_ptr<core::BeatDetector> detector_;
    std::vector<double> sos_, zi_;
    std::int64_t padlen_ = 0;
    int n_sec_ = 0;

    // metrics tracker
    std::deque<core::NativeBeat> beats_;
    double last_t_;
    bool have_points_ = false;
    int points_[2] = {0, 0};  // previous rate_point (1 = p1)
    core::NativeTemplateHeader tpl_hdr_[2];
    std::vector<double> tpl_env_[2];
    std::vector<core::NativeWindowRate> windows_;
    std::vector<std::string> metric_warnings_;
    double adc_ppm_ = 0.0, adc_sigma_ppm_ = 0.0;

    // clock
    std::unique_ptr<ClockDriftTracker> clock_;
    int clock_field_ = ADVTG_CLOCK_FIELD_NONE;
    core::NativeAdcDrift live_{};
    core::NativeAdcDrift applied_{};
    int applied_source_ = ADVTG_DRIFT_SOURCE_NONE;
    double next_refit_ = 0.0;
    std::vector<std::int64_t> log_end_, log_ns_, log_adc_, log_wall_;

    // counters and histories
    std::int64_t beats_total_ = 0;
    std::vector<double> recent_snr_;
    bool has_last_beat_ = false;
    core::NativeBeat last_beat_{};
    std::deque<core::NativeMetricsResult> history_;
    std::deque<double> be_history_, be_p3_history_;
    std::deque<double> shift_hist_[2];
    bool has_tape_origin_ = false;
    std::int64_t tape_k0_ = 0;
    double tape_t0_ = 0.0;
    int tape_point_[2] = {0, 0};  // 1 = p1, 0 = t_fine
    double tape_off_[2] = {0.0, 0.0};

    // outputs of the last update
    std::vector<core::NativeBeat> new_beats_;
    std::vector<double> wave_;
    std::vector<double> tape_t_, tape_off_ms_;
    std::vector<std::int8_t> tape_par_;
    std::vector<double> scratch_;
    std::int64_t display_pre_ = 0, display_len_ = 0;
};

}  // namespace advtg::session
