/*
 * advtg_replay - run the native measurement session over a WAV file, in plain C99.
 *
 * Reference of what a mobile app does with advtg.h: write mono float32 blocks into the
 * session's stream (here 20 ms "callbacks" with synthetic wall-clock stamps), update the
 * session 4 times per second and read the displayed figures. No Python involved.
 *
 *     advtg_replay take.wav 28800 [lift_angle_deg] [quartz]
 *
 * WAV: PCM 16/24/32-bit or IEEE float 32-bit, any channel count (channel 0 is used).
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "advtg.h"

typedef struct {
    float* x;
    size_t n;
    double fs;
} mono_t;

static uint32_t rd32(const unsigned char* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int read_wav(const char* path, mono_t* out) {
    FILE* f = fopen(path, "rb");
    unsigned char hdr[12], ch[8];
    unsigned fmt = 0, channels = 0, bits = 0;
    double fs = 0.0;
    unsigned char* data = NULL;
    uint32_t data_len = 0;
    if (!f) return -1;
    if (fread(hdr, 1, 12, f) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) {
        fclose(f);
        return -2;
    }
    while (fread(ch, 1, 8, f) == 8) {
        uint32_t len = rd32(ch + 4);
        if (!memcmp(ch, "fmt ", 4)) {
            unsigned char b[40];
            uint32_t k = len < sizeof b ? len : (uint32_t)sizeof b;
            if (fread(b, 1, k, f) != k) break;
            if (len > k) fseek(f, (long)(len - k), SEEK_CUR);
            fmt = b[0] | (b[1] << 8);
            channels = b[2] | (b[3] << 8);
            fs = (double)rd32(b + 4);
            bits = b[14] | (b[15] << 8);
            if (fmt == 0xFFFE && k >= 26) fmt = b[24] | (b[25] << 8); /* extensible */
        } else if (!memcmp(ch, "data", 4)) {
            data = (unsigned char*)malloc(len);
            if (!data || fread(data, 1, len, f) != len) break;
            data_len = len;
        } else {
            fseek(f, (long)(len + (len & 1)), SEEK_CUR);
        }
    }
    fclose(f);
    if (!data || !channels || !(fs > 0) || !(fmt == 1 || fmt == 3)) {
        free(data);
        return -3;
    }
    {
        size_t bps = bits / 8, frame = bps * channels, n = data_len / frame, i;
        out->x = (float*)malloc(n * sizeof(float));
        out->n = n;
        out->fs = fs;
        for (i = 0; i < n; ++i) {
            const unsigned char* p = data + i * frame;
            float v = 0.0f;
            if (fmt == 3 && bits == 32) {
                memcpy(&v, p, 4);
            } else if (bits == 16) {
                v = (float)(int16_t)(p[0] | (p[1] << 8)) / 32768.0f;
            } else if (bits == 24) {
                int32_t s = (int32_t)((uint32_t)p[0] << 8 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 24);
                v = (float)(s / 2147483648.0);
            } else if (bits == 32) {
                v = (float)((int32_t)rd32(p) / 2147483648.0);
            }
            out->x[i] = v;
        }
    }
    free(data);
    return 0;
}

int main(int argc, char** argv) {
    mono_t a;
    advtg_session_config cfg;
    advtg_session* s;
    advtg_snapshot snap;
    void* stream;
    size_t block, pos, n_cb = 0, every;
    int64_t t0 = 1700000000000000000LL;
    if (argc < 3) {
        fprintf(stderr, "uso: %s archivo.wav BPH [angulo_levantamiento] [cuarzo]\n", argv[0]);
        return 2;
    }
    if (read_wav(argv[1], &a) != 0) {
        fprintf(stderr, "no se pudo leer %s (WAV PCM 16/24/32 o float32)\n", argv[1]);
        return 2;
    }
    advtg_session_config_default(&cfg, a.fs, atof(argv[2]));
    if (argc > 3) cfg.lift_angle_deg = atof(argv[3]);
    if (argc > 4) cfg.quartz = atoi(argv[4]);
    s = advtg_session_create(&cfg, NULL);
    if (!s) {
        fprintf(stderr, "error: %s\n", advtg_session_last_error(NULL));
        return 1;
    }
    printf("motor %s (ABI %d, SIMD %s) fs=%.0f Hz BPH=%s, %.1f s de audio\n", advtg_version(),
           advtg_abi_version(), advtg_simd_name(), a.fs, argv[2], (double)a.n / a.fs);
    stream = advtg_session_stream(s);
    block = (size_t)(0.020 * a.fs);          /* 20 ms audio callbacks */
    every = (size_t)(0.25 / 0.020 + 0.5);    /* 4 updates per second */
    memset(&snap, 0, sizeof snap);
    for (pos = 0; pos < a.n; pos += block, ++n_cb) {
        size_t m = a.n - pos < block ? a.n - pos : block;
        int64_t wall = t0 + (int64_t)((double)(pos + m) / a.fs * 1e9);
        advtg_stream_write_f32(stream, a.x + pos, m, wall, 1, 0, wall);
        if (n_cb % every == 0 || pos + m >= a.n) {
            if (advtg_session_update(s, &snap) != 0) {
                fprintf(stderr, "error: %s\n", advtg_session_last_error(s));
                break;
            }
            if (n_cb % (every * 20) == 0)
                printf("t=%6.1f s  marcha %+7.2f s/d  BE %5.2f ms  amplitud %5.1f°  conf %.2f\n",
                       snap.audio_s, snap.display.rate_s_per_day, snap.display.beat_error_ms,
                       snap.display.amplitude_deg, snap.display.confidence);
        }
    }
    printf("FINAL t=%.1f s latidos=%lld enganchado=%d marcha=%+.3f s/d BE=%.3f ms "
           "amplitud=%.1f° SNR=%.1f dB\n",
           snap.audio_s, (long long)snap.beats_total, snap.locked, snap.display.rate_s_per_day,
           snap.display.beat_error_ms, snap.display.amplitude_deg, snap.snr_db);
    advtg_session_destroy(s);
    free(a.x);
    return 0;
}
