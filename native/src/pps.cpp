// Native streaming GPS PPS detector (exact C++20 counterpart of src/adv_tg/core/pps.py).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <deque>
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "advtg.h"
#include "advtg_bits.hpp"
#include "advtg_detector.hpp"
#include "advtg_dsp.hpp"
#include "advtg_metrics.hpp"

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

// ------------------------------------------------------------------ PpsCalibrator (core/pps.py)
namespace {

inline constexpr double kJumpMs = 2.0;
inline constexpr std::size_t kJumpConfirm = 3;
inline constexpr std::size_t kMinPulses = 10;
inline constexpr std::size_t kBatchPulses = 30;
inline constexpr std::size_t kMinBatches = 4;
inline constexpr double kRefSigmaPpm = 0.01;
inline constexpr double kReadySpanS = 120.0;
inline constexpr double kReadySigmaPpm = 0.05;
inline constexpr double kUncalibratedSigmaPpm = 30.0;

double batch_excess(const std::vector<double>& res, double sigma) {
    const std::size_t b = res.size() / kBatchPulses;
    if (b < kMinBatches) return 1.0;
    std::vector<double> means(b, 0.0);
    for (std::size_t i = 0; i < b; ++i) {
        double s = 0.0;
        for (std::size_t j = 0; j < kBatchPulses; ++j) s += res[i * kBatchPulses + j];
        means[i] = s / static_cast<double>(kBatchPulses);
    }
    double mmean = 0.0;
    for (double m : means) mmean += m;
    mmean /= static_cast<double>(b);
    double ss = 0.0;
    for (double m : means) {
        const double d = m - mmean;
        ss += d * d;
    }
    const double observed = ss / static_cast<double>(b - 2);
    const double expected = (sigma * sigma) / static_cast<double>(kBatchPulses);
    return std::max(1.0, std::sqrt(observed / expected));
}

void format_cal_note(const NativeAdcDrift& d, std::size_t min_ticks, char* out, std::size_t cap) {
    out[0] = '\0';
    if (d.note_kind == ADVTG_DRIFT_NOTE_FEW_POINTS) {
        std::snprintf(out, cap, "pocos pulsos (%lld < %zu)",
                      static_cast<long long>(d.n_points), min_ticks);
    } else if (d.note_kind == ADVTG_DRIFT_NOTE_OUTLIERS) {
        std::snprintf(out, cap, "demasiados pulsos atípicos");
    }
}

}  // namespace

}  // namespace advtg::core

struct advtg_pps_detector {
    std::unique_ptr<advtg::core::PpsDetector> impl;
    std::vector<advtg::core::NativePpsPulse> last_pulses;
};

struct advtg_pps_calibrator {
    double fs = 48000.0;
    double period = 48000.0;
    double reference_sigma_ppm = advtg::core::kRefSigmaPpm;
    double jump = 0.0;
    std::vector<double> seg_t;
    std::vector<std::int64_t> seg_k;
    std::vector<double> cand_t;
    std::vector<std::int64_t> cand_k;
    std::int64_t pulses = 0;
    std::int64_t jumps = 0;
    std::int64_t rejected = 0;
    std::vector<double> last_t_s;
    std::vector<double> last_res_us;

    void reset() {
        seg_t.clear();
        seg_k.clear();
        cand_t.clear();
        cand_k.clear();
        pulses = 0;
        jumps = 0;
        rejected = 0;
        last_t_s.clear();
        last_res_us.clear();
    }

    std::pair<std::int64_t, double> predict_err(double t, double t_ref, double k_drift) const {
        const std::int64_t n = static_cast<std::int64_t>(std::llrint((t - t_ref) / period));
        return {n, (t - t_ref) - static_cast<double>(n) * (period + k_drift)};
    }

    double drift_per_pulse() const {
        if (seg_t.size() < 2U) return 0.0;
        const std::int64_t dk = seg_k.back() - seg_k.front();
        return dk ? ((seg_t.back() - seg_t.front()) / static_cast<double>(dk) - period) : 0.0;
    }

    void add(const double* indices, std::size_t n_idx) {
        for (std::size_t i = 0; i < n_idx; ++i) {
            const double t = indices[i];
            if (!std::isfinite(t)) continue;
            ++pulses;
            if (seg_t.empty()) {
                seg_t = {t};
                seg_k = {0};
                continue;
            }
            const auto [n, err] = predict_err(t, seg_t.back(), drift_per_pulse());
            if (n >= 1 && std::fabs(err) <= jump) {
                seg_t.push_back(t);
                seg_k.push_back(seg_k.back() + n);
                rejected += static_cast<std::int64_t>(cand_t.size());
                cand_t.clear();
                cand_k.clear();
                continue;
            }
            if (!cand_t.empty()) {
                const auto [m, e2] = predict_err(t, cand_t.back(), 0.0);
                if (m >= 1 && std::fabs(e2) <= jump) {
                    cand_t.push_back(t);
                    cand_k.push_back(cand_k.back() + m);
                } else {
                    rejected += static_cast<std::int64_t>(cand_t.size());
                    cand_t = {t};
                    cand_k = {0};
                }
            } else {
                cand_t = {t};
                cand_k = {0};
            }
            if (cand_t.size() >= advtg::core::kJumpConfirm) {
                if (seg_t.size() < advtg::core::kJumpConfirm) {
                    rejected += static_cast<std::int64_t>(seg_t.size());
                } else {
                    ++jumps;
                }
                seg_t = std::move(cand_t);
                seg_k = std::move(cand_k);
                cand_t.clear();
                cand_k.clear();
            }
        }
    }

    void estimate(advtg_pps_estimate& out) {
        std::memset(&out, 0, sizeof out);
        out.pulses = pulses;
        out.jumps = jumps;
        out.rejected = rejected;
        out.used = static_cast<std::int64_t>(seg_t.size());
        out.excess = 1.0;
        out.jitter_us = advtg::kNaN;
        last_t_s.clear();
        last_res_us.clear();
        if (seg_t.size() < advtg::core::kMinPulses) {
            out.drift.ppm = 0.0;
            out.drift.sigma_ppm = 0.0;
            out.drift.systematic_ppm = advtg::core::kUncalibratedSigmaPpm;
            out.drift.level = ADVTG_CLOCK_UNCALIBRATED;
            out.drift.method = 0;
            out.drift.note_kind = ADVTG_DRIFT_NOTE_FEW_POINTS;
            out.drift.n_points = static_cast<std::int64_t>(seg_t.size());
            out.drift.residual_us = advtg::kNaN;
            std::snprintf(out.note, sizeof out.note, "pocos pulsos (%zu < %zu)",
                          seg_t.size(), advtg::core::kMinPulses);
            return;
        }
        const double nominal_s = period / fs;
        auto drift = advtg::core::drift_from_ticks_cpp(
            seg_t.data(), seg_t.size(), fs, nominal_s, 0.0, reference_sigma_ppm, 5.0,
            static_cast<int>(advtg::core::kMinPulses));
        const std::int64_t k0 = seg_k.front();
        const std::int64_t klast = seg_k.back();
        const double span_s = static_cast<double>(klast - k0) * nominal_s;
        const std::int64_t missing = (klast - k0 + 1) - static_cast<std::int64_t>(seg_t.size());

        const std::size_t n = seg_t.size();
        double kmean = 0.0, tmean = 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            kmean += static_cast<double>(seg_k[i]);
            tmean += seg_t[i];
        }
        kmean /= static_cast<double>(n);
        tmean /= static_cast<double>(n);
        double kckc = 0.0, kctc = 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            const double kc = static_cast<double>(seg_k[i]) - kmean;
            kckc += kc * kc;
            kctc += kc * (seg_t[i] - tmean);
        }
        const double slope = kctc / std::max(kckc, advtg::kTiny);
        last_t_s.resize(n);
        last_res_us.resize(n);
        for (std::size_t i = 0; i < n; ++i) {
            const double kc = static_cast<double>(seg_k[i]) - kmean;
            last_t_s[i] = static_cast<double>(seg_k[i] - k0) * nominal_s;
            last_res_us[i] = (seg_t[i] - tmean - slope * kc) / fs * 1e6;
        }
        std::vector<double> tmp = last_res_us;
        const double med = advtg::exact_median(tmp.data(), tmp.size());
        tmp = last_res_us;
        const double jitter = advtg::kMadToSigma * advtg::exact_median_abs_dev(tmp.data(), tmp.size(), med);

        double rmean = 0.0;
        for (double r : last_res_us) rmean += r;
        rmean /= static_cast<double>(n);
        double rvar = 0.0;
        for (double r : last_res_us) {
            const double d = r - rmean;
            rvar += d * d;
        }
        const double rstd = (n > 2U) ? std::sqrt(rvar / static_cast<double>(n - 2U)) : 0.0;
        const double excess = advtg::core::batch_excess(last_res_us, std::max(rstd, advtg::kTiny));

        out.missing = missing;
        out.span_s = span_s;
        out.jitter_us = jitter;
        if (drift.level != ADVTG_CLOCK_EXTERNAL) {
            drift.systematic_ppm = advtg::core::kUncalibratedSigmaPpm;
            drift.level = ADVTG_CLOCK_UNCALIBRATED;
            drift.method = 0;
            std::memcpy(&out.drift, &drift, sizeof drift);
            advtg::core::format_cal_note(drift, advtg::core::kMinPulses, out.note, sizeof out.note);
            return;
        }
        drift.method = 4;  // "pps"
        drift.sigma_ppm *= excess;
        const double unc = std::hypot(drift.sigma_ppm, drift.systematic_ppm);
        out.excess = excess;
        out.valid = 1;
        out.ready = (span_s >= advtg::core::kReadySpanS && unc <= advtg::core::kReadySigmaPpm) ? 1 : 0;
        std::memcpy(&out.drift, &drift, sizeof drift);
    }
};

extern "C" {

static_assert(sizeof(advtg_pps_pulse) == sizeof(advtg::core::NativePpsPulse));

ADVTG_C_API advtg_pps_detector* advtg_pps_detector_create(double sample_rate,
                                                          double threshold_sigma) {
    try {
        auto d = std::make_unique<advtg_pps_detector>();
        d->impl = std::make_unique<advtg::core::PpsDetector>(sample_rate, threshold_sigma);
        return d.release();
    } catch (...) {
        return nullptr;
    }
}

ADVTG_C_API void advtg_pps_detector_destroy(advtg_pps_detector* d) { delete d; }

ADVTG_C_API void advtg_pps_detector_reset(advtg_pps_detector* d, int64_t start_index) {
    if (d && d->impl) {
        d->last_pulses.clear();
        d->impl->reset(start_index);
    }
}

ADVTG_C_API int64_t advtg_pps_detector_process(advtg_pps_detector* d, const double* chunk,
                                               size_t n, int64_t start_index,
                                               advtg_pps_pulse* out, int64_t cap) {
    if (!d || !d->impl || (!chunk && n > 0)) return ADVTG_ERR_INVALID;
    try {
        d->last_pulses.clear();
        d->impl->process(chunk, n, start_index, d->last_pulses);
        const auto m = static_cast<int64_t>(d->last_pulses.size());
        if (out && cap > 0) {
            const auto k = static_cast<std::size_t>(std::min(m, cap));
            if (k) std::memcpy(out, d->last_pulses.data(), k * sizeof(advtg_pps_pulse));
        }
        return m;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int64_t advtg_pps_detector_last_pulses(const advtg_pps_detector* d,
                                                   advtg_pps_pulse* out, int64_t cap) {
    if (!d) return ADVTG_ERR_INVALID;
    const auto m = static_cast<int64_t>(d->last_pulses.size());
    if (out && cap > 0) {
        const auto k = static_cast<std::size_t>(std::min(m, cap));
        if (k) std::memcpy(out, d->last_pulses.data(), k * sizeof(advtg_pps_pulse));
    }
    return m;
}

ADVTG_C_API int64_t advtg_pps_detector_rejected(const advtg_pps_detector* d) {
    return (d && d->impl) ? d->impl->rejected() : 0;
}

ADVTG_C_API advtg_pps_calibrator* advtg_pps_calibrator_create(double sample_rate,
                                                              double nominal_period_s,
                                                              double reference_sigma_ppm) {
    if (!(sample_rate > 0.0 && nominal_period_s > 0.0)) return nullptr;
    try {
        auto c = std::make_unique<advtg_pps_calibrator>();
        c->fs = sample_rate;
        c->period = nominal_period_s * sample_rate;
        c->reference_sigma_ppm = reference_sigma_ppm;
        c->jump = advtg::core::kJumpMs * 1e-3 * sample_rate;
        return c.release();
    } catch (...) {
        return nullptr;
    }
}

ADVTG_C_API void advtg_pps_calibrator_destroy(advtg_pps_calibrator* c) { delete c; }

ADVTG_C_API void advtg_pps_calibrator_reset(advtg_pps_calibrator* c) {
    if (c) c->reset();
}

ADVTG_C_API int advtg_pps_calibrator_add(advtg_pps_calibrator* c, const double* indices,
                                         size_t n) {
    if (!c || (!indices && n > 0)) return ADVTG_ERR_INVALID;
    try {
        c->add(indices, n);
        return ADVTG_OK;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int advtg_pps_calibrator_estimate(advtg_pps_calibrator* c, advtg_pps_estimate* out) {
    if (!c || !out) return ADVTG_ERR_INVALID;
    try {
        c->estimate(*out);
        return ADVTG_OK;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

ADVTG_C_API int64_t advtg_pps_calibrator_residuals(const advtg_pps_calibrator* c, double* t_s,
                                                   double* residual_us, int64_t cap) {
    if (!c) return ADVTG_ERR_INVALID;
    const auto n = static_cast<int64_t>(c->last_t_s.size());
    if (cap > 0) {
        const auto m = static_cast<std::size_t>(std::min(n, cap));
        if (t_s && m) std::memcpy(t_s, c->last_t_s.data(), m * sizeof(double));
        if (residual_us && m) std::memcpy(residual_us, c->last_res_us.data(), m * sizeof(double));
    }
    return n;
}

ADVTG_C_API int advtg_pps_calibrate_samples(const double* samples, size_t n, double sample_rate,
                                            double chunk_s, advtg_pps_estimate* out) {
    if (!out || (!samples && n > 0) || !(sample_rate > 0.0)) return ADVTG_ERR_INVALID;
    try {
        advtg::core::PpsDetector det(sample_rate, 8.0);
        advtg_pps_calibrator cal;
        cal.fs = sample_rate;
        cal.period = sample_rate;
        cal.reference_sigma_ppm = advtg::core::kRefSigmaPpm;
        cal.jump = advtg::core::kJumpMs * 1e-3 * sample_rate;
        const double cs = (chunk_s > 0.0) ? chunk_s : 1.0;
        const std::size_t step =
            std::max<std::size_t>(1, static_cast<std::size_t>(cs * sample_rate));
        std::vector<advtg::core::NativePpsPulse> pulses;
        std::vector<double> idx;
        for (std::size_t a = 0; a < n; a += step) {
            const std::size_t m = std::min(step, n - a);
            pulses.clear();
            det.process(samples + a, m, static_cast<std::int64_t>(a), pulses);
            idx.resize(pulses.size());
            for (std::size_t i = 0; i < pulses.size(); ++i) idx[i] = pulses[i].index;
            cal.add(idx.data(), idx.size());
        }
        cal.estimate(*out);
        return ADVTG_OK;
    } catch (...) {
        return ADVTG_ERR_INVALID;
    }
}

}  // extern "C"
