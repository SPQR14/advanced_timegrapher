// Synthetic watch signal generator and mock telemetry model (mock/generator.py,
// tests/synth.py).
#include "advtg.h"
#include "advtg_bits.hpp"
#include "advtg_dsp.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>
#include <numbers>
#include <vector>

namespace {

inline constexpr double kSecondsPerDay = 86400.0;
inline constexpr double kPpmToSpd = kSecondsPerDay * 1e-6;
inline constexpr double kTwoPi = 6.283185307179586476925286766559005768;

struct MockBurst {
    double frac;
    double level;
    double carrier;
    double tau;
};

constexpr MockBurst kMockBursts[3] = {
    {0.00, 0.45, 7200.0, 0.00025},
    {0.35, 0.30, 5200.0, 0.00035},
    {1.00, 1.00, 4300.0, 0.00090},
};
constexpr double kTockGain = 0.85;
constexpr double kTockTone = 0.96;

// Deterministic SplitMix64 + xoshiro256** + Box-Muller PRNG for C++ simulation/tests.
struct Rng {
    std::uint64_t s[4]{};
    bool has_spare = false;
    double spare = 0.0;

    explicit Rng(std::uint64_t seed) {
        std::uint64_t x = seed + 0x9e3779b97f4a7c15ULL;
        for (int i = 0; i < 4; ++i) {
            x += 0x9e3779b97f4a7c15ULL;
            std::uint64_t z = x;
            z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
            z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
            s[i] = z ^ (z >> 31);
        }
    }

    static std::uint64_t rotl(std::uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

    std::uint64_t next_u64() {
        const std::uint64_t result = rotl(s[1] * 5, 7) * 9;
        const std::uint64_t t = s[1] << 17;
        s[2] ^= s[0];
        s[3] ^= s[1];
        s[1] ^= s[2];
        s[0] ^= s[3];
        s[2] ^= t;
        s[3] = rotl(s[3], 45);
        return result;
    }

    double uniform01() {
        return static_cast<double>(next_u64() >> 11) * (1.0 / 9007199254740992.0);
    }

    double uniform(double lo, double hi) { return lo + (hi - lo) * uniform01(); }

    double normal(double mean, double sigma) {
        if (sigma == 0.0) return mean;
        if (has_spare) {
            has_spare = false;
            return mean + sigma * spare;
        }
        double u1 = 0.0, u2 = 0.0;
        do {
            u1 = uniform01();
        } while (u1 <= 1e-15);
        u2 = uniform01();
        const double r = std::sqrt(-2.0 * std::log(u1));
        const double th = kTwoPi * u2;
        spare = r * std::sin(th);
        has_spare = true;
        return mean + sigma * (r * std::cos(th));
    }
};

struct SynthMode {
    double f0;
    double amp;
    double decay;
};

void add_synth_burst(double* out, std::size_t n, double fs, double t0, double level,
                     const SynthMode* modes, std::size_t n_modes, double duration_s) {
    const std::int64_t n0 = std::max<std::int64_t>(0, static_cast<std::int64_t>(std::ceil(t0 * fs)));
    const std::int64_t n1 = std::min<std::int64_t>(
        static_cast<std::int64_t>(n),
        static_cast<std::int64_t>(std::floor((t0 + duration_s) * fs)) + 1);
    if (n1 <= n0) return;
    for (std::int64_t i = n0; i < n1; ++i) {
        const double tau = static_cast<double>(i) / fs - t0;
        double y = 0.0;
        for (std::size_t m = 0; m < n_modes; ++m) {
            y += modes[m].amp * std::exp(-tau / modes[m].decay) *
                 std::sin(kTwoPi * modes[m].f0 * tau);
        }
        out[i] += level * y;
    }
}

double band_rms(const std::vector<double>& x, double fs) {
    if (x.empty()) return 1.0;
    // Use power-of-2 chunk up to x.size() for fast in-band RMS estimate (1 kHz .. min(15k, 0.45 fs))
    const std::size_t nfft = static_cast<std::size_t>(advtg::next_pow2(x.size()));
    std::vector<std::complex<double>> spec(nfft / 2 + 1);
    advtg::dsp::rfft(x.data(), x.size(), nfft, spec.data());
    const double hi = std::min(15000.0, 0.45 * fs);
    double sum2 = 0.0;
    for (std::size_t k = 0; k < spec.size(); ++k) {
        const double f = static_cast<double>(k) * fs / static_cast<double>(nfft);
        if (f >= 1000.0 && f <= hi) {
            const double re = spec[k].real(), im = spec[k].imag();
            sum2 += re * re + im * im;
        }
    }
    const double rms = std::sqrt(2.0 * sum2) / static_cast<double>(nfft) *
                       std::sqrt(static_cast<double>(nfft) / static_cast<double>(x.size()));
    return rms > 0.0 ? rms : 1.0;
}

}  // namespace

struct advtg_mock_telemetry {
    Rng tel_rng;
    Rng drift_rng;
    double true_drift_ppm;
    double settle_rate_spd;
    double settle_amp_deg;
    double snr_phase;
    double wander_spd;

    explicit advtg_mock_telemetry(std::uint64_t seed)
        : tel_rng(seed ^ 0x123456789abcdef0ULL), drift_rng(seed ^ 0x0fedcba987654321ULL) {
        true_drift_ppm = drift_rng.uniform(-18.0, 18.0);
        settle_rate_spd = tel_rng.normal(0.0, 4.0);
        settle_amp_deg = -std::fabs(tel_rng.normal(25.0, 5.0));
        snr_phase = tel_rng.uniform(0.0, kTwoPi);
        wander_spd = 0.0;
    }
};

extern "C" {

ADVTG_C_API double advtg_mock_beat_delta_t_s(double amplitude_deg, double lift_angle_deg,
                                             int32_t bph) {
    if (bph <= 0) return advtg::kNaN;
    const double f0 = static_cast<double>(bph) / 7200.0;
    const double ratio =
        std::min(0.999, std::max(1e-6, lift_angle_deg / (2.0 * std::max(amplitude_deg, 1.0))));
    return std::asin(ratio) / (std::numbers::pi * f0);
}

ADVTG_C_API double advtg_mock_beat_interval_s(int32_t bph, double rate_spd) {
    if (bph <= 0) return advtg::kNaN;
    return (3600.0 / static_cast<double>(bph)) / (1.0 + rate_spd / kSecondsPerDay);
}

ADVTG_C_API void advtg_mock_add_beat(double* buf, size_t n, double fs, double t_beat_s,
                                     double dt_p3_s, int is_tock) {
    if (!buf || n == 0 || !(fs > 0.0)) return;
    const double gain = is_tock ? kTockGain : 1.0;
    const double tone = is_tock ? kTockTone : 1.0;
    for (const auto& b : kMockBursts) {
        const double t0 = t_beat_s + b.frac * dt_p3_s;
        const std::int64_t lo =
            std::max<std::int64_t>(0, static_cast<std::int64_t>(std::ceil(t0 * fs)));
        const std::int64_t hi = std::min<std::int64_t>(
            static_cast<std::int64_t>(n),
            static_cast<std::int64_t>(std::ceil((t0 + 8.0 * b.tau) * fs)));
        for (std::int64_t i = lo; i < hi; ++i) {
            const double tt = static_cast<double>(i) / fs - t0;
            buf[i] += gain * b.level * std::exp(-tt / b.tau) *
                      std::sin(kTwoPi * b.carrier * tone * tt);
        }
    }
}

ADVTG_C_API int advtg_mock_generate_tick_train(double duration_s, int32_t fs, int32_t bph,
                                               double rate_spd, double beat_error_ms,
                                               double amplitude_deg, double lift_angle_deg,
                                               double snr_db, uint64_t seed, double* out,
                                               size_t cap) {
    if (!out || !(duration_s >= 0.0) || fs <= 0 || bph <= 0) return ADVTG_ERR_INVALID;
    const std::size_t n = static_cast<std::size_t>(std::llrint(duration_s * fs));
    if (cap < n) return ADVTG_ERR_INVALID;
    std::memset(out, 0, n * sizeof(double));
    Rng rng(seed);
    const double period = advtg_mock_beat_interval_s(bph, rate_spd);
    const double be_s = beat_error_ms / 1000.0;
    const double dt_p3 = advtg_mock_beat_delta_t_s(amplitude_deg, lift_angle_deg, bph);
    double t = 0.005;
    int64_t k = 0;
    while (t < duration_s) {
        advtg_mock_add_beat(out, n, static_cast<double>(fs), t + rng.normal(0.0, 15e-6), dt_p3,
                            (k % 2) != 0);
        t += period + ((k % 2 == 0) ? be_s : -be_s);
        ++k;
    }
    const double sig = std::pow(10.0, -snr_db / 20.0);
    for (std::size_t i = 0; i < n; ++i) out[i] += rng.normal(0.0, sig);
    double peak = 0.0;
    for (std::size_t i = 0; i < n; ++i) peak = std::max(peak, std::fabs(out[i]));
    if (peak > 0.0) {
        const double sc = 0.9 / peak;
        for (std::size_t i = 0; i < n; ++i) out[i] *= sc;
    }
    return ADVTG_OK;
}

ADVTG_C_API advtg_mock_telemetry* advtg_mock_telemetry_create(uint64_t seed) {
    try {
        return new advtg_mock_telemetry(seed);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_C_API void advtg_mock_telemetry_destroy(advtg_mock_telemetry* m) { delete m; }

ADVTG_C_API void advtg_mock_telemetry_step(advtg_mock_telemetry* m, double elapsed_s, double dt_s,
                                           double stabilization_s, double base_rate_spd,
                                           double base_beat_error_ms, double base_amplitude_deg,
                                           advtg_mock_telemetry_sample* out) {
    if (!m || !out) return;
    const double tau = std::max(stabilization_s / 3.0, 0.3);
    const double settle = std::exp(-elapsed_s / tau);
    const double noise_gain = 1.0 + 1.5 * settle;
    const double dt = std::max(dt_s, 0.0);
    m->wander_spd += -m->wander_spd * dt / 20.0 + 0.19 * std::sqrt(dt) * m->tel_rng.normal(0.0, 1.0);

    out->rate_spd = base_rate_spd + m->wander_spd + settle * m->settle_rate_spd +
                    m->tel_rng.normal(0.0, 0.35 * noise_gain);
    out->beat_error_ms = std::fabs(base_beat_error_ms + m->tel_rng.normal(0.0, 0.03 * noise_gain));
    out->amplitude_deg =
        base_amplitude_deg + settle * m->settle_amp_deg + m->tel_rng.normal(0.0, 1.5 * noise_gain);
    out->confidence = std::clamp(0.97 - 0.45 * settle + m->tel_rng.normal(0.0, 0.01), 0.0, 1.0);
    out->snr_db = 33.0 + 7.0 * std::sin(kTwoPi * elapsed_s / 17.0 + m->snr_phase) +
                  m->tel_rng.normal(0.0, 0.8) - 6.0 * settle;
}

ADVTG_C_API void advtg_mock_telemetry_get_drift(advtg_mock_telemetry* m, double elapsed_s,
                                                double nominal_fs,
                                                advtg_mock_telemetry_drift* out) {
    if (!m || !out) return;
    const double sigma = 8.0 / std::sqrt(1.0 + std::max(elapsed_s, 0.0)) + 0.2;
    const double ppm = m->true_drift_ppm + m->drift_rng.normal(0.0, sigma);
    out->effective_fs = nominal_fs * (1.0 + ppm * 1e-6);
    out->drift_ppm = ppm;
    out->rate_bias_spd = ppm * kPpmToSpd;
    out->sigma_ppm = sigma;
    out->confidence = 1.0 - std::exp(-std::max(elapsed_s, 0.0) / 12.0);
}

ADVTG_C_API void advtg_synth_config_default(advtg_synth_config* cfg) {
    if (!cfg) return;
    std::memset(cfg, 0, sizeof *cfg);
    cfg->bph = 28800.0;
    cfg->rate_s_per_day = 0.0;
    cfg->beat_error_ms = 0.0;
    cfg->amplitude_deg = 280.0;
    cfg->lift_angle_deg = 52.0;
    cfg->snr_db = 30.0;
    cfg->jitter_us = 0.0;
    cfg->odd_jitter_us = 0.0;
    cfg->p1_gain = 1.0;
    cfg->sample_rate = 48000;
    cfg->quartz = 0;
    cfg->seconds = 10.0;
    cfg->seed = 0;
}

ADVTG_C_API int64_t advtg_synth_watch(const advtg_synth_config* cfg, double* out, size_t cap) {
    if (!cfg || !out || !(cfg->seconds > 0.0) || cfg->sample_rate <= 0 || !(cfg->bph > 0.0)) {
        return ADVTG_ERR_INVALID;
    }
    const double fs = static_cast<double>(cfg->sample_rate);
    const std::size_t n = static_cast<std::size_t>(std::llrint(cfg->seconds * fs));
    if (cap < n) return ADVTG_ERR_INVALID;
    std::memset(out, 0, n * sizeof(double));

    constexpr double kScale = 0.5;
    constexpr double kBurstS = 4.0e-3;
    constexpr double kP2Frac = 0.45;
    constexpr SynthMode modes[3][2] = {
        {{6500.0, 1.0, 0.40e-3}, {9800.0, 0.4, 0.30e-3}},
        {{4800.0, 1.0, 0.40e-3}, {7400.0, 0.4, 0.30e-3}},
        {{3600.0, 1.0, 0.45e-3}, {5900.0, 0.4, 0.35e-3}},
    };
    constexpr double levels[3] = {0.60, 0.35, 1.00};

    Rng rng(cfg->seed);
    const double nominal = 3600.0 / cfg->bph;
    const double period = nominal / (1.0 + cfg->rate_s_per_day / kSecondsPerDay);
    const double f0 = cfg->bph / 7200.0;
    const double t_lift =
        std::asin(std::clamp(cfg->lift_angle_deg / (2.0 * cfg->amplitude_deg), 1e-6, 0.999)) /
        (std::numbers::pi * f0);
    const double be = cfg->beat_error_ms * 1e-3;
    const double offsets[3] = {0.0, kP2Frac * t_lift, t_lift};
    const double first = rng.uniform(0.0, period);

    std::int64_t k = 0;
    double base = first;
    while (true) {
        double t0 = base + 0.5 * be * ((k % 2 == 0) ? 1.0 : -1.0);
        base = first + static_cast<double>(k + 1) * period;
        if (cfg->jitter_us > 0.0) t0 += rng.normal(0.0, cfg->jitter_us * 1e-6);
        if (cfg->odd_jitter_us > 0.0 && (k % 2 != 0))
            t0 += rng.normal(0.0, cfg->odd_jitter_us * 1e-6);
        for (int p = 0; p < 2; ++p) {
            if (cfg->phase_mod_period_s[p] > 0.0 && cfg->phase_mod_amp_us[p] != 0.0) {
                t0 += cfg->phase_mod_amp_us[p] * 1e-6 *
                      std::sin(kTwoPi * t0 / cfg->phase_mod_period_s[p]);
            }
        }
        double gain = 1.0;
        for (int p = 0; p < 2; ++p) {
            if (cfg->level_mod_period_s[p] > 0.0 && cfg->level_mod_depth[p] != 0.0) {
                gain *= 1.0 + cfg->level_mod_depth[p] *
                                  std::sin(kTwoPi * t0 / cfg->level_mod_period_s[p]);
            }
        }
        if (t0 + t_lift + kBurstS > cfg->seconds) break;
        if (cfg->quartz) {
            add_synth_burst(out, n, fs, t0, gain * levels[2] * kScale, modes[2], 2, kBurstS);
        } else {
            for (int i = 0; i < 3; ++i) {
                const double lv = gain * levels[i] * ((i == 0) ? cfg->p1_gain : 1.0);
                add_synth_burst(out, n, fs, t0 + offsets[i], lv * kScale, modes[i], 2, kBurstS);
            }
        }
        ++k;
    }

    if (std::isfinite(cfg->snr_db) && n > 0) {
        std::vector<double> noise(n);
        for (std::size_t i = 0; i < n; ++i) noise[i] = rng.normal(0.0, 1.0);
        const double target = kScale * std::pow(10.0, -cfg->snr_db / 20.0);
        const double rms = band_rms(noise, fs);
        const double sc = target / rms;
        for (std::size_t i = 0; i < n; ++i) out[i] += noise[i] * sc;
    }
    return static_cast<int64_t>(n);
}

}  // extern "C"
