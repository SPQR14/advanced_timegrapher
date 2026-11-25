// Native BeatDetector (exact C++20 counterpart of src/adv_tg/core/beat_detector.py).
// Fixed-cell chunk-invariant pipeline: optional normalizer & interference filter, zero-phase
// band-pass + Hilbert envelope, MAD noise floor, hysteresis sub-events, 2P phase folding,
// Huber IRLS phase tracker, per-parity SIMD median templates, quartz step onset & mech P1 front.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_detector.hpp"
#include "advtg_dsp.hpp"
#include "advtg_simd.hpp"

namespace advtg::core {
namespace {

inline constexpr long double kPiL = 3.141592653589793238462643383279502884L;
inline constexpr double kP2Fraction = 0.45;
inline constexpr int kQDecideBeats = 4;
inline constexpr double kInterferenceWarnDb = 25.0;

inline std::int64_t py_round(double x) { return static_cast<std::int64_t>(std::llrint(x)); }

inline std::int64_t floor_div(std::int64_t a, std::int64_t b) {
    std::int64_t q = a / b;
    std::int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) --q;
    return q;
}

inline double amplitude_from_lift_time(double t_lift_s, double lift_angle_deg, double bph) {
    const double s = std::sin(static_cast<double>(kPiL) * (bph / 7200.0) * t_lift_s);
    if (!(t_lift_s > 0.0 && s > 0.0)) return kNaN;
    return lift_angle_deg / (2.0 * s);
}

inline double lift_time_s(double amplitude_deg, double lift_angle_deg, double bph) {
    const double ratio = lift_angle_deg / (2.0 * amplitude_deg);
    return std::asin(ratio) / (static_cast<double>(kPiL) * (bph / 7200.0));
}

// Growable 64-byte aligned buffer addressed by absolute index.
struct AbsBuf {
    AlignedBuffer<double> a;
    std::size_t n = 0;
    std::int64_t start = 0;

    explicit AbsBuf(std::size_t cap) : a(std::max<std::size_t>(1024, cap)) {}

    void reset(std::int64_t s) {
        n = 0;
        start = s;
    }

    std::int64_t end() const { return start + static_cast<std::int64_t>(n); }

    void append(const double* src, std::size_t m) {
        if (m == 0) return;
        const std::size_t need = n + m;
        if (need > a.capacity()) a.reserve(std::max(2 * a.capacity(), need), true);
        a.resize(need, true);
        std::memcpy(a.data() + n, src, m * sizeof(double));
        n = need;
    }

    const double* ptr(std::int64_t abs_idx) const { return a.data() + (abs_idx - start); }

    void trim(std::int64_t keep_from) {
        std::int64_t drop = keep_from - start;
        if (drop <= 0 || drop < static_cast<std::int64_t>(n / 2)) return;
        drop = std::min<std::int64_t>(drop, static_cast<std::int64_t>(n));
        const std::size_t rem = n - static_cast<std::size_t>(drop);
        if (rem) std::memmove(a.data(), a.data() + drop, rem * sizeof(double));
        n = rem;
        start += drop;
    }
};

struct Cands {
    std::vector<double> onset, peak, floor_v, spread;
    std::vector<std::uint8_t> used;

    void clear() {
        onset.clear();
        peak.clear();
        floor_v.clear();
        spread.clear();
        used.clear();
    }

    void extend(const dsp::Candidates& c) {
        const std::size_t m = c.onset.size();
        if (m == 0) return;
        onset.insert(onset.end(), c.onset.begin(), c.onset.end());
        peak.insert(peak.end(), c.peak.begin(), c.peak.end());
        floor_v.insert(floor_v.end(), c.floor.begin(), c.floor.end());
        spread.insert(spread.end(), c.spread.begin(), c.spread.end());
        used.insert(used.end(), m, 0);
    }

    void trim(double keep_from) {
        auto it = std::lower_bound(onset.begin(), onset.end(), keep_from);
        const std::size_t idx = static_cast<std::size_t>(it - onset.begin());
        if (idx > 256) {
            onset.erase(onset.begin(), onset.begin() + static_cast<std::ptrdiff_t>(idx));
            peak.erase(peak.begin(), peak.begin() + static_cast<std::ptrdiff_t>(idx));
            floor_v.erase(floor_v.begin(), floor_v.begin() + static_cast<std::ptrdiff_t>(idx));
            spread.erase(spread.begin(), spread.begin() + static_cast<std::ptrdiff_t>(idx));
            used.erase(used.begin(), used.begin() + static_cast<std::ptrdiff_t>(idx));
        }
    }

    std::pair<std::size_t, std::size_t> range(double a, double b) const {
        const std::size_t i0 =
            static_cast<std::size_t>(std::lower_bound(onset.begin(), onset.end(), a) - onset.begin());
        const std::size_t i1 =
            static_cast<std::size_t>(std::lower_bound(onset.begin(), onset.end(), b) - onset.begin());
        return {i0, i1};
    }
};

struct Tpl {
    int maxlen = 24;
    std::size_t length = 0;
    std::size_t stride = 0;  // aligned multiple of 8 doubles for AVX-512 median_segments
    AlignedBuffer<double> buf;
    int n_segs = 0;
    int pos = 0;

    bool has_env = false;
    AlignedBuffer<double> env;
    bool has_burst_bz = false;
    AlignedBuffer<double> burst_bz;
    double burst_nb = 0.0;

    double onset_p1 = kNaN;
    double onset_p3 = kNaN;
    double onset_front = kNaN;
    std::deque<double> off;
    int pending = 0;

    bool has_anchor = false;
    AlignedBuffer<double> anchor;
    double onset_anchor = kNaN;
    double lag = 0.0;

    std::vector<double> q_snr;
    int q_onset = -1;  // -1 = None, 0 = False, 1 = True
    std::deque<double> q_off;

    void init(int n, std::size_t len) {
        maxlen = n;
        length = len;
        stride = (len + 7U) & ~std::size_t(7);
        buf.resize(static_cast<std::size_t>(maxlen) * stride);
        reset_all();
    }

    void reset_all() {
        n_segs = 0;
        pos = 0;
        has_env = false;
        env.clear();
        has_burst_bz = false;
        burst_bz.clear();
        burst_nb = 0.0;
        onset_p1 = kNaN;
        onset_p3 = kNaN;
        onset_front = kNaN;
        off.clear();
        pending = 0;
        has_anchor = false;
        anchor.clear();
        onset_anchor = kNaN;
        lag = 0.0;
        q_snr.clear();
        q_onset = -1;
        q_off.clear();
    }

    void add_seg(const double* seg) {
        std::memcpy(buf.data() + static_cast<std::size_t>(pos) * stride, seg,
                    length * sizeof(double));
        pos = (pos + 1) % maxlen;
        if (n_segs < maxlen) ++n_segs;
    }

    void compute_median_env() {
        env.resize(length);
        if (n_segs == 1) {
            std::memcpy(env.data(), buf.data(), length * sizeof(double));
        } else {
            kernels().median_segments(buf.data(), stride, static_cast<std::size_t>(n_segs), length,
                                      env.data());
        }
        has_env = true;
    }

    double onset_ref() const { return has_anchor ? (onset_anchor + lag) : onset_p3; }
};

struct FloorEntry {
    std::int64_t cell = 0;
    double f = kTiny;
    double d = kTiny;
};

struct ModelCoef {
    bool valid = false;
    double a = 0.0;
    double b = 0.0;
    double cpar = 0.0;
    std::int64_t jl = 0;
    double scale = 0.0;
};

struct ModelObs {
    std::int64_t j = 0;
    double t_ref = 0.0;
};

struct OutlierRec {
    std::int64_t j = 0;
    double t_ref = 0.0;
    double residual = 0.0;
};

// Solve 2x2 or 3x3 linear system A * x = rhs by partial pivoting; false if nearly singular.
bool solve_small(int dim, double A[3][3], double rhs[3], double x[3]) {
    double M[3][4];
    double max_diag = 0.0;
    for (int r = 0; r < dim; ++r) {
        max_diag = std::max(max_diag, std::fabs(A[r][r]));
        for (int c = 0; c < dim; ++c) M[r][c] = A[r][c];
        M[r][dim] = rhs[r];
    }
    const double eps = 1e-13 * std::max(1.0, max_diag);
    for (int col = 0; col < dim; ++col) {
        int piv = col;
        double best = std::fabs(M[col][col]);
        for (int r = col + 1; r < dim; ++r) {
            const double v = std::fabs(M[r][col]);
            if (v > best) {
                best = v;
                piv = r;
            }
        }
        if (!(best > eps)) return false;
        if (piv != col) {
            for (int c = col; c <= dim; ++c) std::swap(M[col][c], M[piv][c]);
        }
        const double inv = 1.0 / M[col][col];
        for (int r = col + 1; r < dim; ++r) {
            const double fac = M[r][col] * inv;
            for (int c = col + 1; c <= dim; ++c) M[r][c] -= fac * M[col][c];
        }
    }
    for (int r = dim - 1; r >= 0; --r) {
        double s = M[r][dim];
        for (int c = r + 1; c < dim; ++c) s -= M[r][c] * x[c];
        x[r] = s / M[r][r];
    }
    return true;
}

template <class Container>
double deque_median(const Container& c) {
    const std::size_t n = c.size();
    if (n == 0) return kNaN;
    thread_local AlignedBuffer<double> tmp;
    tmp.resize(n);
    std::size_t i = 0;
    for (double v : c) tmp[i++] = v;
    return exact_median(tmp.data(), n);
}

inline bool starts_with(const std::string& s, const char* prefix) {
    const std::size_t len = std::strlen(prefix);
    return s.size() >= len && s.compare(0, len, prefix) == 0;
}

}  // namespace

struct BeatDetector::Impl {
    NativeDetectorConfig c;
    double fs = 48000.0;
    double ms = 48.0;
    std::int64_t S = 12000;

    std::vector<double> sos;
    std::vector<double> zi;
    int n_sec = 0;
    std::int64_t padlen = 0;

    std::vector<double> hk;
    std::int64_t smooth_len = 1;
    std::int64_t filter_memory = 1;
    std::int64_t margin = 1;

    std::unique_ptr<dsp::InterferenceFilter> interf;
    std::unique_ptr<dsp::AudioNormalizer> normalizer;

    std::int64_t OV = 1920;
    double period_nom = 8000.0;
    double tmin = 0.0;
    double tmax = 0.0;
    double slack = 0.0;
    double min_sp = 0.0;
    std::int64_t merge = 1;
    double post = 0.0;
    double pre = 0.0;
    std::int64_t dropout_min = 1;
    std::int64_t W_noise = 48000;
    std::int64_t W_acq = 96000;
    std::int64_t tpl_search = 72;
    int tpl_refresh = 2;
    std::int64_t tpl_pre = 0;
    std::int64_t tpl_post = 0;
    std::int64_t tpl_burst = 0;

    bool q_on = false;
    std::vector<double> q_fir;
    std::vector<double> q_fir_rev;
    std::int64_t q_pad = 0;
    std::int64_t q_base0 = 0;
    std::int64_t q_base1 = 0;
    std::int64_t q_win = 0;
    std::int64_t q_cen = 0;
    std::int64_t q_quiet = 2;

    bool p1_on = false;
    std::int64_t p1_back = 0;
    std::int64_t p1_fwd = 0;
    std::int64_t p1_base = 4;
    std::int64_t p1_hold = 1;
    std::int64_t p1_peak = 1;

    std::int64_t m_al = 1;

    // mutable state
    bool started = false;
    bool finished = false;
    std::int64_t expected = 0;
    std::int64_t first_index = 0;
    AbsBuf raw{4096};
    AbsBuf env{8192};
    std::int64_t run_start = 0;
    std::int64_t samples = 0;
    std::int64_t emitted = 0;
    std::int64_t missed = 0;
    std::int64_t gap_beats = 0;
    std::int64_t gaps = 0;
    std::int64_t rejected = 0;
    std::int64_t relocks = 0;
    bool has_lock_sample = false;
    double lock_sample = 0.0;
    std::int64_t n_p[3] = {0, 0, 0};
    std::vector<std::string> warnings;
    bool locked = false;
    bool ever_locked = false;
    double period = 8000.0;
    bool has_last_k = false;
    std::int64_t last_k = 0;
    bool has_last_ref = false;
    double last_ref = 0.0;
    std::deque<double> tlift2;
    std::deque<double> tlift3;
    bool tlift_cached = false;
    bool has_tlift_cache = false;
    double tlift_cache_val = kNaN;
    std::deque<double> tl_obs;
    std::deque<double> tl_fwd;
    bool after_gap = false;
    Tpl tpl[2];
    double fine_center = kNaN;
    double fine_p1_tpl = kNaN;

    std::int64_t next_cell = 0;
    std::int64_t first_cell = 0;
    std::int64_t cand_cell = 0;
    std::int64_t cand_end = 0;
    Cands cands;
    std::vector<FloorEntry> floors;
    std::deque<std::pair<double, double>> zones;
    std::vector<std::pair<std::int64_t, std::int64_t>> dropouts;
    bool has_zero_start = false;
    std::int64_t zero_start = 0;
    double acq_from = 0.0;
    double rej_from = 0.0;

    // phase tracking state during lock
    double phase0 = 0.0;
    std::deque<ModelObs> model;
    ModelCoef coef;
    std::int64_t missed_run = 0;
    std::deque<OutlierRec> outliers;
    bool has_k_offset = false;
    std::int64_t k_offset = 0;
    std::int64_t j_next = 0;

    // scratch buffers reused across calls
    AlignedBuffer<double> scratch_x_interf;
    AlignedBuffer<double> scratch_env_cell;
    AlignedBuffer<double> scratch_F;
    AlignedBuffer<double> scratch_D;
    AlignedBuffer<double> scratch_v;
    AlignedBuffer<double> scratch_rowH;
    AlignedBuffer<double> scratch_region;
    AlignedBuffer<double> scratch_burst;
    AlignedBuffer<double> scratch_cc;
    dsp::Candidates cell_cands;
    std::vector<double> gains_tmp;
    std::vector<dsp::AudioNormalizer::Segment> norm_segs;

    void init(const NativeDetectorConfig& cfg) {
        c = cfg;
        fs = c.sample_rate;
        ms = fs / 1000.0;
        S = std::max<std::int64_t>(1, py_round(c.grid_step_s * fs));
        n_sec = c.n_sec;
        padlen = c.padlen;
        if (n_sec < 1 || !c.sos || !c.zi) {
            throw std::invalid_argument("sos y zi son obligatorios");
        }
        sos.assign(c.sos, c.sos + static_cast<std::size_t>(n_sec) * 6U);
        zi.assign(c.zi, c.zi + static_cast<std::size_t>(n_sec) * 2U);

        hk = dsp::hilbert_fir(static_cast<std::int64_t>(std::ceil(1.5 * fs / c.band_lo_hz)));
        std::int64_t sm = std::max<std::int64_t>(1, py_round(c.envelope_smooth_ms * ms));
        sm += 1 - (sm % 2);
        smooth_len = sm;

        const std::size_t imp_len = 1U + static_cast<std::size_t>(0.2 * fs);
        std::vector<double> imp(imp_len, 0.0);
        imp[0] = 1.0;
        std::vector<double> imp_out(imp_len);
        dsp::sosfilt(sos.data(), n_sec, imp.data(), imp_len, nullptr, imp_out.data());
        double max_imp = 0.0;
        for (double v : imp_out) max_imp = std::max(max_imp, std::fabs(v));
        const double imp_thr = 1e-6 * max_imp;
        std::int64_t last_big = -1;
        for (std::size_t i = 0; i < imp_len; ++i) {
            if (std::fabs(imp_out[i]) > imp_thr) last_big = static_cast<std::int64_t>(i);
        }
        filter_memory = last_big >= 0 ? (last_big + 1) : 1;
        margin = 3 * filter_memory + static_cast<std::int64_t>(hk.size() / 2) + sm / 2 + 1;

        if (c.interference_filter) {
            const std::int64_t nfft =
                std::int64_t(1) << std::max<int>(6, static_cast<int>(py_round(std::log2(c.interference_ms * ms))));
            interf = std::make_unique<dsp::InterferenceFilter>(
                fs, c.band_lo_hz, c.band_hi_hz, nfft, c.interference_history_s,
                c.interference_percentile, c.interference_excess_db, c.interference_max_db);
            margin += nfft / 2;
        }

        OV = py_round(0.04 * fs);
        const double p_s = 3600.0 / c.bph;
        period_nom = p_s * fs;
        tmin = lift_time_s(c.amp_hi_deg, c.lift_angle_deg, c.bph) * fs * (1.0 - c.lift_margin);
        tmax = lift_time_s(c.amp_lo_deg, c.lift_angle_deg, c.bph) * fs * (1.0 + c.lift_margin);
        slack = 0.3 * ms;
        min_sp = c.min_subevent_spacing_ms * ms;
        merge = std::max<std::int64_t>(1, py_round(c.ripple_merge_ms * ms));
        post = c.post_group_ms * ms;
        pre = 0.5 * ms;
        dropout_min = std::max<std::int64_t>(1, py_round(c.dropout_min_ms * ms));
        W_noise = py_round(c.noise_window_s * fs);
        W_acq = py_round(std::max(c.acquisition_s, 8.0 * p_s) * fs);
        tpl_search = std::max<std::int64_t>(1, py_round(c.template_search_ms * ms));
        const double per_parity_s = 2.0 * p_s;
        tpl_refresh = std::max<int>(
            c.template_refresh_beats,
            static_cast<int>(std::ceil(c.template_refresh_min_s / per_parity_s - 1e-9)));
        if (c.quartz) {
            tpl_pre = tpl_post = static_cast<std::int64_t>(std::ceil(c.quartz_template_ms * ms));
            tpl_burst = tpl_pre;
        } else {
            tpl_pre = static_cast<std::int64_t>(std::ceil(tmax + 2.0 * ms));
            tpl_post = static_cast<std::int64_t>(std::ceil(c.template_post_ms * ms));
            tpl_burst = py_round(3.0 * ms);
        }
        q_on = (c.quartz != 0) && (c.quartz_onset != 0) && (period_nom >= 40.0 * ms);
        q_fir = dsp::lowpass_fir(fs, c.quartz_onset_lowpass_hz);
        q_fir_rev.assign(q_fir.rbegin(), q_fir.rend());
        q_pad = static_cast<std::int64_t>((q_fir.size() - 1) / 2);
        q_base0 = py_round(15.0 * ms);
        q_base1 = py_round(4.0 * ms);
        q_win = py_round(3.0 * ms);
        q_cen = py_round(1.0 * ms);
        q_quiet = std::max<std::int64_t>(2, py_round(0.5 * ms));

        p1_on = (c.p1_onset != 0) && (c.quartz == 0);
        p1_back = py_round(c.p1_onset_back_ms * ms);
        p1_fwd = py_round(c.p1_onset_fwd_ms * ms);
        p1_base = std::max<std::int64_t>(4, py_round(c.p1_onset_base_ms * ms));
        p1_hold = std::max<std::int64_t>(1, py_round(c.p1_onset_min_ms * ms));
        p1_peak = std::max<std::int64_t>(1, py_round(c.p1_onset_peak_ms * ms));

        m_al = std::max<std::int64_t>(1, py_round(c.template_align_smooth_ms * ms));

        if (c.normalize) {
            dsp::NormalizerConfig ncfg;
            ncfg.sample_rate = fs;
            ncfg.clip_level = c.saturation_level;
            ncfg.min_follower = 1.0;
            ncfg.release_s = kInf;
            normalizer = std::make_unique<dsp::AudioNormalizer>(ncfg);
        }
        raw = AbsBuf(static_cast<std::size_t>(4 * S));
        env = AbsBuf(static_cast<std::size_t>(8 * S));
        const std::size_t tpl_len = static_cast<std::size_t>(tpl_pre + tpl_post);
        tpl[0].init(c.template_beats, tpl_len);
        tpl[1].init(c.template_beats, tpl_len);
        reset();
    }

    void reset() {
        if (normalizer) normalizer->reset();
        started = false;
        finished = false;
        expected = 0;
        first_index = 0;
        raw.reset(0);
        env.reset(0);
        run_start = 0;
        samples = 0;
        emitted = 0;
        missed = 0;
        gap_beats = 0;
        gaps = 0;
        rejected = 0;
        relocks = 0;
        has_lock_sample = false;
        lock_sample = 0.0;
        n_p[0] = n_p[1] = n_p[2] = 0;
        warnings.clear();
        locked = false;
        ever_locked = false;
        period = period_nom;
        has_last_k = false;
        last_k = 0;
        has_last_ref = false;
        last_ref = 0.0;
        tlift2.clear();
        tlift3.clear();
        tlift_cached = false;
        has_tlift_cache = false;
        tl_obs.clear();
        tl_fwd.clear();
        after_gap = false;
        tpl[0].reset_all();
        tpl[1].reset_all();
        fine_center = kNaN;
        fine_p1_tpl = kNaN;
        model.clear();
        coef = ModelCoef{};
        missed_run = 0;
        outliers.clear();
        has_k_offset = false;
        k_offset = 0;
        j_next = 0;
        start_run(0);
    }

    void start_run(std::int64_t start) {
        run_start = start;
        raw.reset(start);
        env.reset(start);
        if (interf) interf->reset(start);
        next_cell = floor_div(start, S);
        first_cell = next_cell;
        cand_cell = next_cell;
        cand_end = start;
        cands.clear();
        floors.clear();
        zones.clear();
        dropouts.clear();
        has_zero_start = false;
        zero_start = 0;
        acq_from = static_cast<double>(start + filter_memory);
        rej_from = static_cast<double>(start);
    }

    void close_zero_run(std::int64_t end_idx) {
        if (has_zero_start) {
            register_dropout(zero_start, end_idx);
            has_zero_start = false;
        }
    }

    void register_dropout(std::int64_t a, std::int64_t b) {
        if (b - a < dropout_min) return;
        dropouts.emplace_back(a, b);
        ++gaps;
        after_gap = true;
    }

    void scan_zeros(std::int64_t a, const double* x, std::size_t n) {
        bool any_zero = false;
        for (std::size_t i = 0; i < n; ++i) {
            if (x[i] == 0.0) {
                any_zero = true;
                break;
            }
        }
        if (!any_zero) {
            close_zero_run(a);
            return;
        }
        std::size_t i = 0;
        while (i < n) {
            while (i < n && x[i] != 0.0) ++i;
            if (i == n) break;
            const std::size_t s = i;
            while (i < n && x[i] == 0.0) ++i;
            const std::size_t e = i;
            std::int64_t s_abs = a + static_cast<std::int64_t>(s);
            const std::int64_t e_abs = a + static_cast<std::int64_t>(e);
            if (s == 0 && has_zero_start) {
                s_abs = zero_start;
            } else if (has_zero_start) {
                register_dropout(zero_start, a);
            }
            has_zero_start = false;
            if (e == n) {
                has_zero_start = true;
                zero_start = s_abs;
            } else {
                register_dropout(s_abs, e_abs);
            }
        }
    }

    bool in_dropout(double a, double b) const {
        for (const auto& d : dropouts) {
            if (static_cast<double>(d.first) < b && a < static_cast<double>(d.second)) return true;
        }
        return has_zero_start && b > static_cast<double>(zero_start);
    }

    double tol() const { return c.phase_tolerance * period; }

    double predict(std::int64_t j) const {
        if (!coef.valid) {
            if (!model.empty()) {
                return model.back().t_ref + static_cast<double>(j - model.back().j) * period;
            }
            return phase0 + static_cast<double>(j) * period;
        }
        const double signpar = ((j & 1) == 0) ? 1.0 : -1.0;
        return coef.a + coef.b * static_cast<double>(j - coef.jl) + coef.cpar * signpar;
    }

    double gate() const {
        if (!coef.valid || model.size() < 6U) return kNaN;
        const double min_ms =
            c.quartz ? std::max(c.outlier_min_ms, c.quartz_outlier_min_ms) : c.outlier_min_ms;
        return std::max(c.outlier_sigma * coef.scale, min_ms * 1e-3 * fs);
    }

    double tlift_ref() {
        if (!tlift_cached) {
            tlift_cached = true;
            if (tlift3.size() >= 8U) {
                has_tlift_cache = true;
                tlift_cache_val = deque_median(tlift3);
            } else if (tlift2.size() >= 8U) {
                has_tlift_cache = true;
                tlift_cache_val = deque_median(tlift2);
            } else {
                has_tlift_cache = false;
                tlift_cache_val = kNaN;
            }
        }
        return has_tlift_cache ? tlift_cache_val : kNaN;
    }

    std::pair<double, double> lift_window() {
        const double ref = tlift_ref();
        if (is_nan(ref)) return {tmin, tmax};
        const double g = std::max(c.lift_track_tolerance * ref, 0.5e-3 * fs);
        return {std::max(tmin, ref - g), std::min(tmax, ref + g)};
    }

    bool confirm_lift(double tl, std::deque<double>& hist) {
        const double tol_l = std::max(c.lift_track_tolerance * tl, 0.5e-3 * fs);
        int agree = 0;
        for (double prev : hist) {
            if (std::fabs(prev - tl) <= tol_l) ++agree;
        }
        if (hist.size() == 8U) hist.pop_front();
        hist.push_back(tl);
        if (!is_nan(tlift_ref())) return true;
        return agree >= 2;
    }

    void warn_interference(const dsp::InterferenceFilter& f) {
        if (f.peak_excess_db < kInterferenceWarnDb) return;
        for (const auto& w : warnings) {
            if (starts_with(w, "interferencia")) return;
        }
        char buf[256];
        std::snprintf(buf, sizeof(buf),
                      "interferencia a %.1f kHz, %.0f dB sobre el ruido de fondo (filtrada): "
                      "aleja el micrófono de cargadores, pantallas o reguladores de luz",
                      f.peak_hz / 1000.0, f.peak_excess_db);
        warnings.emplace_back(buf);
    }

    void warn_no_lock(double end_idx) {
        if (ever_locked ||
            (end_idx - static_cast<double>(first_index)) <
                std::max(5.0 * fs, 2.0 * static_cast<double>(W_acq))) {
            return;
        }
        for (const auto& w : warnings) {
            if (starts_with(w, "sin enganche")) return;
        }
        char buf[256];
        std::snprintf(buf, sizeof(buf),
                      "sin enganche tras %.1f s: ¿BPH mal configurado (%g) o señal sin batimientos?",
                      (end_idx - static_cast<double>(first_index)) / fs, c.bph);
        warnings.emplace_back(buf);
    }

    void lose_lock() {
        locked = false;
        const double last = has_last_ref ? last_ref : static_cast<double>(cand_end);
        acq_from = std::max(acq_from, last + period * 0.5);
    }

    void refit() {
        const std::size_t n = model.size();
        if (n < 3U) {
            coef.valid = false;
            return;
        }
        const std::int64_t jl = model.back().j;
        const double t0 = model.back().t_ref;
        const bool parity = (n >= 6U);
        const int dim = parity ? 3 : 2;

        std::vector<double> dx(n), sp(n), y(n), w(n, 1.0), r_abs(n), r_copy(n);
        for (std::size_t i = 0; i < n; ++i) {
            dx[i] = static_cast<double>(model[i].j - jl);
            sp[i] = ((model[i].j & 1) == 0) ? 1.0 : -1.0;
            y[i] = model[i].t_ref - t0;
        }
        const double floor_v = 0.02e-3 * fs;
        double scale = floor_v;
        double cur_coef[3] = {0.0, 0.0, 0.0};

        for (int iter = 0; iter < 3; ++iter) {
            double A[3][3] = {};
            double rhs[3] = {};
            for (std::size_t i = 0; i < n; ++i) {
                const double wi = w[i];
                const double row[3] = {1.0, dx[i], sp[i]};
                for (int r = 0; r < dim; ++r) {
                    const double rw = wi * row[r];
                    rhs[r] += rw * y[i];
                    for (int col = r; col < dim; ++col) A[r][col] += rw * row[col];
                }
            }
            for (int r = 0; r < dim; ++r)
                for (int col = 0; col < r; ++col) A[r][col] = A[col][r];

            double next_coef[3] = {};
            if (solve_small(dim, A, rhs, next_coef)) {
                for (int k = 0; k < dim; ++k) cur_coef[k] = next_coef[k];
            } else {
                // Degenerate collinear sample (e.g. only 1 parity present in early model):
                // fall back to 2-param solve and leave cpar = 0.
                double A2[3][3] = {{A[0][0], A[0][1], 0.0}, {A[1][0], A[1][1], 0.0}, {0, 0, 1}};
                double r2[3] = {rhs[0], rhs[1], 0.0};
                double c2[3] = {};
                if (solve_small(2, A2, r2, c2)) {
                    cur_coef[0] = c2[0];
                    cur_coef[1] = c2[1];
                    cur_coef[2] = 0.0;
                }
            }
            for (std::size_t i = 0; i < n; ++i) {
                const double pred =
                    cur_coef[0] + cur_coef[1] * dx[i] + (parity ? cur_coef[2] * sp[i] : 0.0);
                r_abs[i] = std::fabs(y[i] - pred);
                r_copy[i] = r_abs[i];
            }
            scale = std::max(kMadToSigma * exact_median(r_copy.data(), n), floor_v);
            for (std::size_t i = 0; i < n; ++i) {
                w[i] = std::min(1.0, 1.5 * scale / std::max(r_abs[i], kTiny));
            }
        }
        coef.valid = true;
        coef.a = cur_coef[0] + t0;
        coef.b = cur_coef[1];
        coef.cpar = parity ? cur_coef[2] : 0.0;
        coef.jl = jl;
        coef.scale = scale;
        if (0.5 * period_nom < coef.b && coef.b < 1.5 * period_nom) period = coef.b;
        if (parity) {
            const double dev = period / period_nom - 1.0;
            if (std::fabs(dev) > c.period_warn) {
                bool has_w = false;
                for (const auto& ws : warnings) {
                    if (starts_with(ws, "periodo observado")) {
                        has_w = true;
                        break;
                    }
                }
                if (!has_w) {
                    char buf[256];
                    std::snprintf(buf, sizeof(buf),
                                  "periodo observado %+.2f %% respecto al nominal (%g BPH): "
                                  "¿BPH mal configurado?",
                                  dev * 100.0, c.bph);
                    warnings.emplace_back(buf);
                }
            }
        }
    }

    void maybe_reseed() {
        const double g = gate();
        const std::size_t need =
            static_cast<std::size_t>(c.quartz ? c.quartz_reseed_beats : 3);
        if (outliers.size() < need || is_nan(g)) return;
        double rmin = outliers[0].residual, rmax = outliers[0].residual;
        for (const auto& o : outliers) {
            rmin = std::min(rmin, o.residual);
            rmax = std::max(rmax, o.residual);
        }
        if ((rmax - rmin) <= 2.0 * g) {
            model.clear();
            for (const auto& o : outliers) {
                if (model.size() == static_cast<std::size_t>(c.model_beats)) model.pop_front();
                model.push_back({o.j, o.t_ref});
            }
            outliers.clear();
            refit();
        }
    }

    std::pair<double, double> compute_floor(std::int64_t cell) {
        for (const auto& fe : floors) {
            if (fe.cell == cell) return {fe.f, fe.d};
        }
        std::int64_t hi = (cell - 1) * S;
        std::int64_t lo = std::max({run_start, hi - W_noise, env.start});
        if (hi - lo < S / 2) {
            lo = std::max(run_start, env.start);
            hi = std::min(lo + S, env.end());
        }
        const double* e = env.ptr(lo);
        const std::int64_t len = hi - lo;
        std::pair<double, double> res{kTiny, kTiny};
        if (len > 0) {
            thread_local std::vector<std::uint8_t> keep;
            keep.resize(static_cast<std::size_t>(len));
            std::size_t keep_n = 0;
            for (std::int64_t i = 0; i < len; ++i) {
                const bool pos_e = e[i] > 0.0;
                keep[static_cast<std::size_t>(i)] = pos_e ? 1 : 0;
                if (pos_e) ++keep_n;
            }
            for (const auto& z : zones) {
                if (z.second <= static_cast<double>(lo) || z.first >= static_cast<double>(hi))
                    continue;
                const std::int64_t z0 = std::max<std::int64_t>(0, static_cast<std::int64_t>(z.first) - lo);
                const std::int64_t z1 = std::min<std::int64_t>(
                    len, std::max<std::int64_t>(0, static_cast<std::int64_t>(std::ceil(z.second)) - lo));
                for (std::int64_t i = z0; i < z1; ++i) {
                    if (keep[static_cast<std::size_t>(i)]) {
                        keep[static_cast<std::size_t>(i)] = 0;
                        --keep_n;
                    }
                }
            }
            if (static_cast<double>(keep_n) < 0.25 * static_cast<double>(len)) {
                keep_n = 0;
                for (std::int64_t i = 0; i < len; ++i) {
                    const bool pos_e = e[i] > 0.0;
                    keep[static_cast<std::size_t>(i)] = pos_e ? 1 : 0;
                    if (pos_e) ++keep_n;
                }
            }
            if (keep_n > 0) {
                scratch_v.resize(keep_n);
                std::size_t w = 0;
                for (std::int64_t i = 0; i < len; ++i)
                    if (keep[static_cast<std::size_t>(i)]) scratch_v[w++] = e[i];
                const double med = exact_median(scratch_v.data(), keep_n);
                w = 0;
                for (std::int64_t i = 0; i < len; ++i)
                    if (keep[static_cast<std::size_t>(i)]) scratch_v[w++] = std::fabs(e[i] - med);
                const double mad = exact_median(scratch_v.data(), keep_n);
                res = {std::max(med, kTiny), std::max({kMadToSigma * mad, 0.05 * med, kTiny})};
            }
        }
        floors.push_back({cell, res.first, res.second});
        return res;
    }

    void env_cell(std::int64_t m) {
        const std::int64_t a = std::max(m * S - margin, run_start);
        const std::int64_t b = std::min((m + 1) * S + margin, raw.end());
        const std::int64_t c0 = std::max(m * S, run_start);
        const std::int64_t c1 = std::min((m + 1) * S, raw.end());
        const std::size_t span = static_cast<std::size_t>(b - a);
        const double* x_ptr = raw.ptr(a);

        if (interf) {
            interf->add_frames(raw.a.data(), raw.n, raw.start, c0);
            if (interf->gains(c0, gains_tmp)) {
                scratch_x_interf.resize(span);
                interf->apply(x_ptr, span, gains_tmp.data(), scratch_x_interf.data());
                x_ptr = scratch_x_interf.data();
                warn_interference(*interf);
            }
        }
        const std::size_t out_n = static_cast<std::size_t>(c1 - c0);
        scratch_env_cell.resize(out_n);
        dsp::compute_cell_envelope(x_ptr, span, sos.data(), n_sec, zi.data(), hk.data(), hk.size(),
                                   smooth_len, c0 - a, c1 - a, padlen, scratch_env_cell.data());
        env.append(scratch_env_cell.data(), out_n);
        scan_zeros(c0, raw.ptr(c0), out_n);
    }

    void detect_cell(std::int64_t cell) {
        const std::int64_t lo = std::max({cell * S - OV, run_start, env.start});
        const std::int64_t hi = std::min((cell + 1) * S + OV, env.end());
        if (hi - lo < 3) return;
        const std::size_t n = static_cast<std::size_t>(hi - lo);
        const std::int64_t c_lo = floor_div(lo, S);
        const std::int64_t c_hi = floor_div(hi - 1, S);
        scratch_F.resize(n);
        scratch_D.resize(n);
        for (std::int64_t k = c_lo; k <= c_hi; ++k) {
            const auto fl = compute_floor(k);
            const std::int64_t seg_a = std::max(lo, k * S) - lo;
            const std::int64_t seg_b = std::min(hi, (k + 1) * S) - lo;
            for (std::int64_t i = seg_a; i < seg_b; ++i) {
                scratch_F[static_cast<std::size_t>(i)] = fl.first;
                scratch_D[static_cast<std::size_t>(i)] = fl.second;
            }
        }
        cell_cands.clear();
        dsp::detect_cell_candidates(env.ptr(lo), scratch_F.data(), scratch_D.data(), n,
                                    c.threshold_hi, c.threshold_lo, merge, c.split_ratio, min_sp,
                                    c.onset_fraction, cell * S, (cell + 1) * S, lo, run_start,
                                    cell_cands);
        cands.extend(cell_cands);
    }

    void try_acquire() {
        const std::int64_t end = cand_end;
        const double start = std::max(acq_from, static_cast<double>(end - W_acq));
        if (static_cast<double>(end) - start < static_cast<double>(W_acq)) return;

        const auto [i0, i1] = cands.range(start, static_cast<double>(end));
        thread_local std::vector<double> strong_t, strong_w;
        strong_t.clear();
        strong_w.clear();
        for (std::size_t i = i0; i < i1; ++i) {
            const double snr = 20.0 * std::log10(std::max(cands.peak[i], kTiny) / cands.floor_v[i]);
            if (snr >= c.min_anchor_snr_db) {
                strong_t.push_back(cands.onset[i]);
                strong_w.push_back(std::min(cands.peak[i] / cands.floor_v[i], 1e3));
            }
        }
        const double P2n = 2.0 * period_nom;
        const double need_init = c.acquisition_min_fraction * static_cast<double>(W_acq) / P2n;
        if (static_cast<double>(strong_t.size()) < std::max(3.0, need_init)) {
            warn_no_lock(static_cast<double>(end));
            return;
        }
        const double bin_w = 0.5e-3 * fs;
        const double step = bin_w * P2n / (2.0 * static_cast<double>(W_acq));
        const std::int64_t n_p =
            static_cast<std::int64_t>(std::ceil(2.0 * c.period_search * P2n / step)) + 1;
        const double inv_bin = 1.0 / bin_w;

        double best_H = -1.0;
        std::int64_t best_r = 0;
        std::int64_t best_b = 0;
        double best_P2 = P2n;
        const std::size_t n_strong = strong_t.size();

        for (std::int64_t r = 0; r < n_p; ++r) {
            const double frac = n_p > 1
                                    ? (-c.period_search +
                                       (2.0 * c.period_search) * static_cast<double>(r) /
                                           static_cast<double>(n_p - 1))
                                    : 0.0;
            const double p2 = P2n * (1.0 + frac);
            const double inv_p2 = 1.0 / p2;
            const std::int64_t nb = static_cast<std::int64_t>(std::ceil(p2 * inv_bin));
            scratch_rowH.resize(static_cast<std::size_t>(nb));
            std::memset(scratch_rowH.data(), 0, static_cast<std::size_t>(nb) * sizeof(double));
            double* h = scratch_rowH.data();

            for (std::size_t m = 0; m < n_strong; ++m) {
                const double dt = strong_t[m] - start;
                double ph = dt - std::floor(dt * inv_p2) * p2;
                if (ph < 0.0) ph += p2;
                if (ph >= p2) ph -= p2;
                const std::int64_t bi = std::min<std::int64_t>(static_cast<std::int64_t>(ph * inv_bin), nb - 1);
                const std::int64_t b_m1 = (bi == 0) ? (nb - 1) : (bi - 1);
                const std::int64_t b_p1 = (bi + 1 == nb) ? 0 : (bi + 1);
                const double wt = strong_w[m];
                h[b_m1] += wt;
                h[bi] += wt;
                h[b_p1] += wt;
            }
            for (std::int64_t b = 0; b < nb; ++b) {
                if (h[b] > best_H) {
                    best_H = h[b];
                    best_r = r;
                    best_b = b;
                    best_P2 = p2;
                }
            }
        }
        (void)best_r;
        const double center = (static_cast<double>(best_b) + 0.5) * bin_w;
        const double half_p2 = 0.5 * best_P2;
        const double inv_best_p2 = 1.0 / best_P2;
        std::size_t members = 0;
        double sum_wdev = 0.0;
        double sum_w = 0.0;
        for (std::size_t m = 0; m < n_strong; ++m) {
            const double dt = strong_t[m] - start;
            double ph = dt - std::floor(dt * inv_best_p2) * best_P2;
            if (ph < 0.0) ph += best_P2;
            if (ph >= best_P2) ph -= best_P2;
            double xdev = (ph - center + half_p2);
            double dev = xdev - std::floor(xdev * inv_best_p2) * best_P2 - half_p2;
            if (std::fabs(dev) <= 1.5 * bin_w) {
                ++members;
                sum_wdev += strong_w[m] * dev;
                sum_w += strong_w[m];
            }
        }
        const double need_m =
            std::max(3.0, c.acquisition_min_fraction * static_cast<double>(W_acq) / best_P2);
        if (static_cast<double>(members) < need_m || !(sum_w > 0.0)) {
            warn_no_lock(static_cast<double>(end));
            return;
        }
        const double phase = start + center + sum_wdev / sum_w;
        period = best_P2 * 0.5;
        phase0 = phase;
        locked = true;
        model.clear();
        coef = ModelCoef{};
        missed_run = 0;
        outliers.clear();
        has_k_offset = false;
        k_offset = 0;
        tpl[0].off.clear();
        tpl[1].off.clear();
        const double ttol = tol();
        j_next = static_cast<std::int64_t>(std::ceil((acq_from - phase) / period));
        rej_from = std::max(rej_from, phase + static_cast<double>(j_next) * period - ttol);
        if (ever_locked) {
            ++relocks;
        } else {
            has_lock_sample = true;
            lock_sample = static_cast<double>(end);
        }
        ever_locked = true;
    }

    void count_rejected(double until) {
        const auto [i0, i1] = cands.range(rej_from, until);
        for (std::size_t i = i0; i < i1; ++i) {
            if (!cands.used[i]) {
                const double snr = 20.0 * std::log10(std::max(cands.peak[i], kTiny) / cands.floor_v[i]);
                if (snr >= c.min_anchor_snr_db) ++rejected;
                cands.used[i] = 1;
            }
        }
        rej_from = std::max(rej_from, until);
    }

    std::vector<std::size_t> members_range(double lo_t, double hi_t, double height, double min_ratio,
                                           std::size_t ai) const {
        const auto [k0, k1] = cands.range(lo_t, hi_t);
        std::vector<std::size_t> idx;
        for (std::size_t k = k0; k < k1; ++k) {
            if (k == ai || cands.used[k]) continue;
            if ((cands.peak[k] - cands.floor_v[k]) >= c.subevent_rel_threshold * height &&
                cands.peak[k] >= cands.floor_v[k] * min_ratio) {
                idx.push_back(k);
            }
        }
        return idx;
    }

    bool is_p1(double a, double x, const std::vector<std::size_t>& back_idx) {
        if (!is_nan(tlift_ref())) return true;
        const double tl = a - x;
        const double mid = x + kP2Fraction * tl;
        for (std::size_t idx : back_idx) {
            if (std::fabs(cands.onset[idx] - mid) <= 0.2 * tl) return true;
        }
        const double a1 = amplitude_from_lift_time(tl / fs, c.lift_angle_deg, c.bph);
        const double a2 =
            amplitude_from_lift_time(tl / (1.0 - kP2Fraction) / fs, c.lift_angle_deg, c.bph);
        const bool v1 = (c.amp_lo_deg <= a1 && a1 <= c.amp_hi_deg);
        const bool v2 = (c.amp_lo_deg <= a2 && a2 <= c.amp_hi_deg);
        if (v1 != v2) return v1;
        if (!v1) return true;
        auto dist = [&](double amp) {
            return std::max({0.0, c.typical_amp_lo_deg - amp, amp - c.typical_amp_hi_deg});
        };
        return dist(a1) <= dist(a2);
    }

    // --------------------------------------------------------- template onset helpers
    void anchor_frame(Tpl& t) {
        const std::int64_t b0 = tpl_pre - tpl_burst;
        const std::size_t cur_n = t.length - static_cast<std::size_t>(b0);
        AlignedBuffer<double> cur(cur_n);
        dsp::moving_average_slice(t.env.data(), t.length, m_al, b0, static_cast<std::int64_t>(t.length),
                                  cur.data());
        if (!t.has_anchor) {
            if (is_finite(t.onset_p3)) {
                t.anchor = std::move(cur);
                t.has_anchor = true;
                t.onset_anchor = t.onset_p3;
                t.lag = 0.0;
            }
            return;
        }
        const std::size_t s = static_cast<std::size_t>(tpl_search);
        const std::size_t r_off = (s > 0) ? s : 0U;
        const std::size_t r_len = (s > 0) ? (t.anchor.size() - 2 * s) : t.anchor.size();
        const double* ref_p = t.anchor.data() + r_off;
        double mean_r = neumaier_sum(ref_p, r_len) / static_cast<double>(r_len);
        AlignedBuffer<double> rz(r_len);
        for (std::size_t j = 0; j < r_len; ++j) rz[j] = ref_p[j] - mean_r;
        const double norm_rz = kernels().dot(rz.data(), rz.data(), r_len);

        const std::size_t n_cc = cur_n - r_len + 1;
        scratch_cc.resize(n_cc);
        const auto dot = kernels().dot;
        std::size_t best_i = 0;
        double best_c = -kInf;
        for (std::size_t k = 0; k < n_cc; ++k) {
            const double v = dot(cur.data() + k, rz.data(), r_len);
            scratch_cc[k] = v;
            if (v > best_c) {
                best_c = v;
                best_i = k;
            }
        }
        const double* wp = cur.data() + best_i;
        double mean_w = neumaier_sum(wp, r_len) / static_cast<double>(r_len);
        double norm_wz = 0.0;
        for (std::size_t j = 0; j < r_len; ++j) {
            const double d = wp[j] - mean_w;
            norm_wz += d * d;
        }
        const double den = std::sqrt(norm_rz * norm_wz);
        double d = 0.0;
        if (0 < best_i && best_i + 1 < n_cc) {
            const double q = scratch_cc[best_i - 1] - 2.0 * scratch_cc[best_i] + scratch_cc[best_i + 1];
            d = (q < 0.0) ? (0.5 * (scratch_cc[best_i - 1] - scratch_cc[best_i + 1]) / q) : 0.0;
        }
        const double lag = static_cast<double>(best_i) + d - static_cast<double>(tpl_search);
        if (den <= kTiny || (scratch_cc[best_i] / den) < c.template_reanchor_corr || best_i == 0 ||
            best_i == n_cc - 1) {
            t.onset_anchor = is_finite(t.onset_p3) ? t.onset_p3 : (t.onset_anchor + t.lag);
            t.anchor = std::move(cur);
            t.has_anchor = true;
            t.lag = 0.0;
            return;
        }
        t.lag = lag;
    }

    double quartz_onset_tpl(const double* tm, std::size_t n) {
        const std::size_t n0 = std::max<std::size_t>(2, static_cast<std::size_t>(py_round(2.0 * ms)));
        scratch_v.assign(tm, n0);
        const double base = exact_median(scratch_v.data(), n0);
        scratch_v.assign(tm, n0);
        const double spread = kMadToSigma * exact_median_abs_dev(scratch_v.data(), n0, base);
        std::size_t mx = 0;
        for (std::size_t i = 1; i < n; ++i)
            if (tm[i] > tm[mx]) mx = i;
        const double thr =
            base + std::max(c.template_p3_fraction * (tm[mx] - base), 6.0 * spread);
        std::size_t first_above = n;
        for (std::size_t i = n0; i <= mx; ++i) {
            if (tm[i] > thr) {
                first_above = i;
                break;
            }
        }
        if (first_above == n) return kNaN;
        const std::size_t j = first_above - 1;
        const double dd = tm[j + 1] - tm[j];
        return static_cast<double>(j) + (dd > 0.0 ? (thr - tm[j]) / dd : 0.0);
    }

    double front_onset_tpl(const double* tm, std::size_t n) {
        const std::int64_t m0 = std::max<std::int64_t>(0, tpl_pre - py_round(1.5 * ms));
        std::int64_t mx = m0;
        for (std::int64_t i = m0 + 1; i < static_cast<std::int64_t>(n); ++i)
            if (tm[i] > tm[mx]) mx = i;
        const double ref = tlift_ref();
        double back_ms = 2.0 * c.template_burst_ms;
        if (!is_nan(ref))
            back_ms = std::min(back_ms, std::max(1.5, c.template_front_lift_fraction * ref / ms));
        const std::int64_t h = std::max<std::int64_t>(1, py_round(0.1 * ms));
        const std::int64_t lo = std::max(h, mx - py_round(back_ms * ms));
        mx = std::min(mx, static_cast<std::int64_t>(n) - 1 - h);
        if (mx - lo < 2) return kNaN;
        const std::size_t gn = static_cast<std::size_t>(mx - lo + 1);
        thread_local std::vector<double> g;
        g.resize(gn);
        double gmax = -kInf;
        const double inv_2h = 1.0 / static_cast<double>(2 * h);
        for (std::size_t k = 0; k < gn; ++k) {
            const std::int64_t p = lo + static_cast<std::int64_t>(k);
            g[k] = (tm[p + h] - tm[p - h]) * inv_2h;
            if (g[k] > gmax) gmax = g[k];
        }
        if (!(gmax > 0.0)) return kNaN;
        const double gthr = c.template_front_fraction * gmax;
        std::size_t s = 0;
        while (s < gn && g[s] < gthr) ++s;
        if (s == gn) s = 0;  // matches argmax(bool) = 0 if somehow none, though gmax >= gthr
        std::size_t e = s;
        while (e + 1 < gn && g[e + 1] >= gthr) ++e;
        std::size_t best_k = s;
        for (std::size_t k = s + 1; k <= e; ++k)
            if (g[k] > g[best_k]) best_k = k;
        const std::int64_t i = lo + static_cast<std::int64_t>(best_k);
        const std::int64_t p0 = std::max<std::int64_t>(0, i - py_round(1.0 * ms));
        double fl = tm[p0];
        for (std::int64_t q = p0 + 1; q <= i; ++q) fl = std::min(fl, tm[q]);
        const double slope = g[static_cast<std::size_t>(i - lo)];
        return std::max(static_cast<double>(p0), static_cast<double>(i) - (tm[i] - fl) / slope);
    }

    std::pair<double, double> template_onsets(const double* tm, std::size_t n) {
        const std::int64_t m0 = std::max<std::int64_t>(0, tpl_pre - py_round(1.5 * ms));
        std::int64_t mx = m0;
        for (std::int64_t i = m0 + 1; i < static_cast<std::int64_t>(n); ++i)
            if (tm[i] > tm[mx]) mx = i;
        const std::int64_t burst = py_round(c.template_burst_ms * ms);
        const std::int64_t b0 = std::max<std::int64_t>(0, mx - burst);
        const std::int64_t bb = std::max<std::int64_t>(0, mx - 2 * burst);
        double base = tm[bb];
        for (std::int64_t i = bb + 1; i <= mx; ++i) base = std::min(base, tm[i]);
        const double height = tm[mx] - base;
        const double lobe_thr = base + c.template_lobe_fraction * height;
        std::int64_t first = b0;
        for (std::int64_t i = b0; i <= mx; ++i) {
            if (tm[i] >= lobe_thr) {
                first = i;
                break;
            }
        }
        const double level = base + c.template_p3_fraction * height;
        std::int64_t j = -1;
        for (std::int64_t i = first - 1; i >= bb; --i) {
            if (tm[i] < level) {
                j = i;
                break;
            }
        }
        if (j < 0) return {kNaN, kNaN};
        const double d3 = tm[j + 1] - tm[j];
        const double p3 = static_cast<double>(j) + (d3 > 0.0 ? (level - tm[j]) / d3 : 0.0);

        const std::int64_t n0 = std::max<std::int64_t>(2, py_round(1.0 * ms));
        const std::int64_t lo = std::max(n0, static_cast<std::int64_t>(std::floor(p3 - tmax)));
        const std::int64_t hi = static_cast<std::int64_t>(std::floor(p3 - tmin));
        if (hi <= lo) return {p3, kNaN};

        scratch_v.assign(tm, static_cast<std::size_t>(hi));
        const double b1 = exact_median(scratch_v.data(), static_cast<std::size_t>(hi));
        thread_local std::vector<double> low;
        low.clear();
        for (std::int64_t i = 0; i < hi; ++i)
            if (tm[i] <= b1) low.push_back(b1 - tm[i]);
        const double spread =
            low.empty() ? kNaN : kMadToSigma * exact_median(low.data(), low.size());
        const double thr =
            b1 + std::max(c.template_p1_fraction * (tm[mx] - b1), 6.0 * spread);
        const std::int64_t min_run = std::max<std::int64_t>(1, py_round(c.template_p1_min_ms * ms));

        std::int64_t first_s = -1;
        std::int64_t idx = lo;
        while (idx < hi) {
            while (idx < hi && !(tm[idx] > thr)) ++idx;
            if (idx == hi) break;
            const std::int64_t s = idx;
            while (idx < hi && tm[idx] > thr) ++idx;
            if ((idx - s) >= min_run) {
                first_s = s - lo;
                break;
            }
        }
        if (first_s <= 0) return {p3, kNaN};  // not found (-1) or starts above noise (0)
        const std::int64_t j1 = lo + first_s - 1;
        const double d1 = tm[j1 + 1] - tm[j1];
        const double p1 = static_cast<double>(j1) + (d1 > 0.0 ? (thr - tm[j1]) / d1 : 0.0);
        return {p3, p1};
    }

    std::pair<int, int> p3_methods() const {
        const Tpl& t0 = tpl[0];
        const Tpl& t1 = tpl[1];
        if (!t0.has_env || !t1.has_env) return {0, 0};  // 0="nivel", 1="frente"
        auto amp = [&](double p3, double p1) -> double {
            const double tl = (p3 - p1) / fs;
            if (!(is_finite(tl) && tl > 0.0)) return kNaN;
            return amplitude_from_lift_time(tl, c.lift_angle_deg, c.bph);
        };
        const double a0 = amp(t0.onset_p3, t0.onset_p1);
        const double a1 = amp(t1.onset_p3, t1.onset_p1);
        if (!(is_finite(a0) && is_finite(a1))) return {0, 0};
        if (std::fabs(a0 - a1) <= c.template_amp_mismatch_deg) return {0, 0};
        bool has_best = false;
        double best_diff = 0.0;
        int best_cnt = 3;
        std::pair<int, int> best{0, 0};
        for (int m0 = 0; m0 <= 1; ++m0) {
            for (int m1 = 0; m1 <= 1; ++m1) {
                const double b0 = amp(m0 ? t0.onset_front : t0.onset_p3, t0.onset_p1);
                const double b1 = amp(m1 ? t1.onset_front : t1.onset_p3, t1.onset_p1);
                if (!(is_finite(b0) && is_finite(b1))) continue;
                const double diff = std::fabs(b0 - b1);
                const int cnt = m0 + m1;
                if (!has_best || diff < best_diff || (diff == best_diff && cnt < best_cnt)) {
                    has_best = true;
                    best_diff = diff;
                    best_cnt = cnt;
                    best = {m0, m1};
                }
            }
        }
        return best;
    }

    double fine_align(std::int64_t k, double tp) {
        Tpl& t = tpl[static_cast<std::size_t>(k & 1)];
        const std::int64_t pre_ = tpl_pre, post_ = tpl_post, s = tpl_search;
        double center;
        if (!t.has_env) {
            if (is_nan(gate())) return kNaN;
            center = tp;
        } else {
            const double off = t.off.empty() ? 0.0 : deque_median(t.off);
            const double ttol = tol();
            center = tp + std::min(std::max(off, -ttol), ttol);
        }
        const std::int64_t ci = py_round(center);
        const std::int64_t s0 = ci - pre_ - s;
        const std::int64_t s1 = ci + post_ + s;
        if (s0 < env.start || s1 > env.end() ||
            in_dropout(static_cast<double>(s0), static_cast<double>(s1))) {
            return kNaN;
        }
        const double* seg = env.ptr(s0);
        double lag = 0.0;
        if (!t.has_env) {
            lag = static_cast<double>(s);
        } else {
            const std::int64_t b0 = pre_ - tpl_burst;
            const std::int64_t b1 = pre_ + post_;
            const std::size_t blen = static_cast<std::size_t>(b1 - b0);
            if (!t.has_burst_bz) {
                t.burst_bz.resize(blen);
                dsp::moving_average_slice(t.env.data(), t.length, m_al, b0, b1, t.burst_bz.data());
                const double mean_b = neumaier_sum(t.burst_bz.data(), blen) / static_cast<double>(blen);
                for (std::size_t j = 0; j < blen; ++j) t.burst_bz[j] -= mean_b;
                t.burst_nb = std::sqrt(kernels().dot(t.burst_bz.data(), t.burst_bz.data(), blen));
                t.has_burst_bz = true;
            }
            const double nb = t.burst_nb;
            const std::size_t rlen = blen + static_cast<std::size_t>(2 * s);
            scratch_region.resize(rlen);
            dsp::moving_average_slice(seg, static_cast<std::size_t>(s1 - s0), m_al, b0,
                                      b0 + static_cast<std::int64_t>(rlen), scratch_region.data());
            const std::size_t n_cc = static_cast<std::size_t>(2 * s) + 1U;
            scratch_cc.resize(n_cc);
            const auto dot = kernels().dot;
            std::size_t best_i = 0;
            double best_c = -kInf;
            for (std::size_t idx = 0; idx < n_cc; ++idx) {
                const double v = dot(scratch_region.data() + idx, t.burst_bz.data(), blen);
                scratch_cc[idx] = v;
                if (v > best_c) {
                    best_c = v;
                    best_i = idx;
                }
            }
            const double* wp = scratch_region.data() + best_i;
            const double mean_w = neumaier_sum(wp, blen) / static_cast<double>(blen);
            double nw2 = 0.0;
            for (std::size_t j = 0; j < blen; ++j) {
                const double d = wp[j] - mean_w;
                nw2 += d * d;
            }
            const double den = nb * std::sqrt(nw2);
            const bool warming = (t.n_segs < c.template_min_beats);
            if (den <= kTiny || (!warming && (scratch_cc[best_i] / den) < c.template_min_corr)) {
                return kNaN;
            }
            double d = 0.0;
            if (0 < best_i && best_i + 1 < n_cc) {
                const double q =
                    scratch_cc[best_i - 1] - 2.0 * scratch_cc[best_i] + scratch_cc[best_i + 1];
                d = (q < 0.0) ? (0.5 * (scratch_cc[best_i - 1] - scratch_cc[best_i + 1]) / q)
                              : 0.0;
            }
            lag = static_cast<double>(best_i) + d;
        }
        const double origin = static_cast<double>(s0) + lag;
        const std::int64_t li = py_round(lag);
        t.add_seg(seg + li);
        if (t.off.size() == 16U) t.off.pop_front();
        t.off.push_back(origin + static_cast<double>(pre_) - tp);
        ++t.pending;
        const bool full = (t.n_segs == c.template_beats);
        if (!t.has_env || !full || t.pending >= tpl_refresh) {
            t.compute_median_env();
            t.has_burst_bz = false;
            if (c.quartz) {
                t.onset_p3 = quartz_onset_tpl(t.env.data(), t.length);
                t.onset_p1 = kNaN;
                t.onset_front = kNaN;
            } else {
                const auto [p3_v, p1_v] = template_onsets(t.env.data(), t.length);
                t.onset_p3 = p3_v;
                t.onset_p1 = p1_v;
                t.onset_front = front_onset_tpl(t.env.data(), t.length);
            }
            t.pending = 0;
            const bool anchor =
                (c.template_anchor_mode < 0) ? (c.quartz != 0) : (c.template_anchor_mode != 0);
            if (full && anchor) anchor_frame(t);
        }
        const double ref = t.onset_ref();
        fine_center = origin + ref;
        if (t.n_segs < c.template_min_beats || is_nan(ref)) return kNaN;
        if (!c.quartz && t.n_segs >= c.template_beats && is_finite(t.onset_p1)) {
            fine_p1_tpl = origin + t.onset_p1;
        }
        return origin + ref;
    }

    double p1_fine(std::int64_t k, double t_fine, double p1) {
        const Tpl& t = tpl[static_cast<std::size_t>(k & 1)];
        const double off = t.onset_ref() - t.onset_p1;
        double center;
        if (is_finite(t_fine) && is_finite(off)) {
            center = t_fine - off;
        } else if (is_finite(p1)) {
            center = p1;
        } else {
            return kNaN;
        }
        const std::int64_t ci = py_round(center);
        const std::int64_t a = ci - p1_back;
        const std::int64_t b = ci + p1_fwd;
        const std::int64_t b0 = a - p1_base;
        const std::int64_t b1 = b + p1_peak + 1;
        if (b0 < env.start || b1 > env.end() ||
            in_dropout(static_cast<double>(b0), static_cast<double>(b1))) {
            return kNaN;
        }
        const double* e = env.ptr(b0);
        double mu = 0.0, sg = 0.0;
        dsp::robust_level(e, static_cast<std::size_t>(p1_base), mu, sg);
        const double thr = mu + c.p1_onset_sigma * sg;
        const std::int64_t seg_n = b - a;
        const double* seg = e + p1_base;

        std::int64_t first_j = -1;
        std::int64_t idx = 0;
        while (idx < seg_n) {
            while (idx < seg_n && !(seg[idx] > thr)) ++idx;
            if (idx == seg_n) break;
            const std::int64_t rs = idx;
            while (idx < seg_n && seg[idx] > thr) ++idx;
            if ((idx - rs) >= p1_hold || idx == seg_n) {
                first_j = rs;
                break;
            }
        }
        if (first_j <= 0) return kNaN;
        std::int64_t j = first_j + p1_base;
        double peak = e[j];
        for (std::int64_t q = j + 1; q <= j + p1_peak; ++q) peak = std::max(peak, e[q]);
        const double level = std::max(thr, mu + c.p1_onset_fraction * (peak - mu));
        std::int64_t up = -1;
        for (std::int64_t q = 0; q <= p1_peak; ++q) {
            if (e[j + q] >= level) {
                up = q;
                break;
            }
        }
        if (up < 0) return kNaN;
        j += up;
        const double d = e[j] - e[j - 1];
        const double frac = (d > 0.0) ? ((level - e[j - 1]) / d) : 1.0;
        return static_cast<double>(b0 + j - 1) + std::min(std::max(frac, 0.0), 1.0);
    }

    std::pair<double, double> step_onset(double center) {
        const std::int64_t ci = py_round(center);
        const std::int64_t b0 = ci - q_base0;
        const std::int64_t b1 = ci - q_base1;
        const std::int64_t lo = ci - q_win;
        const std::int64_t hi = ci + q_win;
        const std::int64_t a = b0 - q_pad;
        const std::int64_t b = hi + q_pad + 1;
        if (a < raw.start || b > raw.end() ||
            in_dropout(static_cast<double>(a), static_cast<double>(b))) {
            return {kNaN, kNaN};
        }
        const std::size_t raw_n = static_cast<std::size_t>(b - a);
        const std::size_t fir_m = q_fir_rev.size();
        const std::size_t y_n = raw_n - fir_m + 1;
        thread_local AlignedBuffer<double> y, r;
        y.resize(y_n);
        dsp::convolve_valid(raw.ptr(a), raw_n, q_fir.data(), q_fir.size(), y.data());
        double mu = 0.0, sg = 0.0;
        dsp::robust_level(y.data(), static_cast<std::size_t>(b1 - b0), mu, sg);
        const std::size_t r_n = static_cast<std::size_t>(hi - lo + 1);
        r.resize(r_n);
        const double* ys = y.data() + (lo - b0);
        double peak = 0.0;
        for (std::size_t i = 0; i < r_n; ++i) {
            const double v = std::fabs(ys[i] - mu);
            r[i] = v;
            peak = std::max(peak, v);
        }
        const double snr = 20.0 * std::log10(std::max(peak, kTiny) / sg);
        if (snr < c.quartz_onset_beat_snr_db) return {snr, kNaN};
        const std::size_t sub_n = static_cast<std::size_t>(q_win + q_cen + 1);
        const double j = dsp::onset_after_quiet(r.data(), sub_n, c.quartz_onset_sigma * sg, q_quiet);
        return {snr, is_nan(j) ? kNaN : (static_cast<double>(lo) + j)};
    }

    double quartz_fine(std::int64_t k, double t_fine) {
        Tpl& t = tpl[static_cast<std::size_t>(k & 1)];
        const double center = is_finite(t_fine) ? t_fine : fine_center;
        if (!is_finite(center)) return t_fine;
        const auto [snr, onset] = step_onset(center);
        if (t.q_onset < 0) {
            if (is_finite(snr)) t.q_snr.push_back(snr);
            if (static_cast<int>(t.q_snr.size()) < kQDecideBeats) return kNaN;
            scratch_v.assign(t.q_snr.data(), t.q_snr.size());
            t.q_onset = (exact_median(scratch_v.data(), scratch_v.size()) >= c.quartz_onset_snr_db) ? 1 : 0;
        }
        if (t.q_onset == 0 || !is_finite(t_fine)) return t_fine;
        if (!is_nan(onset)) {
            if (t.q_off.size() == 16U) t.q_off.pop_front();
            t.q_off.push_back(onset - t_fine);
            return onset;
        }
        if (t.q_off.size() < 3U) return kNaN;
        return t_fine + deque_median(t.q_off);
    }

    double confidence(double snr, double p1, double p2, double p3, double residual,
                      std::uint32_t flags) const {
        const double q_snr =
            0.2 + 0.8 * std::min(1.0, std::max(0.0, (snr - c.min_anchor_snr_db) / 18.0));
        const double q_struct =
            0.4 + (!is_nan(p3) ? 0.3 : 0.0) + (!is_nan(p1) ? 0.2 : 0.0) + (!is_nan(p2) ? 0.1 : 0.0);
        const double sigma = tol() / 3.0;
        const double q_res = std::exp(-0.5 * (residual / sigma) * (residual / sigma));
        double q = q_snr * q_struct * q_res;
        if (flags & BEAT_TIMING_OUTLIER) q *= 0.5;
        if (flags & BEAT_SATURATED) q *= 0.8;
        return std::min(1.0, std::max(0.0, q));
    }

    bool finish_beat(std::int64_t j, double tp, std::size_t ai, double a, double height, double snr,
                     double g, double p1, double p2, double p3, NativeBeat& out) {
        const double ref = tlift_ref();
        const double t_ref = !is_nan(p3) ? p3 : (p1 + (!is_nan(ref) ? ref : 0.0));
        const double residual = t_ref - tp;
        std::uint32_t flags = BEAT_NONE;
        const bool outlier = !is_nan(g) && std::fabs(residual) > g;
        int n_pts = 0;
        double g0 = kInf, g1 = -kInf;
        for (double v : {p1, p2, p3}) {
            if (!is_nan(v)) {
                g0 = std::min(g0, v);
                g1 = std::max(g1, v);
                ++n_pts;
            }
        }
        const auto [k0, k1] = cands.range(g0 - slack, g1 + slack);
        std::int32_t n_sub = 0;
        for (std::size_t k = k0; k < k1; ++k) {
            if (!cands.used[k] &&
                (cands.peak[k] - cands.floor_v[k]) >= c.subevent_rel_threshold * height) {
                ++n_sub;
            }
        }
        if (outlier && n_pts == 1 && !c.quartz) {
            cands.used[ai] = 1;
            ++rejected;
            return false;
        }
        const auto [u0, u1] = cands.range(std::min(g0, a - tmax) - slack, g1 + post);
        for (std::size_t u = u0; u < u1; ++u) cands.used[u] = 1;
        zones.emplace_back(g0 - pre, g1 + post);

        if (outlier) {
            flags |= BEAT_TIMING_OUTLIER;
            if (outliers.size() == static_cast<std::size_t>(c.quartz ? c.quartz_reseed_beats : 3))
                outliers.pop_front();
            outliers.push_back({j, t_ref, residual});
            maybe_reseed();
        } else {
            outliers.clear();
            if (model.size() == static_cast<std::size_t>(c.model_beats)) model.pop_front();
            model.push_back({j, t_ref});
            refit();
        }
        if (is_nan(p1)) flags |= BEAT_P1_MISSING;
        if (is_nan(p2)) flags |= BEAT_P2_MISSING;
        if (is_nan(p3)) flags |= BEAT_P3_MISSING;
        if (snr < c.weak_snr_db) flags |= BEAT_WEAK;

        const std::int64_t r0 = std::max(static_cast<std::int64_t>(g0 - pre), raw.start);
        const std::int64_t r1 = std::min(static_cast<std::int64_t>(g1 + 4e-3 * fs) + 1, raw.end());
        double peak = 0.0;
        if (r1 > r0) {
            const double* rp = raw.ptr(r0);
            const std::int64_t rn = r1 - r0;
            for (std::int64_t i = 0; i < rn; ++i) peak = std::max(peak, std::fabs(rp[i]));
        }
        if (peak >= c.saturation_level) flags |= BEAT_SATURATED;
        if (after_gap) {
            flags |= BEAT_AFTER_GAP;
            after_gap = false;
        }
        if (!is_nan(p1) && !is_nan(p3)) {
            if (tlift2.size() == 32U) tlift2.pop_front();
            tlift2.push_back(p3 - p1);
            if (!is_nan(p2)) {
                if (tlift3.size() == 32U) tlift3.pop_front();
                tlift3.push_back(p3 - p1);
            }
            tlift_cached = false;
        }
        if (!has_k_offset) {
            std::int64_t kk;
            if (!has_last_k) {
                kk = 0;
            } else {
                kk = last_k + py_round((t_ref - last_ref) / period);
                kk = std::max(kk, last_k + 1);
            }
            k_offset = kk - j;
            has_k_offset = true;
        }
        const std::int64_t kk = j + k_offset;
        if (has_last_k) missed += std::max<std::int64_t>(0, kk - last_k - 1);
        last_k = kk;
        has_last_k = true;
        last_ref = t_ref;
        has_last_ref = true;

        fine_center = kNaN;
        fine_p1_tpl = kNaN;
        double t_fine = fine_align(kk, tp);
        const double t_p1_tpl = fine_p1_tpl;
        if (q_on) t_fine = quartz_fine(kk, t_fine);
        const double t_p1 = p1_on ? p1_fine(kk, t_fine, p1) : kNaN;
        const double conf = confidence(snr, p1, p2, p3, residual, flags);
        ++emitted;
        if (!is_nan(p1)) ++n_p[0];
        if (!is_nan(p2)) ++n_p[1];
        if (!is_nan(p3)) ++n_p[2];

        out.k = kk;
        out.p1 = p1;
        out.p2 = p2;
        out.p3 = p3;
        out.n_subevents = std::max<std::int32_t>(n_sub, n_pts);
        out.peak_dbfs = 20.0 * std::log10(std::max(peak, kTiny));
        out.snr_db = snr;
        out.residual_samples = residual;
        out.confidence = conf;
        out.flags = flags;
        out.t_fine = t_fine;
        out.t_p1 = t_p1;
        out.t_p1_tpl = t_p1_tpl;
        return true;
    }

    bool examine(std::int64_t j, double tp, double ttol, NativeBeat& out) {
        const auto [i0, i1] = cands.range(tp - ttol, tp + ttol);
        if (i1 <= i0) return false;
        const double g = gate();
        const bool has_g = !is_nan(g);

        bool any_ok = false;
        bool any_near = false;
        std::size_t ai_ok = i0, ai_near = i0;
        double best_pk_ok = -kInf, best_pk_near = -kInf;
        double snr_ok = 0.0, snr_near = 0.0;

        for (std::size_t i = i0; i < i1; ++i) {
            if (cands.used[i]) continue;
            const double pk = cands.peak[i];
            const double snr = 20.0 * std::log10(std::max(pk, kTiny) / cands.floor_v[i]);
            if (snr < c.min_anchor_snr_db) continue;
            any_ok = true;
            if (pk > best_pk_ok) {
                best_pk_ok = pk;
                ai_ok = i;
                snr_ok = snr;
            }
            if (has_g && std::fabs(cands.onset[i] - tp) <= g) {
                any_near = true;
                if (pk > best_pk_near) {
                    best_pk_near = pk;
                    ai_near = i;
                    snr_near = snr;
                }
            }
        }
        if (!any_ok) return false;
        const std::size_t ai = any_near ? ai_near : ai_ok;
        const double snr = any_near ? snr_near : snr_ok;
        const double a = cands.onset[ai];
        const double fa = cands.floor_v[ai];
        const double height = cands.peak[ai] - fa;

        if (c.quartz) return finish_beat(j, tp, ai, a, height, snr, g, kNaN, kNaN, a, out);

        const double min_ratio = std::pow(10.0, c.min_subevent_snr_db / 20.0);
        const auto [w_lo, w_hi] = lift_window();
        const double ref = tlift_ref();
        double p1 = kNaN, p2 = kNaN, p3 = kNaN;

        const auto back = members_range(a - tmax - slack, a - min_sp, height, min_ratio, ai);
        double best_p1 = kNaN;
        double best_p1_dist = kInf;
        bool has_in_p1 = false;
        for (std::size_t idx : back) {
            const double b_on = cands.onset[idx];
            if (b_on >= a - w_hi - slack && b_on <= a - w_lo + slack) {
                if (!has_in_p1) {
                    best_p1 = b_on;
                    best_p1_dist = !is_nan(ref) ? std::fabs(a - b_on - ref) : 0.0;
                    has_in_p1 = true;
                } else if (!is_nan(ref)) {
                    const double d = std::fabs(a - b_on - ref);
                    if (d < best_p1_dist) {
                        best_p1_dist = d;
                        best_p1 = b_on;
                    }
                }
            }
        }
        if (has_in_p1) {
            const bool conf = confirm_lift(a - best_p1, tl_obs);
            if (!is_p1(a, best_p1, back)) {
                p2 = best_p1;
                p3 = a;
            } else if (conf) {
                p1 = best_p1;
                p3 = a;
            } else {
                p3 = a;
            }
        } else {
            if (tl_obs.size() == 8U) tl_obs.pop_front();
            tl_obs.push_back(kNaN);
            const auto fwd = members_range(a + min_sp, a + tmax + slack, height, min_ratio, ai);
            double y = kNaN;
            double best_p3_dist = kInf;
            bool has_in_p3 = false;
            for (std::size_t idx : fwd) {
                const double f_on = cands.onset[idx];
                if (f_on >= a + w_lo - slack && f_on <= a + w_hi + slack) {
                    if (!is_nan(ref)) {
                        const double d = std::fabs(f_on - a - ref);
                        if (!has_in_p3 || d < best_p3_dist) {
                            best_p3_dist = d;
                            y = f_on;
                            has_in_p3 = true;
                        }
                    } else {
                        y = f_on;  // latest in_p3[-1]
                        has_in_p3 = true;
                    }
                }
            }
            if (!is_nan(y) && confirm_lift(y - a, tl_fwd)) {
                p1 = a;
                p3 = y;
            } else if (!is_nan(ref) && std::fabs(a + ref - tp) < std::fabs(a - tp)) {
                p1 = a;
            } else {
                p3 = a;
            }
        }

        if (is_nan(p2)) {
            double target = kNaN, half = kNaN;
            if (!is_nan(p1) && !is_nan(p3)) {
                const double tl = p3 - p1;
                target = p1 + kP2Fraction * tl;
                half = 0.25 * tl;
            } else if (!is_nan(ref) && !is_nan(p3)) {
                target = p3 - (1.0 - kP2Fraction) * ref;
                half = 0.25 * ref;
            } else if (!is_nan(ref)) {
                target = p1 + kP2Fraction * ref;
                half = 0.25 * ref;
            } else if (!is_nan(p3)) {
                const double lo_t = p3 - (1.0 - kP2Fraction) * tmax;
                const double hi_t = p3 - (1.0 - kP2Fraction) * tmin;
                target = 0.5 * (lo_t + hi_t);
                half = 0.5 * (hi_t - lo_t);
            }
            if (!is_nan(target)) {
                const auto cand = members_range(target - half, target + half, height, min_ratio, ai);
                double best_d = kInf;
                for (std::size_t idx : cand) {
                    const double d = std::fabs(cands.onset[idx] - target);
                    if (d < best_d) {
                        best_d = d;
                        p2 = cands.onset[idx];
                    }
                }
            }
        }
        return finish_beat(j, tp, ai, a, height, snr, g, p1, p2, p3, out);
    }

    void track(bool final, std::vector<NativeBeat>& out) {
        const double fwd = tmax + slack + post;
        const double back = tmax + slack + pre;
        while (locked) {
            const std::int64_t j = j_next;
            const double tp = predict(j);
            const double ttol = tol();
            if (final) {
                if (tp + 1e-3 * fs > static_cast<double>(cand_end)) break;
            } else if (tp + ttol + fwd > static_cast<double>(cand_end)) {
                break;
            }
            ++j_next;
            if (in_dropout(tp - ttol - back, tp + ttol + fwd)) {
                after_gap = true;
                ++gap_beats;
                count_rejected(tp + ttol);
                continue;
            }
            NativeBeat beat;
            const bool ok = examine(j, tp, ttol, beat);
            count_rejected(tp + ttol);
            if (!ok) {
                ++missed_run;
                if (missed_run > c.max_missed) lose_lock();
                continue;
            }
            missed_run = 0;
            out.push_back(beat);
        }
    }

    void after_cell(bool final, std::vector<NativeBeat>& out) {
        const std::int64_t last = next_cell - (final ? 1 : 2);
        while (cand_cell <= last) {
            detect_cell(cand_cell);
            ++cand_cell;
            cand_end = std::min(cand_cell * S, env.end());
        }
        if (final) cand_end = env.end();
        if (!locked) try_acquire();
        if (locked) {
            track(final, out);
            if (!locked) {
                try_acquire();
                if (locked) track(final, out);
            }
        }
    }

    void trim() {
        const std::int64_t back = W_acq + tpl_pre + tpl_search + S;
        const std::int64_t keep =
            std::min(next_cell * S - margin, cand_end - std::max(static_cast<std::int64_t>(fs), back));
        raw.trim(keep);
        env.trim(cand_cell * S - std::max(W_noise, back) - 3 * S);
        cands.trim(static_cast<double>(cand_end - W_acq) - fs);
        while (!zones.empty() && zones.front().second < static_cast<double>(env.start))
            zones.pop_front();
        const std::int64_t old = next_cell - 16;
        std::erase_if(floors, [old](const FloorEntry& f) { return f.cell < old; });
    }

    void pump(bool final, std::vector<NativeBeat>& out) {
        while (true) {
            const std::int64_t m = next_cell;
            const std::int64_t c0 = std::max(m * S, run_start);
            if (raw.end() >= (m + 1) * S + margin || (final && raw.end() > c0)) {
                env_cell(m);
                ++next_cell;
                after_cell(false, out);
            } else {
                break;
            }
        }
        if (final) after_cell(true, out);
        trim();
    }

    void handle_gap(std::int64_t new_start, std::vector<NativeBeat>& out) {
        pump(true, out);
        close_zero_run(raw.end());
        ++gaps;
        after_gap = true;
        start_run(new_start);
        if (locked) {
            while (predict(j_next) < static_cast<double>(new_start + filter_memory)) {
                ++j_next;
                ++gap_beats;
            }
        }
    }

    void process_raw(const double* chunk, std::size_t n, std::int64_t start_index,
                     std::vector<NativeBeat>& out) {
        if (!started) {
            started = true;
            first_index = start_index;
            expected = start_index;
            start_run(start_index);
        } else if (start_index > expected) {
            handle_gap(start_index, out);
        } else if (start_index < expected) {
            throw std::invalid_argument("el bloque se solapa con muestras ya procesadas");
        }
        if (n == 0) return;
        raw.append(chunk, n);
        samples += static_cast<std::int64_t>(n);
        expected = start_index + static_cast<std::int64_t>(n);
        pump(false, out);
    }
};

BeatDetector::BeatDetector(const NativeDetectorConfig& cfg) : impl_(new Impl) {
    try {
        impl_->init(cfg);
    } catch (...) {
        delete impl_;
        throw;
    }
}

BeatDetector::~BeatDetector() { delete impl_; }

void BeatDetector::reset() { impl_->reset(); }

void BeatDetector::process(const double* chunk, std::size_t n, std::int64_t start_index,
                           std::vector<NativeBeat>& out) {
    if (impl_->finished) {
        throw std::runtime_error("el detector ya terminó (flush); llama a reset() para reutilizarlo");
    }
    if (!impl_->normalizer) {
        impl_->process_raw(chunk, n, start_index, out);
        return;
    }
    impl_->norm_segs.clear();
    impl_->normalizer->process(chunk, n, start_index, impl_->norm_segs);
    for (const auto& s : impl_->norm_segs) {
        impl_->process_raw(s.samples.data(), s.samples.size(), s.start, out);
    }
}

void BeatDetector::flush(std::vector<NativeBeat>& out) {
    if (impl_->finished) return;
    if (impl_->normalizer) {
        impl_->norm_segs.clear();
        impl_->normalizer->flush(impl_->norm_segs);
        for (const auto& s : impl_->norm_segs) {
            impl_->process_raw(s.samples.data(), s.samples.size(), s.start, out);
        }
    }
    if (impl_->started) impl_->pump(true, out);
    impl_->close_zero_run(impl_->raw.end());
    impl_->finished = true;
}

NativeDetectorStats BeatDetector::stats() const {
    const Impl& m = *impl_;
    NativeDetectorStats s;
    const std::int64_t n = std::max<std::int64_t>(1, m.emitted);
    s.samples_processed = m.samples;
    s.beats_emitted = m.emitted;
    s.missed_beats = m.missed;
    s.gap_beats = m.gap_beats;
    s.gaps = m.gaps;
    s.rejected_events = m.rejected;
    s.relocks = m.relocks;
    s.has_lock_sample = m.has_lock_sample ? 1 : 0;
    s.lock_sample = m.lock_sample;
    s.lock_time_s = m.has_lock_sample ? (m.lock_sample - static_cast<double>(m.first_index)) / m.fs : 0.0;
    s.p1_fraction = static_cast<double>(m.n_p[0]) / static_cast<double>(n);
    s.p2_fraction = static_cast<double>(m.n_p[1]) / static_cast<double>(n);
    s.p3_fraction = static_cast<double>(m.n_p[2]) / static_cast<double>(n);
    s.period_samples = m.ever_locked ? m.period : kNaN;
    s.nominal_period_samples = m.period_nom;
    s.interference_db = m.interf ? m.interf->max_attenuation_db : 0.0;
    s.interference_hz = m.interf ? m.interf->peak_hz : kNaN;
    s.locked = m.locked ? 1 : 0;
    s.has_normalizer = m.normalizer ? 1 : 0;
    if (m.normalizer) {
        s.normalizer_clipped_samples = m.normalizer->clipped_samples;
        s.normalizer_gain_db = m.normalizer->gain_db();
        s.has_norm_next = m.normalizer->has_next_index() ? 1 : 0;
        s.norm_next_index = m.normalizer->next_index();
    }
    s.started = m.started ? 1 : 0;
    s.finished = m.finished ? 1 : 0;
    s.expected_index = m.expected;
    return s;
}

const std::vector<std::string>& BeatDetector::warnings() const { return impl_->warnings; }

void BeatDetector::templates(NativeTemplateHeader& hdr0, std::vector<double>& env0,
                             NativeTemplateHeader& hdr1, std::vector<double>& env1) const {
    const Impl& m = *impl_;
    const auto [m0, m1] = m.p3_methods();
    const int meth[2] = {m0, m1};
    NativeTemplateHeader* hdrs[2] = {&hdr0, &hdr1};
    std::vector<double>* envs[2] = {&env0, &env1};
    for (int p = 0; p < 2; ++p) {
        const Tpl& t = m.tpl[p];
        NativeTemplateHeader& h = *hdrs[p];
        std::vector<double>& ev = *envs[p];
        h = NativeTemplateHeader{};
        h.parity = p;
        h.has_anchor = t.has_anchor ? 1 : 0;
        if (!t.has_env) {
            ev.clear();
            continue;
        }
        h.valid = 1;
        h.n_beats = t.n_segs;
        h.length = static_cast<std::int32_t>(t.length);
        h.onset_p1 = t.onset_p1;
        h.onset_p3 = (meth[p] == 1) ? t.onset_front : t.onset_p3;
        h.onset_ref = t.onset_ref();
        h.p3_is_frente = meth[p];
        ev.assign(t.env.data(), t.env.data() + t.length);
    }
}

void BeatDetector::quartz_timing(std::int32_t& q0, std::int32_t& q1) const {
    if (!impl_->q_on) {
        const std::int32_t v = impl_->c.quartz ? 0 : -2;  // -2 = "—", 0 = "template", 1 = "onset"
        q0 = q1 = v;
        return;
    }
    auto map = [](int s) -> std::int32_t { return s < 0 ? -2 : (s == 0 ? 0 : 1); };
    q0 = map(impl_->tpl[0].q_onset);
    q1 = map(impl_->tpl[1].q_onset);
}

std::int64_t BeatDetector::stream_start_hint() const {
    if (impl_->normalizer && impl_->normalizer->has_next_index()) {
        return impl_->normalizer->next_index();
    }
    return impl_->expected;
}

}  // namespace advtg::core
