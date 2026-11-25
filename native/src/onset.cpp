// Onset helpers (counterpart of src/adv_tg/dsp/onset.py): windowed-sinc Blackman low-pass
// FIR, robust median/MAD level and last-quiet-stretch onset crossing.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_dsp.hpp"

namespace advtg::dsp {

namespace {
inline constexpr long double kPiL = 3.141592653589793238462643383279502884L;
}

std::vector<double> lowpass_fir(double fs, double fc, double periods) {
    if (fc >= 0.45 * fs) return {1.0};
    std::size_t n = static_cast<std::size_t>(periods * fs / fc) | 1U;
    std::vector<double> h(n);
    auto w = blackman(n);
    const std::int64_t half = static_cast<std::int64_t>(n / 2);
    const double coeff = 2.0 * fc / fs;
    double sum = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        const std::int64_t m = static_cast<std::int64_t>(i) - half;
        double sinc_v;
        if (m == 0) {
            sinc_v = 1.0;
        } else {
            const long double arg = kPiL * static_cast<long double>(coeff) * static_cast<long double>(m);
            sinc_v = static_cast<double>(std::sin(arg) / arg);
        }
        h[i] = sinc_v * w[i];
        sum += h[i];
    }
    const double inv = 1.0 / sum;
    for (double& v : h) v *= inv;
    return h;
}

void robust_level(const double* x, std::size_t n, double& mu, double& sigma) {
    if (n == 0) {
        mu = kNaN;
        sigma = kTiny;
        return;
    }
    thread_local AlignedBuffer<double> scratch;
    scratch.assign(x, n);
    mu = exact_median(scratch.data(), n);
    scratch.assign(x, n);
    const double mad = exact_median_abs_dev(scratch.data(), n, mu);
    sigma = std::max(kMadToSigma * mad, kTiny);
}

double onset_after_quiet(const double* r, std::size_t n, double threshold, std::int64_t quiet) {
    if (n == 0) return kNaN;
    // Walk backwards to find the last crossing preceded by >= quiet samples below threshold.
    // Equivalent to: act = flatnonzero(r >= threshold); find last gap >= quiet, else act[0] >= quiet.
    std::int64_t prev_act = -1;
    std::int64_t first_act = -1;
    std::int64_t j = -1;
    for (std::size_t i = 0; i < n; ++i) {
        if (r[i] >= threshold) {
            const std::int64_t idx = static_cast<std::int64_t>(i);
            if (first_act < 0) first_act = idx;
            if (prev_act >= 0 && (idx - prev_act - 1) >= quiet) j = idx;
            prev_act = idx;
        }
    }
    if (first_act < 0) return kNaN;
    if (j < 0) {
        if (first_act >= quiet) {
            j = first_act;
        } else {
            return kNaN;
        }
    }
    const double denom = std::max(r[j] - r[j - 1], kTiny);
    double frac = (threshold - r[j - 1]) / denom;
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;
    return static_cast<double>(j - 1) + frac;
}

}  // namespace advtg::dsp
