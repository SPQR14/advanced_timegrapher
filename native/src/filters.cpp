// IIR filter design (dsp/filters.py): Butterworth band-pass as second-order sections, the
// steady-state initial conditions of each section and the default sosfiltfilt padding.
//
// Line-by-line port of the numpy code (itself a port of SciPy 1.18: buttap, lp2bp_zpk,
// bilinear_zpk, zpk2sos with "nearest" pairing, _cplxreal, sosfilt_zi / lfilter_zi), so the
// coefficients agree with Python to a few ulp. It lets the native session (and a mobile app)
// build the detector without Python.
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numbers>
#include <numeric>
#include <stdexcept>
#include <vector>

#include "advtg_dsp.hpp"

namespace advtg::dsp {
namespace {

using cplx = std::complex<double>;
using cvec = std::vector<cplx>;

bool is_real(const cplx& v) { return v.imag() == 0.0; }

// np.lexsort((abs(z.imag), z.real)): by real part, then by |imag| (stable).
void sort_real_then_absimag(cvec& z) {
    std::stable_sort(z.begin(), z.end(), [](const cplx& a, const cplx& b) {
        if (a.real() != b.real()) return a.real() < b.real();
        return std::abs(a.imag()) < std::abs(b.imag());
    });
}

// _cplxreal(z) -> (zc, zr); zpk2sos only uses concat(zc, zr).
cvec cplxreal_concat(cvec z) {
    if (z.empty()) return z;
    const double tol = 100.0 * std::numeric_limits<double>::epsilon();
    sort_real_then_absimag(z);
    cvec zr, rest;
    for (const auto& v : z) {
        if (std::abs(v.imag()) <= tol * std::abs(v))
            zr.emplace_back(v.real(), 0.0);
        else
            rest.push_back(v);
    }
    if (rest.empty()) return zr;
    cvec zp, zn;
    for (const auto& v : rest) (v.imag() > 0 ? zp : zn).push_back(v);
    if (zp.size() != zn.size()) throw std::invalid_argument("complex value without conjugate");
    // runs of (almost) equal real part: sort each run by |imag|
    std::size_t i = 0;
    while (i < zp.size()) {
        std::size_t j = i;
        while (j + 1 < zp.size() && zp[j + 1].real() - zp[j].real() <= tol * std::abs(zp[j])) ++j;
        if (j > i) {
            auto by_imag = [](const cplx& a, const cplx& b) {
                return std::abs(a.imag()) < std::abs(b.imag());
            };
            std::stable_sort(zp.begin() + static_cast<std::ptrdiff_t>(i),
                             zp.begin() + static_cast<std::ptrdiff_t>(j + 1), by_imag);
            std::stable_sort(zn.begin() + static_cast<std::ptrdiff_t>(i),
                             zn.begin() + static_cast<std::ptrdiff_t>(j + 1), by_imag);
        }
        i = j + 1;
    }
    cvec out;
    out.reserve(zp.size() + zr.size());
    for (std::size_t q = 0; q < zp.size(); ++q) {
        if (std::abs(zp[q] - std::conj(zn[q])) > tol * std::abs(zn[q]))
            throw std::invalid_argument("complex value without conjugate");
        out.push_back((zp[q] + std::conj(zn[q])) / 2.0);
    }
    out.insert(out.end(), zr.begin(), zr.end());
    return out;
}

std::size_t argsort_first(const cvec& fro, const cplx& to, int which) {
    // which: 0 = any, 1 = real, 2 = complex
    std::vector<std::size_t> order(fro.size());
    std::iota(order.begin(), order.end(), std::size_t{0});
    std::stable_sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) {
        return std::abs(fro[a] - to) < std::abs(fro[b] - to);
    });
    for (std::size_t idx : order) {
        if (which == 0) return idx;
        const bool r = is_real(fro[idx]);
        if ((which == 1 && r) || (which == 2 && !r)) return idx;
    }
    throw std::invalid_argument("no root of the requested kind");
}

std::size_t idx_worst(const cvec& q) {
    std::size_t best = 0;
    double bv = std::numeric_limits<double>::infinity();
    for (std::size_t i = 0; i < q.size(); ++i) {
        const double v = std::abs(1.0 - std::abs(q[i]));
        if (v < bv) {
            bv = v;
            best = i;
        }
    }
    return best;
}

cplx take(cvec& v, std::size_t i) {
    const cplx x = v[i];
    v.erase(v.begin() + static_cast<std::ptrdiff_t>(i));
    return x;
}

// np.poly: convolve [1, -r] for each root, in order.
cvec poly(const cvec& roots) {
    cvec a{cplx(1.0, 0.0)};
    for (const auto& r : roots) {
        cvec b(a.size() + 1, cplx(0.0, 0.0));
        for (std::size_t i = 0; i < a.size(); ++i) {
            b[i] += a[i];
            b[i + 1] += a[i] * (-r);
        }
        a.swap(b);
    }
    return a;
}

void single_zpksos(const cvec& z, const cvec& p, double* sos) {
    for (int i = 0; i < 6; ++i) sos[i] = 0.0;
    const cvec b = z.empty() ? cvec{cplx(1.0, 0.0)} : poly(z);
    const cvec a = p.empty() ? cvec{cplx(1.0, 0.0)} : poly(p);
    for (std::size_t i = 0; i < b.size(); ++i) sos[3 - b.size() + i] = b[i].real();
    for (std::size_t i = 0; i < a.size(); ++i) sos[6 - a.size() + i] = a[i].real();
}

std::vector<double> zpk2sos_nearest(cvec z, cvec p, double k) {
    if (z.empty() && p.empty()) return {k, 0.0, 0.0, 1.0, 0.0, 0.0};
    while (p.size() < z.size()) p.emplace_back(0.0, 0.0);
    while (z.size() < p.size()) z.emplace_back(0.0, 0.0);
    const std::size_t n_sections = (std::max(p.size(), z.size()) + 1) / 2;
    if (p.size() % 2 == 1) {
        p.emplace_back(0.0, 0.0);
        z.emplace_back(0.0, 0.0);
    }
    z = cplxreal_concat(z);
    p = cplxreal_concat(p);
    std::vector<double> sos(n_sections * 6, 0.0);
    auto n_real = [](const cvec& v) {
        return static_cast<std::size_t>(std::count_if(v.begin(), v.end(), is_real));
    };
    for (std::size_t s1 = n_sections; s1-- > 0;) {
        double* row = &sos[s1 * 6];
        const cplx p1 = take(p, idx_worst(p));
        if (is_real(p1) && n_real(p) == 0) {
            const cplx z1 = take(z, argsort_first(z, p1, 1));
            single_zpksos({z1, cplx(0.0, 0.0)}, {p1, cplx(0.0, 0.0)}, row);
        } else if (p.size() + 1 == z.size() && !is_real(p1) && n_real(p) == 1 && n_real(z) == 1) {
            const cplx z1 = take(z, argsort_first(z, p1, 2));
            single_zpksos({z1, std::conj(z1)}, {p1, std::conj(p1)}, row);
        } else {
            cplx p2;
            if (is_real(p1)) {
                cvec reals;
                std::vector<std::size_t> ridx;
                for (std::size_t i = 0; i < p.size(); ++i)
                    if (is_real(p[i])) {
                        reals.push_back(p[i]);
                        ridx.push_back(i);
                    }
                p2 = take(p, ridx[idx_worst(reals)]);
            } else {
                p2 = std::conj(p1);
            }
            if (!z.empty()) {
                const cplx z1 = take(z, argsort_first(z, p1, 0));
                if (!is_real(z1)) {
                    single_zpksos({z1, std::conj(z1)}, {p1, p2}, row);
                } else if (!z.empty()) {
                    const cplx z2 = take(z, argsort_first(z, p1, 1));
                    single_zpksos({z1, z2}, {p1, p2}, row);
                } else {
                    single_zpksos({z1}, {p1, p2}, row);
                }
            } else {
                single_zpksos({}, {p1, p2}, row);
            }
        }
    }
    for (int i = 0; i < 3; ++i) sos[i] *= k;
    return sos;
}

}  // namespace

std::vector<double> butter_bandpass_sos(int order, double lo_hz, double hi_hz, double fs) {
    if (order < 1) throw std::invalid_argument("invalid filter order");
    const double wn0 = lo_hz / (fs / 2.0);
    const double wn1 = hi_hz / (fs / 2.0);
    if (!(0.0 < wn0 && wn0 < wn1 && wn1 < 1.0)) throw std::invalid_argument("invalid band");
    constexpr double fs_pre = 2.0;
    const double pi = std::numbers::pi;
    const double w0 = 2.0 * fs_pre * std::tan(pi * wn0 / fs_pre);
    const double w1 = 2.0 * fs_pre * std::tan(pi * wn1 / fs_pre);
    const double bw = w1 - w0;
    const double wo = std::sqrt(w0 * w1);
    // buttap
    cvec p;
    for (int m = -order + 1; m < order; m += 2)
        p.push_back(-std::exp(cplx(0.0, 1.0) * (pi * static_cast<double>(m) / (2.0 * order))));
    double k = 1.0;
    // lp2bp_zpk (no zeros: degree = order)
    const std::size_t degree = p.size();
    cvec p_bp;
    p_bp.reserve(2 * degree);
    cvec p_lp(degree);
    for (std::size_t i = 0; i < degree; ++i) p_lp[i] = p[i] * bw / 2.0;
    for (std::size_t i = 0; i < degree; ++i)
        p_bp.push_back(p_lp[i] + std::sqrt(p_lp[i] * p_lp[i] - wo * wo));
    for (std::size_t i = 0; i < degree; ++i)
        p_bp.push_back(p_lp[i] - std::sqrt(p_lp[i] * p_lp[i] - wo * wo));
    cvec z_bp(degree, cplx(0.0, 0.0));
    k = k * std::pow(bw, static_cast<double>(degree));
    // bilinear_zpk
    const double fs2 = 2.0 * fs_pre;
    const std::size_t deg2 = p_bp.size() - z_bp.size();
    cvec z_z, p_z;
    for (const auto& v : z_bp) z_z.push_back((fs2 + v) / (fs2 - v));
    for (const auto& v : p_bp) p_z.push_back((fs2 + v) / (fs2 - v));
    for (std::size_t i = 0; i < deg2; ++i) z_z.emplace_back(-1.0, 0.0);
    cplx num(1.0, 0.0), den(1.0, 0.0);
    for (const auto& v : z_bp) num *= (fs2 - v);
    for (const auto& v : p_bp) den *= (fs2 - v);
    const double k_z = k * (num / den).real();
    return zpk2sos_nearest(std::move(z_z), std::move(p_z), k_z);
}

std::vector<double> sosfilt_zi(const double* sos, int n_sec) {
    std::vector<double> zi(static_cast<std::size_t>(n_sec) * 2, 0.0);
    double scale = 1.0;
    for (int s = 0; s < n_sec; ++s) {
        const double* r = sos + 6 * s;
        double b[3] = {r[0], r[1], r[2]};
        double a[3] = {r[3], r[4], r[5]};
        if (a[0] == 0.0) throw std::invalid_argument("a[0] == 0");
        if (a[0] != 1.0) {
            const double a0 = a[0];
            for (double& v : b) v /= a0;
            for (double& v : a) v /= a0;
        }
        const double sum_a = (a[0] + a[1]) + a[2];
        if (sum_a == 0.0) throw std::invalid_argument("unstable filter: pole at z = 1");
        const double sum_b = (b[0] + b[1]) + b[2];
        const double y_inf = sum_b / sum_a;
        double d[3];
        for (int i = 0; i < 3; ++i) d[i] = b[i] - y_inf * a[i];
        zi[2 * s] = scale * (d[2] + d[1]);
        zi[2 * s + 1] = scale * d[2];
        const double* rr = sos + 6 * s;  // scale uses the raw (un-normalised) section
        scale *= ((rr[0] + rr[1]) + rr[2]) / ((rr[3] + rr[4]) + rr[5]);
    }
    return zi;
}

std::int64_t sosfiltfilt_padlen(const double* sos, int n_sec) {
    std::int64_t ntaps = 2 * static_cast<std::int64_t>(n_sec) + 1;
    std::int64_t zb = 0, za = 0;
    for (int s = 0; s < n_sec; ++s) {
        zb += sos[6 * s + 2] == 0.0;
        za += sos[6 * s + 5] == 0.0;
    }
    ntaps -= std::min(zb, za);
    return 3 * ntaps;
}

}  // namespace advtg::dsp
