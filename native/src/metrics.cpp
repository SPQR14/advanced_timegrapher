// Native Layer 3 metrics engine (exact C++20 counterpart of src/adv_tg/core/metrics.py).
// Parity-weighted robust regressions (O(N) decoupled linear + 4x4 quadratic with QR fallback),
// delete-one-block jackknife, intra-call fit memoization, timing point hysteresis & Spanish warnings.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_metrics.hpp"
#include "advtg_simd.hpp"

namespace advtg::core {
namespace {

inline constexpr double kDefaultLiftAngleDeg = 52.0;
inline constexpr double kSigmaFloor = 1e-3;
inline constexpr double kBeSwitchRatio = 1.0 / 1.6;
inline constexpr double kP1KeepRatio = 0.9;
inline constexpr double kP1MinShare = 0.8;
inline constexpr std::size_t kPairMinTriples = 8;
inline constexpr double kDropRiseWindowS = 0.006;
inline constexpr long double kPiL = 3.141592653589793238462643383279502884L;

inline double median_vec(const double* p, std::size_t n) {
    if (n == 0) return kNaN;
    thread_local AlignedBuffer<double> scratch;
    scratch.assign(p, n);
    return exact_median(scratch.data(), n);
}

inline double mad_sigma_vec(const double* p, std::size_t n) {
    if (n == 0) return kNaN;
    const double med = median_vec(p, n);
    thread_local AlignedBuffer<double> dev;
    dev.resize(n);
    for (std::size_t i = 0; i < n; ++i) dev[i] = std::fabs(p[i] - med);
    return kMadToSigma * exact_median(dev.data(), n);
}

inline double std_vec(const double* p, std::size_t n) {
    if (n == 0) return kNaN;
    const double m = neumaier_sum(p, n) / static_cast<double>(n);
    double s2 = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        const double d = p[i] - m;
        s2 += d * d;
    }
    return std::sqrt(s2 / static_cast<double>(n));
}

inline double amplitude_from_lift_time(double t_lift_s, double la_deg, double bph) {
    const double s = std::sin(static_cast<double>(kPiL) * (bph / 7200.0) * t_lift_s);
    if (!(t_lift_s > 0.0 && s > 0.0)) return kNaN;
    return la_deg / (2.0 * s);
}

inline double nanmax2(double a, double b) {
    if (is_nan(a)) return b;
    if (is_nan(b)) return a;
    return std::max(a, b);
}

double pair_sigma(const std::int64_t* k, const double* t, std::size_t n) {
    if (n < kPairMinTriples + 2U) return kNaN;
    std::vector<double> d;
    d.reserve(n - 2U);
    for (std::size_t i = 0; i + 2U < n; ++i) {
        if ((k[i + 1] - k[i] == 2) && (k[i + 2] - k[i + 1] == 2)) {
            d.push_back(t[i] - 2.0 * t[i + 1] + t[i + 2]);
        }
    }
    if (d.size() < kPairMinTriples) return kNaN;
    return std::max(mad_sigma_vec(d.data(), d.size()) / std::sqrt(6.0), kSigmaFloor);
}

std::pair<double, double> line_scatter(const std::int64_t* k, const double* t, std::size_t n_all,
                                       double outlier_sigma) {
    if (n_all < 3U) return {kInf, 0.0};
    const std::int64_t k0 = k[n_all / 2U];
    std::vector<double> x(n_all), r(n_all), dev(n_all);
    for (std::size_t i = 0; i < n_all; ++i) x[i] = static_cast<double>(k[i] - k0);
    std::vector<std::uint8_t> keep(n_all, 1);
    std::size_t n = n_all;
    for (int iter = 0; iter < 10; ++iter) {
        if (n < 3U) return {kInf, 0.0};
        double sx = 0.0, st = 0.0;
        for (std::size_t i = 0; i < n_all; ++i) {
            if (keep[i]) {
                sx += x[i];
                st += t[i];
            }
        }
        const double xm = sx / static_cast<double>(n);
        const double tm = st / static_cast<double>(n);
        double sxx = 0.0, sxy = 0.0;
        for (std::size_t i = 0; i < n_all; ++i) {
            if (keep[i]) {
                const double dx = x[i] - xm;
                sxx += dx * dx;
                sxy += dx * (t[i] - tm);
            }
        }
        if (!(sxx > 0.0)) return {kInf, 0.0};
        const double slope = sxy / sxx;
        const double icpt = tm - slope * xm;
        std::vector<double> rk;
        rk.reserve(n);
        for (std::size_t i = 0; i < n_all; ++i) {
            r[i] = t[i] - (slope * x[i] + icpt);
            if (keep[i]) rk.push_back(r[i]);
        }
        const double med_r = median_vec(rk.data(), rk.size());
        for (std::size_t i = 0; i < rk.size(); ++i) dev[i] = std::fabs(rk[i] - med_r);
        const double s = std::max(kMadToSigma * exact_median(dev.data(), rk.size()), kSigmaFloor);
        const double thr = outlier_sigma * s;
        bool changed = false;
        std::size_t new_n = 0;
        for (std::size_t i = 0; i < n_all; ++i) {
            const std::uint8_t nk = (std::fabs(r[i] - med_r) <= thr) ? 1 : 0;
            if (nk != keep[i]) changed = true;
            keep[i] = nk;
            new_n += nk;
        }
        if (!changed) break;
        n = new_n;
    }
    if (n < 3U) return {kInf, 0.0};
    std::vector<double> rk;
    rk.reserve(n);
    for (std::size_t i = 0; i < n_all; ++i)
        if (keep[i]) rk.push_back(r[i]);
    return {std::max(std_vec(rk.data(), rk.size()), kSigmaFloor),
            static_cast<double>(n) / static_cast<double>(n_all)};
}

// 4x4 Gauss-Jordan inverse + solve for symmetric PD matrices.
bool invert_4x4(const double A_in[4][4], double inv_out[4][4]) {
    double M[4][8] = {};
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) M[r][c] = A_in[r][c];
        M[r][4 + r] = 1.0;
    }
    for (int col = 0; col < 4; ++col) {
        int piv = col;
        double best = std::fabs(M[col][col]);
        for (int r = col + 1; r < 4; ++r) {
            const double v = std::fabs(M[r][col]);
            if (v > best) {
                best = v;
                piv = r;
            }
        }
        if (!(best > 1e-18)) return false;
        if (piv != col) {
            for (int c = 0; c < 8; ++c) std::swap(M[col][c], M[piv][c]);
        }
        const double inv = 1.0 / M[col][col];
        for (int c = 0; c < 8; ++c) M[col][c] *= inv;
        for (int r = 0; r < 4; ++r) {
            if (r == col) continue;
            const double f = M[r][col];
            for (int c = 0; c < 8; ++c) M[r][c] -= f * M[col][c];
        }
    }
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c) inv_out[r][c] = M[r][4 + c];
    return true;
}

bool solve_wls_4x4(const std::vector<double>& x0, const std::vector<double>& x1,
                   const std::vector<double>& x2, const std::vector<double>& x3,
                   const std::vector<double>& y, const std::vector<double>& w,
                   const std::vector<std::uint8_t>& keep, double coef_out[4]) {
    double A[4][4] = {};
    double rhs[4] = {};
    const std::size_t n = y.size();
    for (std::size_t i = 0; i < n; ++i) {
        if (!keep[i]) continue;
        const double row[4] = {x0[i], x1[i], x2[i], x3[i]};
        const double wi = w[i];
        for (int r = 0; r < 4; ++r) {
            const double rw = wi * row[r];
            rhs[r] += rw * y[i];
            for (int c = r; c < 4; ++c) A[r][c] += rw * row[c];
        }
    }
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < r; ++c) A[r][c] = A[c][r];
    double invA[4][4];
    if (invert_4x4(A, invA)) {
        for (int r = 0; r < 4; ++r) {
            coef_out[r] = invA[r][0] * rhs[0] + invA[r][1] * rhs[1] + invA[r][2] * rhs[2] +
                          invA[r][3] * rhs[3];
        }
        return true;
    }
    // Near-singular fallback: tiny Tikhonov ridge preserves numerical stability.
    for (int d = 0; d < 4; ++d) A[d][d] += 1e-12;
    if (invert_4x4(A, invA)) {
        for (int r = 0; r < 4; ++r) {
            coef_out[r] = invA[r][0] * rhs[0] + invA[r][1] * rhs[1] + invA[r][2] * rhs[2] +
                          invA[r][3] * rhs[3];
        }
        return true;
    }
    coef_out[0] = coef_out[1] = coef_out[2] = coef_out[3] = 0.0;
    return false;
}

NativeParityFit fit_parity_linear(const double* k, const double* t, std::size_t n,
                                  double outlier_sigma, int max_iterations, int min_per_parity,
                                  bool weighted) {
    NativeParityFit out;
    const double k_ref = std::round(median_vec(k, n));
    const double t_ref = median_vec(t, n);

    std::vector<double> ke_all, ye_all, ko_all, yo_all;
    ke_all.reserve(n / 2 + 2);
    ye_all.reserve(n / 2 + 2);
    ko_all.reserve(n / 2 + 2);
    yo_all.reserve(n / 2 + 2);
    for (std::size_t i = 0; i < n; ++i) {
        const std::int64_t ki = static_cast<std::int64_t>(k[i]);
        const double kk = k[i] - k_ref;
        const double yy = t[i] - t_ref;
        if ((ki & 1) == 0) {
            ke_all.push_back(kk);
            ye_all.push_back(yy);
        } else {
            ko_all.push_back(kk);
            yo_all.push_back(yy);
        }
    }
    const std::size_t ne_all = ke_all.size(), no_all = ko_all.size();
    std::vector<std::uint8_t> ke_m(ne_all, 1), ko_m(no_all, 1);
    std::size_t ne = ne_all, no = no_all;
    double we = 1.0, wo = 1.0;

    std::vector<double> re(ne_all), ro(no_all), rk_e, rk_o, dev;
    for (int iter = 0; iter < max_iterations; ++iter) {
        if (static_cast<int>(ne) < min_per_parity || static_cast<int>(no) < min_per_parity)
            return out;
        double ske = 0.0, sye = 0.0, sko = 0.0, syo = 0.0;
        for (std::size_t i = 0; i < ne_all; ++i)
            if (ke_m[i]) {
                ske += ke_all[i];
                sye += ye_all[i];
            }
        for (std::size_t i = 0; i < no_all; ++i)
            if (ko_m[i]) {
                sko += ko_all[i];
                syo += yo_all[i];
            }
        const double mke = ske / static_cast<double>(ne);
        const double mye = sye / static_cast<double>(ne);
        const double mko = sko / static_cast<double>(no);
        const double myo = syo / static_cast<double>(no);
        double sxx_e = 0.0, sxy_e = 0.0, sxx_o = 0.0, sxy_o = 0.0;
        for (std::size_t i = 0; i < ne_all; ++i)
            if (ke_m[i]) {
                const double d = ke_all[i] - mke;
                sxx_e += d * d;
                sxy_e += d * (ye_all[i] - mye);
            }
        for (std::size_t i = 0; i < no_all; ++i)
            if (ko_m[i]) {
                const double d = ko_all[i] - mko;
                sxx_o += d * d;
                sxy_o += d * (yo_all[i] - myo);
            }
        const double sxx = we * sxx_e + wo * sxx_o;
        if (!(sxx > 0.0)) return out;
        const double period = (we * sxy_e + wo * sxy_o) / sxx;
        const double mu_e = mye - period * mke;
        const double mu_o = myo - period * mko;

        rk_e.clear();
        rk_o.clear();
        for (std::size_t i = 0; i < ne_all; ++i) {
            re[i] = ye_all[i] - (mu_e + period * ke_all[i]);
            if (ke_m[i]) rk_e.push_back(re[i]);
        }
        for (std::size_t i = 0; i < no_all; ++i) {
            ro[i] = yo_all[i] - (mu_o + period * ko_all[i]);
            if (ko_m[i]) rk_o.push_back(ro[i]);
        }
        const double ce = median_vec(rk_e.data(), rk_e.size());
        dev.resize(rk_e.size());
        for (std::size_t i = 0; i < rk_e.size(); ++i) dev[i] = std::fabs(rk_e[i] - ce);
        const double se = std::max(kMadToSigma * exact_median(dev.data(), rk_e.size()), kSigmaFloor);

        const double co = median_vec(rk_o.data(), rk_o.size());
        dev.resize(rk_o.size());
        for (std::size_t i = 0; i < rk_o.size(); ++i) dev[i] = std::fabs(rk_o[i] - co);
        const double so = std::max(kMadToSigma * exact_median(dev.data(), rk_o.size()), kSigmaFloor);

        bool changed = false;
        std::size_t new_ne = 0, new_no = 0;
        for (std::size_t i = 0; i < ne_all; ++i) {
            const std::uint8_t nm = (std::fabs(re[i] - ce) <= outlier_sigma * se) ? 1 : 0;
            if (nm != ke_m[i]) changed = true;
            ke_m[i] = nm;
            new_ne += nm;
        }
        for (std::size_t i = 0; i < no_all; ++i) {
            const std::uint8_t nm = (std::fabs(ro[i] - co) <= outlier_sigma * so) ? 1 : 0;
            if (nm != ko_m[i]) changed = true;
            ko_m[i] = nm;
            new_no += nm;
        }
        if (weighted) {
            we = 1.0 / (se * se);
            wo = 1.0 / (so * so);
        }
        ne = new_ne;
        no = new_no;
        if (!changed) break;
    }
    if (static_cast<int>(ne) < min_per_parity || static_cast<int>(no) < min_per_parity) return out;

    double ske = 0.0, sye = 0.0, sko = 0.0, syo = 0.0;
    for (std::size_t i = 0; i < ne_all; ++i)
        if (ke_m[i]) {
            ske += ke_all[i];
            sye += ye_all[i];
        }
    for (std::size_t i = 0; i < no_all; ++i)
        if (ko_m[i]) {
            sko += ko_all[i];
            syo += yo_all[i];
        }
    const double mke = ske / static_cast<double>(ne);
    const double mye = sye / static_cast<double>(ne);
    const double mko = sko / static_cast<double>(no);
    const double myo = syo / static_cast<double>(no);
    double sxx_e = 0.0, sxy_e = 0.0, sxx_o = 0.0, sxy_o = 0.0;
    for (std::size_t i = 0; i < ne_all; ++i)
        if (ke_m[i]) {
            const double d = ke_all[i] - mke;
            sxx_e += d * d;
            sxy_e += d * (ye_all[i] - mye);
        }
    for (std::size_t i = 0; i < no_all; ++i)
        if (ko_m[i]) {
            const double d = ko_all[i] - mko;
            sxx_o += d * d;
            sxy_o += d * (yo_all[i] - myo);
        }
    const double sxx = we * sxx_e + wo * sxx_o;
    if (!(sxx > 0.0)) return out;
    const double period = (we * sxy_e + wo * sxy_o) / sxx;
    const double mu_e = mye - period * mke;
    const double mu_o = myo - period * mko;

    rk_e.clear();
    rk_o.clear();
    for (std::size_t i = 0; i < ne_all; ++i)
        if (ke_m[i]) rk_e.push_back(ye_all[i] - (mu_e + period * ke_all[i]));
    for (std::size_t i = 0; i < no_all; ++i)
        if (ko_m[i]) rk_o.push_back(yo_all[i] - (mu_o + period * ko_all[i]));
    const double s_even = std::max(std_vec(rk_e.data(), rk_e.size()), kSigmaFloor);
    const double s_odd = std::max(std_vec(rk_o.data(), rk_o.size()), kSigmaFloor);
    const double ve = s_even * s_even;
    const double vo = s_odd * s_odd;
    double var_p;
    if (weighted) {
        var_p = 1.0 / (sxx_e / ve + sxx_o / vo);
    } else {
        const double sxx_u = sxx_e + sxx_o;
        var_p = (ve * sxx_e + vo * sxx_o) / (sxx_u * sxx_u);
    }
    const double dm = mke - mko;
    const double var_c = 0.25 * (ve / static_cast<double>(ne) + vo / static_cast<double>(no) + dm * dm * var_p);

    out.valid = 1;
    out.a = 0.5 * (mu_e + mu_o) + t_ref;
    out.period = period;
    out.c = 0.5 * (mu_e - mu_o);
    out.period_sigma = std::sqrt(std::max(var_p, 0.0));
    out.c_sigma = std::sqrt(std::max(var_c, 0.0));
    out.k_ref = k_ref;
    out.sigma_even = s_even;
    out.sigma_odd = s_odd;
    out.n_even = static_cast<std::int64_t>(ne);
    out.n_odd = static_cast<std::int64_t>(no);
    out.n_trimmed = static_cast<std::int64_t>((ne_all - ne) + (no_all - no));
    return out;
}

NativeParityFit fit_parity_quad(const double* k, const double* t, std::size_t n_all,
                                double outlier_sigma, int max_iterations, int min_per_parity,
                                bool weighted) {
    NativeParityFit out;
    const double k_ref = std::round(median_vec(k, n_all));
    double max_kk = 1.0;
    for (std::size_t i = 0; i < n_all; ++i) max_kk = std::max(max_kk, std::fabs(k[i] - k_ref));
    const double t_ref = median_vec(t, n_all);

    std::vector<double> x0(n_all, 1.0), x1(n_all), x2(n_all), x3(n_all), y(n_all), w(n_all, 1.0);
    std::vector<std::uint8_t> even(n_all), keep(n_all, 1);
    for (std::size_t i = 0; i < n_all; ++i) {
        const double ks = (k[i] - k_ref) / max_kk;
        const bool ev = (static_cast<std::int64_t>(k[i]) & 1) == 0;
        even[i] = ev ? 1 : 0;
        x1[i] = ks;
        x2[i] = ev ? 1.0 : -1.0;
        x3[i] = ks * ks;
        y[i] = t[i] - t_ref;
    }

    double coef[4] = {};
    std::vector<double> r(n_all), rp_e, rp_o, dev;
    std::size_t n_even = 0, n_odd = 0;
    for (std::size_t i = 0; i < n_all; ++i) even[i] ? ++n_even : ++n_odd;

    for (int iter = 0; iter < max_iterations; ++iter) {
        if (static_cast<int>(std::min(n_even, n_odd)) < min_per_parity) return out;
        solve_wls_4x4(x0, x1, x2, x3, y, w, keep, coef);
        rp_e.clear();
        rp_o.clear();
        for (std::size_t i = 0; i < n_all; ++i) {
            r[i] = y[i] - (coef[0] + coef[1] * x1[i] + coef[2] * x2[i] + coef[3] * x3[i]);
            if (keep[i]) (even[i] ? rp_e : rp_o).push_back(r[i]);
        }
        const double ce = median_vec(rp_e.data(), rp_e.size());
        dev.resize(rp_e.size());
        for (std::size_t i = 0; i < rp_e.size(); ++i) dev[i] = std::fabs(rp_e[i] - ce);
        const double se = std::max(kMadToSigma * exact_median(dev.data(), rp_e.size()), kSigmaFloor);

        const double co = median_vec(rp_o.data(), rp_o.size());
        dev.resize(rp_o.size());
        for (std::size_t i = 0; i < rp_o.size(); ++i) dev[i] = std::fabs(rp_o[i] - co);
        const double so = std::max(kMadToSigma * exact_median(dev.data(), rp_o.size()), kSigmaFloor);

        bool changed = false;
        n_even = n_odd = 0;
        for (std::size_t i = 0; i < n_all; ++i) {
            const double center = even[i] ? ce : co;
            const double sig = even[i] ? se : so;
            const std::uint8_t nk = (std::fabs(r[i] - center) <= outlier_sigma * sig) ? 1 : 0;
            if (nk != keep[i]) changed = true;
            keep[i] = nk;
            if (nk) even[i] ? ++n_even : ++n_odd;
        }
        if (weighted) {
            const double we = 1.0 / (se * se), wo = 1.0 / (so * so);
            for (std::size_t i = 0; i < n_all; ++i) w[i] = even[i] ? we : wo;
        }
        if (!changed) break;
    }
    if (static_cast<int>(std::min(n_even, n_odd)) < min_per_parity) return out;

    solve_wls_4x4(x0, x1, x2, x3, y, w, keep, coef);
    rp_e.clear();
    rp_o.clear();
    for (std::size_t i = 0; i < n_all; ++i) {
        r[i] = y[i] - (coef[0] + coef[1] * x1[i] + coef[2] * x2[i] + coef[3] * x3[i]);
        if (keep[i]) (even[i] ? rp_e : rp_o).push_back(r[i]);
    }
    const double s_even = std::max(std_vec(rp_e.data(), rp_e.size()), kSigmaFloor);
    const double s_odd = std::max(std_vec(rp_o.data(), rp_o.size()), kSigmaFloor);
    const double ve = s_even * s_even, wo_v = s_odd * s_odd;

    double cov[4][4] = {};
    if (weighted) {
        double A[4][4] = {};
        for (std::size_t i = 0; i < n_all; ++i) {
            if (!keep[i]) continue;
            const double row[4] = {1.0, x1[i], x2[i], x3[i]};
            const double inv_v = 1.0 / (even[i] ? ve : wo_v);
            for (int r_ = 0; r_ < 4; ++r_)
                for (int c_ = r_; c_ < 4; ++c_) A[r_][c_] += inv_v * row[r_] * row[c_];
        }
        for (int r_ = 0; r_ < 4; ++r_)
            for (int c_ = 0; c_ < r_; ++c_) A[r_][c_] = A[c_][r_];
        invert_4x4(A, cov);
    } else {
        double XX[4][4] = {}, B[4][4] = {}, invXX[4][4] = {};
        for (std::size_t i = 0; i < n_all; ++i) {
            if (!keep[i]) continue;
            const double row[4] = {1.0, x1[i], x2[i], x3[i]};
            const double var = even[i] ? ve : wo_v;
            for (int r_ = 0; r_ < 4; ++r_)
                for (int c_ = r_; c_ < 4; ++c_) {
                    XX[r_][c_] += row[r_] * row[c_];
                    B[r_][c_] += var * row[r_] * row[c_];
                }
        }
        for (int r_ = 0; r_ < 4; ++r_)
            for (int c_ = 0; c_ < r_; ++c_) {
                XX[r_][c_] = XX[c_][r_];
                B[r_][c_] = B[c_][r_];
            }
        if (invert_4x4(XX, invXX)) {
            for (int r_ = 0; r_ < 4; ++r_)
                for (int c_ = 0; c_ < 4; ++c_) {
                    double s = 0.0;
                    for (int p = 0; p < 4; ++p)
                        for (int q = 0; q < 4; ++q) s += invXX[r_][p] * B[p][q] * invXX[q][c_];
                    cov[r_][c_] = s;
                }
        }
    }

    const double inv_s = 1.0 / max_kk;
    const double inv_s2 = inv_s * inv_s;
    out.valid = 1;
    out.a = coef[0] + t_ref;
    out.period = coef[1] * inv_s;
    out.c = coef[2];
    out.period_sigma = std::sqrt(std::max(cov[1][1], 0.0)) * inv_s;
    out.c_sigma = std::sqrt(std::max(cov[2][2], 0.0));
    out.k_ref = k_ref;
    out.sigma_even = s_even;
    out.sigma_odd = s_odd;
    out.n_even = static_cast<std::int64_t>(n_even);
    out.n_odd = static_cast<std::int64_t>(n_odd);
    out.n_trimmed = static_cast<std::int64_t>(n_all - (n_even + n_odd));
    out.q = coef[3] * inv_s2;
    out.q_sigma = std::sqrt(std::max(cov[3][3], 0.0)) * inv_s2;
    return out;
}

// Intra-call memo for fit_parity_weighted_cpp.
struct FitMemoEntry {
    std::vector<double> k, t;
    double outlier_sigma;
    int max_iterations;
    int min_per_parity;
    bool weighted;
    bool quadratic;
    NativeParityFit fit;
};

struct FitMemo {
    std::vector<FitMemoEntry> entries;

    NativeParityFit get(const double* k, const double* t, std::size_t n, double outlier_sigma,
                        int max_iterations, int min_per_parity, bool weighted, bool quadratic) {
        for (const auto& e : entries) {
            if (e.k.size() == n && e.outlier_sigma == outlier_sigma &&
                e.max_iterations == max_iterations && e.min_per_parity == min_per_parity &&
                e.weighted == weighted && e.quadratic == quadratic &&
                std::memcmp(e.k.data(), k, n * sizeof(double)) == 0 &&
                std::memcmp(e.t.data(), t, n * sizeof(double)) == 0) {
                return e.fit;
            }
        }
        NativeParityFit f = fit_parity_weighted_cpp(k, t, n, outlier_sigma, max_iterations,
                                                    min_per_parity, weighted, quadratic);
        FitMemoEntry e;
        e.k.assign(k, k + n);
        e.t.assign(t, t + n);
        e.outlier_sigma = outlier_sigma;
        e.max_iterations = max_iterations;
        e.min_per_parity = min_per_parity;
        e.weighted = weighted;
        e.quadratic = quadratic;
        e.fit = f;
        entries.push_back(std::move(e));
        return f;
    }
};

std::pair<double, double> rate_from_period(double period_samples, double period_sigma,
                                           const NativeMetricsConfig& cfg) {
    const double nom = (3600.0 / cfg.bph) * cfg.sample_rate;
    const double d = 1.0 + cfg.adc_drift_ppm * 1e-6;
    const double true_p = period_samples / d;
    const double rate = (nom / true_p - 1.0) * 86400.0;
    const double sig = nom * d / (period_samples * period_samples) * 86400.0 * period_sigma;
    return {rate, sig};
}

double trend_from_fit(const NativeParityFit& fit, const NativeMetricsConfig& cfg) {
    const double nom = (3600.0 / cfg.bph) * cfg.sample_rate;
    const double d = 1.0 + cfg.adc_drift_ppm * 1e-6;
    const double p = fit.period;
    const double drate_dp = -nom * d / (p * p) * 86400.0;
    const double beats_per_min = 60.0 * cfg.sample_rate / p;
    return drate_dp * 2.0 * fit.q * beats_per_min;
}

double jackknife_sigma(const std::vector<double>& vals) {
    const std::size_t n = vals.size();
    const double mean = neumaier_sum(vals.data(), n) / static_cast<double>(n);
    double s2 = 0.0;
    for (double v : vals) {
        const double d = v - mean;
        s2 += d * d;
    }
    return std::sqrt(static_cast<double>(n - 1) / static_cast<double>(n) * s2);
}

struct BeCand {
    int point = 0;  // 1="p1", 2="p1_beat", 3="p1_fine", 4="t_fine"
    double be = kNaN, sigma = kNaN, sigma_block = kNaN, unc = kNaN;
};

BeCand eval_be_cand(FitMemo& memo, int point, const double* k, const double* t, std::size_t n,
                    const NativeMetricsConfig& cfg, double c_shift = 0.0) {
    BeCand out;
    out.point = point;
    if (n == 0) return out;
    const auto fit =
        memo.get(k, t, n, cfg.outlier_sigma, cfg.max_iterations, cfg.min_beats_per_parity, true, false);
    if (!fit.valid) return out;
    const double fs = cfg.sample_rate;
    out.be = 2.0 * std::fabs(fit.c + c_shift) / fs * 1e3;
    out.sigma = 2.0 * fit.c_sigma / fs * 1e3;

    double tmin = t[0], tmax = t[0];
    for (std::size_t i = 1; i < n; ++i) {
        tmin = std::min(tmin, t[i]);
        tmax = std::max(tmax, t[i]);
    }
    const int nb = cfg.trend_blocks;
    std::vector<double> vals, km, tm;
    vals.reserve(static_cast<std::size_t>(nb));
    km.reserve(n);
    tm.reserve(n);
    for (int bi = 0; bi < nb; ++bi) {
        const double lo = tmin + (tmax - tmin) * static_cast<double>(bi) / static_cast<double>(nb);
        const double hi =
            (bi + 1 == nb)
                ? (tmax + 1.0)
                : (tmin + (tmax - tmin) * static_cast<double>(bi + 1) / static_cast<double>(nb));
        km.clear();
        tm.clear();
        for (std::size_t i = 0; i < n; ++i) {
            if (!(t[i] >= lo && t[i] < hi)) {
                km.push_back(k[i]);
                tm.push_back(t[i]);
            }
        }
        const auto bf = memo.get(km.data(), tm.data(), km.size(), cfg.outlier_sigma,
                                 cfg.max_iterations, cfg.min_beats_per_parity, true, false);
        if (!bf.valid) {
            vals.clear();
            break;
        }
        vals.push_back(2.0 * std::fabs(bf.c + c_shift) / fs * 1e3);
    }
    out.sigma_block = vals.empty() ? kNaN : jackknife_sigma(vals);
    out.unc = nanmax2(out.sigma, out.sigma_block);
    return out;
}

double drop_rise_samples(const NativeTemplateInput& tp, double fs) {
    if (!tp.valid || !is_finite(tp.onset_p3) || !tp.env || tp.env_len == 0) return kNaN;
    const std::int64_t a = std::max<std::int64_t>(0, static_cast<std::int64_t>(std::llrint(tp.onset_p3)));
    const std::int64_t b = std::min<std::int64_t>(
        static_cast<std::int64_t>(tp.env_len),
        a + static_cast<std::int64_t>(std::llrint(kDropRiseWindowS * fs)));
    if (b - a < 2) return kNaN;
    std::int64_t best = 0;
    double best_v = tp.env[a];
    for (std::int64_t i = a + 1; i < b; ++i) {
        if (tp.env[i] > best_v) {
            best_v = tp.env[i];
            best = i - a;
        }
    }
    return static_cast<double>(best);
}

}  // namespace

NativeParityFit fit_parity_weighted_cpp(const double* k, const double* t, std::size_t n,
                                        double outlier_sigma, int max_iterations,
                                        int min_per_parity, bool weighted, bool quadratic) {
    NativeParityFit out;
    if (n == 0) return out;
    int ce = 0, co = 0;
    for (std::size_t i = 0; i < n; ++i) ((static_cast<std::int64_t>(k[i]) & 1) == 0) ? ++ce : ++co;
    if (std::min(ce, co) < min_per_parity) return out;
    if (!quadratic)
        return fit_parity_linear(k, t, n, outlier_sigma, max_iterations, min_per_parity, weighted);
    return fit_parity_quad(k, t, n, outlier_sigma, max_iterations, min_per_parity, weighted);
}

void compute_metrics_cpp(const NativeBeat* beats, std::size_t n_beats,
                         const NativeTemplateInput& tpl0, const NativeTemplateInput& tpl1,
                         const NativeMetricsConfig& cfg, NativeMetricsResult& out,
                         std::vector<NativeWindowRate>& windows_out,
                         std::vector<std::string>& warnings_out) {
    out = NativeMetricsResult{};
    windows_out.clear();
    warnings_out.clear();
    const double fs = cfg.sample_rate;
    const double la = cfg.has_lift_angle ? cfg.lift_angle_deg : kDefaultLiftAngleDeg;
    out.n_beats = static_cast<std::int64_t>(n_beats);
    out.lift_angle_deg = la;

    // select_timing
    std::vector<std::int64_t> k_all(n_beats);
    std::vector<double> tf_all(n_beats), tp_all(n_beats), t_sel(n_beats);
    std::vector<std::uint8_t> good(n_beats), use(n_beats);
    for (std::size_t i = 0; i < n_beats; ++i) {
        k_all[i] = beats[i].k;
        tf_all[i] = beats[i].t_fine;
        tp_all[i] = beats[i].t_p1;
        good[i] = ((beats[i].flags & BEAT_SATURATED) == 0U) ? 1 : 0;
        use[i] = (good[i] && is_finite(tf_all[i])) ? 1 : 0;
        t_sel[i] = tf_all[i];
    }
    int pts[2] = {0, 0};  // 0="t_fine", 1="p1"
    if (cfg.p1_timing && !cfg.quartz && n_beats > 0) {
        std::vector<std::int64_t> kf, kp;
        std::vector<double> tf, tp;
        kf.reserve(n_beats / 2 + 2);
        kp.reserve(n_beats / 2 + 2);
        tf.reserve(n_beats / 2 + 2);
        tp.reserve(n_beats / 2 + 2);
        for (int parity = 0; parity <= 1; ++parity) {
            kf.clear();
            tf.clear();
            kp.clear();
            tp.clear();
            for (std::size_t i = 0; i < n_beats; ++i) {
                if (!good[i] || ((k_all[i] & 1) != parity)) continue;
                if (is_finite(tf_all[i])) {
                    kf.push_back(k_all[i]);
                    tf.push_back(tf_all[i]);
                }
                if (is_finite(tp_all[i])) {
                    kp.push_back(k_all[i]);
                    tp.push_back(tp_all[i]);
                }
            }
            const std::size_t nf = kf.size(), np = kp.size();
            if (static_cast<int>(nf) < cfg.min_beats_per_parity ||
                static_cast<double>(np) <
                    std::max(static_cast<double>(cfg.min_beats_per_parity),
                             cfg.p1_min_share * static_cast<double>(nf))) {
                continue;
            }
            const double pf = pair_sigma(kf.data(), tf.data(), nf);
            const double pp = pair_sigma(kp.data(), tp.data(), np);
            if (!(is_finite(pf) && is_finite(pp))) continue;
            const auto [lf, sf_] = line_scatter(kf.data(), tf.data(), nf, cfg.outlier_sigma);
            (void)sf_;
            const auto [lp, share] = line_scatter(kp.data(), tp.data(), np, cfg.outlier_sigma);
            const bool was_p1 = (parity == 0) ? (cfg.prev_p0_p1 != 0) : (cfg.prev_p1_p1 != 0);
            const double ratio = was_p1 ? cfg.p1_keep_ratio : cfg.be_switch_ratio;
            if (share >= cfg.p1_min_share && pp < ratio * pf && lp <= lf) {
                pts[parity] = 1;
                for (std::size_t i = 0; i < n_beats; ++i) {
                    if (good[i] && ((k_all[i] & 1) == parity)) {
                        t_sel[i] = tp_all[i];
                        use[i] = is_finite(tp_all[i]) ? 1 : 0;
                    }
                }
            }
        }
    }

    std::vector<double> k_use, t_use;
    k_use.reserve(n_beats);
    t_use.reserve(n_beats);
    for (std::size_t i = 0; i < n_beats; ++i) {
        if (use[i]) {
            k_use.push_back(static_cast<double>(k_all[i]));
            t_use.push_back(t_sel[i]);
        }
    }
    const std::int64_t excluded = static_cast<std::int64_t>(n_beats - k_use.size());
    out.n_excluded = excluded;
    out.excluded_fraction = n_beats ? (static_cast<double>(excluded) / static_cast<double>(n_beats)) : 0.0;
    out.rate_point_0_p1 = pts[0];
    out.rate_point_1_p1 = pts[1];

    if (!t_use.empty()) {
        double tmin = t_use[0], tmax = t_use[0];
        for (double v : t_use) {
            tmin = std::min(tmin, v);
            tmax = std::max(tmax, v);
        }
        out.span_s = (tmax - tmin) / fs;
    }
    if (cfg.adc_drift_ppm == 0.0 && cfg.adc_drift_sigma_ppm == 0.0) {
        warnings_out.emplace_back(
            "marcha sin calibrar: no se ha corregido la deriva del reloj de la tarjeta (fase 4)");
    } else {
        out.rate_calibrated = 1;
    }

    FitMemo memo;
    const auto fit = memo.get(k_use.data(), t_use.data(), k_use.size(), cfg.outlier_sigma,
                              cfg.max_iterations, cfg.min_beats_per_parity, true, false);
    if (!fit.valid) {
        char buf[128];
        std::snprintf(buf, sizeof(buf),
                      "menos de %d batimientos válidos por paridad: sin cifras",
                      cfg.min_beats_per_parity);
        warnings_out.emplace_back(buf);
        return;
    }

    out.n_used_even = fit.n_even;
    out.n_used_odd = fit.n_odd;
    out.sigma_even_us = fit.sigma_even / fs * 1e6;
    out.sigma_odd_us = fit.sigma_odd / fs * 1e6;

    std::vector<std::int64_t> ke_i, ko_i;
    std::vector<double> te_v, to_v;
    for (std::size_t i = 0; i < k_use.size(); ++i) {
        const std::int64_t ki = static_cast<std::int64_t>(k_use[i]);
        if ((ki & 1) == 0) {
            ke_i.push_back(ki);
            te_v.push_back(t_use[i]);
        } else {
            ko_i.push_back(ki);
            to_v.push_back(t_use[i]);
        }
    }
    const double pe = pair_sigma(ke_i.data(), te_v.data(), ke_i.size());
    const double po = pair_sigma(ko_i.data(), to_v.data(), ko_i.size());
    if (is_finite(pe)) out.pair_sigma_even_us = pe / fs * 1e6;
    if (is_finite(po)) out.pair_sigma_odd_us = po / fs * 1e6;

    std::tie(out.rate_s_per_day, out.rate_sigma_s_per_day) =
        rate_from_period(fit.period, fit.period_sigma, cfg);

    if (cfg.compute_windows && !t_use.empty()) {
        double t0 = t_use[0], t_end = t_use[0];
        for (double v : t_use) {
            t0 = std::min(t0, v);
            t_end = std::max(t_end, v);
        }
        const double w_s = std::max(cfg.window_s, 2.5 * cfg.min_beats_per_parity * (3600.0 / cfg.bph));
        const double w = w_s * fs;
        const double step = ((cfg.window_step_s > 0.0) ? cfg.window_step_s : w_s) * fs;
        const int n_win = std::max(1, static_cast<int>(std::floor((t_end - t0 - w) / step + 1e-9)) + 1);
        std::vector<double> kw, tw;
        for (int i = 0; i < n_win; ++i) {
            const double a = t0 + static_cast<double>(i) * step;
            const double b = a + w;
            const bool last = (i == n_win - 1) && (a + w >= t_end - 1e-9);
            kw.clear();
            tw.clear();
            for (std::size_t idx = 0; idx < t_use.size(); ++idx) {
                const double tv = t_use[idx];
                if (tv >= a && (last ? (tv <= b) : (tv < b))) {
                    kw.push_back(k_use[idx]);
                    tw.push_back(tv);
                }
            }
            const auto wfit = memo.get(kw.data(), tw.data(), kw.size(), cfg.outlier_sigma,
                                       cfg.max_iterations, cfg.min_beats_per_parity, true, false);
            if (!wfit.valid) continue;
            const auto [wr, ws] = rate_from_period(wfit.period, wfit.period_sigma, cfg);
            NativeWindowRate item;
            item.start_s = a / fs;
            item.end_s = b / fs;
            item.center_s = 0.5 * (a + b) / fs;
            item.rate_s_per_day = wr;
            item.rate_sigma_s_per_day = ws;
            item.n_beats = static_cast<std::int64_t>(kw.size());
            windows_out.push_back(item);
        }
    }

    // rate_trend
    {
        const auto fq_full = memo.get(k_use.data(), t_use.data(), k_use.size(), cfg.outlier_sigma,
                                      cfg.max_iterations, cfg.min_beats_per_parity, true, true);
        if (fq_full.valid && !t_use.empty()) {
            out.rate_trend_s_per_day_per_min = trend_from_fit(fq_full, cfg);
            double tmin = t_use[0], tmax = t_use[0];
            for (double v : t_use) {
                tmin = std::min(tmin, v);
                tmax = std::max(tmax, v);
            }
            const int nb = cfg.trend_blocks;
            std::vector<double> jt, jr, km, tm;
            bool ok_j = true;
            for (int bi = 0; bi < nb; ++bi) {
                const double lo = tmin + (tmax - tmin) * static_cast<double>(bi) / static_cast<double>(nb);
                const double hi =
                    (bi + 1 == nb)
                        ? (tmax + 1.0)
                        : (tmin + (tmax - tmin) * static_cast<double>(bi + 1) / static_cast<double>(nb));
                km.clear();
                tm.clear();
                for (std::size_t i = 0; i < t_use.size(); ++i) {
                    if (!(t_use[i] >= lo && t_use[i] < hi)) {
                        km.push_back(k_use[i]);
                        tm.push_back(t_use[i]);
                    }
                }
                const auto fq = memo.get(km.data(), tm.data(), km.size(), cfg.outlier_sigma,
                                         cfg.max_iterations, cfg.min_beats_per_parity, true, true);
                const auto fl = memo.get(km.data(), tm.data(), km.size(), cfg.outlier_sigma,
                                         cfg.max_iterations, cfg.min_beats_per_parity, true, false);
                if (!fq.valid || !fl.valid) {
                    ok_j = false;
                    break;
                }
                jt.push_back(trend_from_fit(fq, cfg));
                jr.push_back(rate_from_period(fl.period, 0.0, cfg).first);
            }
            if (ok_j) {
                out.rate_trend_sigma_s_per_day_per_min = jackknife_sigma(jt);
                out.rate_sigma_block_s_per_day = jackknife_sigma(jr);
            }
        }
    }

    out.rate_clock_sigma_s_per_day = cfg.adc_drift_sigma_ppm * 1e-6 * 86400.0;
    const double base_sig = nanmax2(out.rate_sigma_s_per_day, out.rate_sigma_block_s_per_day);
    out.rate_uncertainty_s_per_day = std::hypot(base_sig, out.rate_clock_sigma_s_per_day);

    // _window_sigma_factor
    double w_factor = 1.0;
    if (windows_out.size() < 6U) {
        if (is_finite(out.rate_sigma_block_s_per_day) && out.rate_sigma_s_per_day > 0.0) {
            w_factor = std::max(w_factor, out.rate_sigma_block_s_per_day / out.rate_sigma_s_per_day);
        }
    } else {
        const std::size_t nw = windows_out.size();
        double xm = 0.0;
        for (const auto& w : windows_out) xm += w.center_s;
        xm /= static_cast<double>(nw);
        double sw = 0.0, sxx = 0.0, sx = 0.0, sy = 0.0, sxy = 0.0;
        for (const auto& w : windows_out) {
            const double sig = std::max(w.rate_sigma_s_per_day, 1e-9);
            const double wt = 1.0 / (sig * sig);
            const double dx = w.center_s - xm;
            sw += wt;
            sx += wt * dx;
            sxx += wt * dx * dx;
            sy += wt * w.rate_s_per_day;
            sxy += wt * dx * w.rate_s_per_day;
        }
        const double det = sw * sxx - sx * sx;
        if (std::fabs(det) > 0.0) {
            const double b0 = (sxx * sy - sx * sxy) / det;
            const double b1 = (sw * sxy - sx * sy) / det;
            double chi2 = 0.0;
            for (const auto& w : windows_out) {
                const double sig = std::max(w.rate_sigma_s_per_day, 1e-9);
                const double wt = 1.0 / (sig * sig);
                const double d = w.rate_s_per_day - (b0 + b1 * (w.center_s - xm));
                chi2 += wt * d * d;
            }
            chi2 /= static_cast<double>(nw - 2U);
            w_factor = std::max(w_factor, std::sqrt(std::max(chi2, 0.0)));
        }
    }
    out.window_sigma_factor = w_factor;
    for (auto& w : windows_out) w.rate_uncertainty_s_per_day = w.rate_sigma_s_per_day * w_factor;

    if (is_finite(out.rate_trend_sigma_s_per_day_per_min) &&
        std::fabs(out.rate_trend_s_per_day_per_min) >
            cfg.trend_sigma * out.rate_trend_sigma_s_per_day_per_min &&
        std::fabs(out.rate_trend_s_per_day_per_min) >= cfg.trend_min_s_per_day_per_min &&
        std::fabs(out.rate_trend_s_per_day_per_min) * out.span_s / 60.0 >=
            cfg.trend_min_change_s_per_day) {
        out.rate_trend_flag = 1;
        const double change = out.rate_trend_s_per_day_per_min * out.span_s / 60.0;
        char buf[256];
        std::snprintf(buf, sizeof(buf),
                      "la marcha cambia durante la medida: %+.1f s/d en %.0f s (%+.1f ± %.1f "
                      "s/d por minuto); el reloj aún se está asentando",
                      change, out.span_s, out.rate_trend_s_per_day_per_min,
                      out.rate_trend_sigma_s_per_day_per_min);
        warnings_out.emplace_back(buf);
    }

    if (!cfg.quartz) {
        std::vector<double> kf_drop, tf_drop;
        kf_drop.reserve(n_beats);
        tf_drop.reserve(n_beats);
        for (std::size_t i = 0; i < n_beats; ++i) {
            if (good[i] && is_finite(beats[i].t_fine)) {
                kf_drop.push_back(static_cast<double>(beats[i].k));
                tf_drop.push_back(beats[i].t_fine);
            }
        }
        auto fit_drop = fit;
        if (pts[0] != 0 || pts[1] != 0) {
            fit_drop = memo.get(kf_drop.data(), tf_drop.data(), kf_drop.size(), cfg.outlier_sigma,
                                cfg.max_iterations, cfg.min_beats_per_parity, true, false);
        }
        if (fit_drop.valid) out.beat_error_p3_ms = 2.0 * std::fabs(fit_drop.c) / fs * 1e3;

        // Beat error at P1
        std::vector<BeCand> cands;
        {
            std::vector<double> kt, tt;
            for (std::size_t i = 0; i < n_beats; ++i) {
                if (good[i] && is_finite(beats[i].t_p1_tpl)) {
                    kt.push_back(static_cast<double>(beats[i].k));
                    tt.push_back(beats[i].t_p1_tpl);
                }
            }
            BeCand res = eval_be_cand(memo, 1, kt.data(), tt.data(), kt.size(), cfg);
            if (!is_finite(res.be)) {
                const double off0 = tpl0.valid ? (tpl0.onset_ref - tpl0.onset_p1) : kNaN;
                const double off1 = tpl1.valid ? (tpl1.onset_ref - tpl1.onset_p1) : kNaN;
                if (is_finite(off0) && is_finite(off1)) {
                    res = eval_be_cand(memo, 1, kf_drop.data(), tf_drop.data(), kf_drop.size(), cfg,
                                       -0.5 * (off0 - off1));
                }
            }
            if (is_finite(res.be)) cands.push_back(res);
        }
        {
            std::vector<double> kb, tb;
            for (std::size_t i = 0; i < n_beats; ++i) {
                if (good[i] && is_finite(beats[i].p1)) {
                    kb.push_back(static_cast<double>(beats[i].k));
                    tb.push_back(beats[i].p1);
                }
            }
            BeCand res = eval_be_cand(memo, 2, kb.data(), tb.data(), kb.size(), cfg);
            if (is_finite(res.be)) cands.push_back(res);
        }
        if (pts[0] == 1 && pts[1] == 1) {
            BeCand res = eval_be_cand(memo, 3, k_use.data(), t_use.data(), k_use.size(), cfg);
            if (is_finite(res.be)) cands.push_back(res);
        }
        BeCand chosen;
        if (!cands.empty()) {
            chosen = cands[0];
            for (std::size_t i = 1; i < cands.size(); ++i) {
                const auto& cand = cands[i];
                const bool win_unc = cand.unc < cfg.be_switch_ratio * chosen.unc;
                const bool win_p1_fine =
                    (cand.point == 3) && (cand.unc <= chosen.unc) &&
                    (cand.sigma < cfg.be_switch_ratio * chosen.sigma ||
                     cand.sigma_block < cfg.be_switch_ratio * chosen.sigma_block);
                if (win_unc || win_p1_fine) chosen = cand;
            }
        } else {
            chosen = eval_be_cand(memo, 4, kf_drop.data(), tf_drop.data(), kf_drop.size(), cfg);
            chosen.point = 4;
            warnings_out.emplace_back(
                "error de batimiento medido en t_fine (inicio de P3): sin instantes de P1 válidos");
        }
        out.beat_error_point = chosen.point;
        out.beat_error_ms = chosen.be;
        out.beat_error_sigma_ms = chosen.sigma;
        out.beat_error_sigma_block_ms = chosen.sigma_block;
        out.beat_error_uncertainty_ms = chosen.unc;

        // Amplitude from templates
        const NativeTemplateInput tpls[2] = {tpl0, tpl1};
        double amps[2] = {kNaN, kNaN};
        double amps_p1[2] = {kNaN, kNaN};
        int n_valid_a = 0;
        double sum_a = 0.0, sum_ap1 = 0.0;
        for (int p = 0; p < 2; ++p) {
            if (!tpls[p].valid) continue;
            const double tl = (tpls[p].onset_p3 - tpls[p].onset_p1) / fs;
            if (is_finite(tl) && tl > 0.0) {
                const double v = amplitude_from_lift_time(tl, la, cfg.bph);
                if (cfg.amp_lo_deg <= v && v <= cfg.amp_hi_deg) {
                    amps[p] = v;
                    amps_p1[p] = amplitude_from_lift_time(tl, la + 1.0, cfg.bph);
                    sum_a += v;
                    sum_ap1 += amps_p1[p];
                    ++n_valid_a;
                }
            }
        }
        out.amplitude_tic_deg = amps[0];
        out.amplitude_tac_deg = amps[1];
        if (n_valid_a > 0) {
            out.amplitude_deg = sum_a / static_cast<double>(n_valid_a);
            out.amplitude_la_uncertainty_deg =
                std::fabs(sum_ap1 / static_cast<double>(n_valid_a) - out.amplitude_deg);
        }
        if (!cfg.has_lift_angle) {
            out.amplitude_indicative = 1;
            char buf[160];
            std::snprintf(buf, sizeof(buf),
                          "ángulo de levantamiento desconocido: se usan %g°; la amplitud es orientativa",
                          kDefaultLiftAngleDeg);
            warnings_out.emplace_back(buf);
        }
        if (!is_finite(out.amplitude_deg)) {
            char buf[160];
            std::snprintf(buf, sizeof(buf),
                          "amplitud no disponible (sin t_lift en las plantillas o fuera de %g-%g°)",
                          cfg.amp_lo_deg, cfg.amp_hi_deg);
            warnings_out.emplace_back(buf);
        } else if (is_finite(amps[0]) && is_finite(amps[1]) &&
                   std::fabs(amps[0] - amps[1]) > cfg.amplitude_mismatch_deg) {
            const double r0 = drop_rise_samples(tpl0, fs);
            const double r1 = drop_rise_samples(tpl1, fs);
            int sharp = -1;
            if (is_finite(r0) && is_finite(r1)) {
                const int p = (r0 <= r1) ? 0 : 1;
                const double rp = std::max(p == 0 ? r0 : r1, 1.0);
                const double ro = std::max(p == 0 ? r1 : r0, 1.0);
                if (ro >= cfg.drop_sharpness_ratio * rp) sharp = p;
            }
            if (sharp < 0) {
                char buf[160];
                std::snprintf(buf, sizeof(buf),
                              "amplitud de tic y tac distinta (%.0f° / %.0f°): revisar la "
                              "identificación de P1/P3",
                              amps[0], amps[1]);
                warnings_out.emplace_back(buf);
            } else {
                const char* names[2] = {"tic", "tac"};
                const double mean_orig = out.amplitude_deg;
                out.amplitude_parity = sharp + 1;  // 1="tic", 2="tac"
                out.amplitude_deg = amps[sharp];
                out.amplitude_la_uncertainty_deg *= amps[sharp] / mean_orig;
                char buf[192];
                std::snprintf(buf, sizeof(buf),
                              "amplitud tomada del %s (%.0f°): la caída del %s no es nítida "
                              "(daría %.0f°)",
                              names[sharp], amps[sharp], names[1 - sharp], amps[1 - sharp]);
                warnings_out.emplace_back(buf);
            }
        }
    }

    // Confidence
    const std::int64_t n_min = std::min(out.n_used_even, out.n_used_odd);
    const double q_n =
        std::min(1.0, static_cast<double>(n_min) / (3.0 * static_cast<double>(cfg.min_beats_per_parity)));
    const double s_line = std::max(out.sigma_even_us, out.sigma_odd_us);
    const double s_pair = std::max(out.pair_sigma_even_us, out.pair_sigma_odd_us);
    const double s_max =
        (is_finite(s_line) && is_finite(s_pair)) ? std::min(s_line, s_pair) : s_line;
    const double q_sigma = is_finite(s_max) ? (1.0 / (1.0 + s_max / 500.0)) : 0.0;
    const double q_excl = std::max(0.0, 1.0 - out.excluded_fraction);
    const double q_time = std::min(1.0, std::max(0.0, q_n * q_sigma * q_excl));
    double q_amp = 1.0;
    if (out.amplitude_parity != 0) {
        q_amp = 0.5;
    } else if (is_finite(out.amplitude_tic_deg) && is_finite(out.amplitude_tac_deg)) {
        q_amp = 1.0 / (1.0 + std::fabs(out.amplitude_tic_deg - out.amplitude_tac_deg) / 20.0);
    }
    out.confidence = std::min(1.0, std::max(0.0, q_time * q_amp));
}

}  // namespace advtg::core
