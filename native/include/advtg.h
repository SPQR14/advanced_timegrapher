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

/* ------------------------------------------------------------------ return codes */
enum {
    ADVTG_OK = 0,
    ADVTG_ERR_INVALID = -1,
    ADVTG_ERR_IO = -2,
    ADVTG_ERR_FORMAT = -3,
    ADVTG_ERR_CORRUPT_STORE = -4,
    ADVTG_ERR_NOT_ENOUGH_BEATS = -5,
    ADVTG_ERR_MEMORY_LIMIT = -6,
    ADVTG_ERR_UNAVAILABLE = -7
};

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
    int32_t method;          /* 0 none, 1 host-envelope, 2 reference, 3 manual, 4 pps */
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

/* ------------------------------------------------------------------ localizable warnings */
enum {
    ADVTG_WARN_NONE = 0,
    ADVTG_WARN_INTERFERENCE = 1,          /* args: [freq_khz, excess_db] */
    ADVTG_WARN_NO_LOCK = 2,               /* args: [seconds, bph] */
    ADVTG_WARN_WRONG_BPH = 3,             /* args: [dev_pct, bph] */
    ADVTG_WARN_RATE_UNCALIBRATED = 4,     /* no args */
    ADVTG_WARN_FEW_BEATS = 5,             /* param_int: min_beats_per_parity */
    ADVTG_WARN_RATE_TREND = 6,            /* args: [change_spd, span_s, trend_spd_min, sigma] */
    ADVTG_WARN_BE_ON_T_FINE = 7,          /* no args */
    ADVTG_WARN_UNKNOWN_LIFT_ANGLE = 8,    /* args: [default_lift_deg] */
    ADVTG_WARN_NO_AMPLITUDE = 9,          /* args: [amp_lo_deg, amp_hi_deg] */
    ADVTG_WARN_AMP_MISMATCH = 10,         /* args: [amp_tic_deg, amp_tac_deg] */
    ADVTG_WARN_AMP_ONE_PARITY = 11,       /* param_int: 0=tic,1=tac; args: [used_deg, other_deg] */
    ADVTG_WARN_AMP_MISMATCH_INDICATIVE = 12, /* args: [tic_deg, tac_deg] */
    ADVTG_WARN_SATURATED_BEATS = 13       /* param_int: count; args: [suggest_normalize (0/1)] */
};

typedef struct advtg_warning {
    int32_t code;
    int32_t param_int;
    double args[4];
} advtg_warning;

/* Format a warning into buf in "es" or "en"; returns full length in bytes. */
ADVTG_C_API int32_t advtg_format_warning(const advtg_warning* w, const char* lang, char* buf,
                                         int32_t cap);

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
ADVTG_C_API int32_t advtg_session_metric_warning_codes(const advtg_session* s,
                                                       advtg_warning* out, int32_t cap);
ADVTG_C_API int32_t advtg_session_detector_warning_codes(const advtg_session* s,
                                                         advtg_warning* out, int32_t cap);
ADVTG_C_API int advtg_session_detector_stats(const advtg_session* s, advtg_detector_stats* out);
/* (samples before the reference instant, total samples) of the oscilloscope window. */
ADVTG_C_API void advtg_session_display_window(const advtg_session* s, int64_t* pre,
                                              int64_t* len);
/* Last error message (thread-local; s may be NULL). */
ADVTG_C_API const char* advtg_session_last_error(const advtg_session* s);

/* ------------------------------------------------------------------ batch detector & metrics */
typedef struct advtg_detector_config {
    double sample_rate;
    double bph;
    double lift_angle_deg;       /* NaN or <= 0 -> 52.0 */
    int32_t quartz;
    int32_t normalize;
} advtg_detector_config;

typedef struct advtg_detector advtg_detector;

ADVTG_C_API void advtg_detector_config_default(advtg_detector_config* cfg, double sample_rate,
                                               double bph);
ADVTG_C_API advtg_detector* advtg_detector_create(const advtg_detector_config* cfg);
ADVTG_C_API void advtg_detector_destroy(advtg_detector* d);
ADVTG_C_API void advtg_detector_reset(advtg_detector* d);
/* Feed chunk[0..n) starting at start_index. Returns total beats produced (>= 0, or < 0 on error);
 * copies up to cap items into out, and keeps all produced beats of this call available via
 * advtg_detector_last_beats(). */
ADVTG_C_API int64_t advtg_detector_process(advtg_detector* d, const double* chunk, size_t n,
                                           int64_t start_index, advtg_beat* out, int64_t cap);
ADVTG_C_API int64_t advtg_detector_flush(advtg_detector* d, advtg_beat* out, int64_t cap);
ADVTG_C_API int64_t advtg_detector_last_beats(const advtg_detector* d, advtg_beat* out,
                                              int64_t cap);
ADVTG_C_API int advtg_detector_stats_get(const advtg_detector* d, advtg_detector_stats* out);
ADVTG_C_API int32_t advtg_detector_warnings(const advtg_detector* d, char* buf, int32_t cap);
ADVTG_C_API int32_t advtg_detector_warning_codes(const advtg_detector* d, advtg_warning* out,
                                                 int32_t cap);

typedef struct advtg_metrics_config {
    double sample_rate;
    double bph;
    double lift_angle_deg;
    double adc_drift_ppm;
    double adc_drift_sigma_ppm;
    double window_s;
    double window_step_s;
    int32_t quartz;
    int32_t has_lift_angle;
    int32_t compute_windows;
    int32_t _pad;
} advtg_metrics_config;

ADVTG_C_API void advtg_metrics_config_default(advtg_metrics_config* cfg, double sample_rate,
                                              double bph);
/* Compute batch metrics over beats[0..n_beats) using templates from det (or empty if det == NULL).
 * Thread-local windows and warnings from this call are readable via advtg_metrics_last_*(). */
ADVTG_C_API int advtg_metrics_compute(const advtg_detector* det, const advtg_beat* beats,
                                      size_t n_beats, const advtg_metrics_config* cfg,
                                      advtg_metrics* out);
ADVTG_C_API int64_t advtg_metrics_last_windows(advtg_window_rate* out, int64_t cap);
ADVTG_C_API int32_t advtg_metrics_last_warnings(char* buf, int32_t cap);
ADVTG_C_API int32_t advtg_metrics_last_warning_codes(advtg_warning* out, int32_t cap);

/* ------------------------------------------------------------------ GPS PPS detector & calibrator */
typedef struct advtg_pps_pulse {
    double index;
    double snr_db;
    double peak;
} advtg_pps_pulse;

typedef struct advtg_pps_detector advtg_pps_detector;

ADVTG_C_API advtg_pps_detector* advtg_pps_detector_create(double sample_rate,
                                                          double threshold_sigma);
ADVTG_C_API void advtg_pps_detector_destroy(advtg_pps_detector* d);
ADVTG_C_API void advtg_pps_detector_reset(advtg_pps_detector* d, int64_t start_index);
/* Feed chunk[0..n). Returns total pulses found (>= 0, or < 0 on error); copies up to cap into
 * out and stores full list in d (readable via advtg_pps_detector_last_pulses). */
ADVTG_C_API int64_t advtg_pps_detector_process(advtg_pps_detector* d, const double* chunk,
                                               size_t n, int64_t start_index,
                                               advtg_pps_pulse* out, int64_t cap);
ADVTG_C_API int64_t advtg_pps_detector_last_pulses(const advtg_pps_detector* d,
                                                   advtg_pps_pulse* out, int64_t cap);
ADVTG_C_API int64_t advtg_pps_detector_rejected(const advtg_pps_detector* d);

typedef struct advtg_pps_estimate {
    advtg_adc_drift drift;
    int64_t pulses;
    int64_t used;
    int64_t missing;
    int64_t jumps;
    int64_t rejected;
    double span_s;
    double jitter_us;
    double excess;
    int32_t valid;
    int32_t ready;
    char note[256];
} advtg_pps_estimate;

typedef struct advtg_pps_calibrator advtg_pps_calibrator;

ADVTG_C_API advtg_pps_calibrator* advtg_pps_calibrator_create(double sample_rate,
                                                              double nominal_period_s,
                                                              double reference_sigma_ppm);
ADVTG_C_API void advtg_pps_calibrator_destroy(advtg_pps_calibrator* c);
ADVTG_C_API void advtg_pps_calibrator_reset(advtg_pps_calibrator* c);
ADVTG_C_API int advtg_pps_calibrator_add(advtg_pps_calibrator* c, const double* indices,
                                         size_t n);
ADVTG_C_API int advtg_pps_calibrator_estimate(advtg_pps_calibrator* c, advtg_pps_estimate* out);
/* Returns total segment points (>= 0); copies up to cap (t_s, residual_us) from last estimate. */
ADVTG_C_API int64_t advtg_pps_calibrator_residuals(const advtg_pps_calibrator* c, double* t_s,
                                                   double* residual_us, int64_t cap);
ADVTG_C_API int advtg_pps_calibrate_samples(const double* samples, size_t n, double sample_rate,
                                            double chunk_s, advtg_pps_estimate* out);

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

/* ------------------------------------------------------------------ audio file I/O (libsndfile) */
typedef struct advtg_audio_file_info {
    int64_t frames;
    int32_t sample_rate;
    int32_t channels;
    double duration_s;
    char format[16];
    char subtype[16];
} advtg_audio_file_info;

typedef struct advtg_audio_tag {
    const char* key;
    const char* value; /* NULL deletes the tag on update */
} advtg_audio_tag;

typedef void (*advtg_tag_cb)(const char* key, const char* value, void* user);

ADVTG_C_API int advtg_set_sndfile_library(const char* path);
ADVTG_C_API const char* advtg_audio_last_error(void);
ADVTG_C_API int advtg_audio_info(const char* path, advtg_audio_file_info* out);
/* Read decoded samples (interleaved float64) into out[0..cap). If out == NULL or cap is smaller
 * than frames * channels, returns ADVTG_ERR_INVALID (call advtg_audio_info first).
 * max_bytes <= 0 disables the memory limit check. comment_buf may be NULL. */
ADVTG_C_API int advtg_audio_read_f64(const char* path, int64_t max_bytes, double* out,
                                     size_t cap_samples, advtg_audio_file_info* info_out,
                                     char* comment_buf, int32_t comment_cap);
/* Select one channel from interleaved float64 (frames x channels). mode == -1 selects max_energy;
 * mode >= 0 selects that channel index. */
ADVTG_C_API int advtg_audio_select_channel(const double* interleaved, int64_t frames,
                                           int32_t channels, int32_t mode, double* mono_out);
ADVTG_C_API int advtg_audio_read_mono(const char* path, int32_t channel_mode, int64_t max_bytes,
                                      double* out, size_t cap_frames,
                                      advtg_audio_file_info* info_out);
ADVTG_C_API int advtg_audio_read_tags(const char* path, advtg_tag_cb cb, void* user);
ADVTG_C_API int advtg_audio_update_tags(const char* path, const advtg_audio_tag* tags,
                                        size_t n_tags);
ADVTG_C_API int advtg_audio_write_f64(const char* path, const double* samples, int64_t frames,
                                      int32_t channels, int32_t sample_rate,
                                      const char* subtype, const char* comment,
                                      const advtg_audio_tag* tags, size_t n_tags,
                                      int64_t* clipped_out);

typedef struct advtg_audio_reader advtg_audio_reader;
ADVTG_C_API advtg_audio_reader* advtg_audio_reader_open(const char* path, int64_t blocksize,
                                                        int32_t channel_mode);
ADVTG_C_API int advtg_audio_reader_info(const advtg_audio_reader* r, advtg_audio_file_info* out,
                                        int32_t* selected_channel_out);
/* Read next block. Returns frames read (> 0), 0 on EOF, or < 0 on error. */
ADVTG_C_API int64_t advtg_audio_reader_read_block(advtg_audio_reader* r, double* out,
                                                  size_t cap_samples, int64_t* start_index_out);
ADVTG_C_API void advtg_audio_reader_close(advtg_audio_reader* r);

typedef struct advtg_audio_writer advtg_audio_writer;
ADVTG_C_API advtg_audio_writer* advtg_audio_writer_open(const char* path, int32_t sample_rate,
                                                        int32_t channels, const char* subtype,
                                                        const char* comment,
                                                        const advtg_audio_tag* tags,
                                                        size_t n_tags);
ADVTG_C_API int advtg_audio_writer_write(advtg_audio_writer* w, const double* samples,
                                         int64_t frames, int32_t channels,
                                         int64_t* clipped_now_out);
ADVTG_C_API int advtg_audio_writer_set_tag(advtg_audio_writer* w, const char* key,
                                           const char* value);
ADVTG_C_API int advtg_audio_writer_close(advtg_audio_writer* w, int discard,
                                         int64_t* frames_written_out,
                                         int64_t* clipped_total_out);

/* ------------------------------------------------------------------ paths & logger */
ADVTG_C_API int32_t advtg_config_dir(char* buf, int32_t cap);
ADVTG_C_API int32_t advtg_default_log_dir(char* buf, int32_t cap);
ADVTG_C_API int advtg_log_init(const char* log_dir, const char* log_name);
ADVTG_C_API int advtg_log_write(int level, const char* component, const char* msg);
ADVTG_C_API int32_t advtg_log_file_path(char* buf, int32_t cap);

/* ------------------------------------------------------------------ security */
ADVTG_C_API int32_t advtg_sanitize_csv_cell(const char* in, char* out, int32_t cap);
ADVTG_C_API int32_t advtg_safe_filename_base(const char* name, const char* fallback, char* out,
                                             int32_t cap);
ADVTG_C_API int32_t advtg_anonymize_text(const char* text, const char* username,
                                         const char* home_variants_nl, char* out, int32_t cap);

/* ------------------------------------------------------------------ watch caliber database */
typedef struct advtg_watch_caliber {
    char name[128];
    int32_t bph;
    int32_t _pad;
    double lift_angle_deg;
} advtg_watch_caliber;

/* Returns total calibers loaded (>= 1); copies up to cap entries into out. */
ADVTG_C_API int32_t advtg_load_watch_db(const char* csv_path, advtg_watch_caliber* out,
                                        int32_t cap);

/* ------------------------------------------------------------------ calibration store & tags */
typedef struct advtg_calibration {
    char device[256];
    int32_t sample_rate;
    int32_t _pad;
    advtg_adc_drift drift;
    int64_t measured_at_epoch_s;
    char note[256];
} advtg_calibration;

ADVTG_C_API const char* advtg_cal_last_error(void);
ADVTG_C_API int32_t advtg_cal_format_iso(int64_t epoch_s, char* buf, int32_t cap);
ADVTG_C_API int advtg_cal_parse_iso(const char* iso, int64_t* epoch_s_out);
ADVTG_C_API double advtg_cal_age_days(int64_t measured_at_epoch_s, int64_t now_epoch_s);
ADVTG_C_API void advtg_cal_effective(const advtg_calibration* cal, int64_t now_epoch_s,
                                     double aging_ppm_per_year, advtg_adc_drift* drift_out);
/* Serialize / parse ADV_TG_ADC_DRIFT_* tags (advtg_tag_cb receives each key/value). */
ADVTG_C_API int advtg_drift_to_tags(const advtg_adc_drift* drift, const char* note,
                                    const char* calibrated_at_iso, advtg_tag_cb cb, void* user);
/* Returns 1 if parsed, 0 if ADV_TG_ADC_DRIFT_PPM missing, < 0 on invalid tags. */
ADVTG_C_API int advtg_drift_from_tags(const advtg_audio_tag* tags, size_t n_tags,
                                      advtg_adc_drift* drift_out, char* note_buf, int32_t note_cap);
/* Store operations (path == NULL uses config_dir()/adc_calibration.json). */
ADVTG_C_API int32_t advtg_cal_store_records(const char* store_path, const char* device_filter,
                                            int32_t sample_rate_filter, advtg_calibration* out,
                                            int32_t cap);
/* Returns 1 if found, 0 if none, < 0 on error (ADVTG_ERR_CORRUPT_STORE on corrupt JSON). */
ADVTG_C_API int advtg_cal_store_best(const char* store_path, const char* device,
                                     int32_t sample_rate, int64_t now_epoch_s,
                                     double aging_ppm_per_year, advtg_calibration* out);
ADVTG_C_API int advtg_cal_store_add(const char* store_path, const advtg_calibration* cal);
ADVTG_C_API int32_t advtg_cal_store_forget(const char* store_path, const char* device,
                                           int32_t sample_rate_filter);
ADVTG_C_API int advtg_cal_recording_tags(const char* store_path, const char* device,
                                         int32_t sample_rate, int64_t now_epoch_s,
                                         advtg_tag_cb cb, void* user,
                                         int* has_cal_out, advtg_calibration* cal_out);

/* ------------------------------------------------------------------ audio devices, CSV & report */
typedef struct advtg_audio_device_info {
    int32_t device_id;         /* -1 for synthetic generator */
    int32_t max_input_channels;
    double default_samplerate;
    int32_t is_synthetic;
    int32_t _pad;
    char name[256];
} advtg_audio_device_info;

ADVTG_C_API int advtg_set_portaudio_library(const char* path);
ADVTG_C_API int32_t advtg_list_input_devices(advtg_audio_device_info* out, int32_t cap);
ADVTG_C_API int32_t advtg_audio_devices_text(char* buf, int32_t cap);

typedef struct advtg_csv_result_row {
    const char* test_name;
    const char* caliber;
    int32_t bph;
    double lift_angle;
    int32_t sample_rate;
    const char* position;
    const char* record_type;
    const char* elapsed_s;
    const char* rate_spd;
    const char* beat_error_ms;
    const char* amplitude_deg;
    const char* confidence;
    const char* snr_db;
} advtg_csv_result_row;

typedef struct advtg_csv_global_row {
    const char* test_name;
    const char* caliber;
    int32_t bph;
    double lift_angle;
    const char* position;
    const char* rate_spd;
    const char* beat_error_ms;
    const char* amplitude_deg;
    const char* confidence;
    const char* global_mean_spd;
    const char* positional_delta_spd;
} advtg_csv_global_row;

ADVTG_C_API int advtg_write_results_csv(const char* path, const advtg_csv_result_row* rows,
                                        size_t n_rows);
ADVTG_C_API int advtg_write_global_stats_csv(const char* path, const advtg_csv_global_row* rows,
                                             size_t n_rows);
ADVTG_C_API int advtg_write_traces_csv(const char* path, const double* even_x, const double* even_y,
                                       size_t n_even, const double* odd_x, const double* odd_y,
                                       size_t n_odd, const double* osc_x, const double* osc_y,
                                       size_t n_osc, const double* wav_x, const double* wav_y,
                                       size_t n_wav);

ADVTG_C_API int32_t advtg_report_default_name(const char* version, int64_t now_epoch_s, char* buf,
                                              int32_t cap);
/* Build bug/result .zip report atomically with miniz. notes_buf receives '\n'-separated Spanish notes. */
ADVTG_C_API int advtg_report_build(const char* dest_path, const char* version,
                                   const char* system_info, const char* runtime_info,
                                   const char* description,
                                   const advtg_audio_tag* settings, size_t n_settings,
                                   const char* recording_path, const char* log_dir,
                                   const char* calibrations_path, const char* username,
                                   const char* home_variants_nl, int64_t max_logs_bytes,
                                   int64_t max_recording_bytes, char* notes_buf,
                                   int32_t notes_cap);

/* ------------------------------------------------------------------ gear-train & diagnosis */
enum {
    ADVTG_GEAR_COMP_ESCAPE = 0,
    ADVTG_GEAR_COMP_FOURTH = 1,
    ADVTG_GEAR_SERIES_TIMING = 0,
    ADVTG_GEAR_SERIES_ENERGY = 1
};

enum {
    ADVTG_GEAR_WARN_SHORT_ESCAPE = 1,
    ADVTG_GEAR_WARN_SHORT_FOURTH = 2,
    ADVTG_GEAR_WARN_MANY_MISSING = 4
};

typedef struct advtg_gear_component {
    int32_t name;            /* ADVTG_GEAR_COMP_* */
    int32_t series;          /* ADVTG_GEAR_SERIES_* */
    double expected_period_s;
    double period_s;
    double amplitude;
    double rate_ripple_s_per_day;
    double fap;
    int32_t resolvable;
    int32_t significant;
} advtg_gear_component;

typedef struct advtg_gear_summary {
    double sample_rate;
    double bph;
    double duration_s;
    double rate_s_per_day;
    double lift_angle_deg;
    double escape_period_s;
    int64_t beats_detected;
    int64_t missing_beats;
    int64_t outliers;
    int64_t n_series;
    int32_t escape_teeth;
    uint32_t warning_mask;   /* ADVTG_GEAR_WARN_* */
} advtg_gear_summary;

typedef struct advtg_feature_vector {
    double window_start_s;
    double window_end_s;
    double rate_spd;
    double beat_error_ms;
    double amplitude_deg;
    double escape_power;
    double escape_amp_us;
    double fourth_power;
    double fourth_amp_us;
    double d12_mean_ms;
    double d12_var_ms2;
    double d12_skew;
    double d12_kurt;
    double d23_mean_ms;
    double d23_var_ms2;
    double d23_skew;
    double d23_kurt;
    double e1_e2;
    double e3_e2;
    double spurious_fraction;
    int64_t n_beats;
    int64_t n_subevents;
    int64_t timing_jumps;
    int32_t whole;
    int32_t _pad;
} advtg_feature_vector;

enum {
    ADVTG_FINDING_REBANKING = 1,
    ADVTG_FINDING_PALLET_LUBRICATION = 2,
    ADVTG_FINDING_MAGNETISM = 3,
    ADVTG_FINDING_ESCAPE_WHEEL = 4,
    ADVTG_FINDING_FOURTH_WHEEL = 5
};
enum { ADVTG_FINDING_POSSIBLE = 0, ADVTG_FINDING_PROBABLE = 1 };

typedef struct advtg_diagnostic_finding {
    int32_t code;            /* ADVTG_FINDING_* */
    int32_t level;           /* ADVTG_FINDING_POSSIBLE / PROBABLE */
    double values[4];
    char keys[4][32];
    int32_t n_evidence;
    int32_t _pad;
} advtg_diagnostic_finding;

typedef struct advtg_positional_record {
    char name[8];            /* "DU", "DD", "CU", "CD", "CL", "CR" */
    double rate_spd;
    double beat_error_ms;
    double amplitude_deg;
} advtg_positional_record;

typedef struct advtg_positional_deltas {
    double delta_rate_hv_spd;
    double delta_amp_hv_deg;
    double be_spread_ms;
    int32_t valid;
    int32_t n_positions;
    char positions[6][8];
} advtg_positional_deltas;

typedef struct advtg_gear_analysis advtg_gear_analysis;

ADVTG_C_API double advtg_escape_wheel_period_s(double bph, int32_t teeth);
ADVTG_C_API const char* advtg_gear_last_error(void);
ADVTG_C_API advtg_gear_analysis* advtg_gear_extract(const double* samples, size_t n,
                                                    double sample_rate, double bph,
                                                    double lift_angle_deg, int32_t escape_teeth,
                                                    int32_t* err_out, int64_t* beats_found_out);
ADVTG_C_API int advtg_gear_recompute(advtg_gear_analysis* h, int32_t escape_teeth);
ADVTG_C_API void advtg_gear_destroy(advtg_gear_analysis* h);
ADVTG_C_API int advtg_gear_get_summary(const advtg_gear_analysis* h, advtg_gear_summary* out);
ADVTG_C_API int advtg_gear_get_components(const advtg_gear_analysis* h,
                                          advtg_gear_component out[4]);
ADVTG_C_API int advtg_gear_series_copy(const advtg_gear_analysis* h, int64_t* k, double* t_s,
                                       double* timing_residual_s, double* energy_rel,
                                       double* p1_s, double* p2_s, double* p3_s, double* e1,
                                       double* e2, double* e3, double* pre_p1_db,
                                       double* amplitude_deg, int64_t cap);
ADVTG_C_API int64_t advtg_gear_outliers_copy(const advtg_gear_analysis* h, double* outlier_t_s,
                                             int64_t cap);
ADVTG_C_API void advtg_gear_spectra_len(const advtg_gear_analysis* h, int64_t* ls_len_out,
                                        int64_t* fft_len_out);
ADVTG_C_API int advtg_gear_spectra_copy(const advtg_gear_analysis* h, int32_t series_kind,
                                        double* freqs_hz, double* ls_power, int64_t ls_cap,
                                        double* fft_freqs_hz, double* fft_amp, int64_t fft_cap);
ADVTG_C_API int32_t advtg_gear_window_features(const advtg_gear_analysis* h, int32_t escape_teeth,
                                               double window_s, advtg_feature_vector* out,
                                               int32_t cap);
ADVTG_C_API int advtg_gear_recording_features(const advtg_gear_analysis* h, int32_t escape_teeth,
                                              advtg_feature_vector* out);
ADVTG_C_API int advtg_compute_positional_deltas(const advtg_positional_record* recs, size_t n_recs,
                                                advtg_positional_deltas* out);
ADVTG_C_API int32_t advtg_diagnose(const advtg_feature_vector* whole,
                                   const advtg_feature_vector* windows, size_t n_windows,
                                   const advtg_positional_deltas* positional,
                                   const advtg_gear_component* components, size_t n_components,
                                   advtg_diagnostic_finding* out, int32_t cap);

/* ------------------------------------------------------------------ mock & synthetic watch */
typedef struct advtg_mock_telemetry_sample {
    double rate_spd;
    double beat_error_ms;
    double amplitude_deg;
    double confidence;
    double snr_db;
} advtg_mock_telemetry_sample;

typedef struct advtg_mock_telemetry_drift {
    double effective_fs;
    double drift_ppm;
    double rate_bias_spd;
    double sigma_ppm;
    double confidence;
} advtg_mock_telemetry_drift;

typedef struct advtg_mock_telemetry advtg_mock_telemetry;

ADVTG_C_API double advtg_mock_beat_delta_t_s(double amplitude_deg, double lift_angle_deg,
                                             int32_t bph);
ADVTG_C_API double advtg_mock_beat_interval_s(int32_t bph, double rate_spd);
ADVTG_C_API void advtg_mock_add_beat(double* buf, size_t n, double fs, double t_beat_s,
                                     double dt_p3_s, int is_tock);
ADVTG_C_API int advtg_mock_generate_tick_train(double duration_s, int32_t fs, int32_t bph,
                                               double rate_spd, double beat_error_ms,
                                               double amplitude_deg, double lift_angle_deg,
                                               double snr_db, uint64_t seed, double* out,
                                               size_t cap);
ADVTG_C_API advtg_mock_telemetry* advtg_mock_telemetry_create(uint64_t seed);
ADVTG_C_API void advtg_mock_telemetry_destroy(advtg_mock_telemetry* m);
ADVTG_C_API void advtg_mock_telemetry_step(advtg_mock_telemetry* m, double elapsed_s, double dt_s,
                                           double stabilization_s, double base_rate_spd,
                                           double base_beat_error_ms, double base_amplitude_deg,
                                           advtg_mock_telemetry_sample* out);
ADVTG_C_API void advtg_mock_telemetry_get_drift(advtg_mock_telemetry* m, double elapsed_s,
                                                double nominal_fs,
                                                advtg_mock_telemetry_drift* out);

/* Analytic synthetic mechanical watch for C++ tests (mirrors tests/synth.py). */
typedef struct advtg_synth_config {
    double bph;
    double rate_s_per_day;
    double beat_error_ms;
    double amplitude_deg;
    double lift_angle_deg;
    double snr_db;
    double jitter_us;
    double odd_jitter_us;
    double p1_gain;
    double phase_mod_period_s[2];
    double phase_mod_amp_us[2];
    double level_mod_period_s[2];
    double level_mod_depth[2];
    int32_t sample_rate;
    int32_t quartz;
    double seconds;
    uint64_t seed;
} advtg_synth_config;

ADVTG_C_API void advtg_synth_config_default(advtg_synth_config* cfg);
ADVTG_C_API int64_t advtg_synth_watch(const advtg_synth_config* cfg, double* out, size_t cap);

#ifdef __cplusplus
}
#endif

#endif /* ADVTG_H */
