// Fixed-block incremental normalizer (exact counterpart of src/adv_tg/dsp/normalizer.py).
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "advtg_dsp.hpp"

namespace advtg::dsp {

struct AudioNormalizer::Impl {
    NormalizerConfig cfg;
    std::int64_t block = 1;
    std::int64_t hold_blocks = 0;
    double decay = 1.0;
    double max_gain = 1.0;
    double floor_val = 0.0;

    std::vector<double> pending;
    bool has_pending_start = false;
    std::int64_t pending_start = 0;
    double follower = 0.0;
    std::int64_t hold = 0;
    bool has_gain = false;
    double gain = 1.0;

    double gain_for(double peak) {
        if (peak >= follower) {
            follower = peak;
            hold = hold_blocks;
        } else if (hold > 0) {
            --hold;
        } else {
            follower *= decay;
        }
        return cfg.target / std::max(follower, floor_val);
    }

    Segment emit_pending(std::int64_t& clipped_out) {
        Segment seg;
        seg.start = pending_start;
        pending_start += static_cast<std::int64_t>(pending.size());
        const std::size_t n = pending.size();
        double peak = 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            const double a = std::fabs(pending[i]);
            if (a > peak) peak = a;
            if (a >= cfg.clip_level) ++clipped_out;
        }
        const double g_new = gain_for(peak);
        const double g_old = gain;
        const bool first = !has_gain;
        has_gain = true;
        gain = g_new;
        seg.samples = std::move(pending);
        pending.clear();
        if (first || g_new <= g_old) {
            for (double& v : seg.samples) v *= g_new;
        } else {
            const double dn = static_cast<double>(n);
            const double diff = g_new - g_old;
            for (std::size_t i = 0; i < n; ++i) {
                const double ramp = g_old + diff * (static_cast<double>(i + 1) / dn);
                seg.samples[i] *= ramp;
            }
        }
        return seg;
    }
};

AudioNormalizer::AudioNormalizer(const NormalizerConfig& cfg) : impl_(new Impl) {
    if (!(std::isfinite(cfg.sample_rate) && cfg.sample_rate > 0.0)) {
        delete impl_;
        throw std::invalid_argument("sample_rate no válido");
    }
    if (!(cfg.target > 0.0 && cfg.target < 1.0)) {
        delete impl_;
        throw std::invalid_argument("target fuera de (0, 1)");
    }
    if (!(std::isfinite(cfg.block_s) && cfg.block_s > 0.0)) {
        delete impl_;
        throw std::invalid_argument("block_s debe ser positivo");
    }
    if (!(cfg.release_s > 0.0)) {
        delete impl_;
        throw std::invalid_argument("release_s debe ser positivo");
    }
    if (!(std::isfinite(cfg.hold_s) && cfg.hold_s >= 0.0)) {
        delete impl_;
        throw std::invalid_argument("hold_s debe ser >= 0");
    }
    if (!(std::isfinite(cfg.max_gain_db) && cfg.max_gain_db >= 0.0)) {
        delete impl_;
        throw std::invalid_argument("max_gain_db debe ser >= 0");
    }
    if (!(cfg.clip_level > 0.0 && cfg.clip_level <= 1.0)) {
        delete impl_;
        throw std::invalid_argument("clip_level fuera de (0, 1]");
    }
    if (!(std::isfinite(cfg.min_follower) && cfg.min_follower >= 0.0)) {
        delete impl_;
        throw std::invalid_argument("min_follower debe ser >= 0");
    }
    impl_->cfg = cfg;
    const double fs = cfg.sample_rate;
    impl_->block = std::max<std::int64_t>(1, static_cast<std::int64_t>(std::llround(cfg.block_s * fs)));
    const double block_dur = static_cast<double>(impl_->block) / fs;
    impl_->hold_blocks = static_cast<std::int64_t>(std::ceil(cfg.hold_s / block_dur - 1e-9));
    impl_->decay = std::isinf(cfg.release_s) ? 1.0 : std::exp(-block_dur / cfg.release_s);
    impl_->max_gain = std::pow(10.0, cfg.max_gain_db / 20.0);
    impl_->floor_val = std::max(cfg.target / impl_->max_gain, cfg.min_follower);
    impl_->pending.reserve(static_cast<std::size_t>(impl_->block));
    reset();
}

AudioNormalizer::~AudioNormalizer() { delete impl_; }

void AudioNormalizer::reset() {
    impl_->pending.clear();
    impl_->has_pending_start = false;
    impl_->pending_start = 0;
    impl_->follower = 0.0;
    impl_->hold = 0;
    impl_->has_gain = false;
    impl_->gain = 1.0;
    clipped_samples = 0;
    samples_in = 0;
}

double AudioNormalizer::gain() const { return impl_->has_gain ? impl_->gain : 1.0; }
double AudioNormalizer::gain_db() const { return 20.0 * std::log10(gain()); }
bool AudioNormalizer::has_next_index() const { return impl_->has_pending_start; }
std::int64_t AudioNormalizer::next_index() const {
    return impl_->pending_start + static_cast<std::int64_t>(impl_->pending.size());
}

void AudioNormalizer::process(const double* chunk, std::size_t n, std::int64_t start_index,
                              std::vector<Segment>& out) {
    Impl& m = *impl_;
    if (m.has_pending_start) {
        const std::int64_t expected = next_index();
        if (start_index < expected)
            throw std::invalid_argument("el bloque se solapa con muestras ya recibidas");
        if (start_index > expected && !m.pending.empty())
            out.push_back(m.emit_pending(clipped_samples));
    }
    if (m.pending.empty()) {
        m.has_pending_start = true;
        m.pending_start = start_index;
    }
    if (n == 0) return;
    samples_in += static_cast<std::int64_t>(n);
    const std::int64_t B = m.block;
    std::size_t pos = 0;
    while (pos < n) {
        const std::int64_t abs_pos = start_index + static_cast<std::int64_t>(pos);
        std::int64_t mod = abs_pos % B;
        if (mod < 0) mod += B;
        const std::size_t room = static_cast<std::size_t>(B - mod);
        const std::size_t take = std::min(room, n - pos);
        m.pending.insert(m.pending.end(), chunk + pos, chunk + pos + take);
        pos += take;
        if (take == room) out.push_back(m.emit_pending(clipped_samples));
    }
}

void AudioNormalizer::flush(std::vector<Segment>& out) {
    Impl& m = *impl_;
    if (!m.pending.empty() && m.has_pending_start) out.push_back(m.emit_pending(clipped_samples));
}

}  // namespace advtg::dsp
