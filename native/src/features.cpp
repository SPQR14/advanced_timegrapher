// Feature vectors per 60 s window, positional differentials, and rule-based diagnosis
// (analysis/features.py).
#include "advtg.h"
#include "advtg_analysis.hpp"
#include "advtg_bits.hpp"
#include "advtg_metrics.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace advtg::analysis {
namespace {

inline constexpr double kWindowS = 60.0;
inline constexpr double kMinWindowS = 30.0;
inline constexpr std::size_t kMinWindowBeats = 32;
inline constexpr double kSpuriousDb = 20.0;
inline constexpr double kFourthWheelMinRecordingS = 180.0;
inline constexpr double kFourthWheelPeriodS = 60.0;

inline constexpr double kRebankingAmpThresholdDeg = 310.0;
inline constexpr double kRebankingSpuriousThreshold = 0.05;
inline constexpr double kLubricationE3E2Threshold = 10.0;
inline constexpr double kLubricationD12CvThreshold = 0.05;
inline constexpr double kMagnetismRateThresholdSpd = 30.0;
inline constexpr double kMagnetismBeSpreadThresholdMs = 0.5;
inline constexpr double kMagnetismBeWindowStdThresholdMs = 0.2;

void moments(const std::vector<double>& vals, double& mean_o, double& var_o, double& skew_o,
             double& kurt_o) {
    std::vector<double> v;
    v.reserve(vals.size());
    for (double x : vals) {
        if (std::isfinite(x)) v.push_back(x);
    }
    if (v.size() < 4U) {
        mean_o = var_o = skew_o = kurt_o = kNaN;
        return;
    }
    const double n = static_cast<double>(v.size());
    const double mean = neumaier_sum(v.data(), v.size()) / n;
    double s2 = 0.0, s3 = 0.0, s4 = 0.0;
    for (double x : v) {
        const double c = x - mean;
        const double c2 = c * c;
        s2 += c2;
        s3 += c2 * c;
        s4 += c2 * c2;
    }
    const double m2 = s2 / n;
    if (!(m2 > 0.0)) {
        mean_o = mean;
        var_o = 0.0;
        skew_o = kNaN;
        kurt_o = kNaN;
        return;
    }
    mean_o = mean;
    var_o = m2;
    skew_o = (s3 / n) / std::pow(m2, 1.5);
    kurt_o = (s4 / n) / (m2 * m2) - 3.0;
}

double median_ratio(const std::vector<double>& a, const std::vector<double>& b) {
    std::vector<double> r;
    r.reserve(a.size());
    for (std::size_t i = 0; i < a.size(); ++i) {
        if (std::isfinite(a[i]) && std::isfinite(b[i]) && a[i] > 0.0 && b[i] > 0.0) {
            r.push_back(a[i] / b[i]);
        }
    }
    if (r.empty()) return kNaN;
    return exact_median(r.data(), r.size());
}

void rate_and_be(const std::vector<std::int64_t>& k, const std::vector<double>& t, double bph,
                 double& rate_o, double& be_o) {
    const std::size_t n = k.size();
    if (n < 8U) {
        rate_o = be_o = kNaN;
        return;
    }
    // 3x3 normal equations for t = a + b*k + d*(-1)^k (centered k for numerical stability)
    double ksum = 0.0;
    for (std::int64_t kv : k) ksum += static_cast<double>(kv);
    const double kmean = ksum / static_cast<double>(n);

    double A[3][3] = {};
    double rhs[3] = {};
    for (std::size_t i = 0; i < n; ++i) {
        const double x[3] = {1.0, static_cast<double>(k[i]) - kmean, ((k[i] & 1) == 0) ? 1.0 : -1.0};
        for (int r = 0; r < 3; ++r) {
            rhs[r] += x[r] * t[i];
            for (int c = 0; c < 3; ++c) A[r][c] += x[r] * x[c];
        }
    }
    // Gaussian elimination with partial pivoting
    for (int col = 0; col < 3; ++col) {
        int piv = col;
        for (int r = col + 1; r < 3; ++r) {
            if (std::fabs(A[r][col]) > std::fabs(A[piv][col])) piv = r;
        }
        if (piv != col) {
            for (int c = col; c < 3; ++c) std::swap(A[col][c], A[piv][c]);
            std::swap(rhs[col], rhs[piv]);
        }
        if (std::fabs(A[col][col]) <= 1e-30) {
            rate_o = be_o = kNaN;
            return;
        }
        for (int r = col + 1; r < 3; ++r) {
            const double f = A[r][col] / A[col][col];
            for (int c = col; c < 3; ++c) A[r][c] -= f * A[col][c];
            rhs[r] -= f * rhs[col];
        }
    }
    double coef[3] = {};
    for (int r = 2; r >= 0; --r) {
        double s = rhs[r];
        for (int c = r + 1; c < 3; ++c) s -= A[r][c] * coef[c];
        coef[r] = s / A[r][r];
    }
    const double slope = coef[1];
    const double d = coef[2];
    rate_o = ((3600.0 / bph) / slope - 1.0) * 86400.0;
    be_o = 2.0 * std::fabs(d) * 1e3;
}

void power_at(const std::vector<double>& t, const std::vector<double>& y, double f,
              double& power_o, double& amp_us_o) {
    if (t.size() < 8U || !std::isfinite(f)) {
        power_o = amp_us_o = kNaN;
        return;
    }
    double p = 0.0;
    lomb_scargle_cpp(t.data(), y.data(), t.size(), &f, 1, &p);
    double amp = 0.0, ph = 0.0;
    sine_fit_cpp(t.data(), y.data(), t.size(), f, amp, ph);
    (void)ph;
    power_o = p;
    amp_us_o = amp * 1e6;
}

advtg_feature_vector build_vector(const GearSeriesData& s, const std::vector<std::size_t>& sel,
                                  double t0, double t1, bool whole, int32_t teeth) {
    advtg_feature_vector v{};
    v.window_start_s = t0;
    v.window_end_s = t1;
    v.whole = whole ? 1 : 0;
    v.n_beats = static_cast<int64_t>(sel.size());

    std::vector<std::int64_t> k;
    std::vector<double> t, res, d12, d23, e1, e2, e3, pre, amp;
    k.reserve(sel.size());
    t.reserve(sel.size());
    res.reserve(sel.size());
    d12.reserve(sel.size());
    d23.reserve(sel.size());
    e1.reserve(sel.size());
    e2.reserve(sel.size());
    e3.reserve(sel.size());
    std::int64_t n_sub = 0;

    for (std::size_t idx : sel) {
        k.push_back(s.k[idx]);
        t.push_back(s.t_s[idx]);
        res.push_back(s.timing_residual_s[idx]);
        if (idx < s.p1_s.size()) {
            d12.push_back((s.p2_s[idx] - s.p1_s[idx]) * 1e3);
            d23.push_back((s.p3_s[idx] - s.p2_s[idx]) * 1e3);
        }
        if (idx < s.e1.size()) {
            e1.push_back(s.e1[idx]);
            e2.push_back(s.e2[idx]);
            e3.push_back(s.e3[idx]);
            if (std::isfinite(s.e1[idx])) ++n_sub;
        }
        if (idx < s.pre_p1_db.size() && std::isfinite(s.pre_p1_db[idx])) {
            pre.push_back(s.pre_p1_db[idx]);
        }
        if (idx < s.amplitude_deg.size() && std::isfinite(s.amplitude_deg[idx])) {
            amp.push_back(s.amplitude_deg[idx]);
        }
    }

    const double f_beat = s.bph / 3600.0;
    rate_and_be(k, t, s.bph, v.rate_spd, v.beat_error_ms);
    power_at(t, res, f_beat / (2.0 * static_cast<double>(teeth)), v.escape_power, v.escape_amp_us);
    v.fourth_power = kNaN;
    v.fourth_amp_us = kNaN;
    if (whole && s.duration_s >= kFourthWheelMinRecordingS) {
        power_at(t, res, 1.0 / kFourthWheelPeriodS, v.fourth_power, v.fourth_amp_us);
    }

    moments(d12, v.d12_mean_ms, v.d12_var_ms2, v.d12_skew, v.d12_kurt);
    moments(d23, v.d23_mean_ms, v.d23_var_ms2, v.d23_skew, v.d23_kurt);

    v.amplitude_deg = amp.empty() ? kNaN : exact_median(amp.data(), amp.size());
    v.n_subevents = n_sub;
    v.e1_e2 = median_ratio(e1, e2);
    v.e3_e2 = median_ratio(e3, e2);
    if (pre.empty()) {
        v.spurious_fraction = kNaN;
    } else {
        std::size_t hit = 0;
        for (double p : pre)
            if (p > kSpuriousDb) ++hit;
        v.spurious_fraction = static_cast<double>(hit) / static_cast<double>(pre.size());
    }
    int64_t jumps = 0;
    for (double ot : s.outlier_t_s) {
        if (ot >= t0 && ot < t1) ++jumps;
    }
    v.timing_jumps = jumps;
    return v;
}

int level_from_signs(int signs, int needed) {
    if (signs >= needed) return ADVTG_FINDING_PROBABLE;
    if (signs == needed - 1 && signs > 0) return ADVTG_FINDING_POSSIBLE;
    return -1;
}

void push_ev(advtg_diagnostic_finding& f, const char* key, double val) {
    if (f.n_evidence >= 4) return;
    const int idx = f.n_evidence++;
    std::snprintf(f.keys[idx], sizeof(f.keys[idx]), "%s", key);
    f.values[idx] = val;
}

bool is_horizontal(const char* pos) {
    return std::strcmp(pos, "DU") == 0 || std::strcmp(pos, "DD") == 0;
}

bool is_vertical(const char* pos) {
    return std::strcmp(pos, "CU") == 0 || std::strcmp(pos, "CD") == 0 ||
           std::strcmp(pos, "CL") == 0 || std::strcmp(pos, "CR") == 0;
}

}  // namespace

std::vector<advtg_feature_vector> window_features_cpp(const GearSeriesData& s, int32_t teeth,
                                                      double window_s) {
    std::vector<advtg_feature_vector> out;
    if (s.t_s.empty()) return out;
    const double start = s.t_s.front();
    const double end = s.t_s.back();
    std::vector<double> edges;
    for (double x = start; x < end; x += window_s) edges.push_back(x);
    edges.push_back(end);
    if (edges.size() > 2U && (edges[edges.size() - 1] - edges[edges.size() - 2]) < kMinWindowS) {
        edges.erase(edges.end() - 2);
    }
    for (std::size_t i = 0; i + 1 < edges.size(); ++i) {
        const double a = edges[i];
        const double b = edges[i + 1];
        const bool last = (i + 2 == edges.size());
        std::vector<std::size_t> sel;
        for (std::size_t j = 0; j < s.t_s.size(); ++j) {
            if (s.t_s[j] >= a && (last ? (s.t_s[j] <= b) : (s.t_s[j] < b))) sel.push_back(j);
        }
        if (sel.size() >= kMinWindowBeats) {
            out.push_back(build_vector(s, sel, a, last ? (b + 1e-9) : b, false, teeth));
        }
    }
    return out;
}

advtg_feature_vector recording_features_cpp(const GearSeriesData& s, int32_t teeth) {
    std::vector<std::size_t> sel(s.k.size());
    for (std::size_t i = 0; i < sel.size(); ++i) sel[i] = i;
    const double start = s.t_s.empty() ? 0.0 : s.t_s.front();
    const double end = s.t_s.empty() ? 0.0 : (s.t_s.back() + 1e-9);
    return build_vector(s, sel, start, end, true, teeth);
}

}  // namespace advtg::analysis

struct advtg_gear_analysis {
    advtg::analysis::GearReportData data;
};

extern "C" {

ADVTG_C_API int32_t advtg_gear_window_features(const advtg_gear_analysis* h, int32_t escape_teeth,
                                               double window_s, advtg_feature_vector* out,
                                               int32_t cap) {
    if (!h || escape_teeth < 1) return ADVTG_ERR_INVALID;
    const double ws = window_s > 0.0 ? window_s : advtg::analysis::kWindowS;
    const auto vecs = advtg::analysis::window_features_cpp(h->data.series, escape_teeth, ws);
    if (out && cap > 0) {
        const auto m = std::min<std::size_t>(vecs.size(), static_cast<std::size_t>(cap));
        if (m) std::memcpy(out, vecs.data(), m * sizeof(advtg_feature_vector));
    }
    return static_cast<int32_t>(vecs.size());
}

ADVTG_C_API int advtg_gear_recording_features(const advtg_gear_analysis* h, int32_t escape_teeth,
                                              advtg_feature_vector* out) {
    if (!h || !out || escape_teeth < 1) return ADVTG_ERR_INVALID;
    *out = advtg::analysis::recording_features_cpp(h->data.series, escape_teeth);
    return ADVTG_OK;
}

ADVTG_C_API int advtg_compute_positional_deltas(const advtg_positional_record* recs, size_t n_recs,
                                                advtg_positional_deltas* out) {
    if (!out || (!recs && n_recs > 0)) return ADVTG_ERR_INVALID;
    std::memset(out, 0, sizeof *out);
    out->delta_rate_hv_spd = advtg::kNaN;
    out->delta_amp_hv_deg = advtg::kNaN;
    out->be_spread_ms = advtg::kNaN;

    const char* order[6] = {"DU", "DD", "CU", "CD", "CL", "CR"};
    double h_rate_sum = 0.0, v_rate_sum = 0.0;
    int h_cnt = 0, v_cnt = 0;
    double h_amp_sum = 0.0, v_amp_sum = 0.0;
    int h_amp_cnt = 0, v_amp_cnt = 0;
    double be_min = advtg::kInf, be_max = -advtg::kInf;
    int be_cnt = 0;

    for (const char* pos : order) {
        for (size_t i = 0; i < n_recs; ++i) {
            if (std::strcmp(recs[i].name, pos) != 0) continue;
            if (out->n_positions < 6) {
                std::snprintf(out->positions[out->n_positions++], 8, "%s", pos);
            }
            if (advtg::analysis::is_horizontal(pos)) {
                h_rate_sum += recs[i].rate_spd;
                ++h_cnt;
                if (std::isfinite(recs[i].amplitude_deg)) {
                    h_amp_sum += recs[i].amplitude_deg;
                    ++h_amp_cnt;
                }
            } else if (advtg::analysis::is_vertical(pos)) {
                v_rate_sum += recs[i].rate_spd;
                ++v_cnt;
                if (std::isfinite(recs[i].amplitude_deg)) {
                    v_amp_sum += recs[i].amplitude_deg;
                    ++v_amp_cnt;
                }
            }
            if (std::isfinite(recs[i].beat_error_ms)) {
                be_min = std::min(be_min, recs[i].beat_error_ms);
                be_max = std::max(be_max, recs[i].beat_error_ms);
                ++be_cnt;
            }
            break;
        }
    }
    if (h_cnt == 0 || v_cnt == 0) return 0;
    out->valid = 1;
    out->delta_rate_hv_spd =
        (h_rate_sum / static_cast<double>(h_cnt)) - (v_rate_sum / static_cast<double>(v_cnt));
    out->delta_amp_hv_deg =
        (h_amp_cnt > 0 && v_amp_cnt > 0)
            ? ((h_amp_sum / static_cast<double>(h_amp_cnt)) -
               (v_amp_sum / static_cast<double>(v_amp_cnt)))
            : advtg::kNaN;
    out->be_spread_ms = (be_cnt >= 2) ? (be_max - be_min) : advtg::kNaN;
    return 1;
}

ADVTG_C_API int32_t advtg_diagnose(const advtg_feature_vector* whole,
                                   const advtg_feature_vector* windows, size_t n_windows,
                                   const advtg_positional_deltas* positional,
                                   const advtg_gear_component* components, size_t n_components,
                                   advtg_diagnostic_finding* out, int32_t cap) {
    if (!whole) return ADVTG_ERR_INVALID;
    using advtg::analysis::push_ev;
    std::vector<advtg_diagnostic_finding> findings;

    // 1. Rebanking
    if (std::isfinite(whole->amplitude_deg) &&
        whole->amplitude_deg > advtg::analysis::kRebankingAmpThresholdDeg) {
        const bool spur = std::isfinite(whole->spurious_fraction) &&
                          whole->spurious_fraction > advtg::analysis::kRebankingSpuriousThreshold;
        const int signs = static_cast<int>(spur) + static_cast<int>(whole->timing_jumps > 0);
        if (signs == 2 || signs == 1) {
            advtg_diagnostic_finding f{};
            f.code = ADVTG_FINDING_REBANKING;
            f.level = (signs == 2) ? ADVTG_FINDING_PROBABLE : ADVTG_FINDING_POSSIBLE;
            push_ev(f, "amplitude_deg", whole->amplitude_deg);
            push_ev(f, "spurious_fraction", whole->spurious_fraction);
            push_ev(f, "timing_jumps", static_cast<double>(whole->timing_jumps));
            findings.push_back(f);
        }
    }

    // 2. Pallet lubrication
    const double cv12 =
        (std::isfinite(whole->d12_var_ms2) && whole->d12_mean_ms > 0.0)
            ? (std::sqrt(whole->d12_var_ms2) / whole->d12_mean_ms)
            : advtg::kNaN;
    const int lub_signs =
        static_cast<int>(std::isfinite(whole->e3_e2) &&
                         whole->e3_e2 > advtg::analysis::kLubricationE3E2Threshold) +
        static_cast<int>(std::isfinite(cv12) && cv12 > advtg::analysis::kLubricationD12CvThreshold);
    const int lub_lvl = advtg::analysis::level_from_signs(lub_signs, 2);
    if (lub_lvl >= 0) {
        advtg_diagnostic_finding f{};
        f.code = ADVTG_FINDING_PALLET_LUBRICATION;
        f.level = lub_lvl;
        push_ev(f, "e3_e2", whole->e3_e2);
        push_ev(f, "d12_cv", cv12);
        findings.push_back(f);
    }

    // 3. Magnetism
    if (std::isfinite(whole->rate_spd) &&
        whole->rate_spd > advtg::analysis::kMagnetismRateThresholdSpd) {
        if (positional && positional->valid && std::isfinite(positional->be_spread_ms)) {
            if (positional->be_spread_ms > advtg::analysis::kMagnetismBeSpreadThresholdMs) {
                advtg_diagnostic_finding f{};
                f.code = ADVTG_FINDING_MAGNETISM;
                f.level = ADVTG_FINDING_PROBABLE;
                push_ev(f, "rate_spd", whole->rate_spd);
                push_ev(f, "be_spread_ms", positional->be_spread_ms);
                findings.push_back(f);
            }
        } else if (windows && n_windows > 0) {
            std::vector<double> bes;
            for (size_t i = 0; i < n_windows; ++i) {
                if (std::isfinite(windows[i].beat_error_ms)) bes.push_back(windows[i].beat_error_ms);
            }
            if (bes.size() >= 2U) {
                double mean = 0.0;
                for (double b : bes) mean += b;
                mean /= static_cast<double>(bes.size());
                double ss = 0.0;
                for (double b : bes) ss += (b - mean) * (b - mean);
                const double be_std = std::sqrt(ss / static_cast<double>(bes.size()));
                if (be_std > advtg::analysis::kMagnetismBeWindowStdThresholdMs) {
                    advtg_diagnostic_finding f{};
                    f.code = ADVTG_FINDING_MAGNETISM;
                    f.level = ADVTG_FINDING_POSSIBLE;
                    push_ev(f, "rate_spd", whole->rate_spd);
                    push_ev(f, "be_window_std_ms", be_std);
                    findings.push_back(f);
                }
            }
        }
    }

    // 4. Gear train components
    if (components && n_components > 0) {
        const int32_t kinds[2] = {ADVTG_GEAR_COMP_ESCAPE, ADVTG_GEAR_COMP_FOURTH};
        const int32_t fcodes[2] = {ADVTG_FINDING_ESCAPE_WHEEL, ADVTG_FINDING_FOURTH_WHEEL};
        for (int idx = 0; idx < 2; ++idx) {
            bool any = false;
            double period_s = 0.0, amp_us = 0.0, ripple = 0.0, energy_mod = 0.0, fap = 1.0;
            bool has_timing = false, has_energy = false;
            for (size_t i = 0; i < n_components; ++i) {
                const auto& c = components[i];
                if (c.name != kinds[idx] || !c.significant) continue;
                any = true;
                period_s = c.period_s;
                if (c.series == ADVTG_GEAR_SERIES_TIMING) {
                    has_timing = true;
                    amp_us = c.amplitude * 1e6;
                    ripple = c.rate_ripple_s_per_day;
                } else {
                    has_energy = true;
                    energy_mod = c.amplitude;
                }
                fap = std::min(fap, c.fap);
            }
            if (any) {
                advtg_diagnostic_finding f{};
                f.code = fcodes[idx];
                f.level = ADVTG_FINDING_PROBABLE;
                push_ev(f, "period_s", period_s);
                if (has_timing) {
                    push_ev(f, "amplitude_us", amp_us);
                    push_ev(f, "rate_ripple_spd", ripple);
                }
                if (has_energy) {
                    push_ev(f, "energy_mod", energy_mod);
                }
                push_ev(f, "fap", fap);
                findings.push_back(f);
            }
        }
    }

    if (out && cap > 0) {
        const auto m = std::min<std::size_t>(findings.size(), static_cast<std::size_t>(cap));
        if (m) std::memcpy(out, findings.data(), m * sizeof(advtg_diagnostic_finding));
    }
    return static_cast<int32_t>(findings.size());
}

}  // extern "C"
