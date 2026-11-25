// DSP kernels of the native engine: exact C++ counterparts of src/adv_tg/dsp/*.py.
//
// Contract between the DSP module (fft.cpp, dsp_engine.cpp, interference.cpp, normalizer.cpp,
// onset.cpp) and its consumers (beat_detector.cpp, pps.cpp, gear_train.cpp, ...). Everything
// is float64, pointers are to contiguous memory, results must match the Python reference
// (the numpy engine of v1.x) to rounding (<= ~1e-12 relative), NaN is a sentinel.
//
// Ownership: the DSP module implements these declarations. Consumers may APPEND declarations
// at the end of the file under their own comment block; never rewrite existing lines.
#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

#include "advtg_bits.hpp"

namespace advtg::dsp {

// Noise gate of every division: a denominator below kGate counts as zero (engine.GATE).
inline constexpr double kGate = 1e-12;

// ------------------------------------------------------------------ windows / designs
// numpy.blackman(n) / numpy.hanning(n) (symmetric windows, n == 1 -> [1.0]).
std::vector<double> blackman(std::size_t n);
std::vector<double> hanning(std::size_t n);
// beat_detector._hilbert_fir(half): odd-lag Hilbert taps 2 / (pi n) times blackman, length
// 2 * half + 1 (hk[half] == 0, hk[half + d] == -hk[half - d], even lags 0).
std::vector<double> hilbert_fir(std::int64_t half);

// ------------------------------------------------------------------ FFT (power-of-two sizes)
// numpy.fft.rfft(x, nfft): x is zero-padded / truncated to nfft; out has nfft / 2 + 1 bins.
void rfft(const double* x, std::size_t n_in, std::size_t nfft, std::complex<double>* out);
// numpy.fft.irfft(spec, nfft): spec has nfft / 2 + 1 bins; out has nfft samples (1 / nfft scaled).
void irfft(const std::complex<double>* spec, std::size_t nfft, double* out);

// ------------------------------------------------------------------ IIR (second-order sections)
// sos is row-major (n_sec x 6) as scipy; zi (n_sec x 2, may be nullptr = zero state) is the
// initial state of each section (transposed direct form II). y may alias x.
// == engine.sosfilt (scipy.signal.sosfilt) to ~1e-13 relative.
void sosfilt(const double* sos, int n_sec, const double* x, std::size_t n, const double* zi,
             double* y);
// == engine.sosfiltfilt: odd extension of `padlen` samples, state zi * first sample (forward) and
// zi * last output (backward). Requires n > padlen (std::invalid_argument otherwise).
// y must not alias x; y has n samples.
void sosfiltfilt(const double* sos, int n_sec, const double* zi, std::int64_t padlen,
                 const double* x, std::size_t n, double* y);

// ------------------------------------------------------------------ IIR design (dsp/filters.py)
// scipy.signal.butter(order, (lo, hi), "bandpass", fs=fs, output="sos"): n_sec x 6, row-major.
// std::invalid_argument for an invalid order or band (0 < lo < hi < fs / 2).
std::vector<double> butter_bandpass_sos(int order, double lo_hz, double hi_hz, double fs);
// scipy.signal.sosfilt_zi(sos): n_sec x 2.
std::vector<double> sosfilt_zi(const double* sos, int n_sec);
// Default odd-extension length of scipy.signal.sosfiltfilt for sos.
std::int64_t sosfiltfilt_padlen(const double* sos, int n_sec);

// ------------------------------------------------------------------ FIR and smoothing
// engine._convolve_same: centred convolution with the length of x,
// np.convolve(x, k, "full")[(m - 1) // 2 : (m - 1) // 2 + n]. Detects the odd-antisymmetric
// Hilbert kernel (even lags zero) and uses the folded SIMD path; FFT for long general kernels.
void convolve_same(const double* x, std::size_t n, const double* k, std::size_t m, double* out);
// np.convolve(x, k, "valid") (out has n - m + 1 samples, n >= m); fast path for symmetric k.
void convolve_valid(const double* x, std::size_t n, const double* k, std::size_t m, double* out);
// engine._moving_average_same(x, m) == np.convolve(x, full(m, 1/m), "same") (m <= 1: copy).
void moving_average_same(const double* x, std::size_t n, std::int64_t m, double* out);
// == moving_average_same(x, m)[start:stop] without smoothing the whole array
// (BeatDetector._moving_average_slice); out has stop - start samples.
void moving_average_slice(const double* x, std::size_t n, std::int64_t m, std::int64_t start,
                          std::int64_t stop, double* out);

// ------------------------------------------------------------------ detector kernels
// engine.compute_cell_envelope: zero-phase band-pass (sosfiltfilt with padlen), Hilbert FIR
// by `hk` (odd length), magnitude, centred moving average of length smooth_len, cropped to
// [crop_start, crop_end). out has crop_end - crop_start samples; all zeros if n <= padlen + 1.
// Throws std::invalid_argument on invalid crop indices / smooth_len < 1.
void compute_cell_envelope(const double* x, std::size_t n, const double* sos, int n_sec,
                           const double* zi, const double* hk, std::size_t hk_len,
                           std::int64_t smooth_len, std::int64_t crop_start,
                           std::int64_t crop_end, std::int64_t padlen, double* out);

// engine.find_local_maxima (scipy find_peaks(x)[0]): strict maxima, plateau -> middle sample
// (rounded down), plateaus touching an end are not peaks. Returns the count written to out
// (capacity >= n / 2 + 1).
std::size_t find_local_maxima(const double* x, std::size_t n, std::int64_t* out);

struct Candidates {
    std::vector<double> onset, peak, floor, spread;
    void clear() {
        onset.clear();
        peak.clear();
        floor.clear();
        spread.clear();
    }
};
// engine.detect_cell_candidates: sub-event candidates of one cell (hysteresis, runs merged
// closer than merge_samples, valley splits, interpolated onsets). env / F / D have n samples.
// APPENDS to `out` (absolute samples / envelope units, in the order of the Python version).
void detect_cell_candidates(const double* env, const double* F, const double* D, std::size_t n,
                            double threshold_hi, double threshold_lo, std::int64_t merge_samples,
                            double split_ratio, double min_spacing_samples,
                            double onset_fraction, std::int64_t cell_start_abs,
                            std::int64_t cell_end_abs, std::int64_t lo_abs,
                            std::int64_t run_start_abs, Candidates& out);

// ------------------------------------------------------------------ onset (dsp/onset.py)
// Windowed-sinc low-pass (Blackman, odd length n = int(periods * fs / fc) | 1, unit DC gain);
// {1.0} when fc >= 0.45 fs.
std::vector<double> lowpass_fir(double fs, double fc, double periods = 4.0);
// (median, sigma = max(1.4826 * MAD, 1e-12)) of x (x is not modified).
void robust_level(const double* x, std::size_t n, double& mu, double& sigma);
// onset.onset_after_quiet: index (fractional, into r) of the first crossing of `threshold`
// after the last quiet stretch of >= `quiet` samples; NaN when None.
double onset_after_quiet(const double* r, std::size_t n, double threshold, std::int64_t quiet);

// ------------------------------------------------------------------ interference filter
// dsp/interference.py: noise-adaptive notch (see that module's docstring). Same semantics and
// same fixed grid of absolute sample indices.
class InterferenceFilter {
public:
    InterferenceFilter(double sample_rate, double band_lo_hz, double band_hi_hz,
                       std::int64_t nfft = 2048, double history_s = 2.0, double percentile = 25.0,
                       double excess_db = 6.0, double max_attenuation_db = 40.0,
                       std::int64_t min_frames = 16, double skirt = 0.7,
                       std::int64_t skirt_order = 4, std::int64_t widen_bins = 1);
    ~InterferenceFilter();
    InterferenceFilter(const InterferenceFilter&) = delete;
    InterferenceFilter& operator=(const InterferenceFilter&) = delete;

    void reset(std::int64_t start);
    std::pair<std::int64_t, std::int64_t> next_frame() const;  // [start, end) of the next grid frame
    void skip_frame();
    void add_frame(const double* samples, std::size_t n);  // n == nfft
    // Batched add of every pending grid frame ending at or before `up_to` from buf (absolute
    // index buf_start, buf_n samples).
    void add_frames(const double* buf, std::size_t buf_n, std::int64_t buf_start,
                    std::int64_t up_to);
    // Per-bin gains (nfft / 2 + 1) for audio from `at` on; false = nothing attenuated (None).
    bool gains(std::int64_t at, std::vector<double>& g_out);
    // Zero-delay FIR filtering of x with the gains (y may not alias x); n samples.
    void apply(const double* x, std::size_t n, const double* gains, double* y);

    std::int64_t nfft() const;
    int active_bins = 0;
    double max_attenuation_db = 0.0;
    double peak_hz = kNaN;
    double peak_excess_db = 0.0;

private:
    struct Impl;
    Impl* impl_;
};

// ------------------------------------------------------------------ audio normalizer
// dsp/normalizer.py: fixed-block level normalizer, chunking invariant.
struct NormalizerConfig {
    double sample_rate = 48000.0;
    double target = 0.5;
    double block_s = 0.1;
    double hold_s = 3.0;
    double release_s = 10.0;  // +inf allowed (no release)
    double max_gain_db = 24.0;
    double clip_level = 0.99;
    double min_follower = 0.0;
};

class AudioNormalizer {
public:
    struct Segment {
        std::int64_t start;
        std::vector<double> samples;
    };
    explicit AudioNormalizer(const NormalizerConfig& cfg);
    ~AudioNormalizer();
    AudioNormalizer(const AudioNormalizer&) = delete;
    AudioNormalizer& operator=(const AudioNormalizer&) = delete;
    void reset();
    // Feed contiguous samples starting at absolute `start_index`; completed blocks are appended
    // to `out`. Throws std::invalid_argument when the chunk overlaps already received samples.
    void process(const double* chunk, std::size_t n, std::int64_t start_index,
                 std::vector<Segment>& out);
    void flush(std::vector<Segment>& out);
    double gain() const;
    double gain_db() const;
    bool has_next_index() const;
    std::int64_t next_index() const;
    std::int64_t clipped_samples = 0;
    std::int64_t samples_in = 0;

private:
    struct Impl;
    Impl* impl_;
};

}  // namespace advtg::dsp
