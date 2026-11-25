// Core DSP kernels: IIR biquad filtering, Hilbert FIR convolution, moving average,
// compute_cell_envelope and detect_cell_candidates.
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "advtg_dsp.hpp"
#include "advtg_simd.hpp"

namespace advtg::dsp {
namespace {

inline constexpr long double kPiL = 3.141592653589793238462643383279502884L;
inline constexpr std::size_t kFftMinKernel = 256;

struct Section {
    double b0, b1, b2, a1, a2;
};

void load_sections(const double* sos, int n_sec, std::vector<Section>& out) {
    if (n_sec < 1) throw std::invalid_argument("sos debe tener al menos una seccion");
    out.resize(static_cast<std::size_t>(n_sec));
    for (int s = 0; s < n_sec; ++s) {
        const double* p = sos + s * 6;
        const double a0 = p[3];
        if (!(std::fabs(a0) > kGate)) throw std::invalid_argument("sos: a0 es (casi) cero");
        const double inv = 1.0 / a0;
        out[static_cast<std::size_t>(s)] = {p[0] * inv, p[1] * inv, p[2] * inv, p[4] * inv,
                                            p[5] * inv};
    }
}

// Fast 4-section fused register cascade: the standard 4th-order Butterworth bandpass is 4
// biquad sections, so all 8 state variables stay in FP64 registers for the entire pass.
void sosfilt_4sec(const Section* sec, const double* x, std::size_t n, const double* zi,
                  double zi_scale, double* y) {
    const double b00 = sec[0].b0, b10 = sec[0].b1, b20 = sec[0].b2, a10 = sec[0].a1, a20 = sec[0].a2;
    const double b01 = sec[1].b0, b11 = sec[1].b1, b21 = sec[1].b2, a11 = sec[1].a1, a21 = sec[1].a2;
    const double b02 = sec[2].b0, b12 = sec[2].b1, b22 = sec[2].b2, a12 = sec[2].a1, a22 = sec[2].a2;
    const double b03 = sec[3].b0, b13 = sec[3].b1, b23 = sec[3].b2, a13 = sec[3].a1, a23 = sec[3].a2;
    double z10 = zi ? zi[0] * zi_scale : 0.0, z20 = zi ? zi[1] * zi_scale : 0.0;
    double z11 = zi ? zi[2] * zi_scale : 0.0, z21 = zi ? zi[3] * zi_scale : 0.0;
    double z12 = zi ? zi[4] * zi_scale : 0.0, z22 = zi ? zi[5] * zi_scale : 0.0;
    double z13 = zi ? zi[6] * zi_scale : 0.0, z23 = zi ? zi[7] * zi_scale : 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        const double x0 = x[i];
        const double y0 = b00 * x0 + z10;
        z10 = b10 * x0 - a10 * y0 + z20;
        z20 = b20 * x0 - a20 * y0;

        const double y1 = b01 * y0 + z11;
        z11 = b11 * y0 - a11 * y1 + z21;
        z21 = b21 * y0 - a21 * y1;

        const double y2 = b02 * y1 + z12;
        z12 = b12 * y1 - a12 * y2 + z22;
        z22 = b22 * y1 - a22 * y2;

        const double y3 = b03 * y2 + z13;
        z13 = b13 * y2 - a13 * y3 + z23;
        z23 = b23 * y2 - a23 * y3;

        y[i] = y3;
    }
}

void sosfilt_core(const std::vector<Section>& secs, const double* x, std::size_t n,
                  const double* zi, double zi_scale, double* y) {
    if (n == 0) return;
    if (secs.size() == 4) {
        sosfilt_4sec(secs.data(), x, n, zi, zi_scale, y);
        return;
    }
    if (y != x) std::memcpy(y, x, n * sizeof(double));
    for (std::size_t s = 0; s < secs.size(); ++s) {
        const Section& c = secs[s];
        double w1 = zi ? zi[2 * s] * zi_scale : 0.0;
        double w2 = zi ? zi[2 * s + 1] * zi_scale : 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            const double xi = y[i];
            const double yi = c.b0 * xi + w1;
            w1 = c.b1 * xi - c.a1 * yi + w2;
            w2 = c.b2 * xi - c.a2 * yi;
            y[i] = yi;
        }
    }
}

void sosfiltfilt_core(const std::vector<Section>& secs, const double* zi, std::int64_t padlen,
                      const double* x, std::size_t n, double* ext, double* tmp, double* y) {
    if (padlen < 0) throw std::invalid_argument("padlen debe ser >= 0");
    const std::size_t edge = static_cast<std::size_t>(padlen);
    if (n <= edge) throw std::invalid_argument("la señal debe ser más larga que padlen");
    const std::size_t total = n + 2 * edge;
    if (edge > 0) {
        const double x0 = x[0];
        for (std::size_t i = 0; i < edge; ++i) ext[i] = 2.0 * x0 - x[edge - i];
        std::memcpy(ext + edge, x, n * sizeof(double));
        const double xn = x[n - 1];
        for (std::size_t i = 0; i < edge; ++i) ext[edge + n + i] = 2.0 * xn - x[n - 2 - i];
    } else {
        std::memcpy(ext, x, n * sizeof(double));
    }
    sosfilt_core(secs, ext, total, zi, ext[0], tmp);
    std::reverse(tmp, tmp + total);
    sosfilt_core(secs, tmp, total, zi, tmp[0], ext);
    for (std::size_t i = 0; i < n; ++i) y[i] = ext[total - 1 - (edge + i)];
}

// Check if kernel k[0..m) is odd-length odd-antisymmetric Hilbert FIR (even lags == 0).
bool is_odd_antisym_hilbert(const double* k, std::size_t m) {
    if ((m & 1U) == 0 || m < 3) return false;
    const std::size_t half = m >> 1;
    if (k[half] != 0.0) return false;
    for (std::size_t d = 1; d <= half; ++d) {
        if ((d & 1U) == 0) {
            if (k[half + d] != 0.0 || k[half - d] != 0.0) return false;
        } else {
            if (std::fabs(k[half + d] + k[half - d]) > 1e-15 * std::max(1.0, std::fabs(k[half + d])))
                return false;
        }
    }
    return true;
}

bool is_symmetric_kernel(const double* k, std::size_t m) {
    if ((m & 1U) == 0 || m < 3) return false;
    const std::size_t half = m >> 1;
    for (std::size_t d = 1; d <= half; ++d) {
        if (std::fabs(k[half + d] - k[half - d]) > 1e-15 * std::max(1.0, std::fabs(k[half + d])))
            return false;
    }
    return true;
}

void convolve_same_impl(const double* x, std::size_t n, const double* k, std::size_t m,
                        double* out) {
    if (n == 0) return;
    if (m == 0) {
        std::memset(out, 0, n * sizeof(double));
        return;
    }
    if (is_odd_antisym_hilbert(k, m)) {
        const std::size_t half = m >> 1;
        thread_local AlignedBuffer<double> yp, coef;
        thread_local std::vector<std::int32_t> lags;
        yp.resize(n + 2 * half);
        std::memset(yp.data(), 0, half * sizeof(double));
        std::memcpy(yp.data() + half, x, n * sizeof(double));
        std::memset(yp.data() + half + n, 0, half * sizeof(double));
        const std::size_t nt = (half + 1) >> 1;
        coef.resize(nt);
        lags.resize(nt);
        std::size_t idx = 0;
        for (std::size_t d = 1; d <= half; d += 2) {
            coef[idx] = k[half + d];
            lags[idx] = static_cast<std::int32_t>(d);
            ++idx;
        }
        kernels().hilbert_fold(yp.data(), n, half, coef.data(), lags.data(), nt, out);
        return;
    }
    const std::size_t start = (m - 1) >> 1;
    if (m < kFftMinKernel) {
        thread_local AlignedBuffer<double> xp, krev;
        xp.resize(n + m - 1);
        krev.resize(m);
        for (std::size_t j = 0; j < m; ++j) krev[j] = k[m - 1 - j];
        const std::size_t pad_l = m - 1 - start;
        const std::size_t pad_r = start;
        std::memset(xp.data(), 0, pad_l * sizeof(double));
        std::memcpy(xp.data() + pad_l, x, n * sizeof(double));
        std::memset(xp.data() + pad_l + n, 0, pad_r * sizeof(double));
        const auto dot = kernels().dot;
        for (std::size_t i = 0; i < n; ++i) out[i] = dot(xp.data() + i, krev.data(), m);
        return;
    }
    const std::size_t full = n + m - 1;
    const std::size_t nfft = next_pow2(full);
    const std::size_t bins = (nfft >> 1) + 1;
    thread_local std::vector<std::complex<double>> sx, sk;
    thread_local AlignedBuffer<double> yfull;
    sx.resize(bins);
    sk.resize(bins);
    yfull.resize(nfft);
    rfft(x, n, nfft, sx.data());
    rfft(k, m, nfft, sk.data());
    for (std::size_t i = 0; i < bins; ++i) sx[i] *= sk[i];
    irfft(sx.data(), nfft, yfull.data());
    std::memcpy(out, yfull.data() + start, n * sizeof(double));
}

struct CellWorkspace {
    std::vector<Section> secs;
    AlignedBuffer<double> ext, tmp, y, h, env;
};

// _find_local_maxima_runs(x): returns midpoints, run_starts, run_ends (matching SciPy plateaus).
void find_local_maxima_runs(const double* x, std::size_t n, std::vector<std::int64_t>& mid,
                            std::vector<std::int64_t>& rs, std::vector<std::int64_t>& re) {
    mid.clear();
    rs.clear();
    re.clear();
    if (n < 3) return;
    std::size_t i = 1;
    const std::size_t i_max = n - 1;
    while (i < i_max) {
        if (x[i - 1] < x[i]) {
            std::size_t ahead = i + 1;
            while (ahead < i_max && x[ahead] == x[i]) ++ahead;
            if (x[ahead] < x[i]) {
                const std::int64_t s = static_cast<std::int64_t>(i);
                const std::int64_t e = static_cast<std::int64_t>(ahead - 1);
                mid.push_back((s + e) / 2);
                rs.push_back(s);
                re.push_back(e);
                i = ahead;
            }
        }
        ++i;
    }
}

}  // namespace

// ------------------------------------------------------------------ windows
std::vector<double> blackman(std::size_t n) {
    std::vector<double> w(n, 1.0);
    if (n <= 1) return w;
    const long double denom = static_cast<long double>(n - 1);
    for (std::size_t i = 0; i < n; ++i) {
        const long double a = (2.0L * kPiL * static_cast<long double>(i)) / denom;
        w[i] = static_cast<double>(0.42L - 0.5L * std::cos(a) + 0.08L * std::cos(2.0L * a));
        if (w[i] < 0.0 && w[i] > -1e-15) w[i] = 0.0;
    }
    return w;
}

std::vector<double> hanning(std::size_t n) {
    std::vector<double> w(n, 1.0);
    if (n <= 1) return w;
    const long double denom = static_cast<long double>(n - 1);
    for (std::size_t i = 0; i < n; ++i) {
        const long double a = (2.0L * kPiL * static_cast<long double>(i)) / denom;
        w[i] = static_cast<double>(0.5L - 0.5L * std::cos(a));
    }
    return w;
}

std::vector<double> hilbert_fir(std::int64_t half) {
    const std::size_t len = static_cast<std::size_t>(2 * half + 1);
    std::vector<double> h(len, 0.0);
    auto w = blackman(len);
    for (std::int64_t k = -half; k <= half; ++k) {
        if ((k & 1) != 0) {
            const std::size_t idx = static_cast<std::size_t>(k + half);
            h[idx] = (2.0 / (static_cast<double>(kPiL) * static_cast<double>(k))) * w[idx];
        }
    }
    return h;
}

// ------------------------------------------------------------------ IIR public
void sosfilt(const double* sos, int n_sec, const double* x, std::size_t n, const double* zi,
             double* y) {
    std::vector<Section> secs;
    load_sections(sos, n_sec, secs);
    sosfilt_core(secs, x, n, zi, 1.0, y);
}

void sosfiltfilt(const double* sos, int n_sec, const double* zi, std::int64_t padlen,
                 const double* x, std::size_t n, double* y) {
    std::vector<Section> secs;
    load_sections(sos, n_sec, secs);
    const std::size_t edge = padlen >= 0 ? static_cast<std::size_t>(padlen) : 0;
    thread_local AlignedBuffer<double> ext, tmp;
    ext.resize(n + 2 * edge);
    tmp.resize(n + 2 * edge);
    sosfiltfilt_core(secs, zi, padlen, x, n, ext.data(), tmp.data(), y);
}

// ------------------------------------------------------------------ FIR / moving average
void convolve_same(const double* x, std::size_t n, const double* k, std::size_t m, double* out) {
    convolve_same_impl(x, n, k, m, out);
}

void convolve_valid(const double* x, std::size_t n, const double* k, std::size_t m, double* out) {
    if (m == 0 || n < m) return;
    const std::size_t n_out = n - m + 1;
    thread_local AlignedBuffer<double> krev;
    krev.resize(m);
    for (std::size_t j = 0; j < m; ++j) krev[j] = k[m - 1 - j];
    if (is_symmetric_kernel(k, m)) {
        const std::size_t half = m >> 1;
        const double center_w = k[half];
        for (std::size_t i = 0; i < n_out; ++i) {
            const double* c = x + i + half;
            double acc = center_w * c[0];
            for (std::size_t d = 1; d <= half; ++d) acc += k[half + d] * (c[-static_cast<std::ptrdiff_t>(d)] + c[d]);
            out[i] = acc;
        }
        return;
    }
    const auto dot = kernels().dot;
    for (std::size_t i = 0; i < n_out; ++i) out[i] = dot(x + i, krev.data(), m);
}

void moving_average_same(const double* x, std::size_t n, std::int64_t m, double* out) {
    moving_average_slice(x, n, m, 0, static_cast<std::int64_t>(n), out);
}

void moving_average_slice(const double* x, std::size_t n, std::int64_t m, std::int64_t start,
                          std::int64_t stop, double* out) {
    if (stop <= start) return;
    const std::size_t out_n = static_cast<std::size_t>(stop - start);
    if (m <= 1) {
        std::memcpy(out, x + start, out_n * sizeof(double));
        return;
    }
    const double inv_m = 1.0 / static_cast<double>(m);
    const std::int64_t n64 = static_cast<std::int64_t>(n);
    const std::int64_t half_l = (m - 1) / 2;
    if (m <= 64) {  // direct sum: zero cumulative cancellation
        for (std::size_t idx = 0; idx < out_n; ++idx) {
            const std::int64_t i = start + static_cast<std::int64_t>(idx) + half_l;
            const std::int64_t hi = std::min(i + 1, n64);
            const std::int64_t lo = std::max<std::int64_t>(i - m + 1, 0);
            double s = 0.0;
            for (std::int64_t j = lo; j < hi; ++j) s += x[j];
            out[idx] = s * inv_m;
        }
        return;
    }
    const std::int64_t half_r = m - 1 - half_l;
    const std::int64_t a = std::max<std::int64_t>(0, start - half_r);
    const std::int64_t b = std::min<std::int64_t>(n64, stop + half_l);
    const std::size_t sub_n = static_cast<std::size_t>(b - a);
    thread_local AlignedBuffer<double> cs;
    cs.resize(sub_n + 1);
    cs[0] = 0.0;
    NeumaierSum acc;
    for (std::size_t j = 0; j < sub_n; ++j) {
        acc.add(x[a + static_cast<std::int64_t>(j)]);
        cs[j + 1] = acc.value();
    }
    for (std::size_t idx = 0; idx < out_n; ++idx) {
        const std::int64_t i = start + static_cast<std::int64_t>(idx) + half_l;
        const std::int64_t hi = std::min(i + 1, n64) - a;
        const std::int64_t lo = std::max<std::int64_t>(i - m + 1, 0) - a;
        out[idx] = (cs[static_cast<std::size_t>(hi)] - cs[static_cast<std::size_t>(lo)]) * inv_m;
    }
}

// ------------------------------------------------------------------ compute_cell_envelope
void compute_cell_envelope(const double* x, std::size_t n, const double* sos, int n_sec,
                           const double* zi, const double* hk, std::size_t hk_len,
                           std::int64_t smooth_len, std::int64_t crop_start,
                           std::int64_t crop_end, std::int64_t padlen, double* out) {
    if (crop_start < 0 || crop_end < crop_start || static_cast<std::size_t>(crop_end) > n)
        throw std::invalid_argument("crop indices fuera de límites");
    if (smooth_len < 1) throw std::invalid_argument("smooth_len debe ser >= 1");
    thread_local CellWorkspace ws;
    load_sections(sos, n_sec, ws.secs);
    const std::size_t out_n = static_cast<std::size_t>(crop_end - crop_start);
    if (static_cast<std::int64_t>(n) <= padlen + 1) {
        std::memset(out, 0, out_n * sizeof(double));
        return;
    }
    const std::size_t edge = padlen >= 0 ? static_cast<std::size_t>(padlen) : 0;
    ws.ext.resize(n + 2 * edge);
    ws.tmp.resize(n + 2 * edge);
    ws.y.resize(n);
    ws.h.resize(n);
    ws.env.resize(n);
    sosfiltfilt_core(ws.secs, zi, padlen, x, n, ws.ext.data(), ws.tmp.data(), ws.y.data());
    convolve_same_impl(ws.y.data(), n, hk, hk_len, ws.h.data());
    kernels().hypot_env(ws.y.data(), ws.h.data(), n, ws.env.data());
    moving_average_slice(ws.env.data(), n, smooth_len, crop_start, crop_end, out);
}

// ------------------------------------------------------------------ peaks & candidates
std::size_t find_local_maxima(const double* x, std::size_t n, std::int64_t* out) {
    thread_local std::vector<std::int64_t> mid, rs, re;
    find_local_maxima_runs(x, n, mid, rs, re);
    if (!mid.empty()) std::memcpy(out, mid.data(), mid.size() * sizeof(std::int64_t));
    return mid.size();
}

void detect_cell_candidates(const double* env, const double* F, const double* D, std::size_t n,
                            double threshold_hi, double threshold_lo, std::int64_t merge_samples,
                            double split_ratio, double min_spacing_samples,
                            double onset_fraction, std::int64_t cell_start_abs,
                            std::int64_t cell_end_abs, std::int64_t lo_abs,
                            std::int64_t run_start_abs, Candidates& out) {
    if (n < 3) return;
    thread_local std::vector<std::size_t> starts, ends;
    thread_local std::vector<std::int64_t> all_peaks, all_rs, all_re;
    thread_local std::vector<std::int64_t> peaks;
    starts.clear();
    ends.clear();

    bool in_run = false;
    std::size_t cur_s = 0;
    for (std::size_t i = 0; i < n; ++i) {
        const double e = env[i];
        const bool lo_on = e > (F[i] + threshold_lo * D[i]);
        if (lo_on) {
            if (!in_run) {
                if (!starts.empty() && static_cast<std::int64_t>(i - ends.back()) < merge_samples) {
                    in_run = true;
                    cur_s = starts.back();
                    starts.pop_back();
                    ends.pop_back();
                } else {
                    in_run = true;
                    cur_s = i;
                }
            }
        } else if (in_run) {
            starts.push_back(cur_s);
            ends.push_back(i);
            in_run = false;
        }
    }
    if (in_run) {
        starts.push_back(cur_s);
        ends.push_back(n);
    }
    if (starts.empty()) return;

    // Drop runs without any sample above thr_hi (reproduces cs_hi hysteresis pre-filter).
    std::size_t keep_n = 0;
    for (std::size_t r = 0; r < starts.size(); ++r) {
        const std::size_t s = starts[r], t = ends[r];
        bool ok = false;
        for (std::size_t j = s; j < t; ++j) {
            if (env[j] > F[j] + threshold_hi * D[j]) {
                ok = true;
                break;
            }
        }
        if (ok) {
            starts[keep_n] = s;
            ends[keep_n] = t;
            ++keep_n;
        }
    }
    starts.resize(keep_n);
    ends.resize(keep_n);
    if (starts.empty()) return;

    find_local_maxima_runs(env, n, all_peaks, all_rs, all_re);
    const std::int64_t min_sp_int = static_cast<std::int64_t>(min_spacing_samples);
    std::size_t p_cursor = 0;

    for (std::size_t r = 0; r < starts.size(); ++r) {
        const std::int64_t s = static_cast<std::int64_t>(starts[r]);
        const std::int64_t t = static_cast<std::int64_t>(ends[r]);
        while (p_cursor < all_peaks.size() && all_peaks[p_cursor] < s + 1) ++p_cursor;
        std::size_t p_end = p_cursor;
        while (p_end < all_peaks.size() && all_peaks[p_end] < t - 1) ++p_end;

        peaks.clear();
        for (std::size_t pi = p_cursor; pi < p_end; ++pi) {
            if (all_rs[pi] > s && all_re[pi] < t - 1) {
                const std::int64_t abs_i = all_peaks[pi];
                if (env[abs_i] > F[abs_i] + threshold_hi * D[abs_i]) peaks.push_back(abs_i - s);
            }
        }
        p_cursor = p_end;

        if (peaks.empty()) {
            std::int64_t best = 0;
            double best_v = env[s];
            for (std::int64_t j = s + 1; j < t; ++j) {
                if (env[j] > best_v) {
                    best_v = env[j];
                    best = j - s;
                }
            }
            const std::int64_t abs_i = s + best;
            if (env[abs_i] > F[abs_i] + threshold_hi * D[abs_i]) peaks.push_back(best);
        }
        if (peaks.empty()) continue;

        struct Group {
            std::int64_t p;
            std::int64_t v_rel;
        };
        thread_local std::vector<Group> groups;
        groups.clear();
        for (std::int64_t p : peaks) {
            if (groups.empty()) {
                groups.push_back({p, -1});
                continue;
            }
            Group& cur = groups.back();
            std::int64_t v_rel = cur.p;
            double v_min = env[s + cur.p];
            for (std::int64_t j = cur.p + 1; j <= p; ++j) {
                if (env[s + j] < v_min) {
                    v_min = env[s + j];
                    v_rel = j;
                }
            }
            const double valley = std::max(env[s + v_rel], F[s + p]);
            if (env[s + p] >= split_ratio * valley &&
                static_cast<double>(p - cur.p) >= min_spacing_samples) {
                groups.push_back({p, v_rel});
            } else if (env[s + p] > env[s + cur.p]) {
                cur.p = p;
            }
        }

        for (const Group& g : groups) {
            const std::int64_t i = s + g.p;
            const double base = g.v_rel < 0 ? 0.0 : std::max(env[s + g.v_rel], F[i]);
            double level = base + onset_fraction * (env[i] - base);
            level = std::max(level, F[i] + threshold_lo * D[i]);
            const std::int64_t j0 =
                g.v_rel >= 0 ? s + g.v_rel : std::max<std::int64_t>(0, s - min_sp_int);
            std::int64_t j_below = -1;
            for (std::int64_t j = i - 1; j >= j0; --j) {
                if (env[j] < level) {
                    j_below = j;
                    break;
                }
            }
            double onset;
            if (j_below < 0) {
                onset = static_cast<double>(j0);
            } else {
                const double dd = env[j_below + 1] - env[j_below];
                onset = static_cast<double>(j_below) +
                        (dd > kGate ? (level - env[j_below]) / dd : 0.0);
            }
            const double o_abs = static_cast<double>(lo_abs) + onset;
            if (o_abs >= static_cast<double>(cell_start_abs) &&
                o_abs < static_cast<double>(cell_end_abs) &&
                o_abs >= static_cast<double>(run_start_abs)) {
                out.onset.push_back(o_abs);
                out.peak.push_back(env[i]);
                out.floor.push_back(F[i]);
                out.spread.push_back(D[i]);
            }
        }
    }
}

}  // namespace advtg::dsp
