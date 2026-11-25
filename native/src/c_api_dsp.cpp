// C API wrappers for advtg::dsp (exported to Python via ctypes).
#include <complex>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <vector>

#include "advtg_api.hpp"
#include "advtg_dsp.hpp"

ADVTG_API int advtg_sosfilt(const double* sos, int n_sec, const double* x, std::size_t n,
                            const double* zi, double* y) {
    try {
        advtg::dsp::sosfilt(sos, n_sec, x, n, zi, y);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API int advtg_sosfiltfilt(const double* sos, int n_sec, const double* zi,
                                std::int64_t padlen, const double* x, std::size_t n, double* y) {
    try {
        advtg::dsp::sosfiltfilt(sos, n_sec, zi, padlen, x, n, y);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API int advtg_compute_cell_envelope(const double* x, std::size_t n, const double* sos,
                                          int n_sec, const double* zi, const double* hk,
                                          std::size_t hk_len, std::int64_t smooth_len,
                                          std::int64_t crop_start, std::int64_t crop_end,
                                          std::int64_t padlen, double* out) {
    try {
        advtg::dsp::compute_cell_envelope(x, n, sos, n_sec, zi, hk, hk_len, smooth_len,
                                          crop_start, crop_end, padlen, out);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_moving_average_same(const double* x, std::size_t n, std::int64_t m,
                                         double* out) {
    try {
        advtg::dsp::moving_average_same(x, n, m, out);
    } catch (...) {
    }
}

ADVTG_API std::size_t advtg_find_local_maxima(const double* x, std::size_t n, std::int64_t* out) {
    try {
        return advtg::dsp::find_local_maxima(x, n, out);
    } catch (...) {
        return 0;
    }
}

ADVTG_API std::int64_t advtg_detect_cell_candidates(
    const double* env, const double* F, const double* D, std::size_t n, double threshold_hi,
    double threshold_lo, std::int64_t merge_samples, double split_ratio,
    double min_spacing_samples, double onset_fraction, std::int64_t cell_start_abs,
    std::int64_t cell_end_abs, std::int64_t lo_abs, std::int64_t run_start_abs, double* out_onsets,
    double* out_peaks, double* out_floors, double* out_spreads, std::size_t cap) {
    try {
        thread_local advtg::dsp::Candidates c;
        c.clear();
        advtg::dsp::detect_cell_candidates(
            env, F, D, n, threshold_hi, threshold_lo, merge_samples, split_ratio,
            min_spacing_samples, onset_fraction, cell_start_abs, cell_end_abs, lo_abs,
            run_start_abs, c);
        const std::size_t k = c.onset.size();
        if (k > cap) return -static_cast<std::int64_t>(k);
        if (k) {
            std::memcpy(out_onsets, c.onset.data(), k * sizeof(double));
            std::memcpy(out_peaks, c.peak.data(), k * sizeof(double));
            std::memcpy(out_floors, c.floor.data(), k * sizeof(double));
            std::memcpy(out_spreads, c.spread.data(), k * sizeof(double));
        }
        return static_cast<std::int64_t>(k);
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_robust_level(const double* x, std::size_t n, double* mu_out,
                                  double* sigma_out) {
    try {
        double mu = 0.0, sg = 0.0;
        advtg::dsp::robust_level(x, n, mu, sg);
        if (mu_out) *mu_out = mu;
        if (sigma_out) *sigma_out = sg;
    } catch (...) {
    }
}

ADVTG_API double advtg_onset_after_quiet(const double* r, std::size_t n, double threshold,
                                         std::int64_t quiet) {
    try {
        return advtg::dsp::onset_after_quiet(r, n, threshold, quiet);
    } catch (...) {
        return advtg::kNaN;
    }
}

ADVTG_API int advtg_rfft(const double* x, std::size_t n_in, std::size_t nfft, double* out_re_im) {
    try {
        advtg::dsp::rfft(x, n_in, nfft, reinterpret_cast<std::complex<double>*>(out_re_im));
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API int advtg_irfft(const double* spec_re_im, std::size_t nfft, double* out) {
    try {
        advtg::dsp::irfft(reinterpret_cast<const std::complex<double>*>(spec_re_im), nfft, out);
        return 0;
    } catch (...) {
        return -1;
    }
}

// ---- InterferenceFilter handle
ADVTG_API void* advtg_interf_create(double fs, double band_lo, double band_hi, std::int64_t nfft,
                                    double history_s, double percentile, double excess_db,
                                    double max_attenuation_db, std::int64_t min_frames,
                                    double skirt, std::int64_t skirt_order,
                                    std::int64_t widen_bins) {
    try {
        return new advtg::dsp::InterferenceFilter(fs, band_lo, band_hi, nfft, history_s, percentile,
                                                  excess_db, max_attenuation_db, min_frames, skirt,
                                                  skirt_order, widen_bins);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_interf_destroy(void* h) {
    delete static_cast<advtg::dsp::InterferenceFilter*>(h);
}

ADVTG_API void advtg_interf_reset(void* h, std::int64_t start) {
    static_cast<advtg::dsp::InterferenceFilter*>(h)->reset(start);
}

ADVTG_API void advtg_interf_next_frame(void* h, std::int64_t* out_a, std::int64_t* out_b) {
    auto p = static_cast<advtg::dsp::InterferenceFilter*>(h)->next_frame();
    if (out_a) *out_a = p.first;
    if (out_b) *out_b = p.second;
}

ADVTG_API void advtg_interf_skip_frame(void* h) {
    static_cast<advtg::dsp::InterferenceFilter*>(h)->skip_frame();
}

ADVTG_API int advtg_interf_add_frame(void* h, const double* samples, std::size_t n) {
    try {
        static_cast<advtg::dsp::InterferenceFilter*>(h)->add_frame(samples, n);
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_interf_add_frames(void* h, const double* buf, std::size_t buf_n,
                                       std::int64_t buf_start, std::int64_t up_to) {
    try {
        static_cast<advtg::dsp::InterferenceFilter*>(h)->add_frames(buf, buf_n, buf_start, up_to);
    } catch (...) {
    }
}

ADVTG_API int advtg_interf_gains(void* h, std::int64_t at, double* g_out, int* active_bins_out,
                                 double* max_att_out, double* peak_hz_out,
                                 double* peak_excess_out) {
    try {
        auto* f = static_cast<advtg::dsp::InterferenceFilter*>(h);
        thread_local std::vector<double> g;
        const bool has = f->gains(at, g);
        if (has && g_out) std::memcpy(g_out, g.data(), g.size() * sizeof(double));
        if (active_bins_out) *active_bins_out = f->active_bins;
        if (max_att_out) *max_att_out = f->max_attenuation_db;
        if (peak_hz_out) *peak_hz_out = f->peak_hz;
        if (peak_excess_out) *peak_excess_out = f->peak_excess_db;
        return has ? 1 : 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_interf_apply(void* h, const double* x, std::size_t n, const double* gains,
                                  double* y) {
    try {
        static_cast<advtg::dsp::InterferenceFilter*>(h)->apply(x, n, gains, y);
    } catch (...) {
    }
}

// ---- AudioNormalizer handle
ADVTG_API void* advtg_norm_create(double sample_rate, double target, double block_s, double hold_s,
                                  double release_s, double max_gain_db, double clip_level,
                                  double min_follower) {
    try {
        advtg::dsp::NormalizerConfig cfg{sample_rate, target, block_s, hold_s,
                                         release_s,   max_gain_db, clip_level, min_follower};
        return new advtg::dsp::AudioNormalizer(cfg);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_norm_destroy(void* h) {
    delete static_cast<advtg::dsp::AudioNormalizer*>(h);
}

ADVTG_API void advtg_norm_reset(void* h) {
    static_cast<advtg::dsp::AudioNormalizer*>(h)->reset();
}

ADVTG_API std::int64_t advtg_norm_process(void* h, const double* chunk, std::size_t n,
                                          std::int64_t start_index, double* out_samples,
                                          std::size_t out_samples_cap, std::int64_t* out_starts,
                                          std::int64_t* out_lens, std::size_t out_segs_cap) {
    try {
        auto* norm = static_cast<advtg::dsp::AudioNormalizer*>(h);
        thread_local std::vector<advtg::dsp::AudioNormalizer::Segment> segs;
        segs.clear();
        norm->process(chunk, n, start_index, segs);
        if (segs.size() > out_segs_cap) return -2;
        std::size_t pos = 0;
        for (std::size_t i = 0; i < segs.size(); ++i) {
            const std::size_t m = segs[i].samples.size();
            if (pos + m > out_samples_cap) return -2;
            out_starts[i] = segs[i].start;
            out_lens[i] = static_cast<std::int64_t>(m);
            if (m) std::memcpy(out_samples + pos, segs[i].samples.data(), m * sizeof(double));
            pos += m;
        }
        return static_cast<std::int64_t>(segs.size());
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::int64_t advtg_norm_flush(void* h, double* out_samples, std::size_t out_samples_cap,
                                        std::int64_t* out_starts, std::int64_t* out_lens,
                                        std::size_t out_segs_cap) {
    try {
        auto* norm = static_cast<advtg::dsp::AudioNormalizer*>(h);
        thread_local std::vector<advtg::dsp::AudioNormalizer::Segment> segs;
        segs.clear();
        norm->flush(segs);
        if (segs.size() > out_segs_cap) return -2;
        std::size_t pos = 0;
        for (std::size_t i = 0; i < segs.size(); ++i) {
            const std::size_t m = segs[i].samples.size();
            if (pos + m > out_samples_cap) return -2;
            out_starts[i] = segs[i].start;
            out_lens[i] = static_cast<std::int64_t>(m);
            if (m) std::memcpy(out_samples + pos, segs[i].samples.data(), m * sizeof(double));
            pos += m;
        }
        return static_cast<std::int64_t>(segs.size());
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_norm_state(void* h, double* gain_out, std::int64_t* clipped_out,
                                std::int64_t* samples_in_out, int* has_next_out,
                                std::int64_t* next_index_out) {
    const auto* norm = static_cast<const advtg::dsp::AudioNormalizer*>(h);
    if (gain_out) *gain_out = norm->gain();
    if (clipped_out) *clipped_out = norm->clipped_samples;
    if (samples_in_out) *samples_in_out = norm->samples_in;
    if (has_next_out) *has_next_out = norm->has_next_index() ? 1 : 0;
    if (next_index_out) *next_index_out = norm->next_index();
}
