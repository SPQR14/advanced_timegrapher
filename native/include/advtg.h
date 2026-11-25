/*
 * advtg.h - public C API of the Advanced Timegrapher measurement engine (libadvtg_native).
 *
 * Plain C99, no C++ types: include it from a JNI bridge (Android), a Swift/Objective-C module
 * map (iOS / macOS), Python ctypes or any other FFI. Everything a live timegrapher needs is
 * here: feed audio into a lock-free sample stream from the audio callback, call
 * advtg_session_update() 4 times per second from ONE worker thread, and read the snapshot.
 *
 *     advtg_session_config cfg;
 *     advtg_session_config_default(&cfg, 48000.0, 28800.0);
 *     cfg.lift_angle_deg = 52.0;
 *     advtg_session* s = advtg_session_create(&cfg, NULL);       // owns its stream
 *     void* stream = advtg_session_stream(s);
 *     // audio thread (Oboe / AAudio / AVAudioEngine callback), mono float32 [-1, 1]:
 *     advtg_stream_write_f32(stream, pcm, frames, 0, 0, 0, advtg_wall_now_ns());
 *     // worker thread, every 0.25 s:
 *     advtg_snapshot snap;
 *     if (advtg_session_update(s, &snap) == 0) show(snap.display.rate_s_per_day, ...);
 *     advtg_session_destroy(s);
 *
 * Threading: one producer writes the stream (lock-free SPSC); one consumer thread calls
 * advtg_session_update() and the advtg_session_* accessors (they return data of the last
 * update). No function lets a C++ exception escape: errors are negative return codes and
 * advtg_session_last_error() describes the last one.
 *
 * Units: sample indices are absolute (since the stream started); instants in beats (p1, p3,
 * t_fine, ...) are fractional sample indices; NaN means "not resolved / not available".
 *
 * ABI: the struct layouts below are part of ADVTG_ABI_VERSION (advtg_abi_version()); they
 * are checked against the C++ definitions with static_assert in c_api_session.cpp.
 */
#ifndef ADVTG_H
#define ADVTG_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32) && !defined(ADVTG_STATIC)
#  if defined(ADVTG_BUILDING)
#    define ADVTG_C_API __declspec(dllexport)
#  else
#    define ADVTG_C_API __declspec(dllimport)
#  endif
#elif defined(__GNUC__) || defined(__clang__)
#  define ADVTG_C_API __attribute__((visibility("default")))
#else
#  define ADVTG_C_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ beat flags */
enum {
    ADVTG_BEAT_P1_MISSING = 1,
    ADVTG_BEAT_P2_MISSING = 2,
    ADVTG_BEAT_P3_MISSING = 4,
    ADVTG_BEAT_WEAK = 8,
    ADVTG_BEAT_SATURATED = 16,
    ADVTG_BEAT_TIMING_OUTLIER = 32,
    ADVTG_BEAT_AFTER_GAP = 64
};

/* One detected beat (core/beat_types.py Beat). 96 bytes. */
typedef struct advtg_beat {
    int64_t k;               /* beat number of the phase model (parity: k & 1, 0 = tic) */
    double p1, p2, p3;       /* sub-event instants (samples), NaN if not found */
    double peak_dbfs;
    double snr_db;
    double residual_samples;
    double confidence;
    double t_fine;           /* timing instant of the drop (samples) */
    double t_p1;             /* unlocking front of this beat */
    double t_p1_tpl;         /* P1 instant from the parity template */
    int32_t n_subevents;
    uint32_t flags;          /* ADVTG_BEAT_* */
} advtg_beat;

/* ADC clock drift estimate (core/adc_clock.py AdcDrift). */
enum { ADVTG_CLOCK_UNCALIBRATED = 0, ADVTG_CLOCK_HOST_RAW = 1,
       ADVTG_CLOCK_HOST_DISCIPLINED = 2, ADVTG_CLOCK_EXTERNAL = 3 };
enum { ADVTG_DRIFT_NOTE_NONE = 0, ADVTG_DRIFT_NOTE_FEW_POINTS = 1,
       ADVTG_DRIFT_NOTE_TOO_MANY_JUMPS = 2, ADVTG_DRIFT_NOTE_SHORT_SPAN = 3,
       ADVTG_DRIFT_NOTE_OUTLIERS = 4, ADVTG_DRIFT_NOTE_SEGMENTS = 5,
       ADVTG_DRIFT_NOTE_SESSION_TOO_SHORT = 6 };
typedef struct advtg_adc_drift {
    double ppm;              /* > 0 = ADC fast */
    double sigma_ppm;        /* statistical */
    double systematic_ppm;   /* of the time base; total = hypot(sigma, systematic) */
    double span_s;
    double residual_us;
    int64_t n_points;
    int64_t n_segments;
    int32_t level;           /* ADVTG_CLOCK_* */
    int32_t method;          /* 0 none, 1 host-envelope, 2 reference, 3 manual */
    int32_t note_kind;       /* ADVTG_DRIFT_NOTE_* */
    int32_t _pad;
} advtg_adc_drift;

/* Metrics over the sliding window (core/metrics.py Metrics). */
enum { ADVTG_BE_POINT_NONE = 0, ADVTG_BE_POINT_P1 = 1, ADVTG_BE_POINT_P1_BEAT = 2,
       ADVTG_BE_POINT_P1_FINE = 3, ADVTG_BE_POINT_T_FINE = 4 };
enum { ADVTG_AMP_BOTH = 0, ADVTG_AMP_TIC = 1, ADVTG_AMP_TAC = 2 };
typedef struct advtg_metrics {
    int64_t n_beats, n_used_even, n_used_odd, n_excluded;
    double excluded_fraction;
    double rate_s_per_day, rate_sigma_s_per_day, rate_sigma_block_s_per_day;
    double rate_uncertainty_s_per_day, rate_clock_sigma_s_per_day;
    double beat_error_ms, beat_error_sigma_ms, beat_error_sigma_block_ms;
    double beat_error_uncertainty_ms, beat_error_p3_ms;
    double amplitude_deg, amplitude_tic_deg, amplitude_tac_deg, amplitude_la_uncertainty_deg;
    double lift_angle_deg;
    double sigma_even_us, sigma_odd_us, pair_sigma_even_us, pair_sigma_odd_us;
    double rate_trend_s_per_day_per_min, rate_trend_sigma_s_per_day_per_min;
    double window_sigma_factor;
    double span_s;
    double confidence;
    int32_t rate_calibrated;
    int32_t rate_point_0_p1, rate_point_1_p1;   /* timing point per parity: 1 = P1, 0 = drop */
    int32_t beat_error_point;                   /* ADVTG_BE_POINT_* */
    int32_t amplitude_parity;                   /* ADVTG_AMP_* */
    int32_t amplitude_indicative;               /* lift angle not given: amplitude indicative */
    int32_t rate_trend_flag;
    int32_t _pad;
} advtg_metrics;

/* One rate window inside the span (Metrics.windows). */
typedef struct advtg_window_rate {
    double start_s, end_s, center_s;
    double rate_s_per_day, rate_sigma_s_per_day, rate_uncertainty_s_per_day;
    int64_t n_beats;
} advtg_window_rate;

/* Counters of the beat detector (core/beat_detector.py DetectorStats). */
typedef struct advtg_detector_stats {
    int64_t samples_processed, beats_emitted, missed_beats, gap_beats, gaps;
    int64_t rejected_events, relocks, normalizer_clipped_samples;
    int64_t expected_index, norm_next_index;
    double lock_sample, lock_time_s;
    double p1_fraction, p2_fraction, p3_fraction;
    double period_samples, nominal_period_samples;
    double interference_db, interference_hz;
    double normalizer_gain_db;
    int32_t has_lock_sample, locked, has_normalizer, started, finished, has_norm_next;
} advtg_detector_stats;

/* ------------------------------------------------------------------ session */
typedef struct advtg_session_config {
    double sample_rate;          /* Hz (>= 8000) */
    double bph;                  /* nominal beats per hour */
    double lift_angle_deg;       /* NaN or <= 0 = unknown (52 deg used, amplitude indicative) */
    double span_s;               /* window of the figures (30) */
    double window_s;             /* rate windows inside the span (10) */
    double display_pre_ms;       /* oscilloscope window before P1 (3) */
    double display_post_ms;      /* oscilloscope window after the reference instant (10) */
    double wall_clock_sigma_ppm; /* systematic error of the wall clock (NTP: 1) */
    double mono_clock_sigma_ppm; /* systematic error of the monotonic clock (30, 0.5 with NTP) */
    advtg_adc_drift calibration; /* stored calibration of the device (if has_calibration) */
    int32_t quartz;              /* quartz watch: rate only, one event per step */
    int32_t normalize;           /* software level normalization before the detector */
    int32_t live_clock;          /* estimate the ADC drift from the block stamps (1) */
    int32_t has_calibration;
    int32_t wall_clock_disciplined; /* wall clock steered by NTP (1) */
    int32_t mono_clock_disciplined; /* monotonic clock steered by NTP (0) */
    int64_t stream_capacity;     /* samples of the owned stream (0 = 120 s) */
} advtg_session_config;

typedef struct advtg_display {   /* figures to show: median of the last updates */
    double rate_s_per_day, beat_error_ms, beat_error_p3_ms;
    double amplitude_deg, amplitude_tic_deg, amplitude_tac_deg;
    double confidence, timing_confidence;
} advtg_display;

enum { ADVTG_DRIFT_SOURCE_NONE = 0, ADVTG_DRIFT_SOURCE_CALIBRATION = 1,
       ADVTG_DRIFT_SOURCE_LIVE = 2 };
enum { ADVTG_CLOCK_FIELD_NONE = 0, ADVTG_CLOCK_FIELD_WALL = 1, ADVTG_CLOCK_FIELD_HOST = 2 };

typedef struct advtg_snapshot {  /* core/session.py SessionSnapshot */
    advtg_metrics metrics;       /* raw figures over the span (NaN while not available) */
    advtg_display display;
    advtg_beat last_beat;        /* valid if has_last_beat */
    advtg_adc_drift drift;       /* applied to the rate */
    advtg_adc_drift live_drift;  /* live estimate from the block stamps */
    double waveform_start;       /* absolute index of waveform[0] (NaN if none) */
    double snr_db;               /* mean anchor SNR of the last 16 beats */
    double detection_ratio;      /* detected / predicted beats in the span */
    double usable_ratio;
    double audio_s;              /* seconds of audio processed */
    double real_bph;
    double tape_shift_ms[2];     /* (tic, tac) shift of the paper tape rows */
    int64_t new_beats;
    int64_t beats_total;
    int64_t saturated_beats;
    int64_t waveform_len;        /* advtg_session_waveform() */
    int64_t tape_len;            /* advtg_session_tape() */
    int32_t has_last_beat;
    int32_t locked;
    int32_t drift_source;        /* ADVTG_DRIFT_SOURCE_* */
    int32_t clock_field;         /* ADVTG_CLOCK_FIELD_* of the live estimate */
    int32_t normalized;
    int32_t _pad;
} advtg_snapshot;

typedef struct advtg_session advtg_session;

/* Defaults of core/session.py SessionConfig for (sample_rate, bph). */
ADVTG_C_API void advtg_session_config_default(advtg_session_config* cfg, double sample_rate,
                                              double bph);
/* stream: a handle of advtg_stream_create() fed by the caller (kept alive by the caller), or
 * NULL for a stream owned by the session (advtg_session_stream). NULL on error
 * (advtg_session_last_error(NULL)). */
ADVTG_C_API advtg_session* advtg_session_create(const advtg_session_config* cfg, void* stream);
ADVTG_C_API void advtg_session_destroy(advtg_session* s);
ADVTG_C_API void* advtg_session_stream(advtg_session* s);
/* Process the audio that arrived since the previous call. 0 = ok, < 0 = error. */
ADVTG_C_API int advtg_session_update(advtg_session* s, advtg_snapshot* out);
/* Arrays of the last update. Each returns the full count and copies at most cap items. */
ADVTG_C_API int64_t advtg_session_waveform(const advtg_session* s, double* out, int64_t cap);
ADVTG_C_API int64_t advtg_session_tape(const advtg_session* s, double* time_s,
                                       double* offset_ms, int8_t* parity, int64_t cap);
ADVTG_C_API int64_t advtg_session_new_beats(const advtg_session* s, advtg_beat* out,
                                            int64_t cap);
ADVTG_C_API int64_t advtg_session_window_beats(const advtg_session* s, advtg_beat* out,
                                               int64_t cap);
ADVTG_C_API int64_t advtg_session_metric_windows(const advtg_session* s,
                                                 advtg_window_rate* out, int64_t cap);
/* '\n'-separated texts (Spanish, as the desktop app); returns the full length in bytes. */
ADVTG_C_API int32_t advtg_session_metric_warnings(const advtg_session* s, char* buf,
                                                  int32_t cap);
ADVTG_C_API int32_t advtg_session_detector_warnings(const advtg_session* s, char* buf,
                                                    int32_t cap);
ADVTG_C_API int advtg_session_detector_stats(const advtg_session* s, advtg_detector_stats* out);
/* (samples before the reference instant, total samples) of the oscilloscope window. */
ADVTG_C_API void advtg_session_display_window(const advtg_session* s, int64_t* pre,
                                              int64_t* len);
/* Last error message (thread-local; s may be NULL). */
ADVTG_C_API const char* advtg_session_last_error(const advtg_session* s);

/* ------------------------------------------------------------------ sample stream */
/* Lock-free SPSC ring of float32 samples with a log of block stamps (core/sample_stream.py).
 * perf_offset_ns is added to the steady clock when host_ns is measured internally. */
ADVTG_C_API void* advtg_stream_create(size_t capacity, double sample_rate, size_t block_log_size,
                                      int64_t perf_offset_ns);
ADVTG_C_API void advtg_stream_destroy(void* h);
ADVTG_C_API void advtg_stream_reset(void* h);
ADVTG_C_API int64_t advtg_stream_total(const void* h);
ADVTG_C_API int64_t advtg_stream_oldest(const void* h);
/* Producer (audio callback): has_host_ns = 0 measures the steady clock now; adc_ns / wall_ns
 * are 0 when not available (wall_ns enables the NTP-disciplined live clock estimate). */
ADVTG_C_API void advtg_stream_write_f32(void* h, const float* data, size_t n, int64_t host_ns,
                                        int has_host_ns, int64_t adc_ns, int64_t wall_ns);
ADVTG_C_API void advtg_stream_write_f64(void* h, const double* data, size_t n, int64_t host_ns,
                                        int has_host_ns, int64_t adc_ns, int64_t wall_ns);
/* 0 = ok, 1 = not yet available, 2 = overwritten, -1 = invalid argument. */
ADVTG_C_API int advtg_stream_read_f64(const void* h, int64_t start, int64_t stop, double* out);
ADVTG_C_API int advtg_stream_read_f32(const void* h, int64_t start, int64_t stop, float* out);

/* ------------------------------------------------------------------ engine information */
ADVTG_C_API int advtg_abi_version(void);
ADVTG_C_API const char* advtg_version(void);
ADVTG_C_API const char* advtg_simd_name(void);
ADVTG_C_API int64_t advtg_steady_now_ns(void);
ADVTG_C_API int64_t advtg_wall_now_ns(void);

/* ------------------------------------------------------------------ filter design */
/* scipy.signal.butter(order, (lo, hi), "bandpass", fs=fs, output="sos"): writes
 * n_sec x 6 coefficients (n_sec = order) if cap >= 6 * order; returns n_sec or < 0. */
ADVTG_C_API int advtg_butter_bandpass_sos(int order, double lo_hz, double hi_hz, double fs,
                                          double* sos, int cap);
ADVTG_C_API int advtg_sosfilt_zi(const double* sos, int n_sec, double* zi);
ADVTG_C_API int64_t advtg_sosfiltfilt_padlen(const double* sos, int n_sec);

#ifdef __cplusplus
}
#endif

#endif /* ADVTG_H */
