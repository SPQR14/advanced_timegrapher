// Native ADC clock estimation (exact C++20 counterpart of src/adv_tg/core/adc_clock.py).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_metrics.hpp"

namespace advtg::core {
namespace {

inline std::int64_t floor_div(std::int64_t a, std::int64_t b) {
    std::int64_t q = a / b;
    std::int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) --q;
    return q;
}

double percentile_linear(const std::vector<double>& sorted, double p) {
    const std::size_t n = sorted.size();
    if (n == 0) return kNaN;
    if (n == 1) return sorted[0];
    const double idx = (p / 100.0) * static_cast<double>(n - 1);
    const std::size_t lo = static_cast<std::size_t>(std::floor(idx));
    const std::size_t hi = std::min(lo + 1, n - 1);
    const double frac = idx - static_cast<double>(lo);
    return sorted[lo] + frac * (sorted[hi] - sorted[lo]);
}

double theil_sen_slope(const std::vector<double>& x, const std::vector<double>& y,
                       const std::vector<std::int64_t>& seg) {
    constexpr std::size_t kMaxPairs = 200000;
    const std::size_t m = x.size();
    if (m < 2) return 0.0;
    const std::size_t total_pairs = m * (m - 1) / 2;
    std::vector<double> slopes;
    if (total_pairs <= kMaxPairs) {
        slopes.reserve(total_pairs);
        for (std::size_t i = 0; i < m; ++i) {
            for (std::size_t j = i + 1; j < m; ++j) {
                if (seg[i] == seg[j] && x[j] != x[i]) {
                    slopes.push_back((y[j] - y[i]) / (x[j] - x[i]));
                }
            }
        }
    } else {
        slopes.reserve(kMaxPairs);
        std::uint64_t state = 0x9e3779b97f4a7c15ULL;
        for (std::size_t k = 0; k < kMaxPairs; ++k) {
            state ^= state >> 12;
            state ^= state << 25;
            state ^= state >> 27;
            const std::size_t i = static_cast<std::size_t>((state * 0x2545F4914F6CDD1DULL) % m);
            state ^= state >> 12;
            state ^= state << 25;
            state ^= state >> 27;
            const std::size_t j = static_cast<std::size_t>((state * 0x2545F4914F6CDD1DULL) % m);
            if (i != j && seg[i] == seg[j] && x[j] != x[i]) {
                slopes.push_back((y[j] - y[i]) / (x[j] - x[i]));
            }
        }
    }
    if (slopes.empty()) return 0.0;
    return exact_median(slopes.data(), slopes.size());
}

struct CommonSlopeFit {
    double slope = 0.0;
    double sigma = kInf;
    std::vector<double> res;
    std::size_t n_uniq = 0;
};

CommonSlopeFit fit_common_slope(const std::vector<double>& x, const std::vector<double>& y,
                                const std::vector<std::int64_t>& seg) {
    const std::size_t n = x.size();
    std::vector<std::int64_t> uniq = seg;
    std::sort(uniq.begin(), uniq.end());
    uniq.erase(std::unique(uniq.begin(), uniq.end()), uniq.end());

    std::vector<double> xc(x), yc(y);
    for (std::int64_t s : uniq) {
        double sx = 0.0, sy = 0.0;
        std::size_t cnt = 0;
        for (std::size_t i = 0; i < n; ++i) {
            if (seg[i] == s) {
                sx += x[i];
                sy += y[i];
                ++cnt;
            }
        }
        const double mx = sx / static_cast<double>(cnt);
        const double my = sy / static_cast<double>(cnt);
        for (std::size_t i = 0; i < n; ++i) {
            if (seg[i] == s) {
                xc[i] -= mx;
                yc[i] -= my;
            }
        }
    }
    double sxx = 0.0, sxy = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        sxx += xc[i] * xc[i];
        sxy += xc[i] * yc[i];
    }
    CommonSlopeFit out;
    out.n_uniq = uniq.size();
    if (!(sxx > 0.0)) {
        out.slope = 0.0;
        out.sigma = kInf;
        out.res = std::move(yc);
        return out;
    }
    out.slope = sxy / sxx;
    out.res.resize(n);
    double rss = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        out.res[i] = yc[i] - out.slope * xc[i];
        rss += out.res[i] * out.res[i];
    }
    const std::int64_t dof =
        static_cast<std::int64_t>(n) - static_cast<std::int64_t>(uniq.size()) - 1;
    const double s2 = dof > 0 ? (rss / static_cast<double>(dof)) : kInf;
    out.sigma = std::sqrt(s2 / sxx);
    return out;
}

}  // namespace

std::size_t lower_envelope_cpp(const std::int64_t* end_indices, const std::int64_t* host_ns,
                               std::size_t n, double sample_rate, double bin_s,
                               std::int64_t* out_n, std::int64_t* out_t) {
    if (n == 0) return 0;
    const std::int64_t bin_samples =
        std::max<std::int64_t>(1, static_cast<std::int64_t>(std::llrint(bin_s * sample_rate)));
    const std::int64_t n0 = end_indices[0];
    const std::int64_t t0 = host_ns[0];

    struct Item {
        std::int64_t bin;
        double r;
        std::size_t idx;
    };
    std::vector<Item> items(n);
    for (std::size_t i = 0; i < n; ++i) {
        items[i] = {
            floor_div(end_indices[i], bin_samples),
            static_cast<double>(host_ns[i] - t0) * 1e-9 -
                static_cast<double>(end_indices[i] - n0) / sample_rate,
            i,
        };
    }
    std::stable_sort(items.begin(), items.end(), [](const Item& a, const Item& b) {
        if (a.bin != b.bin) return a.bin < b.bin;
        return a.r < b.r;
    });
    std::vector<std::size_t> keep;
    keep.reserve(n);
    keep.push_back(items[0].idx);
    for (std::size_t i = 1; i < n; ++i) {
        if (items[i].bin != items[i - 1].bin) keep.push_back(items[i].idx);
    }
    std::sort(keep.begin(), keep.end());
    std::size_t lo = 0, hi = keep.size();
    if (keep.size() > 3) {
        lo = 1;
        hi = keep.size() - 1;
    }
    std::size_t w = 0;
    for (std::size_t i = lo; i < hi; ++i) {
        const std::size_t k = keep[i];
        out_n[w] = end_indices[k];
        out_t[w] = host_ns[k];
        ++w;
    }
    return w;
}

NativeAdcDrift fit_envelope_cpp(const std::int64_t* end_indices, const std::int64_t* host_ns,
                                std::size_t n, double sample_rate, int host_level,
                                double host_sigma_ppm, double jump_ms, double outlier_sigma,
                                int min_points, int min_segment, double min_span_s) {
    NativeAdcDrift res;
    if (static_cast<int>(n) < min_points) {
        res.note_kind = 1;
        return res;
    }
    const std::int64_t n0 = end_indices[0];
    const std::int64_t t0 = host_ns[0];
    std::vector<double> x(n), y(n);
    std::vector<std::int64_t> seg(n, 0);
    const double jump_s = jump_ms * 1e-3;
    for (std::size_t i = 0; i < n; ++i) {
        x[i] = static_cast<double>(end_indices[i] - n0) / sample_rate;
        y[i] = static_cast<double>(host_ns[i] - t0) * 1e-9 - x[i];
        if (i > 0) {
            seg[i] = seg[i - 1] + (std::fabs(y[i] - y[i - 1]) > jump_s ? 1 : 0);
        }
    }
    const std::size_t n_seg_ids = static_cast<std::size_t>(seg.back() + 1);
    std::vector<std::int64_t> counts(n_seg_ids, 0);
    for (std::int64_t s : seg) ++counts[static_cast<std::size_t>(s)];

    std::vector<double> xf, yf;
    std::vector<std::int64_t> segf;
    xf.reserve(n);
    yf.reserve(n);
    segf.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        if (counts[static_cast<std::size_t>(seg[i])] >= min_segment) {
            xf.push_back(x[i]);
            yf.push_back(y[i]);
            segf.push_back(seg[i]);
        }
    }
    if (static_cast<int>(xf.size()) < min_points) {
        res.note_kind = 2;
        return res;
    }

    const double c0 = theil_sen_slope(xf, yf, segf);
    std::vector<double> e(xf.size());
    for (std::size_t i = 0; i < xf.size(); ++i) e[i] = yf[i] - c0 * xf[i];

    std::vector<std::int64_t> uniq = segf;
    uniq.erase(std::unique(uniq.begin(), uniq.end()), uniq.end());
    std::vector<double> tmp;
    for (std::int64_t s : uniq) {
        tmp.clear();
        for (std::size_t i = 0; i < xf.size(); ++i)
            if (segf[i] == s) tmp.push_back(e[i]);
        const double med = exact_median(tmp.data(), tmp.size());
        for (std::size_t i = 0; i < xf.size(); ++i)
            if (segf[i] == s) e[i] -= med;
    }
    tmp.resize(e.size());
    for (std::size_t i = 0; i < e.size(); ++i) tmp[i] = std::fabs(e[i]);
    const double sig = std::max(kMadToSigma * exact_median(tmp.data(), tmp.size()), 20e-6);

    std::fill(counts.begin(), counts.end(), 0);
    for (std::size_t i = 0; i < xf.size(); ++i) {
        if (std::fabs(e[i]) <= outlier_sigma * sig) ++counts[static_cast<std::size_t>(segf[i])];
    }
    std::vector<double> xk, yk;
    std::vector<std::int64_t> segk;
    for (std::size_t i = 0; i < xf.size(); ++i) {
        if (std::fabs(e[i]) <= outlier_sigma * sig && counts[static_cast<std::size_t>(segf[i])] >= 2) {
            xk.push_back(xf[i]);
            yk.push_back(yf[i]);
            segk.push_back(segf[i]);
        }
    }
    double span = 0.0;
    if (!xk.empty()) {
        std::vector<std::int64_t> uk = segk;
        uk.erase(std::unique(uk.begin(), uk.end()), uk.end());
        for (std::int64_t s : uk) {
            double xmin = kInf, xmax = -kInf;
            for (std::size_t i = 0; i < xk.size(); ++i) {
                if (segk[i] == s) {
                    xmin = std::min(xmin, xk[i]);
                    xmax = std::max(xmax, xk[i]);
                }
            }
            if (xmax >= xmin) span += (xmax - xmin);
        }
    }
    if (static_cast<int>(xk.size()) < min_points || span < min_span_s) {
        res.span_s = span;
        res.note_kind = 3;
        return res;
    }

    auto fit = fit_common_slope(xk, yk, segk);
    const double c = fit.slope;
    const double ppm = (1.0 / (1.0 + c) - 1.0) * 1e6;
    const double sigma_ppm = fit.sigma / ((1.0 + c) * (1.0 + c)) * 1e6;
    tmp = fit.res;
    const double rmed = exact_median(tmp.data(), tmp.size());
    tmp = fit.res;
    const double resid = kMadToSigma * exact_median_abs_dev(tmp.data(), tmp.size(), rmed);

    res.ppm = ppm;
    res.sigma_ppm = sigma_ppm;
    res.systematic_ppm = host_sigma_ppm;
    res.level = host_level;
    res.method = 1;  // "host-envelope"
    res.span_s = span;
    res.n_points = static_cast<std::int64_t>(xk.size());
    res.n_segments = static_cast<std::int64_t>(fit.n_uniq);
    res.residual_us = resid * 1e6;
    res.note_kind = (fit.n_uniq > 1) ? 5 : 0;
    return res;
}

NativeAdcDrift drift_from_ticks_cpp(const double* ticks, std::size_t n, double sample_rate,
                                    double nominal_period_s, double reference_ppm,
                                    double reference_sigma_ppm, double outlier_sigma,
                                    int min_ticks) {
    NativeAdcDrift res;
    std::vector<double> x;
    x.reserve(n);
    for (std::size_t i = 0; i < n; ++i)
        if (std::isfinite(ticks[i])) x.push_back(ticks[i]);
    std::sort(x.begin(), x.end());
    if (static_cast<int>(x.size()) < min_ticks) {
        res.n_points = static_cast<std::int64_t>(x.size());
        res.note_kind = 1;
        return res;
    }
    const double p_nom = nominal_period_s * sample_rate;
    const double x0 = x[0];
    std::vector<double> kd, xd;
    kd.reserve(x.size());
    xd.reserve(x.size());
    for (std::size_t i = 0; i < x.size(); ++i) {
        const double kv = std::rint((x[i] - x0) / p_nom);
        if (kd.empty() || kv != kd.back()) {
            kd.push_back(kv);
            xd.push_back(x[i]);
        }
    }
    const std::size_t m = kd.size();
    std::vector<double> y(m);
    for (std::size_t i = 0; i < m; ++i) y[i] = xd[i] - x0 - kd[i] * p_nom;

    std::vector<std::uint8_t> keep(m, 1);
    std::vector<double> kk, yy, tmp;
    for (int iter = 0; iter < 5; ++iter) {
        kk.clear();
        yy.clear();
        for (std::size_t i = 0; i < m; ++i) {
            if (keep[i]) {
                kk.push_back(kd[i]);
                yy.push_back(y[i]);
            }
        }
        std::vector<std::int64_t> zseg(kk.size(), 0);
        auto fit = fit_common_slope(kk, yy, zseg);
        std::vector<double> r_all(m), r_kept;
        r_kept.reserve(kk.size());
        for (std::size_t i = 0; i < m; ++i) {
            r_all[i] = y[i] - fit.slope * kd[i];
            if (keep[i]) r_kept.push_back(r_all[i]);
        }
        tmp = r_kept;
        const double med = exact_median(tmp.data(), tmp.size());
        tmp = r_kept;
        const double sig = std::max(kMadToSigma * exact_median_abs_dev(tmp.data(), tmp.size(), med), 1e-3);
        bool changed = false;
        for (std::size_t i = 0; i < m; ++i) {
            const std::uint8_t nk = (std::fabs(r_all[i] - med) <= outlier_sigma * sig) ? 1 : 0;
            if (nk != keep[i]) changed = true;
            keep[i] = nk;
        }
        if (!changed) break;
    }

    kk.clear();
    yy.clear();
    for (std::size_t i = 0; i < m; ++i) {
        if (keep[i]) {
            kk.push_back(kd[i]);
            yy.push_back(y[i]);
        }
    }
    if (static_cast<int>(kk.size()) < min_ticks) {
        res.note_kind = 4;
        return res;
    }
    std::vector<std::int64_t> zseg(kk.size(), 0);
    auto fit = fit_common_slope(kk, yy, zseg);
    const double dp = fit.slope;
    const double ratio = (p_nom + dp) / p_nom;
    const double ppm = (ratio * (1.0 + reference_ppm * 1e-6) - 1.0) * 1e6;
    const double sigma_ppm = fit.sigma / p_nom * 1e6;
    for (double& v : fit.res) v = std::fabs(v);
    const double resid = kMadToSigma * exact_median(fit.res.data(), fit.res.size()) / sample_rate;
    const double kmin = *std::min_element(kk.begin(), kk.end());
    const double kmax = *std::max_element(kk.begin(), kk.end());

    res.ppm = ppm;
    res.sigma_ppm = sigma_ppm;
    res.systematic_ppm = reference_sigma_ppm;
    res.level = 3;   // ClockLevel::EXTERNAL
    res.method = 2;  // "reference"
    res.span_s = (kmax - kmin) * nominal_period_s;
    res.n_points = static_cast<std::int64_t>(kk.size());
    res.n_segments = 1;
    res.residual_us = resid * 1e6;
    res.note_kind = 0;
    return res;
}

std::size_t detect_pps_edges_cpp(const double* samples, std::size_t n, double sample_rate,
                                 std::int64_t start_index, int has_threshold, double threshold,
                                 int polarity, double min_interval_s, double* out_edges) {
    if (n < 2) return 0;
    const double pol = static_cast<double>(polarity);
    double thr;
    if (!has_threshold) {
        std::vector<double> sorted(n);
        for (std::size_t i = 0; i < n; ++i) sorted[i] = samples[i] * pol;
        std::sort(sorted.begin(), sorted.end());
        const double lo = percentile_linear(sorted, 1.0);
        const double hi = percentile_linear(sorted, 99.0);
        if (!((hi - lo) > 0.0)) return 0;
        thr = 0.5 * (lo + hi);
    } else {
        thr = threshold * pol;
    }
    const double min_gap = min_interval_s * sample_rate;
    std::size_t w = 0;
    double last_edge = 0.0;
    for (std::size_t i = 0; i + 1 < n; ++i) {
        const double x0 = samples[i] * pol;
        const double x1 = samples[i + 1] * pol;
        if (x0 < thr && x1 >= thr) {
            const double frac = (thr - x0) / (x1 - x0);
            const double e = static_cast<double>(i) + frac;
            if (w == 0 || (e - last_edge) >= min_gap) {
                last_edge = e;
                out_edges[w++] = e + static_cast<double>(start_index);
            }
        }
    }
    return w;
}

}  // namespace advtg::core
