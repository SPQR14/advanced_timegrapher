// Native MeasurementSession: line-by-line port of core/session.py (see its module docstring
// for the physics of every step). Compiled with -ffp-contract=off (CMakeLists.txt) so every
// expression rounds as in numpy and the snapshots agree with the Python session.
#include "advtg_session.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <numbers>
#include <stdexcept>

#include "advtg_dsp.hpp"

namespace advtg::session {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
constexpr double kDefaultLiftAngle = 52.0;        // metrics.DEFAULT_LIFT_ANGLE_DEG
constexpr double kDriftRefitS = 1.0;              // DRIFT_REFIT_S
constexpr std::size_t kSnrBeats = 16;             // SNR_BEATS
constexpr std::size_t kDisplayUpdates = 7;        // DISPLAY_UPDATES
constexpr std::size_t kBeDisplayUpdates = 20;     // BE_DISPLAY_UPDATES
constexpr double kDisplayMinAmplitude = 150.0;    // DISPLAY_MIN_AMPLITUDE_DEG
constexpr double kUncalibratedSigmaPpm = 30.0;    // adc_clock.UNCALIBRATED_SIGMA_PPM
constexpr int kMinBeatsPerParity = 10;            // MetricsConfig.min_beats_per_parity
constexpr double kAmplitudeMismatch = 20.0;       // MetricsConfig.amplitude_mismatch_deg
constexpr double kAmpLo = 90.0, kAmpHi = 400.0;   // MetricsConfig.amplitude_range_deg

// Python round() (half to even) for the sample counts of the display window.
std::int64_t py_round(double x) { return static_cast<std::int64_t>(std::nearbyint(x)); }

// numpy pairwise summation of a contiguous float64 array (np.add.reduce), so np.mean agrees
// to the last bit.
double np_pairwise(const double* a, std::size_t n) {
    if (n < 8) {
        double res = -0.0;
        for (std::size_t i = 0; i < n; ++i) res += a[i];
        return res;
    }
    if (n <= 128) {
        double r[8];
        for (int j = 0; j < 8; ++j) r[j] = a[j];
        std::size_t i = 8;
        for (; i < n - (n % 8); i += 8)
            for (int j = 0; j < 8; ++j) r[j] += a[i + j];
        double res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; ++i) res += a[i];
        return res;
    }
    std::size_t n2 = n / 2;
    n2 -= n2 % 8;
    return np_pairwise(a, n2) + np_pairwise(a + n2, n - n2);
}

double np_mean(const std::vector<double>& v) {
    if (v.empty()) return kNaN;
    return np_pairwise(v.data(), v.size()) / static_cast<double>(v.size());
}

// np.median (no NaN in v).
double np_median(std::vector<double> v) {
    const std::size_t n = v.size();
    if (n == 0) return kNaN;
    const std::size_t h = n / 2;
    std::nth_element(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(h), v.end());
    const double hi = v[h];
    if (n % 2) return hi;
    const double lo = *std::max_element(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(h));
    return (lo + hi) / 2.0;
}

// session._nanmedian: median of the finite values, NaN if none.
template <class It, class F>
double nanmedian(It first, It last, F get) {
    std::vector<double> v;
    for (It it = first; it != last; ++it) {
        const double x = get(*it);
        if (std::isfinite(x)) v.push_back(x);
    }
    return np_median(std::move(v));
}

double reference(const core::NativeBeat& b) { return std::isnan(b.p3) ? b.p1 : b.p3; }

// math.hypot(x, y) of CPython 3.12 (Modules/mathmodule.c vector_norm): it does not call the C
// library hypot, whose last bit differs between glibc, MSVC and Apple's libm, so the drift
// uncertainty (and the rate uncertainty through it) matches Python on every platform.
double py_hypot(double x, double y) {
    x = std::fabs(x);
    y = std::fabs(y);
    if (std::isinf(x) || std::isinf(y)) return std::numeric_limits<double>::infinity();
    if (std::isnan(x) || std::isnan(y)) return kNaN;
    const double mx = std::max(x, y);
    if (mx == 0.0) return 0.0;
    int max_e = 0;
    std::frexp(mx, &max_e);
    if (max_e < -1023) {  // subnormal maximum (never for ppm values; kept for completeness)
        constexpr double dmin = std::numeric_limits<double>::min();
        return dmin * py_hypot(x / dmin, y / dmin);
    }
    const double scale = std::ldexp(1.0, -max_e);
    double csum = 1.0, frac1 = 0.0, frac2 = 0.0;
    auto add = [&](double v) {
        v *= scale;
        const double hi = v * v, lo = std::fma(v, v, -hi);  // lossless square
        const double s = csum + hi, z = s - csum, e = hi - z;  // lossless sum (|csum| >= |hi|)
        csum = s;
        frac1 += lo;
        frac2 += e;
    };
    add(x);
    add(y);
    double h = std::sqrt(csum - 1.0 + (frac1 + frac2));
    {
        const double hi = -h * h, lo = std::fma(-h, h, -hi);
        const double s = csum + hi, z = s - csum, e = hi - z;
        csum = s;
        frac1 += lo;
        frac2 += e;
    }
    const double r = csum - 1.0 + (frac1 + frac2);
    h += r / (2.0 * h);  // differential correction
    return h / scale;
}

double uncertainty_ppm(const core::NativeAdcDrift& d) { return py_hypot(d.sigma_ppm, d.systematic_ppm); }

core::NativeAdcDrift uncalibrated(int note_kind) {
    core::NativeAdcDrift d{};
    d.ppm = 0.0;
    d.sigma_ppm = 0.0;
    d.systematic_ppm = kUncalibratedSigmaPpm;
    d.level = ADVTG_CLOCK_UNCALIBRATED;
    d.method = 0;
    d.note_kind = note_kind;
    d.span_s = 0.0;
    d.residual_us = kNaN;
    return d;
}

double lift_time_s(double amplitude_deg, double lift_angle_deg, double bph) {
    const double ratio = lift_angle_deg / (2.0 * amplitude_deg);
    if (!(ratio > 0.0 && ratio < 1.0)) throw std::invalid_argument("impossible amplitude");
    return std::asin(ratio) / (std::numbers::pi * (bph / 7200.0));
}

}  // namespace

// ====================================================================== ClockDriftTracker
ClockDriftTracker::ClockDriftTracker(double sample_rate, int host_level, double host_sigma_ppm,
                                     double bin_s)
    : fs_(sample_rate), host_level_(host_level), host_sigma_ppm_(host_sigma_ppm) {
    if (!(sample_rate > 0 && bin_s > 0)) throw std::invalid_argument("sample_rate, bin_s > 0");
    bin_ = std::max<std::int64_t>(1, py_round(bin_s * sample_rate));
    reset();
}

void ClockDriftTracker::reset() {
    n_.clear();
    t_.clear();
    last_end_ = -1;
    cur_bin_ = -1;
    cur_n_ = 0;
    cur_t_ = 0;
    cur_v_ = std::numeric_limits<double>::infinity();
    bins_seen_ = 0;
}

void ClockDriftTracker::update(const std::int64_t* end, const std::int64_t* stamps, std::size_t n) {
    if (n == 0) return;
    if (end[n - 1] < last_end_) reset();
    const double k = 1e9 / fs_;
    std::int64_t last_new = -1;
    for (std::size_t i = 0; i < n; ++i) {
        const std::int64_t ni = end[i];
        const std::int64_t ti = stamps[i];
        if (!(ni > last_end_ && ti > 0)) continue;  // already seen, or no stamp
        last_new = ni;
        const std::int64_t b = ni >= 0 ? ni / bin_ : -((-ni + bin_ - 1) / bin_);
        const double v = static_cast<double>(ti) - static_cast<double>(ni) * k;
        if (b != cur_bin_) {
            if (cur_bin_ >= 0 && bins_seen_ > 1) {  // the first bin is partial
                n_.push_back(cur_n_);
                t_.push_back(cur_t_);
            }
            cur_bin_ = b;
            cur_v_ = std::numeric_limits<double>::infinity();
            ++bins_seen_;
        }
        if (v < cur_v_) {
            cur_v_ = v;
            cur_n_ = ni;
            cur_t_ = ti;
        }
    }
    if (last_new >= 0) last_end_ = last_new;
}

core::NativeAdcDrift ClockDriftTracker::estimate() const {
    constexpr int kMinPoints = 10;
    if (n_.size() < static_cast<std::size_t>(kMinPoints)) {
        auto d = uncalibrated(ADVTG_DRIFT_NOTE_FEW_POINTS);
        d.n_points = static_cast<std::int64_t>(n_.size());
        return d;
    }
    core::NativeAdcDrift r = core::fit_envelope_cpp(n_.data(), t_.data(), n_.size(), fs_,
                                                    host_level_, host_sigma_ppm_, 5.0, 5.0,
                                                    kMinPoints, 3, 20.0);
    if (r.note_kind == ADVTG_DRIFT_NOTE_FEW_POINTS || r.note_kind == ADVTG_DRIFT_NOTE_TOO_MANY_JUMPS ||
        r.note_kind == ADVTG_DRIFT_NOTE_SHORT_SPAN) {
        auto d = uncalibrated(r.note_kind);
        d.n_points = static_cast<std::int64_t>(n_.size());
        if (r.note_kind == ADVTG_DRIFT_NOTE_SHORT_SPAN) d.span_s = r.span_s;  // for the note
        return d;
    }
    r.method = 1;  // host-envelope
    return r;
}

// ====================================================================== MeasurementSession
MeasurementSession::MeasurementSession(const advtg_session_config& cfg,
                                       runtime::NativeSampleStream* stream)
    : cfg_(cfg), fs_(cfg.sample_rate), last_t_(-std::numeric_limits<double>::infinity()) {
    const double fs = fs_;
    if (!(std::isfinite(fs) && fs >= 8000.0)) throw std::invalid_argument("sample_rate must be >= 8000 Hz");
    if (!(std::isfinite(cfg.bph) && cfg.bph >= 3600.0 && cfg.bph <= 360000.0))
        throw std::invalid_argument("bph out of range [3600, 360000]");
    has_lift_ = std::isfinite(cfg.lift_angle_deg) && cfg.lift_angle_deg > 0.0;
    lift_ = has_lift_ ? cfg.lift_angle_deg : kDefaultLiftAngle;
    if (!(lift_ > 0.0 && lift_ < 90.0)) throw std::invalid_argument("lift_angle_deg out of (0, 90)");
    if (!(cfg.span_s > 0.0)) throw std::invalid_argument("span_s must be positive");
    if (!(cfg.window_s > 0.0)) throw std::invalid_argument("window_s must be positive");
    window_s_ = std::min(cfg.window_s, cfg.span_s);

    if (stream) {
        stream_ = stream;
    } else {
        const std::int64_t cap =
            cfg.stream_capacity > 0 ? cfg.stream_capacity : py_round(120.0 * fs);
        own_stream_ = std::make_unique<runtime::NativeSampleStream>(
            static_cast<std::size_t>(cap), fs, 8192, 0);
        stream_ = own_stream_.get();
    }

    // detector: BeatDetectorConfig(sample_rate, bph, lift_angle_deg, quartz, normalize)
    core::NativeDetectorConfig dc{};
    dc.sample_rate = fs;
    dc.bph = cfg.bph;
    dc.lift_angle_deg = lift_;
    dc.quartz = cfg.quartz ? 1 : 0;
    dc.normalize = cfg.normalize ? 1 : 0;
    dc.band_lo_hz = 1000.0;
    dc.band_hi_hz = std::min(15000.0, 0.45 * fs);
    n_sec_ = dc.filter_order;
    det_sos_ = dsp::butter_bandpass_sos(dc.filter_order, dc.band_lo_hz, dc.band_hi_hz, fs);
    n_sec_ = static_cast<int>(det_sos_.size() / 6);
    det_zi_ = dsp::sosfilt_zi(det_sos_.data(), n_sec_);
    dc.sos = det_sos_.data();
    dc.zi = det_zi_.data();
    dc.n_sec = n_sec_;
    dc.padlen = dsp::sosfiltfilt_padlen(det_sos_.data(), n_sec_);
    detector_ = std::make_unique<core::BeatDetector>(dc);

    // display band-pass: butter_bandpass_sos(4, band, fs) (the detector's band and order)
    sos_ = dsp::butter_bandpass_sos(4, dc.band_lo_hz, dc.band_hi_hz, fs);
    zi_ = dsp::sosfilt_zi(sos_.data(), static_cast<int>(sos_.size() / 6));
    padlen_ = dsp::sosfiltfilt_padlen(sos_.data(), static_cast<int>(sos_.size() / 6));

    const double pre_s = lift_time_s(kDisplayMinAmplitude, lift_, cfg.bph) + cfg.display_pre_ms * 1e-3;
    display_pre_ = py_round(pre_s * fs);
    display_len_ = display_pre_ + py_round(cfg.display_post_ms * 1e-3 * fs);

    live_ = uncalibrated(ADVTG_DRIFT_NOTE_SESSION_TOO_SHORT);
    applied_ = uncalibrated(ADVTG_DRIFT_NOTE_NONE);
    apply_drift();
}

MeasurementSession::~MeasurementSession() = default;

// ------------------------------------------------------------------ detector
void MeasurementSession::advance_detector() {
    new_beats_.clear();
    std::int64_t total = stream_->total_samples();
    const core::NativeDetectorStats st = detector_->stats();
    std::int64_t start = st.has_norm_next ? st.norm_next_index
                                          : (st.started ? st.expected_index : stream_->oldest_available());
    if (total <= start) return;
    const std::int64_t cap = static_cast<std::int64_t>(stream_->capacity());
    for (int attempt = 0;; ++attempt) {
        if (attempt == 8) throw std::runtime_error("el productor adelantó al detector repetidamente");
        scratch_.resize(static_cast<std::size_t>(total - start));
        const int rc = stream_->read_f64(start, total, scratch_.data());
        if (rc == 0) break;
        if (rc != 2) throw std::runtime_error("stream read failed");
        total = stream_->total_samples();
        start = std::min(total, std::max(start, stream_->oldest_available() + cap / 8));
    }
    detector_->process(scratch_.data(), scratch_.size(), start, new_beats_);
}

void MeasurementSession::read_templates() {
    detector_->templates(tpl_hdr_[0], tpl_env_[0], tpl_hdr_[1], tpl_env_[1]);
}

// ------------------------------------------------------------------ clock
void MeasurementSession::update_clock(double audio_s) {
    if (!cfg_.live_clock) return;
    const std::size_t cap = stream_->log_size();
    log_end_.resize(cap);
    log_ns_.resize(cap);
    log_adc_.resize(cap);
    log_wall_.resize(cap);
    const std::size_t m = stream_->clock_log(-1, log_end_.data(), log_ns_.data(), log_adc_.data(),
                                             log_wall_.data(), cap);
    if (m == 0) return;
    if (!clock_) {
        const bool use_wall = log_wall_[m - 1] > 0;
        clock_field_ = use_wall ? ADVTG_CLOCK_FIELD_WALL : ADVTG_CLOCK_FIELD_HOST;
        const bool disc = use_wall ? cfg_.wall_clock_disciplined : cfg_.mono_clock_disciplined;
        const double sig = use_wall ? cfg_.wall_clock_sigma_ppm : cfg_.mono_clock_sigma_ppm;
        clock_ = std::make_unique<ClockDriftTracker>(
            fs_, disc ? ADVTG_CLOCK_HOST_DISCIPLINED : ADVTG_CLOCK_HOST_RAW, sig);
    }
    const auto& stamps = clock_field_ == ADVTG_CLOCK_FIELD_WALL ? log_wall_ : log_ns_;
    clock_->update(log_end_.data(), stamps.data(), m);
    if (audio_s >= next_refit_) {
        next_refit_ = audio_s + kDriftRefitS;
        live_ = clock_->estimate();
        apply_drift();
    }
}

void MeasurementSession::apply_drift() {
    bool have = false;
    core::NativeAdcDrift best{};
    int source = ADVTG_DRIFT_SOURCE_NONE;
    if (cfg_.has_calibration && cfg_.calibration.level != ADVTG_CLOCK_UNCALIBRATED) {
        std::memcpy(static_cast<void*>(&best), &cfg_.calibration, sizeof best);
        source = ADVTG_DRIFT_SOURCE_CALIBRATION;
        have = true;
    }
    if (live_.level >= ADVTG_CLOCK_HOST_DISCIPLINED && std::isfinite(uncertainty_ppm(live_))) {
        if (!have || uncertainty_ppm(live_) < uncertainty_ppm(best)) {  // min(): first on ties
            best = live_;
            source = ADVTG_DRIFT_SOURCE_LIVE;
            have = true;
        }
    }
    if (!have) {
        best = uncalibrated(ADVTG_DRIFT_NOTE_NONE);
        source = ADVTG_DRIFT_SOURCE_NONE;
    }
    applied_ = best;
    applied_source_ = source;
    adc_ppm_ = best.ppm;
    adc_sigma_ppm_ = best.level != ADVTG_CLOCK_UNCALIBRATED ? uncertainty_ppm(best) : 0.0;
}

// ------------------------------------------------------------------ metrics
void MeasurementSession::compute_metrics(bool compute_windows, core::NativeMetricsResult& m) {
    core::NativeMetricsConfig mc{};
    mc.sample_rate = fs_;
    mc.bph = cfg_.bph;
    mc.lift_angle_deg = lift_;
    mc.has_lift_angle = has_lift_ ? 1 : 0;
    mc.quartz = cfg_.quartz ? 1 : 0;
    mc.window_s = window_s_;
    mc.window_step_s = 0.0;
    mc.adc_drift_ppm = adc_ppm_;
    mc.adc_drift_sigma_ppm = adc_sigma_ppm_;
    mc.prev_p0_p1 = have_points_ && points_[0] ? 1 : 0;
    mc.prev_p1_p1 = have_points_ && points_[1] ? 1 : 0;
    mc.compute_windows = compute_windows ? 1 : 0;
    core::NativeTemplateInput t[2];
    for (int p = 0; p < 2; ++p) {
        t[p] = core::NativeTemplateInput{};
        t[p].valid = tpl_hdr_[p].valid;
        if (tpl_hdr_[p].valid) {
            t[p].onset_p1 = tpl_hdr_[p].onset_p1;
            t[p].onset_p3 = tpl_hdr_[p].onset_p3;
            t[p].onset_ref = tpl_hdr_[p].onset_ref;
            const std::size_t len = std::min<std::size_t>(tpl_env_[p].size(),
                                                          static_cast<std::size_t>(tpl_hdr_[p].length));
            t[p].env = len ? tpl_env_[p].data() : nullptr;
            t[p].env_len = len;
        }
    }
    std::vector<core::NativeBeat> bl(beats_.begin(), beats_.end());
    m = core::NativeMetricsResult{};
    metric_warnings_.clear();
    core::compute_metrics_cpp(bl.data(), bl.size(), t[0], t[1], mc, m, windows_, metric_warnings_);
    have_points_ = std::isfinite(m.rate_s_per_day);
    if (have_points_) {
        points_[0] = m.rate_point_0_p1;
        points_[1] = m.rate_point_1_p1;
    }
}

double MeasurementSession::timing_confidence(const core::NativeMetricsResult& m) const {
    if (!std::isfinite(m.rate_s_per_day)) return 0.0;
    const double n_min = static_cast<double>(std::min(m.n_used_even, m.n_used_odd));
    const double q_n = std::min(1.0, n_min / (3.0 * kMinBeatsPerParity));
    const double s_line = std::max(m.sigma_even_us, m.sigma_odd_us);
    const double s_pair = std::max(m.pair_sigma_even_us, m.pair_sigma_odd_us);
    const double s_max =
        (std::isfinite(s_line) && std::isfinite(s_pair)) ? std::min(s_line, s_pair) : s_line;
    const double q_sigma = std::isfinite(s_max) ? 1.0 / (1.0 + s_max / 500.0) : 0.0;
    const double q_excl = std::max(0.0, 1.0 - m.excluded_fraction);
    return std::min(1.0, std::max(0.0, q_n * q_sigma * q_excl));
}

// ------------------------------------------------------------------ waveform
void MeasurementSession::waveform_of(const core::NativeBeat& beat, double& start) {
    wave_.clear();
    const std::int64_t a = static_cast<std::int64_t>(std::floor(reference(beat))) - display_pre_;
    const std::int64_t b = a + display_len_;
    start = static_cast<double>(a);
    const std::int64_t pad = static_cast<std::int64_t>(0.005 * fs_);
    const std::int64_t lo = a - pad, hi = b + pad;
    if (lo < stream_->oldest_available() || hi > stream_->total_samples() || b <= a) return;
    std::vector<double> x(static_cast<std::size_t>(hi - lo));
    if (stream_->read_f64(lo, hi, x.data()) != 0) return;
    if (static_cast<std::int64_t>(x.size()) <= padlen_) return;
    std::vector<double> y(x.size());
    dsp::sosfiltfilt(sos_.data(), static_cast<int>(sos_.size() / 6), zi_.data(), padlen_, x.data(),
                     x.size(), y.data());
    wave_.assign(y.begin() + pad, y.begin() + pad + (b - a));
}

// ------------------------------------------------------------------ display
void MeasurementSession::display(const core::NativeMetricsResult& m, advtg_display& d) {
    d = advtg_display{kNaN, kNaN, kNaN, kNaN, kNaN, kNaN, 0.0, 0.0};
    if (!std::isfinite(m.rate_s_per_day)) {
        history_.clear();
        be_history_.clear();
        be_p3_history_.clear();
        return;
    }
    history_.push_back(m);
    if (history_.size() > kDisplayUpdates) history_.pop_front();
    be_history_.push_back(m.beat_error_ms);
    if (be_history_.size() > kBeDisplayUpdates) be_history_.pop_front();
    be_p3_history_.push_back(m.beat_error_p3_ms);
    if (be_p3_history_.size() > kBeDisplayUpdates) be_p3_history_.pop_front();
    const auto& h = history_;
    const double tic = nanmedian(h.begin(), h.end(), [](const auto& x) { return x.amplitude_tic_deg; });
    const double tac = nanmedian(h.begin(), h.end(), [](const auto& x) { return x.amplitude_tac_deg; });
    double amp;
    if (std::isfinite(tic) && std::isfinite(tac) && std::abs(tic - tac) <= kAmplitudeMismatch)
        amp = 0.5 * (tic + tac);
    else
        amp = nanmedian(h.begin(), h.end(), [](const auto& x) { return x.amplitude_deg; });
    if (!(std::isfinite(amp) && kAmpLo <= amp && amp <= kAmpHi)) amp = kNaN;
    auto same = [](double x) { return x; };
    d.rate_s_per_day = nanmedian(h.begin(), h.end(), [](const auto& x) { return x.rate_s_per_day; });
    d.beat_error_ms = nanmedian(be_history_.begin(), be_history_.end(), same);
    d.beat_error_p3_ms = nanmedian(be_p3_history_.begin(), be_p3_history_.end(), same);
    d.amplitude_deg = amp;
    d.amplitude_tic_deg = tic;
    d.amplitude_tac_deg = tac;
    std::vector<double> conf, tconf;
    for (const auto& x : h) {
        conf.push_back(x.confidence);
        tconf.push_back(timing_confidence(x));
    }
    d.confidence = np_median(conf);
    d.timing_confidence = np_median(tconf);
}

// ------------------------------------------------------------------ paper tape
double MeasurementSession::tape_instant(const core::NativeBeat& b) const {
    const int parity = static_cast<int>(b.k & 1);
    const double t = tape_point_[parity] ? b.t_p1 : b.t_fine;
    return t + tape_off_[parity];
}

void MeasurementSession::tape_follow(const core::NativeMetricsResult& m) {
    if (!std::isfinite(m.rate_s_per_day)) return;
    const int pts[2] = {m.rate_point_0_p1, m.rate_point_1_p1};
    for (int parity = 0; parity < 2; ++parity) {
        const int nw = pts[parity] ? 1 : 0;
        if (nw == tape_point_[parity]) continue;
        std::vector<double> d;
        for (const auto& b : beats_)
            if (static_cast<int>(b.k & 1) == parity && std::isfinite(b.t_fine) && std::isfinite(b.t_p1))
                d.push_back(b.t_fine - b.t_p1);
        if (d.empty()) continue;
        const double delta = np_median(std::move(d));
        tape_off_[parity] += nw ? delta : -delta;
        tape_point_[parity] = nw;
    }
}

void MeasurementSession::tape(const std::vector<core::NativeBeat>& beats) {
    tape_t_.clear();
    tape_off_ms_.clear();
    tape_par_.clear();
    const double period = beat_period_nominal_samples() * (1.0 + applied_.ppm * 1e-6);
    for (const auto& b : beats) {
        const double t = tape_instant(b);
        if (!std::isfinite(t)) continue;
        if (!has_tape_origin_) {
            has_tape_origin_ = true;
            tape_k0_ = b.k;
            tape_t0_ = t;
        }
        const double k = static_cast<double>(b.k);
        const double k0 = static_cast<double>(tape_k0_);
        tape_t_.push_back(t / fs_);
        tape_off_ms_.push_back(-(t - tape_t0_ - (k - k0) * period) / fs_ * 1e3);
        tape_par_.push_back(static_cast<std::int8_t>(b.k & 1));
    }
}

void MeasurementSession::tape_shift(const core::NativeMetricsResult& m, double out[2]) {
    const int point = m.beat_error_point;
    double off[2] = {kNaN, kNaN};
    if (point == ADVTG_BE_POINT_P1 || point == ADVTG_BE_POINT_P1_BEAT || point == ADVTG_BE_POINT_P1_FINE) {
        double tpl_off[2];
        for (int p = 0; p < 2; ++p)
            tpl_off[p] = tpl_hdr_[p].valid ? tpl_hdr_[p].onset_ref - tpl_hdr_[p].onset_p1 : kNaN;
        std::vector<double> d[2];
        for (const auto& b : beats_) {
            const int parity = static_cast<int>(b.k & 1);
            double p1;
            if (point == ADVTG_BE_POINT_P1)
                p1 = std::isfinite(b.t_p1_tpl) ? b.t_p1_tpl : b.t_fine - tpl_off[parity];
            else
                p1 = point == ADVTG_BE_POINT_P1_BEAT ? b.p1 : b.t_p1;
            const double v = (tape_instant(b) - p1) / fs_ * 1e3;
            if (std::isfinite(v)) d[parity].push_back(v);
        }
        if (!d[0].empty() && !d[1].empty()) {
            off[0] = np_median(std::move(d[0]));
            off[1] = np_median(std::move(d[1]));
        }
    }
    if (std::isfinite(off[0]) && std::isfinite(off[1])) {
        const double mid = 0.5 * (off[0] + off[1]);
        for (int p = 0; p < 2; ++p) {
            shift_hist_[p].push_back(off[p] - mid);
            if (shift_hist_[p].size() > kBeDisplayUpdates) shift_hist_[p].pop_front();
        }
    } else if (point == ADVTG_BE_POINT_T_FINE) {
        shift_hist_[0].clear();
        shift_hist_[1].clear();
    }
    if (shift_hist_[0].empty()) {
        out[0] = out[1] = 0.0;
        return;
    }
    out[0] = np_median(std::vector<double>(shift_hist_[0].begin(), shift_hist_[0].end()));
    out[1] = np_median(std::vector<double>(shift_hist_[1].begin(), shift_hist_[1].end()));
}

// ------------------------------------------------------------------ update
void MeasurementSession::update(advtg_snapshot& out) {
    advance_detector();
    core::NativeDetectorStats st = detector_->stats();
    const double audio_s = static_cast<double>(st.samples_processed) / fs_;
    update_clock(audio_s);

    // MetricsTracker.add(beats, templates)
    read_templates();
    for (const auto& b : new_beats_) {
        beats_.push_back(b);
        if (std::isfinite(b.t_fine)) last_t_ = std::max(last_t_, b.t_fine);
    }
    if (std::isfinite(last_t_)) {
        const double start = last_t_ - cfg_.span_s * fs_;
        while (!beats_.empty()) {
            const auto& b = beats_.front();
            const double ref = std::isfinite(b.t_fine) ? b.t_fine : reference(b);
            if (ref < start)
                beats_.pop_front();
            else
                break;
        }
    }
    beats_total_ += static_cast<std::int64_t>(new_beats_.size());
    for (const auto& b : new_beats_) {
        if (std::isfinite(b.snr_db)) recent_snr_.push_back(b.snr_db);
        if (std::isfinite(reference(b))) {
            last_beat_ = b;
            has_last_beat_ = true;
        }
    }
    if (recent_snr_.size() > kSnrBeats)
        recent_snr_.erase(recent_snr_.begin(),
                          recent_snr_.end() - static_cast<std::ptrdiff_t>(kSnrBeats));

    const bool need_windows = cfg_.span_s >= 6.0 * window_s_;
    core::NativeMetricsResult metrics;
    compute_metrics(need_windows, metrics);
    tape_follow(metrics);
    tape(new_beats_);
    double shift[2];
    tape_shift(metrics, shift);

    double det_ratio = kNaN;
    std::int64_t saturated = 0;
    if (!beats_.empty()) {
        const std::int64_t span_k = beats_.back().k - beats_.front().k + 1;
        det_ratio = span_k > 0 ? static_cast<double>(beats_.size()) / static_cast<double>(span_k) : kNaN;
        for (const auto& b : beats_)
            if (b.flags & core::BEAT_SATURATED) ++saturated;
    }
    const double usable = !beats_.empty() ? det_ratio * (1.0 - metrics.excluded_fraction) : kNaN;

    double w0 = kNaN;
    wave_.clear();
    if (!new_beats_.empty() && has_last_beat_) waveform_of(last_beat_, w0);
    st = detector_->stats();
    advtg_display disp;
    display(metrics, disp);
    const double bph = cfg_.bph;
    double real_bph;
    if (std::isfinite(disp.rate_s_per_day)) {
        real_bph = bph * (1.0 + disp.rate_s_per_day / 86400.0);
    } else if (st.locked && std::isfinite(st.period_samples)) {
        const double dev = st.period_samples / st.nominal_period_samples - 1.0;
        real_bph = bph / (1.0 + dev) * (1.0 + applied_.ppm * 1e-6);
    } else {
        real_bph = kNaN;
    }

    std::memset(&out, 0, sizeof out);
    static_assert(sizeof(advtg_metrics) == sizeof(core::NativeMetricsResult));
    static_assert(sizeof(advtg_beat) == sizeof(core::NativeBeat));
    static_assert(sizeof(advtg_adc_drift) == sizeof(core::NativeAdcDrift));
    std::memcpy(&out.metrics, &metrics, sizeof metrics);
    out.display = disp;
    if (has_last_beat_) {
        std::memcpy(&out.last_beat, &last_beat_, sizeof last_beat_);
        out.has_last_beat = 1;
    } else {
        core::NativeBeat empty{};
        std::memcpy(&out.last_beat, &empty, sizeof empty);
    }
    std::memcpy(&out.drift, &applied_, sizeof applied_);
    std::memcpy(&out.live_drift, &live_, sizeof live_);
    out.waveform_start = w0;
    out.snr_db = np_mean(recent_snr_);
    out.detection_ratio = det_ratio;
    out.usable_ratio = usable;
    out.audio_s = audio_s;
    out.real_bph = real_bph;
    out.tape_shift_ms[0] = shift[0];
    out.tape_shift_ms[1] = shift[1];
    out.new_beats = static_cast<std::int64_t>(new_beats_.size());
    out.beats_total = beats_total_;
    out.saturated_beats = saturated;
    out.waveform_len = static_cast<std::int64_t>(wave_.size());
    out.tape_len = static_cast<std::int64_t>(tape_t_.size());
    out.locked = st.locked ? 1 : 0;
    out.drift_source = applied_source_;
    out.clock_field = clock_field_;
    out.normalized = cfg_.normalize ? 1 : 0;
}

}  // namespace advtg::session
