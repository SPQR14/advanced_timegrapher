// Complete offline gear-train analysis & SIMD Lomb-Scargle / Teager-Kaiser
// (analysis/gear_train.py).
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <numbers>
#include <string>
#include <thread>
#include <vector>

#include "advtg.h"
#include "advtg_analysis.hpp"
#include "advtg_bits.hpp"
#include "advtg_detector.hpp"
#include "advtg_dsp.hpp"
#include "advtg_metrics.hpp"
#include "advtg_simd.hpp"

namespace advtg::analysis {
namespace {

inline constexpr double kTwoPi = 6.283185307179586476925286766559005768;
inline constexpr std::size_t kReanchorStep = 64;

inline constexpr int32_t kDefaultEscapeTeeth = 15;
inline constexpr double kFourthWheelPeriodS = 60.0;
inline constexpr double kMinCycles = 3.0;
inline constexpr double kFapSignificant = 0.01;
inline constexpr std::size_t kMinBeats = 64;
inline constexpr double kOversample = 5.0;
inline constexpr double kMinPeriodBeats = 4.0;
inline constexpr double kEnergyPreMs = 1.0;
inline constexpr double kEnergyPostMs = 6.0;
inline constexpr double kPreP1FromMs = 6.0;
inline constexpr double kPreP1ToMs = 0.5;
inline constexpr double kOutlierSigma = 5.0;

thread_local std::string g_gear_error;

// Small dense Householder QR solver for m x ncols (ncols <= 4).
bool solve_ls(std::vector<double>& A, std::vector<double>& b, std::size_t m, std::size_t ncols,
              double* coef_out) {
    if (m < ncols) return false;
    for (std::size_t k = 0; k < ncols; ++k) {
        double norm2 = 0.0;
        for (std::size_t i = k; i < m; ++i) {
            const double v = A[i * ncols + k];
            norm2 += v * v;
        }
        const double norm = std::sqrt(norm2);
        if (!(norm > 0.0)) return false;
        const double alpha = (A[k * ncols + k] >= 0.0) ? -norm : norm;
        const double v0 = A[k * ncols + k] - alpha;
        std::vector<double> v(m - k, 0.0);
        v[0] = 1.0;
        for (std::size_t i = k + 1; i < m; ++i) v[i - k] = A[i * ncols + k] / v0;
        double vtv = 0.0;
        for (double x : v) vtv += x * x;
        const double beta = 2.0 / vtv;
        A[k * ncols + k] = alpha;
        for (std::size_t i = k + 1; i < m; ++i) A[i * ncols + k] = 0.0;
        for (std::size_t j = k + 1; j < ncols; ++j) {
            double dot = 0.0;
            for (std::size_t i = k; i < m; ++i) dot += v[i - k] * A[i * ncols + j];
            for (std::size_t i = k; i < m; ++i) A[i * ncols + j] -= beta * dot * v[i - k];
        }
        double dot_b = 0.0;
        for (std::size_t i = k; i < m; ++i) dot_b += v[i - k] * b[i];
        for (std::size_t i = k; i < m; ++i) b[i] -= beta * dot_b * v[i - k];
    }
    for (int r = static_cast<int>(ncols) - 1; r >= 0; --r) {
        double s = b[static_cast<std::size_t>(r)];
        for (std::size_t c = static_cast<std::size_t>(r) + 1; c < ncols; ++c) {
            s -= A[static_cast<std::size_t>(r) * ncols + c] * coef_out[c];
        }
        const double diag = A[static_cast<std::size_t>(r) * ncols + static_cast<std::size_t>(r)];
        if (std::fabs(diag) <= 1e-30) return false;
        coef_out[r] = s / diag;
    }
    return true;
}

void detrend_poly(const std::vector<std::int64_t>& k, const std::vector<double>& y, bool parity,
                  std::vector<double>& res_out) {
    const std::size_t n = k.size();
    res_out.assign(n, 0.0);
    if (n == 0) return;
    std::int64_t kmin = k[0], kmax = k[0];
    double ksum = 0.0;
    for (std::int64_t v : k) {
        kmin = std::min(kmin, v);
        kmax = std::max(kmax, v);
        ksum += static_cast<double>(v);
    }
    const double kmean = ksum / static_cast<double>(n);
    const double scale = std::max(1.0, static_cast<double>(kmax - kmin));
    const std::size_t ncols = parity ? 4U : 3U;
    std::vector<double> M(n * ncols);
    for (std::size_t i = 0; i < n; ++i) {
        const double kc = (static_cast<double>(k[i]) - kmean) / scale;
        M[i * ncols + 0] = 1.0;
        M[i * ncols + 1] = kc;
        M[i * ncols + 2] = kc * kc;
        if (parity) M[i * ncols + 3] = ((k[i] & 1) == 0) ? 1.0 : -1.0;
    }
    std::vector<double> A = M;
    std::vector<double> rhs = y;
    double coef[4] = {};
    if (!solve_ls(A, rhs, n, ncols, coef)) {
        res_out = y;
        return;
    }
    for (std::size_t i = 0; i < n; ++i) {
        double fit = 0.0;
        for (std::size_t c = 0; c < ncols; ++c) fit += M[i * ncols + c] * coef[c];
        res_out[i] = y[i] - fit;
    }
}

double amplitude_from_lift_time(double dt_s, double lift_angle_deg, double bph) {
    if (!(dt_s > 0.0 && lift_angle_deg > 0.0 && bph > 0.0)) return kNaN;
    const double f0 = bph / 7200.0;
    const double s = std::sin(std::numbers::pi * f0 * dt_s);
    if (!(s > 0.0)) return kNaN;
    return lift_angle_deg / (2.0 * s);
}

// Arbitrary-length complex radix-2 FFT + Bluestein for exact np.fft.rfft(n) at any n.
void cfft_pow2(std::vector<std::complex<double>>& a, bool inverse) {
    const std::size_t n = a.size();
    for (std::size_t i = 1, j = 0; i < n; ++i) {
        std::size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (std::size_t len = 2; len <= n; len <<= 1) {
        const double ang = (inverse ? 2.0 : -2.0) * std::numbers::pi / static_cast<double>(len);
        const std::complex<double> wlen(std::cos(ang), std::sin(ang));
        for (std::size_t i = 0; i < n; i += len) {
            std::complex<double> w(1.0, 0.0);
            for (std::size_t j = 0; j < len / 2; ++j) {
                const std::complex<double> u = a[i + j];
                const std::complex<double> v = a[i + j + len / 2] * w;
                a[i + j] = u + v;
                a[i + j + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
    if (inverse) {
        for (auto& x : a) x /= static_cast<double>(n);
    }
}

std::vector<std::complex<double>> rfft_any(const std::vector<double>& x) {
    const std::size_t n = x.size();
    std::vector<std::complex<double>> out(n / 2 + 1);
    if (n == 0) return out;
    if (is_pow2(n)) {
        dsp::rfft(x.data(), n, n, out.data());
        return out;
    }
    const std::size_t m = next_pow2(2 * n - 1);
    std::vector<std::complex<double>> w(n);
    for (std::size_t k = 0; k < n; ++k) {
        const std::size_t kk = (k * k) % (2 * n);
        const double ang = -std::numbers::pi * static_cast<double>(kk) / static_cast<double>(n);
        w[k] = std::complex<double>(std::cos(ang), std::sin(ang));
    }
    std::vector<std::complex<double>> a(m, {0.0, 0.0}), b(m, {0.0, 0.0});
    for (std::size_t j = 0; j < n; ++j) a[j] = x[j] * w[j];
    b[0] = {1.0, 0.0};
    for (std::size_t j = 1; j < n; ++j) {
        b[j] = std::conj(w[j]);
        b[m - j] = b[j];
    }
    cfft_pow2(a, false);
    cfft_pow2(b, false);
    for (std::size_t j = 0; j < m; ++j) a[j] *= b[j];
    cfft_pow2(a, true);
    for (std::size_t k = 0; k <= n / 2; ++k) out[k] = a[k] * w[k];
    return out;
}

void fft_spectrum(const GearSeriesData& s, const std::vector<double>& y,
                  std::vector<double>& freqs_out, std::vector<double>& amp_out) {
    freqs_out.clear();
    amp_out.clear();
    if (s.k.empty()) return;
    const std::int64_t k0 = s.k.front();
    const std::int64_t k1 = s.k.back();
    const std::size_t n = static_cast<std::size_t>(k1 - k0 + 1);
    std::vector<double> yu(n, 0.0);
    std::size_t idx = 0;
    for (std::size_t g = 0; g < n; ++g) {
        const std::int64_t kg = k0 + static_cast<std::int64_t>(g);
        while (idx + 1 < s.k.size() && s.k[idx + 1] <= kg) ++idx;
        if (s.k[idx] == kg || idx + 1 >= s.k.size()) {
            yu[g] = y[idx];
        } else {
            const double frac = static_cast<double>(kg - s.k[idx]) /
                                static_cast<double>(s.k[idx + 1] - s.k[idx]);
            yu[g] = y[idx] + frac * (y[idx + 1] - y[idx]);
        }
    }
    const double mean = neumaier_sum(yu.data(), n) / static_cast<double>(n);
    const std::vector<double> win = dsp::hanning(n);
    double wsum = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        yu[i] = (yu[i] - mean) * win[i];
        wsum += win[i];
    }
    const auto spec = rfft_any(yu);
    const double dt = 3600.0 / s.bph;
    const std::size_t nf = spec.size();
    freqs_out.resize(nf);
    amp_out.resize(nf);
    const double inv_ndt = 1.0 / (static_cast<double>(n) * dt);
    const double scale = (wsum > 0.0) ? (2.0 / wsum) : 0.0;
    for (std::size_t i = 0; i < nf; ++i) {
        freqs_out[i] = static_cast<double>(i) * inv_ndt;
        amp_out[i] = std::abs(spec[i]) * scale;
    }
}

double compute_fap(double z, std::size_t n, double m) {
    if (!std::isfinite(z) || n <= 3U) return 1.0;
    const double base = std::max(0.0, 1.0 - 2.0 * z / static_cast<double>(n));
    const double single = std::pow(base, static_cast<double>(n - 3U) / 2.0);
    return -std::expm1(std::max(1.0, m) * std::log1p(-std::min(single, 1.0 - 1e-16)));
}

bool is_resolvable(const GearSeriesData& s, double period_s) {
    return s.duration_s >= kMinCycles * period_s;
}

advtg_gear_component eval_component(int32_t name, int32_t series_kind, double period_s,
                                    const GearSeriesData& s, const std::vector<double>& y,
                                    const GearSpectrumData& spec) {
    advtg_gear_component c{};
    c.name = name;
    c.series = series_kind;
    c.expected_period_s = period_s;
    c.period_s = kNaN;
    c.amplitude = kNaN;
    c.rate_ripple_s_per_day = kNaN;
    c.fap = 1.0;
    c.resolvable = is_resolvable(s, period_s) ? 1 : 0;
    c.significant = 0;

    const double span = s.t_s.back() - s.t_s.front();
    const double f0 = 1.0 / period_s;
    const double df = 1.0 / span;
    std::size_t best_i = spec.freqs_hz.size();
    double best_p = -1.0;
    for (std::size_t i = 0; i < spec.freqs_hz.size(); ++i) {
        if (std::fabs(spec.freqs_hz[i] - f0) <= df) {
            if (best_i == spec.freqs_hz.size() || spec.ls_power[i] > best_p) {
                best_i = i;
                best_p = spec.ls_power[i];
            }
        }
    }
    if (!c.resolvable || best_i == spec.freqs_hz.size()) return c;
    const double f = spec.freqs_hz[best_i];
    double amp = 0.0, phase = 0.0;
    sine_fit_cpp(s.t_s.data(), y.data(), s.t_s.size(), f, amp, phase);
    (void)phase;
    const double m = 2.0 * df * span;
    const double fap = compute_fap(spec.ls_power[best_i], y.size(), m);
    c.period_s = 1.0 / f;
    c.amplitude = amp;
    c.rate_ripple_s_per_day =
        (series_kind == ADVTG_GEAR_SERIES_TIMING) ? (kTwoPi * amp * f * 86400.0) : kNaN;
    c.fap = fap;
    c.significant = (c.resolvable && fap < kFapSignificant) ? 1 : 0;
    return c;
}

}  // namespace

void teager_kaiser_cpp(const double* x, std::size_t n, double* out) {
    if (n == 0) return;
    if (n < 3) {
        for (std::size_t i = 0; i < n; ++i) out[i] = 0.0;
        return;
    }
    for (std::size_t i = 1; i + 1 < n; ++i) {
        out[i] = x[i] * x[i] - x[i - 1] * x[i + 1];
    }
    out[0] = out[1];
    out[n - 1] = out[n - 2];
}

void lomb_scargle_cpp(const double* t, const double* y, std::size_t n_t, const double* freqs,
                      std::size_t n_f, double* out) {
    for (std::size_t i = 0; i < n_f; ++i) out[i] = 0.0;
    if (n_f == 0 || n_t < 3) return;

    AlignedBuffer<double> yc(n_t);
    const double mean_y = neumaier_sum(y, n_t) / static_cast<double>(n_t);
    for (std::size_t j = 0; j < n_t; ++j) yc[j] = y[j] - mean_y;
    const double var = kernels().dot(yc.data(), yc.data(), n_t) / static_cast<double>(n_t);
    if (!(var > 0.0)) return;

    bool uniform = false;
    double f0 = freqs[0];
    double df = 0.0;
    if (n_f >= 8) {
        df = (freqs[n_f - 1] - f0) / static_cast<double>(n_f - 1);
        if (df > 0.0) {
            uniform = true;
            const double tol = 1e-10 * df;
            for (std::size_t i = 1; i + 1 < n_f; ++i) {
                if (std::fabs(freqs[i] - (f0 + static_cast<double>(i) * df)) > tol) {
                    uniform = false;
                    break;
                }
            }
        }
    }

    AlignedBuffer<double> cos_d, sin_d;
    if (uniform) {
        cos_d.resize(n_t);
        sin_d.resize(n_t);
        const double dw = kTwoPi * df;
        for (std::size_t j = 0; j < n_t; ++j) {
            const double d = dw * t[j];
            cos_d[j] = std::cos(d);
            sin_d[j] = std::sin(d);
        }
    }

    const unsigned hw = std::max(1U, std::min(8U, std::thread::hardware_concurrency()));
    const unsigned n_threads =
        (n_f * n_t >= 120000U && n_f >= hw * 8U) ? hw : 1U;

    auto worker = [&](std::size_t fa, std::size_t fb) {
        AlignedBuffer<double> c0(n_t), s0(n_t);
        const double* ycp = yc.data();
        const double inv_var = 1.0 / var;

        for (std::size_t fi = fa; fi < fb; ++fi) {
            if (!uniform || ((fi - fa) % kReanchorStep) == 0) {
                const double w = kTwoPi * (uniform ? (f0 + static_cast<double>(fi) * df) : freqs[fi]);
                for (std::size_t j = 0; j < n_t; ++j) {
                    const double wt = w * t[j];
                    c0[j] = std::cos(wt);
                    s0[j] = std::sin(wt);
                }
            } else {
                const double* cd = cos_d.data();
                const double* sd = sin_d.data();
                double* cp = c0.data();
                double* sp = s0.data();
                for (std::size_t j = 0; j < n_t; ++j) {
                    const double c_old = cp[j];
                    const double s_old = sp[j];
                    cp[j] = c_old * cd[j] - s_old * sd[j];
                    sp[j] = s_old * cd[j] + c_old * sd[j];
                }
            }

            double s2 = 0.0, c2_sum = 0.0, cy = 0.0, sy = 0.0;
            const double* cp = c0.data();
            const double* sp = s0.data();
            for (std::size_t j = 0; j < n_t; ++j) {
                const double cj = cp[j];
                const double sj = sp[j];
                const double yj = ycp[j];
                s2 += sj * cj;
                c2_sum += cj * cj;
                cy += cj * yj;
                sy += sj * yj;
            }
            s2 *= 2.0;
            const double c2 = 2.0 * c2_sum - static_cast<double>(n_t);
            const double wt_tau = 0.5 * std::atan2(s2, c2);
            const double ct = std::cos(wt_tau);
            const double st = std::sin(wt_tau);
            const double yc_rot = cy * ct + sy * st;
            const double ys_rot = sy * ct - cy * st;
            const double r = std::hypot(c2, s2);
            const double cc = std::max(0.5 * (static_cast<double>(n_t) + r), 1e-300);
            const double ss = std::max(0.5 * (static_cast<double>(n_t) - r), 1e-300);
            out[fi] = 0.5 * (yc_rot * yc_rot / cc + ys_rot * ys_rot / ss) * inv_var;
        }
    };

    if (n_threads <= 1) {
        worker(0, n_f);
        return;
    }
    std::vector<std::thread> pool;
    pool.reserve(n_threads - 1);
    const std::size_t chunk = (n_f + n_threads - 1) / n_threads;
    for (unsigned tid = 1; tid < n_threads; ++tid) {
        const std::size_t a = std::min(n_f, static_cast<std::size_t>(tid) * chunk);
        const std::size_t b = std::min(n_f, a + chunk);
        if (a < b) pool.emplace_back(worker, a, b);
    }
    worker(0, std::min(n_f, chunk));
    for (auto& th : pool) th.join();
}

void sine_fit_cpp(const double* t, const double* y, std::size_t n, double f, double& amp_out,
                  double& phase_out) {
    if (n < 3U) {
        amp_out = 0.0;
        phase_out = 0.0;
        return;
    }
    const double w = kTwoPi * f;
    std::vector<double> M(n * 3U), rhs(y, y + n);
    for (std::size_t i = 0; i < n; ++i) {
        M[i * 3U + 0] = std::cos(w * t[i]);
        M[i * 3U + 1] = std::sin(w * t[i]);
        M[i * 3U + 2] = 1.0;
    }
    double coef[3] = {};
    if (!solve_ls(M, rhs, n, 3U, coef)) {
        amp_out = 0.0;
        phase_out = 0.0;
        return;
    }
    amp_out = std::hypot(coef[0], coef[1]);
    phase_out = std::atan2(-coef[1], coef[0]);
}

namespace {

bool extract_series_cpp(const double* x, std::size_t n, double fs, double bph, double lift_angle_deg,
                        GearSeriesData& out, int64_t& beats_found_out) {
    beats_found_out = 0;
    core::NativeDetectorConfig dc{};
    dc.sample_rate = fs;
    dc.bph = bph;
    dc.lift_angle_deg = lift_angle_deg;
    dc.band_lo_hz = 1000.0;
    dc.band_hi_hz = std::min(15000.0, 0.45 * fs);
    auto det_sos = dsp::butter_bandpass_sos(dc.filter_order, dc.band_lo_hz, dc.band_hi_hz, fs);
    const int n_sec = static_cast<int>(det_sos.size() / 6);
    auto det_zi = dsp::sosfilt_zi(det_sos.data(), n_sec);
    dc.sos = det_sos.data();
    dc.zi = det_zi.data();
    dc.n_sec = n_sec;
    dc.padlen = dsp::sosfiltfilt_padlen(det_sos.data(), n_sec);

    core::BeatDetector det(dc);
    std::vector<core::NativeBeat> beats;
    det.process(x, n, 0, beats);
    det.flush(beats);
    const std::int64_t missed = det.stats().missed_beats;

    std::vector<core::NativeBeat> good;
    good.reserve(beats.size());
    for (const auto& b : beats) {
        if (std::isfinite(b.t_fine)) good.push_back(b);
    }
    beats_found_out = static_cast<int64_t>(good.size());
    if (good.size() < kMinBeats) {
        g_gear_error = "solo " + std::to_string(good.size()) +
                       " batimientos con tiempo fino (mínimo " + std::to_string(kMinBeats) + ")";
        return false;
    }

    const std::size_t ng = good.size();
    std::vector<std::int64_t> kg(ng);
    std::vector<double> tg(ng);
    for (std::size_t i = 0; i < ng; ++i) {
        kg[i] = good[i].k;
        tg[i] = good[i].t_fine / fs;
    }

    std::vector<std::uint8_t> keep(ng, 1);
    std::vector<std::int64_t> kk;
    std::vector<double> tk, res, dev;
    for (int iter = 0; iter < 3; ++iter) {
        kk.clear();
        tk.clear();
        for (std::size_t i = 0; i < ng; ++i) {
            if (keep[i]) {
                kk.push_back(kg[i]);
                tk.push_back(tg[i]);
            }
        }
        detrend_poly(kk, tk, true, res);
        std::vector<double> tmp = res;
        const double rmed = exact_median(tmp.data(), tmp.size());
        dev.resize(res.size());
        for (std::size_t i = 0; i < res.size(); ++i) dev[i] = std::fabs(res[i] - rmed);
        tmp = dev;
        const double sig = std::max(kMadToSigma * exact_median(tmp.data(), tmp.size()), 1e-9);
        bool all_ok = true;
        std::size_t ki = 0;
        for (std::size_t i = 0; i < ng; ++i) {
            if (!keep[i]) continue;
            if (dev[ki] > kOutlierSigma * sig) {
                keep[i] = 0;
                all_ok = false;
            }
            ++ki;
        }
        if (all_ok) break;
    }

    std::vector<core::NativeBeat> kept;
    out.k.clear();
    out.t_s.clear();
    out.outlier_t_s.clear();
    for (std::size_t i = 0; i < ng; ++i) {
        if (keep[i]) {
            kept.push_back(good[i]);
            out.k.push_back(kg[i]);
            out.t_s.push_back(tg[i]);
        } else {
            out.outlier_t_s.push_back(tg[i]);
        }
    }
    detrend_poly(out.k, out.t_s, true, out.timing_residual_s);

    // Linear fit slope of t vs k -> rate_s_per_day
    const std::size_t nk = out.k.size();
    double km = 0.0, tm = 0.0;
    for (std::size_t i = 0; i < nk; ++i) {
        km += static_cast<double>(out.k[i]);
        tm += out.t_s[i];
    }
    km /= static_cast<double>(nk);
    tm /= static_cast<double>(nk);
    double sxx = 0.0, sxy = 0.0;
    for (std::size_t i = 0; i < nk; ++i) {
        const double dk = static_cast<double>(out.k[i]) - km;
        sxx += dk * dk;
        sxy += dk * (out.t_s[i] - tm);
    }
    const double slope = sxx > 0.0 ? (sxy / sxx) : (3600.0 / bph);
    const double rate = ((3600.0 / bph) / slope - 1.0) * 86400.0;

    // P3 energy via band-pass + Teager-Kaiser + O(1) prefix sums
    auto bp_sos = dsp::butter_bandpass_sos(4, 1000.0, std::min(15000.0, 0.45 * fs), fs);
    const int bp_sec = static_cast<int>(bp_sos.size() / 6);
    auto bp_zi = dsp::sosfilt_zi(bp_sos.data(), bp_sec);
    const std::int64_t bp_pad = dsp::sosfiltfilt_padlen(bp_sos.data(), bp_sec);
    std::vector<double> filtered(n);
    dsp::sosfiltfilt(bp_sos.data(), bp_sec, bp_zi.data(), bp_pad, x, n, filtered.data());
    std::vector<double> psi(n);
    teager_kaiser_cpp(filtered.data(), n, psi.data());
    std::vector<double> psi_cs(n + 1, 0.0);
    for (std::size_t i = 0; i < n; ++i) psi_cs[i + 1] = psi_cs[i] + psi[i];

    const std::int64_t pre = static_cast<std::int64_t>(std::llrint(kEnergyPreMs * 1e-3 * fs));
    const std::int64_t post = static_cast<std::int64_t>(std::llrint(kEnergyPostMs * 1e-3 * fs));
    std::vector<double> energy(nk), p0_e, p1_e;
    for (std::size_t i = 0; i < nk; ++i) {
        const std::int64_t idx = static_cast<std::int64_t>(std::llrint(out.t_s[i] * fs));
        const std::int64_t lo = std::clamp<std::int64_t>(idx - pre, 0, static_cast<std::int64_t>(n));
        const std::int64_t hi = std::clamp<std::int64_t>(idx + post, 0, static_cast<std::int64_t>(n));
        energy[i] = psi_cs[static_cast<std::size_t>(hi)] - psi_cs[static_cast<std::size_t>(lo)];
        if ((out.k[i] & 1) == 0) p0_e.push_back(energy[i]);
        else p1_e.push_back(energy[i]);
    }
    const double med0 = p0_e.empty() ? 0.0 : exact_median(p0_e.data(), p0_e.size());
    const double med1 = p1_e.empty() ? 0.0 : exact_median(p1_e.data(), p1_e.size());
    std::vector<double> rel(nk);
    for (std::size_t i = 0; i < nk; ++i) {
        const double med = ((out.k[i] & 1) == 0) ? med0 : med1;
        rel[i] = (med > 0.0) ? (energy[i] / med - 1.0) : 0.0;
    }
    detrend_poly(out.k, rel, false, out.energy_rel);

    // Sub-events
    std::vector<double> abs_psi(n);
    for (std::size_t i = 0; i < n; ++i) abs_psi[i] = std::fabs(psi[i]);
    double noise = exact_median(abs_psi.data(), n);
    if (!(noise > 0.0)) noise = 1e-30;
    const std::int64_t pre_a = static_cast<std::int64_t>(std::llrint(kPreP1FromMs * 1e-3 * fs));
    const std::int64_t pre_b = static_cast<std::int64_t>(std::llrint(kPreP1ToMs * 1e-3 * fs));

    out.p1_s.assign(nk, kNaN);
    out.p2_s.assign(nk, kNaN);
    out.p3_s.assign(nk, kNaN);
    out.e1.assign(nk, kNaN);
    out.e2.assign(nk, kNaN);
    out.e3.assign(nk, kNaN);
    out.pre_p1_db.assign(nk, kNaN);
    out.amplitude_deg.assign(nk, kNaN);

    for (std::size_t i = 0; i < nk; ++i) {
        const double a = kept[i].p1, b = kept[i].p2, c = kept[i].p3;
        out.p1_s[i] = a / fs;
        out.p2_s[i] = b / fs;
        out.p3_s[i] = c / fs;
        if (std::isfinite(a)) {
            const std::int64_t i1 = static_cast<std::int64_t>(std::llrint(a));
            const std::int64_t s0 = std::max<std::int64_t>(0, i1 - pre_a);
            const std::int64_t s1 = std::max<std::int64_t>(0, i1 - pre_b);
            if (s1 > s0 && s0 < static_cast<std::int64_t>(n)) {
                const std::int64_t s1c = std::min<std::int64_t>(s1, static_cast<std::int64_t>(n));
                double seg_max = -kInf;
                for (std::int64_t j = s0; j < s1c; ++j) seg_max = std::max(seg_max, psi[static_cast<std::size_t>(j)]);
                out.pre_p1_db[i] = 10.0 * std::log10(std::max(seg_max, 1e-30) / noise);
            }
            if (std::isfinite(c)) {
                out.amplitude_deg[i] = amplitude_from_lift_time((c - a) / fs, lift_angle_deg, bph);
            }
        }
        if (std::isfinite(a) && std::isfinite(b) && std::isfinite(c) && a < b && b < c) {
            const std::size_t i1 = static_cast<std::size_t>(std::clamp<std::int64_t>(std::llrint(a), 0, static_cast<std::int64_t>(n)));
            const std::size_t i2 = static_cast<std::size_t>(std::clamp<std::int64_t>(std::llrint(b), 0, static_cast<std::int64_t>(n)));
            const std::size_t i3 = static_cast<std::size_t>(std::clamp<std::int64_t>(std::llrint(c), 0, static_cast<std::int64_t>(n)));
            const std::size_t i4 = std::min(n, i3 + static_cast<std::size_t>(post));
            out.e1[i] = psi_cs[i2] - psi_cs[i1];
            out.e2[i] = psi_cs[i3] - psi_cs[i2];
            out.e3[i] = psi_cs[i4] - psi_cs[i3];
        }
    }

    out.sample_rate = fs;
    out.bph = bph;
    out.duration_s = static_cast<double>(n) / fs;
    out.beats_detected = static_cast<std::int64_t>(beats.size());
    out.missing_beats = missed;
    out.outliers = static_cast<std::int64_t>(ng - nk);
    out.rate_s_per_day = rate;
    out.lift_angle_deg = lift_angle_deg;
    return true;
}

void compute_spectral_report(GearReportData& r, int32_t escape_teeth) {
    const GearSeriesData& s = r.series;
    r.escape_teeth = escape_teeth;
    r.escape_period_s = advtg_escape_wheel_period_s(s.bph, escape_teeth);

    const double span = s.t_s.back() - s.t_s.front();
    const double f_hi = 1.0 / (kMinPeriodBeats * (3600.0 / s.bph));
    const double df = 1.0 / (kOversample * span);
    std::vector<double> freqs;
    for (double f = 1.0 / span; f < f_hi + 0.5 * df; f += df) freqs.push_back(f);

    r.timing.freqs_hz = freqs;
    r.timing.ls_power.resize(freqs.size());
    lomb_scargle_cpp(s.t_s.data(), s.timing_residual_s.data(), s.t_s.size(), freqs.data(),
                     freqs.size(), r.timing.ls_power.data());
    fft_spectrum(s, s.timing_residual_s, r.timing.fft_freqs_hz, r.timing.fft_amplitude);

    r.energy.freqs_hz = freqs;
    r.energy.ls_power.resize(freqs.size());
    lomb_scargle_cpp(s.t_s.data(), s.energy_rel.data(), s.t_s.size(), freqs.data(), freqs.size(),
                     r.energy.ls_power.data());
    fft_spectrum(s, s.energy_rel, r.energy.fft_freqs_hz, r.energy.fft_amplitude);

    r.components[0] = eval_component(ADVTG_GEAR_COMP_ESCAPE, ADVTG_GEAR_SERIES_TIMING,
                                     r.escape_period_s, s, s.timing_residual_s, r.timing);
    r.components[1] = eval_component(ADVTG_GEAR_COMP_ESCAPE, ADVTG_GEAR_SERIES_ENERGY,
                                     r.escape_period_s, s, s.energy_rel, r.energy);
    r.components[2] = eval_component(ADVTG_GEAR_COMP_FOURTH, ADVTG_GEAR_SERIES_TIMING,
                                     kFourthWheelPeriodS, s, s.timing_residual_s, r.timing);
    r.components[3] = eval_component(ADVTG_GEAR_COMP_FOURTH, ADVTG_GEAR_SERIES_ENERGY,
                                     kFourthWheelPeriodS, s, s.energy_rel, r.energy);

    r.warning_mask = 0;
    if (!is_resolvable(s, r.escape_period_s)) r.warning_mask |= ADVTG_GEAR_WARN_SHORT_ESCAPE;
    if (!is_resolvable(s, kFourthWheelPeriodS)) r.warning_mask |= ADVTG_GEAR_WARN_SHORT_FOURTH;
    if (static_cast<double>(s.missing_beats) >
        0.1 * static_cast<double>(s.beats_detected + s.missing_beats)) {
        r.warning_mask |= ADVTG_GEAR_WARN_MANY_MISSING;
    }
}

}  // namespace

}  // namespace advtg::analysis

struct advtg_gear_analysis {
    advtg::analysis::GearReportData data;
};

extern "C" {

ADVTG_C_API double advtg_escape_wheel_period_s(double bph, int32_t teeth) {
    if (!(bph > 0.0) || teeth < 1) return advtg::kNaN;
    return 2.0 * static_cast<double>(teeth) * 3600.0 / bph;
}

ADVTG_C_API const char* advtg_gear_last_error(void) {
    return advtg::analysis::g_gear_error.c_str();
}

ADVTG_C_API advtg_gear_analysis* advtg_gear_extract(const double* samples, size_t n,
                                                    double sample_rate, double bph,
                                                    double lift_angle_deg, int32_t escape_teeth,
                                                    int32_t* err_out, int64_t* beats_found_out) {
    if (err_out) *err_out = ADVTG_OK;
    if (beats_found_out) *beats_found_out = 0;
    if (!samples || n == 0 || !(sample_rate > 0.0) || !(bph > 0.0) || escape_teeth < 1) {
        advtg::analysis::g_gear_error = "parámetros inválidos";
        if (err_out) *err_out = ADVTG_ERR_INVALID;
        return nullptr;
    }
    const double la =
        (std::isfinite(lift_angle_deg) && lift_angle_deg > 0.0) ? lift_angle_deg : 52.0;
    try {
        auto h = std::make_unique<advtg_gear_analysis>();
        int64_t found = 0;
        if (!advtg::analysis::extract_series_cpp(samples, n, sample_rate, bph, la,
                                                 h->data.series, found)) {
            if (beats_found_out) *beats_found_out = found;
            if (err_out) *err_out = ADVTG_ERR_NOT_ENOUGH_BEATS;
            return nullptr;
        }
        if (beats_found_out) *beats_found_out = found;
        advtg::analysis::compute_spectral_report(h->data, escape_teeth);
        return h.release();
    } catch (const std::exception& e) {
        advtg::analysis::g_gear_error = e.what();
        if (err_out) *err_out = ADVTG_ERR_INVALID;
        return nullptr;
    }
}

ADVTG_C_API int advtg_gear_recompute(advtg_gear_analysis* h, int32_t escape_teeth) {
    if (!h || escape_teeth < 1) return ADVTG_ERR_INVALID;
    try {
        advtg::analysis::compute_spectral_report(h->data, escape_teeth);
        return ADVTG_OK;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API void advtg_gear_destroy(advtg_gear_analysis* h) { delete h; }

ADVTG_C_API int advtg_gear_get_summary(const advtg_gear_analysis* h, advtg_gear_summary* out) {
    if (!h || !out) return ADVTG_ERR_INVALID;
    const auto& s = h->data.series;
    out->sample_rate = s.sample_rate;
    out->bph = s.bph;
    out->duration_s = s.duration_s;
    out->rate_s_per_day = s.rate_s_per_day;
    out->lift_angle_deg = s.lift_angle_deg;
    out->escape_period_s = h->data.escape_period_s;
    out->beats_detected = s.beats_detected;
    out->missing_beats = s.missing_beats;
    out->outliers = s.outliers;
    out->n_series = static_cast<int64_t>(s.k.size());
    out->escape_teeth = h->data.escape_teeth;
    out->warning_mask = h->data.warning_mask;
    return ADVTG_OK;
}

ADVTG_C_API int advtg_gear_get_components(const advtg_gear_analysis* h,
                                          advtg_gear_component out[4]) {
    if (!h || !out) return ADVTG_ERR_INVALID;
    std::memcpy(out, h->data.components, 4 * sizeof(advtg_gear_component));
    return ADVTG_OK;
}

ADVTG_C_API int advtg_gear_series_copy(const advtg_gear_analysis* h, int64_t* k, double* t_s,
                                       double* timing_residual_s, double* energy_rel,
                                       double* p1_s, double* p2_s, double* p3_s, double* e1,
                                       double* e2, double* e3, double* pre_p1_db,
                                       double* amplitude_deg, int64_t cap) {
    if (!h) return ADVTG_ERR_INVALID;
    const auto& s = h->data.series;
    const auto m = static_cast<std::size_t>(std::min<int64_t>(static_cast<int64_t>(s.k.size()), std::max<int64_t>(0, cap)));
    auto cp = [&](double* dst, const std::vector<double>& src) {
        if (dst && m) std::memcpy(dst, src.data(), m * sizeof(double));
    };
    if (k && m) std::memcpy(k, s.k.data(), m * sizeof(int64_t));
    cp(t_s, s.t_s);
    cp(timing_residual_s, s.timing_residual_s);
    cp(energy_rel, s.energy_rel);
    cp(p1_s, s.p1_s);
    cp(p2_s, s.p2_s);
    cp(p3_s, s.p3_s);
    cp(e1, s.e1);
    cp(e2, s.e2);
    cp(e3, s.e3);
    cp(pre_p1_db, s.pre_p1_db);
    cp(amplitude_deg, s.amplitude_deg);
    return ADVTG_OK;
}

ADVTG_C_API int64_t advtg_gear_outliers_copy(const advtg_gear_analysis* h, double* outlier_t_s,
                                             int64_t cap) {
    if (!h) return ADVTG_ERR_INVALID;
    const auto& v = h->data.series.outlier_t_s;
    const auto n = static_cast<int64_t>(v.size());
    if (outlier_t_s && cap > 0) {
        const auto m = static_cast<std::size_t>(std::min(n, cap));
        if (m) std::memcpy(outlier_t_s, v.data(), m * sizeof(double));
    }
    return n;
}

ADVTG_C_API void advtg_gear_spectra_len(const advtg_gear_analysis* h, int64_t* ls_len_out,
                                        int64_t* fft_len_out) {
    if (!h) return;
    if (ls_len_out) *ls_len_out = static_cast<int64_t>(h->data.timing.freqs_hz.size());
    if (fft_len_out) *fft_len_out = static_cast<int64_t>(h->data.timing.fft_freqs_hz.size());
}

ADVTG_C_API int advtg_gear_spectra_copy(const advtg_gear_analysis* h, int32_t series_kind,
                                        double* freqs_hz, double* ls_power, int64_t ls_cap,
                                        double* fft_freqs_hz, double* fft_amp, int64_t fft_cap) {
    if (!h) return ADVTG_ERR_INVALID;
    const auto& sp = (series_kind == ADVTG_GEAR_SERIES_ENERGY) ? h->data.energy : h->data.timing;
    const auto n_ls = static_cast<std::size_t>(std::min<int64_t>(static_cast<int64_t>(sp.freqs_hz.size()), std::max<int64_t>(0, ls_cap)));
    const auto n_ft = static_cast<std::size_t>(std::min<int64_t>(static_cast<int64_t>(sp.fft_freqs_hz.size()), std::max<int64_t>(0, fft_cap)));
    if (freqs_hz && n_ls) std::memcpy(freqs_hz, sp.freqs_hz.data(), n_ls * sizeof(double));
    if (ls_power && n_ls) std::memcpy(ls_power, sp.ls_power.data(), n_ls * sizeof(double));
    if (fft_freqs_hz && n_ft) std::memcpy(fft_freqs_hz, sp.fft_freqs_hz.data(), n_ft * sizeof(double));
    if (fft_amp && n_ft) std::memcpy(fft_amp, sp.fft_amplitude.data(), n_ft * sizeof(double));
    return ADVTG_OK;
}

}  // extern "C"
