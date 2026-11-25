// Power-of-two real/complex FFT with thread-local cached twiddles and bit-reversal tables.
// Complex FFT uses iterative Cooley-Tukey radix-2; rfft/irfft pack N real samples into N/2
// complex points via conjugate symmetry (2x faster than a full N-point complex FFT).
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#include "advtg_dsp.hpp"

namespace advtg::dsp {
namespace {

inline constexpr long double kPiL = 3.141592653589793238462643383279502884L;

struct Plan {
    std::size_t n = 0;
    int stages = 0;
    std::vector<std::uint32_t> bitrev;
    // Half-size twiddle table: W_n^k = cos(2*pi*k/n) - i*sin(2*pi*k/n) for k in [0, n/2).
    // Stage s (half = 2^s) steps through twiddles with stride n / (2 * half).
    std::vector<double> tw_re;
    std::vector<double> tw_im;
};

const Plan& get_plan(std::size_t n) {
    thread_local std::unordered_map<std::size_t, Plan> cache;
    auto it = cache.find(n);
    if (it != cache.end()) return it->second;
    if (!is_pow2(n)) throw std::invalid_argument("fft size must be a power of two");
    Plan p;
    p.n = n;
    p.stages = ilog2(n);
    p.bitrev.resize(n);
    for (std::size_t i = 0; i < n; ++i) {
        std::size_t r = 0;
        std::size_t v = i;
        for (int b = 0; b < p.stages; ++b) {
            r = (r << 1) | (v & 1U);
            v >>= 1;
        }
        p.bitrev[i] = static_cast<std::uint32_t>(r);
    }
    const std::size_t half = n >> 1;
    p.tw_re.resize(half);
    p.tw_im.resize(half);
    for (std::size_t k = 0; k < half; ++k) {
        const long double ang = (2.0L * kPiL * static_cast<long double>(k)) / static_cast<long double>(n);
        p.tw_re[k] = static_cast<double>(std::cos(ang));
        p.tw_im[k] = -static_cast<double>(std::sin(ang));
    }
    auto [ins, _] = cache.emplace(n, std::move(p));
    return ins->second;
}

// Forward complex FFT in-place on `re[0..n), im[0..n)` (n power of 2).
void cfft_forward(double* re, double* im, std::size_t n) {
    if (n <= 1) return;
    const Plan& p = get_plan(n);
    for (std::size_t i = 0; i < n; ++i) {
        const std::size_t j = p.bitrev[i];
        if (i < j) {
            std::swap(re[i], re[j]);
            std::swap(im[i], im[j]);
        }
    }
    const double* twr = p.tw_re.data();
    const double* twi = p.tw_im.data();
    for (std::size_t half = 1; half < n; half <<= 1) {
        const std::size_t step = half << 1;
        const std::size_t stride = n / step;
        for (std::size_t k = 0; k < n; k += step) {
            // j == 0: twiddle is (1, 0)
            {
                const std::size_t i0 = k;
                const std::size_t i1 = k + half;
                const double xr = re[i1], xi = im[i1];
                re[i1] = re[i0] - xr;
                im[i1] = im[i0] - xi;
                re[i0] += xr;
                im[i0] += xi;
            }
            for (std::size_t j = 1; j < half; ++j) {
                const std::size_t i0 = k + j;
                const std::size_t i1 = i0 + half;
                const std::size_t tw = j * stride;
                const double wr = twr[tw], wi = twi[tw];
                const double ur = re[i1], ui = im[i1];
                const double vr = ur * wr - ui * wi;
                const double vi = ur * wi + ui * wr;
                const double ar = re[i0], ai = im[i0];
                re[i1] = ar - vr;
                im[i1] = ai - vi;
                re[i0] = ar + vr;
                im[i0] = ai + vi;
            }
        }
    }
}

struct RfftWorkspace {
    AlignedBuffer<double> re, im;
    std::vector<double> w_re, w_im;
    std::size_t cached_nfft = 0;
    void prepare(std::size_t nfft) {
        const std::size_t m = nfft >> 1;
        re.resize(m);
        im.resize(m);
        if (cached_nfft != nfft) {
            const std::size_t q = (m >> 1) + 1;
            w_re.resize(q);
            w_im.resize(q);
            for (std::size_t k = 0; k < q; ++k) {
                const long double ang =
                    (2.0L * kPiL * static_cast<long double>(k)) / static_cast<long double>(nfft);
                w_re[k] = static_cast<double>(std::cos(ang));
                w_im[k] = -static_cast<double>(std::sin(ang));
            }
            cached_nfft = nfft;
        }
    }
};

}  // namespace

void rfft(const double* x, std::size_t n_in, std::size_t nfft, std::complex<double>* out) {
    if (!is_pow2(nfft) || nfft == 0)
        throw std::invalid_argument("rfft: nfft must be a power of two");
    if (nfft == 1) {
        out[0] = std::complex<double>(n_in > 0 ? x[0] : 0.0, 0.0);
        return;
    }
    const std::size_t m = nfft >> 1;
    thread_local RfftWorkspace ws;
    ws.prepare(nfft);
    double* re = ws.re.data();
    double* im = ws.im.data();

    const std::size_t use = std::min(n_in, nfft);
    const std::size_t pairs = use >> 1;
    for (std::size_t k = 0; k < pairs; ++k) {
        re[k] = x[2 * k];
        im[k] = x[2 * k + 1];
    }
    std::size_t k0 = pairs;
    if (use & 1U) {
        re[k0] = x[2 * k0];
        im[k0] = 0.0;
        ++k0;
    }
    for (std::size_t k = k0; k < m; ++k) {
        re[k] = 0.0;
        im[k] = 0.0;
    }

    cfft_forward(re, im, m);

    out[0] = std::complex<double>(re[0] + im[0], 0.0);
    out[m] = std::complex<double>(re[0] - im[0], 0.0);
    if (m >= 2) {
        const std::size_t q = m >> 1;
        out[q] = std::complex<double>(re[q], -im[q]);
        const double* wr = ws.w_re.data();
        const double* wi = ws.w_im.data();
        for (std::size_t k = 1; k < q; ++k) {
            const double zr1 = re[k], zi1 = im[k];
            const double zr2 = re[m - k], zi2 = -im[m - k];  // conj(Z[m - k])
            const double er = 0.5 * (zr1 + zr2);
            const double ei = 0.5 * (zi1 + zi2);
            // O = -0.5i * (Z[k] - conj(Z[m - k]))
            const double dr = zr1 - zr2, di = zi1 - zi2;
            const double or_ = 0.5 * di;
            const double oi = -0.5 * dr;
            // W_N^k * O
            const double tr = or_ * wr[k] - oi * wi[k];
            const double ti = or_ * wi[k] + oi * wr[k];
            out[k] = std::complex<double>(er + tr, ei + ti);
            out[m - k] = std::complex<double>(er - tr, -(ei - ti));
        }
    }
}

void irfft(const std::complex<double>* spec, std::size_t nfft, double* out) {
    if (!is_pow2(nfft) || nfft == 0)
        throw std::invalid_argument("irfft: nfft must be a power of two");
    if (nfft == 1) {
        out[0] = spec[0].real();
        return;
    }
    const std::size_t m = nfft >> 1;
    thread_local RfftWorkspace ws;
    ws.prepare(nfft);
    double* re = ws.re.data();
    double* im = ws.im.data();

    // Pack Z_conj[k] = conj(E[k] + i O'[k]) so forward FFT_M computes m * (x_{2k} - i x_{2k+1}).
    const double x0 = spec[0].real();
    const double xm = spec[m].real();
    // E[0] = 0.5*(x0+xm), O'[0] = 0.5*(x0-xm) -> Z[0] = E[0] + i O'[0], Z_conj[0] = E[0] - i O'[0]
    re[0] = 0.5 * (x0 + xm);
    im[0] = -0.5 * (x0 - xm);
    if (m >= 2) {
        const std::size_t q = m >> 1;
        // X[q] = conj(Z[q]) -> Z_conj[q] = X[q]
        re[q] = spec[q].real();
        im[q] = spec[q].imag();
        const double* wr = ws.w_re.data();
        const double* wi = ws.w_im.data();
        for (std::size_t k = 1; k < q; ++k) {
            const double xr1 = spec[k].real(), xi1 = spec[k].imag();
            const double xr2 = spec[m - k].real(), xi2 = -spec[m - k].imag();  // conj(X[m - k])
            const double er = 0.5 * (xr1 + xr2);
            const double ei = 0.5 * (xi1 + xi2);
            const double dr = xr1 - xr2, di = xi1 - xi2;
            // O'[k] = 0.5 * conj(W_N^k) * (X[k] - conj(X[m-k]))
            const double wcr = wr[k], wci = -wi[k];
            const double or_ = 0.5 * (dr * wcr - di * wci);
            const double oi = 0.5 * (dr * wci + di * wcr);
            // Z[k] = E + i O = (er - oi) + i (ei + or_) -> Z_conj[k] = (er - oi) - i (ei + or_)
            re[k] = er - oi;
            im[k] = -(ei + or_);
            // Z[m - k] = conj(E - i O) -> Z_conj[m - k] = E - i O = (er + oi) + i (ei - or_)
            re[m - k] = er + oi;
            im[m - k] = ei - or_;
        }
    }

    cfft_forward(re, im, m);

    const double inv_m = 1.0 / static_cast<double>(m);
    for (std::size_t k = 0; k < m; ++k) {
        out[2 * k] = re[k] * inv_m;
        out[2 * k + 1] = -im[k] * inv_m;
    }
}

}  // namespace advtg::dsp
