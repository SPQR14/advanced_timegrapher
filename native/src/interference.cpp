// Noise-adaptive interference filter (exact counterpart of src/adv_tg/dsp/interference.py).
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_dsp.hpp"

namespace advtg::dsp {
namespace {
inline constexpr double kPsdTiny = 1e-30;
}

struct InterferenceFilter::Impl {
    double fs;
    std::int64_t nfft;
    std::int64_t hop;
    std::int64_t half;
    std::int64_t history;
    double percentile;
    std::int64_t min_frames;
    double excess;
    double floor_gain;
    std::int64_t widen;

    std::size_t n_bins = 0;   // nfft / 2 + 1
    std::size_t n_watch = 0;
    std::vector<std::size_t> watch_bins;
    std::vector<std::size_t> band_in_watch;
    std::vector<double> watch_freqs;
    std::vector<double> weight;
    std::vector<double> win;
    std::vector<double> taper;

    // 2D ring buffer storing only the watched bins
    std::size_t cap = 0;
    AlignedBuffer<double> buf;
    AlignedBuffer<std::int64_t> ends;
    AlignedBuffer<double> scratch;
    std::size_t head = 0;
    std::size_t count = 0;
    std::int64_t next = 0;

    // reusable temporaries
    AlignedBuffer<double> windowed;
    std::vector<std::complex<double>> spec;
    std::vector<double> psd_w;
    std::vector<double> noise;
    std::vector<double> band_tmp;
    std::vector<double> gb;
    std::vector<std::complex<double>> gspec;
    AlignedBuffer<double> fir_h;
    std::vector<std::complex<double>> sx, sh;
    AlignedBuffer<double> yfull;

    void grow_ring(std::size_t min_cap) {
        const std::size_t new_cap = std::max<std::size_t>(2 * cap, min_cap);
        AlignedBuffer<double> new_buf(new_cap * n_watch);
        AlignedBuffer<std::int64_t> new_ends(new_cap);
        if (count) {
            const std::size_t first = std::min(count, cap - head);
            std::memcpy(new_buf.data(), buf.data() + head * n_watch,
                        first * n_watch * sizeof(double));
            std::memcpy(new_ends.data(), ends.data() + head, first * sizeof(std::int64_t));
            if (first < count) {
                std::memcpy(new_buf.data() + first * n_watch, buf.data(),
                            (count - first) * n_watch * sizeof(double));
                std::memcpy(new_ends.data() + first, ends.data(),
                            (count - first) * sizeof(std::int64_t));
            }
        }
        buf = std::move(new_buf);
        ends = std::move(new_ends);
        scratch.resize(new_cap * n_watch);
        cap = new_cap;
        head = 0;
    }

    void push_psd(std::int64_t end, const double* p) {
        if (count == cap) grow_ring(cap + 1);
        const std::size_t slot = (head + count) % cap;
        std::memcpy(buf.data() + slot * n_watch, p, n_watch * sizeof(double));
        ends[slot] = end;
        ++count;
    }
};

InterferenceFilter::InterferenceFilter(double sample_rate, double band_lo_hz, double band_hi_hz,
                                       std::int64_t nfft, double history_s, double percentile,
                                       double excess_db, double max_attenuation_db_,
                                       std::int64_t min_frames, double skirt,
                                       std::int64_t skirt_order, std::int64_t widen_bins)
    : impl_(new Impl) {
    if (nfft < 64 || (nfft & (nfft - 1)) != 0) {
        delete impl_;
        throw std::invalid_argument("nfft debe ser una potencia de 2 >= 64");
    }
    if (percentile < 0.0 || percentile > 100.0) {
        delete impl_;
        throw std::invalid_argument("percentile fuera de [0, 100]");
    }
    if (!(history_s > 0.0 && excess_db >= 0.0 && max_attenuation_db_ > 0.0 && min_frames >= 2)) {
        delete impl_;
        throw std::invalid_argument("parámetros del filtro de interferencias no válidos");
    }
    Impl& m = *impl_;
    m.fs = sample_rate;
    m.nfft = nfft;
    m.hop = nfft / 2;
    m.half = nfft / 2;
    m.history = static_cast<std::int64_t>(std::llround(history_s * m.fs));
    m.percentile = percentile;
    m.min_frames = min_frames;
    m.excess = std::pow(10.0, excess_db / 10.0);
    m.floor_gain = std::pow(10.0, -max_attenuation_db_ / 20.0);
    m.widen = widen_bins;
    m.n_bins = static_cast<std::size_t>(nfft / 2 + 1);

    const double df = m.fs / static_cast<double>(nfft);
    const double watch_lo = std::min(skirt, 1.0) * band_lo_hz;
    const int exp2 = 2 * static_cast<int>(skirt_order);
    for (std::size_t b = 0; b < m.n_bins; ++b) {
        const double f = static_cast<double>(b) * df;
        if (f >= watch_lo && f <= band_hi_hz) {
            const std::size_t widx = m.watch_bins.size();
            m.watch_bins.push_back(b);
            m.watch_freqs.push_back(f);
            if (f >= band_lo_hz && f <= band_hi_hz) m.band_in_watch.push_back(widx);
            const double fw = std::max(f, 1e-9);
            if (fw < band_lo_hz) {
                const double t = 1.0 + std::pow(band_lo_hz / fw, exp2);
                m.weight.push_back(1.0 / (t * t));
            } else {
                m.weight.push_back(1.0);
            }
        }
    }
    m.n_watch = m.watch_bins.size();
    m.win = hanning(static_cast<std::size_t>(nfft));
    auto tlong = hanning(static_cast<std::size_t>(nfft + 1));
    m.taper.assign(tlong.begin(), tlong.begin() + static_cast<std::ptrdiff_t>(nfft));

    const std::size_t cap_est = static_cast<std::size_t>(
        std::ceil(static_cast<double>(m.history) / static_cast<double>(m.hop))) + 32;
    m.cap = std::max(static_cast<std::size_t>(min_frames + 8), cap_est);
    m.buf.resize(m.cap * m.n_watch);
    m.ends.resize(m.cap);
    m.scratch.resize(m.cap * m.n_watch);
    m.windowed.resize(static_cast<std::size_t>(nfft));
    m.spec.resize(m.n_bins);
    m.psd_w.resize(m.n_watch);
    m.noise.resize(m.n_watch);
    m.band_tmp.resize(m.band_in_watch.size());
    m.gb.resize(m.n_watch);
    m.gspec.resize(m.n_bins);
    m.fir_h.resize(static_cast<std::size_t>(nfft));
}

InterferenceFilter::~InterferenceFilter() { delete impl_; }

std::int64_t InterferenceFilter::nfft() const { return impl_->nfft; }

void InterferenceFilter::reset(std::int64_t start) {
    impl_->head = 0;
    impl_->count = 0;
    // ceil(start / hop) for non-negative start
    impl_->next = (start >= 0) ? (start + impl_->hop - 1) / impl_->hop : start / impl_->hop;
    active_bins = 0;
    max_attenuation_db = 0.0;
    peak_hz = kNaN;
    peak_excess_db = 0.0;
}

std::pair<std::int64_t, std::int64_t> InterferenceFilter::next_frame() const {
    const std::int64_t a = impl_->next * impl_->hop;
    return {a, a + impl_->nfft};
}

void InterferenceFilter::skip_frame() { ++impl_->next; }

void InterferenceFilter::add_frame(const double* samples, std::size_t n) {
    Impl& m = *impl_;
    if (static_cast<std::int64_t>(n) != m.nfft)
        throw std::invalid_argument("tamano de trama incorrecto para InterferenceFilter");
    const std::int64_t end = m.next * m.hop + m.nfft;
    ++m.next;
    for (std::size_t i = 0; i < n; ++i) m.windowed[i] = samples[i] * m.win[i];
    rfft(m.windowed.data(), n, static_cast<std::size_t>(m.nfft), m.spec.data());
    for (std::size_t w = 0; w < m.n_watch; ++w) {
        const auto c = m.spec[m.watch_bins[w]];
        m.psd_w[w] = c.real() * c.real() + c.imag() * c.imag();
    }
    double band_sum = 0.0;
    for (std::size_t idx : m.band_in_watch) band_sum += m.psd_w[idx];
    if (band_sum <= 0.0) return;
    m.push_psd(end, m.psd_w.data());
}

void InterferenceFilter::add_frames(const double* buf, std::size_t buf_n, std::int64_t buf_start,
                                    std::int64_t up_to) {
    Impl& m = *impl_;
    const std::int64_t hop = m.hop, nfft = m.nfft;
    const std::int64_t buf_end = buf_start + static_cast<std::int64_t>(buf_n);
    if (m.next * hop < buf_start) {
        const std::int64_t d = buf_start;
        m.next = (d >= 0) ? (d + hop - 1) / hop : d / hop;
    }
    const std::int64_t last_start = std::min(up_to, buf_end) - nfft;
    while (m.next * hop <= last_start) {
        const std::int64_t offset = m.next * hop - buf_start;
        add_frame(buf + offset, static_cast<std::size_t>(nfft));
    }
}

bool InterferenceFilter::gains(std::int64_t at, std::vector<double>& g_out) {
    Impl& m = *impl_;
    const std::int64_t cutoff = at - m.history + m.nfft;
    while (m.count && m.ends[m.head] < cutoff) {
        m.head = (m.head + 1) % m.cap;
        --m.count;
    }
    active_bins = 0;
    max_attenuation_db = 0.0;
    peak_hz = kNaN;
    peak_excess_db = 0.0;
    if (static_cast<std::int64_t>(m.count) < m.min_frames || m.n_watch == 0 ||
        m.band_in_watch.empty())
        return false;

    // Collect rows with end <= at into column-major scratch? Better: scratch[w * row_cap + row]
    // so percentile per frequency runs over contiguous memory!
    std::size_t row_n = 0;
    for (std::size_t j = 0; j < m.count; ++j) {
        const std::size_t slot = (m.head + j) % m.cap;
        if (m.ends[slot] <= at) ++row_n;
    }
    if (static_cast<std::int64_t>(row_n) < m.min_frames) return false;

    double* sc = m.scratch.data();
    std::size_t r = 0;
    for (std::size_t j = 0; j < m.count; ++j) {
        const std::size_t slot = (m.head + j) % m.cap;
        if (m.ends[slot] <= at) {
            const double* src = m.buf.data() + slot * m.n_watch;
            for (std::size_t w = 0; w < m.n_watch; ++w) sc[w * row_n + r] = src[w];
            ++r;
        }
    }

    const std::size_t k = std::min(
        row_n - 1, static_cast<std::size_t>(std::llround(m.percentile / 100.0 * static_cast<double>(row_n - 1))));
    for (std::size_t w = 0; w < m.n_watch; ++w) {
        double* col = sc + w * row_n;
        std::nth_element(col, col + k, col + row_n);
        m.noise[w] = col[k] * m.weight[w];
    }

    for (std::size_t i = 0; i < m.band_in_watch.size(); ++i)
        m.band_tmp[i] = m.noise[m.band_in_watch[i]];
    const double ref = exact_median(m.band_tmp.data(), m.band_tmp.size());
    if (!(ref > 0.0)) return false;

    const double limit = m.excess * ref;
    int n_over = 0;
    double min_gb = 1.0;
    std::size_t top_w = 0;
    double top_noise = m.noise[0];
    for (std::size_t w = 0; w < m.n_watch; ++w) {
        const double nw = m.noise[w];
        if (nw > top_noise) {
            top_noise = nw;
            top_w = w;
        }
        if (nw > limit) {
            ++n_over;
            const double gval = std::max(std::sqrt(limit / std::max(nw, kPsdTiny)), m.floor_gain);
            m.gb[w] = gval;
            if (gval < min_gb) min_gb = gval;
        } else {
            m.gb[w] = 1.0;
        }
    }
    if (n_over == 0) return false;

    g_out.assign(m.n_bins, 1.0);
    for (std::size_t w = 0; w < m.n_watch; ++w) g_out[m.watch_bins[w]] = m.gb[w];
    thread_local std::vector<double> gw;
    for (std::int64_t step = 0; step < m.widen; ++step) {
        gw = g_out;
        for (std::size_t b = 1; b + 1 < m.n_bins; ++b)
            g_out[b] = std::min(gw[b], std::min(gw[b - 1], gw[b + 1]));
    }
    active_bins = n_over;
    max_attenuation_db = -20.0 * std::log10(min_gb);
    peak_hz = m.watch_freqs[top_w];
    peak_excess_db = 10.0 * std::log10(top_noise / ref);
    return true;
}

void InterferenceFilter::apply(const double* x, std::size_t n, const double* gains_in, double* y) {
    if (n == 0) return;
    Impl& m = *impl_;
    const std::size_t nfft_u = static_cast<std::size_t>(m.nfft);
    const std::size_t half_u = static_cast<std::size_t>(m.half);
    for (std::size_t b = 0; b < m.n_bins; ++b) m.gspec[b] = std::complex<double>(gains_in[b], 0.0);
    irfft(m.gspec.data(), nfft_u, m.fir_h.data());
    // fftshift + Hann taper
    for (std::size_t i = 0; i < half_u; ++i) std::swap(m.fir_h[i], m.fir_h[i + half_u]);
    for (std::size_t i = 0; i < nfft_u; ++i) m.fir_h[i] *= m.taper[i];

    const std::size_t size = next_pow2(n + nfft_u - 1);
    const std::size_t bins = (size >> 1) + 1;
    m.sx.resize(bins);
    m.sh.resize(bins);
    m.yfull.resize(size);
    rfft(x, n, size, m.sx.data());
    rfft(m.fir_h.data(), nfft_u, size, m.sh.data());
    for (std::size_t i = 0; i < bins; ++i) m.sx[i] *= m.sh[i];
    irfft(m.sx.data(), size, m.yfull.data());
    std::memcpy(y, m.yfull.data() + half_u, n * sizeof(double));
}

}  // namespace advtg::dsp
