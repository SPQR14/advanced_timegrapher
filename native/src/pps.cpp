// Native streaming GPS PPS detector (exact C++20 counterpart of src/adv_tg/core/pps.py).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <deque>
#include <stdexcept>
#include <vector>

#include "advtg_bits.hpp"
#include "advtg_detector.hpp"
#include "advtg_dsp.hpp"

namespace advtg::core {
namespace {

inline constexpr double kLowpassHz = 9000.0;
inline constexpr double kTriggerSigma = 30.0;
inline constexpr double kQuietMs = 0.5;
inline constexpr double kBaselineMs = 60.0;
inline constexpr double kBaselineGapMs = 10.0;
inline constexpr double kMinSnrDb = 26.0;
inline constexpr double kConfirmMs = 20.0;
inline constexpr double kHoldoffS = 0.2;
inline constexpr double kNoiseBlockS = 0.1;
inline constexpr double kNoiseHistoryS = 2.0;

inline std::int64_t floor_div(std::int64_t a, std::int64_t b) {
    std::int64_t q = a / b;
    std::int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) --q;
    return q;
}

struct NoiseBlock {
    std::int64_t end = 0;
    double dc = 0.0;
    double sigma = kTiny;
};

}  // namespace

struct PpsDetector::Impl {
    double fs = 48000.0;
    double threshold_sigma = 8.0;
    std::int64_t quiet = 2;
    std::int64_t base = 1;
    std::int64_t gap = 1;
    std::int64_t confirm = 1;
    std::int64_t holdoff = 1;
    std::int64_t block = 1;
    double min_ratio = 1.0;
    std::int64_t history = 1;
    std::size_t n_hist = 1;

    std::vector<double> fir;
    std::size_t delay = 0;

    std::int64_t in_next = 0;
    bool has_tail = false;
    AlignedBuffer<double> tail;
    AlignedBuffer<double> ext;
    AlignedBuffer<double> y_tmp;

    AlignedBuffer<double> buf;
    std::int64_t buf_start = 0;
    std::int64_t scan = 0;
    std::deque<NoiseBlock> blocks;
    std::int64_t next_block = 0;
    std::int64_t rejected = 0;

    AlignedBuffer<double> scratch_r;

    void init(double sample_rate, double thr_sig) {
        if (!(sample_rate > 0.0)) throw std::invalid_argument("sample_rate debe ser positivo");
        if (!(thr_sig > 0.0)) throw std::invalid_argument("threshold_sigma debe ser positivo");
        fs = sample_rate;
        threshold_sigma = thr_sig;
        const double ms = fs / 1000.0;
        quiet = std::max<std::int64_t>(2, static_cast<std::int64_t>(std::llrint(kQuietMs * ms)));
        base = static_cast<std::int64_t>(std::llrint(kBaselineMs * ms));
        gap = static_cast<std::int64_t>(std::llrint(kBaselineGapMs * ms));
        confirm = static_cast<std::int64_t>(std::llrint(kConfirmMs * ms));
        holdoff = static_cast<std::int64_t>(std::llrint(kHoldoffS * fs));
        block = static_cast<std::int64_t>(std::llrint(kNoiseBlockS * fs));
        min_ratio = std::pow(10.0, kMinSnrDb / 20.0);
        history = gap + base + quiet + 2;
        n_hist = std::max<std::size_t>(1, static_cast<std::size_t>(std::llrint(kNoiseHistoryS / kNoiseBlockS)));
        fir = dsp::lowpass_fir(fs, kLowpassHz);
        delay = (fir.size() - 1) / 2;
        reset(0);
    }

    void reset(std::int64_t start_index) {
        in_next = start_index;
        has_tail = false;
        tail.clear();
        const std::int64_t start = in_next - static_cast<std::int64_t>(delay);
        buf.clear();
        buf_start = start;
        scan = start;
        blocks.clear();
        next_block = (floor_div(start, block) + 1) * block;
        rejected = 0;
    }

    void update_noise() {
        const std::int64_t end = buf_start + static_cast<std::int64_t>(buf.size());
        while (next_block <= end) {
            const std::int64_t a = next_block - block;
            if (a >= buf_start) {
                const double* b = buf.data() + (a - buf_start);
                double med = 0.0, sg = 0.0;
                thread_local AlignedBuffer<double> scratch;
                scratch.assign(b, static_cast<std::size_t>(block));
                med = exact_median(scratch.data(), static_cast<std::size_t>(block));
                scratch.assign(b, static_cast<std::size_t>(block));
                sg = kMadToSigma * exact_median_abs_dev(scratch.data(), static_cast<std::size_t>(block), med);
                blocks.push_back({next_block, med, sg});
            }
            next_block += block;
        }
    }

    bool get_noise(std::int64_t at, double& dc_out, double& sig_out) const {
        thread_local std::vector<NoiseBlock> done;
        done.clear();
        for (const auto& b : blocks) {
            if (b.end <= at) done.push_back(b);
        }
        if (done.empty()) return false;
        const std::size_t take = std::min(n_hist, done.size());
        const std::size_t off = done.size() - take;
        thread_local AlignedBuffer<double> dcs, sgs;
        dcs.resize(take);
        sgs.resize(take);
        for (std::size_t i = 0; i < take; ++i) {
            dcs[i] = done[off + i].dc;
            sgs[i] = done[off + i].sigma;
        }
        dc_out = exact_median(dcs.data(), take);
        sig_out = std::max(exact_median(sgs.data(), take), kTiny);
        return true;
    }

    bool measure(std::int64_t trig, NativePpsPulse& out) {
        const std::int64_t b0 = trig - gap - base;
        if (b0 < buf_start) return false;
        const double* bptr = buf.data() + (b0 - buf_start);
        double mu = 0.0, sg = 0.0;
        dsp::robust_level(bptr, static_cast<std::size_t>(trig - gap - b0), mu, sg);
        const double* cptr = buf.data() + (trig - buf_start);
        double peak = 0.0;
        for (std::int64_t i = 0; i < confirm; ++i) peak = std::max(peak, std::fabs(cptr[i] - mu));
        if (peak < min_ratio * sg) return false;
        const std::int64_t lo = trig - gap;
        const std::size_t rn = static_cast<std::size_t>(trig + 1 - lo);
        scratch_r.resize(rn);
        const double* rsrc = buf.data() + (lo - buf_start);
        for (std::size_t i = 0; i < rn; ++i) scratch_r[i] = std::fabs(rsrc[i] - mu);
        const double j = dsp::onset_after_quiet(scratch_r.data(), rn, threshold_sigma * sg, quiet);
        if (is_nan(j)) return false;
        out.index = static_cast<double>(lo) + j;
        out.snr_db = 20.0 * std::log10(peak / sg);
        out.peak = peak;
        return true;
    }

    void trim() {
        const std::int64_t end = buf_start + static_cast<std::int64_t>(buf.size());
        const std::int64_t keep_from = std::min(std::min(scan, end) - history, next_block - block);
        const std::int64_t cut = keep_from - buf_start;
        if (cut > 0) {
            const std::size_t c = static_cast<std::size_t>(std::min<std::int64_t>(
                cut, static_cast<std::int64_t>(buf.size())));
            const std::size_t rem = buf.size() - c;
            if (rem) std::memmove(buf.data(), buf.data() + c, rem * sizeof(double));
            buf.resize(rem, true);
            buf_start += static_cast<std::int64_t>(c);
        }
        while (blocks.size() > n_hist && blocks[n_hist].end <= scan) blocks.pop_front();
    }

    void process(const double* x, std::size_t n, std::int64_t start_index,
                 std::vector<NativePpsPulse>& out) {
        if (start_index != in_next) reset(start_index);
        if (n == 0) return;
        in_next += static_cast<std::int64_t>(n);

        const std::size_t tlen = fir.size() - 1;
        if (tlen == 0) {
            buf.append(x, n);
        } else {
            if (!has_tail) {
                tail.resize(tlen);
                for (std::size_t i = 0; i < tlen; ++i) tail[i] = x[0];
                has_tail = true;
            }
            ext.resize(tlen + n);
            std::memcpy(ext.data(), tail.data(), tlen * sizeof(double));
            std::memcpy(ext.data() + tlen, x, n * sizeof(double));
            std::memcpy(tail.data(), ext.data() + n, tlen * sizeof(double));
            y_tmp.resize(n);
            dsp::convolve_valid(ext.data(), tlen + n, fir.data(), fir.size(), y_tmp.data());
            buf.append(y_tmp.data(), n);
        }

        update_noise();
        const std::int64_t end = buf_start + static_cast<std::int64_t>(buf.size());
        while (true) {
            if (scan >= end) break;
            double dc = 0.0, sigma = 0.0;
            if (!get_noise(scan, dc, sigma)) {
                if (blocks.empty()) break;
                scan = blocks.front().end;
                continue;
            }
            const std::int64_t stop = std::min(end, (floor_div(scan, block) + 1) * block);
            const double thr = kTriggerSigma * sigma;
            std::int64_t trig = -1;
            const double* b = buf.data();
            for (std::int64_t i = scan; i < stop; ++i) {
                if (std::fabs(b[i - buf_start] - dc) >= thr) {
                    trig = i;
                    break;
                }
            }
            if (trig < 0) {
                scan = stop;
                continue;
            }
            if (trig + confirm > end) {
                scan = trig;
                break;
            }
            NativePpsPulse p;
            if (!measure(trig, p)) {
                ++rejected;
                scan = trig + confirm;
                continue;
            }
            scan = trig + holdoff;
            out.push_back(p);
        }
        trim();
    }
};

PpsDetector::PpsDetector(double sample_rate, double threshold_sigma) : impl_(new Impl) {
    try {
        impl_->init(sample_rate, threshold_sigma);
    } catch (...) {
        delete impl_;
        throw;
    }
}

PpsDetector::~PpsDetector() { delete impl_; }
void PpsDetector::reset(std::int64_t start_index) { impl_->reset(start_index); }
void PpsDetector::process(const double* chunk, std::size_t n, std::int64_t start_index,
                          std::vector<NativePpsPulse>& out) {
    impl_->process(chunk, n, start_index, out);
}
std::int64_t PpsDetector::rejected() const { return impl_->rejected; }

}  // namespace advtg::core
