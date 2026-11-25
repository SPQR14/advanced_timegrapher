// Multi-threaded SIMD Teager-Kaiser and Lomb-Scargle periodogram (analysis/gear_train.py).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <thread>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_metrics.hpp"
#include "advtg_simd.hpp"

namespace advtg::analysis {
namespace {

inline constexpr double kTwoPi = 6.283185307179586476925286766559005768;
inline constexpr std::size_t kReanchorStep = 64;

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

}  // namespace advtg::analysis
