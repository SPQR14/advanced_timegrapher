// GIL-free PortAudio C++20 live audio capture into NativeSampleStream.
// Dynamically resolves PortAudio at runtime (dlopen/LoadLibraryW) so the engine has zero
// compile-time PortAudio header or link dependency on any OS.
#include "advtg_runtime.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <new>

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <dlfcn.h>
#endif

namespace advtg::runtime {

namespace {

// Minimal ABI-compatible PortAudio C definitions (v19).
constexpr unsigned long kPaFloat32 = 0x00000001UL;
constexpr int kPaContinue = 0;
constexpr int kPaNoDevice = -1;
constexpr int kPaFormatIsSupported = 0;

struct PaDeviceInfo {
    int structVersion;
    const char* name;
    int hostApi;
    int maxInputChannels;
    int maxOutputChannels;
    double defaultLowInputLatency;
    double defaultLowOutputLatency;
    double defaultHighInputLatency;
    double defaultHighOutputLatency;
    double defaultSampleRate;
};

struct PaStreamParameters {
    int device;
    int channelCount;
    unsigned long sampleFormat;
    double suggestedLatency;
    void* hostApiSpecificStreamInfo;
};

struct PaStreamCallbackTimeInfo {
    double inputBufferAdcTime;
    double currentTime;
    double outputBufferDacTime;
};

struct PaStreamInfo {
    int structVersion;
    double inputLatency;
    double outputLatency;
    double sampleRate;
};

using PaStreamCallbackFn = int (*)(const void*, void*, unsigned long,
                                   const PaStreamCallbackTimeInfo*, unsigned long, void*);

inline void atomic_max_i64(std::atomic<std::int64_t>& dst, std::int64_t v) noexcept {
    std::int64_t cur = dst.load(std::memory_order_relaxed);
    while (v > cur && !dst.compare_exchange_weak(cur, v, std::memory_order_relaxed)) {
    }
}

// Same edges (us) as adv_tg.audio.gil_stats.GIL_WAIT_EDGES_MS * 1000.0 (11 edges -> 12 buckets).
inline constexpr std::array<double, 11> kWaitEdgesUs = {
    100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0, 50000.0, 100000.0, 250000.0, 500000.0,
};

inline int wait_bucket(double wait_us) noexcept {
    for (std::size_t i = 0; i < kWaitEdgesUs.size(); ++i) {
        if (wait_us < kWaitEdgesUs[i]) return static_cast<int>(i);
    }
    return static_cast<int>(kWaitEdgesUs.size());
}

int pa_trampoline(const void* input, void* /*output*/, unsigned long frames,
                  const PaStreamCallbackTimeInfo* time_info, unsigned long status_flags,
                  void* user_data) noexcept {
    auto* self = static_cast<NativeAudioCapture*>(user_data);
    return self->on_pa_callback(static_cast<const float*>(input), frames, time_info, status_flags);
}

#if defined(_WIN32)
void* open_pa_handle(const char* hint) {
    if (hint && *hint) {
        int wlen = MultiByteToWideChar(CP_UTF8, 0, hint, -1, nullptr, 0);
        if (wlen > 0) {
            std::wstring ws(static_cast<std::size_t>(wlen), L'\0');
            MultiByteToWideChar(CP_UTF8, 0, hint, -1, ws.data(), wlen);
            HMODULE m = LoadLibraryW(ws.c_str());
            if (m) return reinterpret_cast<void*>(m);
        }
    }
    const wchar_t* names[] = {L"portaudio.dll", L"libportaudio.dll", L"portaudio_x64.dll"};
    for (const wchar_t* n : names) {
        HMODULE m = GetModuleHandleW(n);
        if (m) return reinterpret_cast<void*>(m);
        m = LoadLibraryW(n);
        if (m) return reinterpret_cast<void*>(m);
    }
    return nullptr;
}
void* pa_sym(void* h, const char* name) {
    return reinterpret_cast<void*>(GetProcAddress(reinterpret_cast<HMODULE>(h), name));
}
#else
void* open_pa_handle(const char* hint) {
    if (hint && *hint) {
        if (void* h = dlopen(hint, RTLD_LAZY | RTLD_LOCAL)) return h;
    }
    if (dlsym(RTLD_DEFAULT, "Pa_Initialize")) return RTLD_DEFAULT;
    const char* cands[] = {
        "libportaudio.so.2",
        "libportaudio.so",
        "libportaudio.2.dylib",
        "libportaudio.dylib",
    };
    for (const char* c : cands) {
        if (void* h = dlopen(c, RTLD_LAZY | RTLD_LOCAL)) return h;
    }
    return nullptr;
}
void* pa_sym(void* h, const char* name) { return dlsym(h, name); }
#endif

}  // namespace

struct NativeAudioCapture::PaApi {
    void* lib = nullptr;
    int (*Initialize)() = nullptr;
    int (*Terminate)() = nullptr;
    const char* (*GetErrorText)(int) = nullptr;
    int (*GetDefaultInputDevice)() = nullptr;
    const PaDeviceInfo* (*GetDeviceInfo)(int) = nullptr;
    int (*IsFormatSupported)(const PaStreamParameters*, const PaStreamParameters*, double) = nullptr;
    int (*OpenStream)(void**, const PaStreamParameters*, const PaStreamParameters*, double,
                      unsigned long, unsigned long, PaStreamCallbackFn, void*) = nullptr;
    int (*CloseStream)(void*) = nullptr;
    int (*StartStream)(void*) = nullptr;
    int (*StopStream)(void*) = nullptr;
    int (*IsStreamActive)(void*) = nullptr;
    const PaStreamInfo* (*GetStreamInfo)(void*) = nullptr;
    double (*GetStreamTime)(void*) = nullptr;

    bool load(const char* hint) {
        lib = open_pa_handle(hint);
        if (!lib) return false;
        Initialize = reinterpret_cast<decltype(Initialize)>(pa_sym(lib, "Pa_Initialize"));
        Terminate = reinterpret_cast<decltype(Terminate)>(pa_sym(lib, "Pa_Terminate"));
        GetErrorText = reinterpret_cast<decltype(GetErrorText)>(pa_sym(lib, "Pa_GetErrorText"));
        GetDefaultInputDevice = reinterpret_cast<decltype(GetDefaultInputDevice)>(
            pa_sym(lib, "Pa_GetDefaultInputDevice"));
        GetDeviceInfo = reinterpret_cast<decltype(GetDeviceInfo)>(pa_sym(lib, "Pa_GetDeviceInfo"));
        IsFormatSupported =
            reinterpret_cast<decltype(IsFormatSupported)>(pa_sym(lib, "Pa_IsFormatSupported"));
        OpenStream = reinterpret_cast<decltype(OpenStream)>(pa_sym(lib, "Pa_OpenStream"));
        CloseStream = reinterpret_cast<decltype(CloseStream)>(pa_sym(lib, "Pa_CloseStream"));
        StartStream = reinterpret_cast<decltype(StartStream)>(pa_sym(lib, "Pa_StartStream"));
        StopStream = reinterpret_cast<decltype(StopStream)>(pa_sym(lib, "Pa_StopStream"));
        IsStreamActive =
            reinterpret_cast<decltype(IsStreamActive)>(pa_sym(lib, "Pa_IsStreamActive"));
        GetStreamInfo = reinterpret_cast<decltype(GetStreamInfo)>(pa_sym(lib, "Pa_GetStreamInfo"));
        GetStreamTime = reinterpret_cast<decltype(GetStreamTime)>(pa_sym(lib, "Pa_GetStreamTime"));
        return Initialize && Terminate && GetDeviceInfo && IsFormatSupported && OpenStream &&
               CloseStream && StartStream && StopStream;
    }
};

NativeAudioCapture::NativeAudioCapture(NativeSampleStream* stream, int device, double sample_rate,
                                       std::size_t blocksize, int high_latency)
    : stream_(stream),
      device_(device),
      sample_rate_(sample_rate),
      blocksize_(blocksize),
      high_latency_(high_latency) {
    pa_ = new PaApi();
    ch_window_ = std::max<std::size_t>(1, static_cast<std::size_t>(std::llround(0.25 * sample_rate_)));
}

NativeAudioCapture::~NativeAudioCapture() {
    close();
    delete pa_;
    pa_ = nullptr;
    aligned_free_bytes(ch_energy_);
    aligned_free_bytes(mono_buf_);
}

int NativeAudioCapture::open(const char* pa_lib_path, int requested_channels) {
    if (pa_stream_) return 0;
    if (!stream_) {
        last_error_ = "NativeSampleStream nulo";
        return -1;
    }
    if (!pa_->load(pa_lib_path)) {
        last_error_ = "No se pudo cargar libportaudio nativa";
        return -2;
    }
    const int init_rc = pa_->Initialize();
    if (init_rc < 0) {
        last_error_ = pa_->GetErrorText ? pa_->GetErrorText(init_rc) : "Pa_Initialize falló";
        return init_rc;
    }
    pa_initialized_ = true;

    int dev = (device_ >= 0) ? device_ : (pa_->GetDefaultInputDevice ? pa_->GetDefaultInputDevice() : kPaNoDevice);
    if (dev == kPaNoDevice || dev < 0) {
        last_error_ = "No hay dispositivo de entrada de audio predeterminado";
        return -3;
    }
    const PaDeviceInfo* dinfo = pa_->GetDeviceInfo(dev);
    if (!dinfo) {
        last_error_ = "Pa_GetDeviceInfo devolvió nullptr";
        return -4;
    }
    const double latency =
        high_latency_ ? dinfo->defaultHighInputLatency : dinfo->defaultLowInputLatency;

    int try_ch[2] = {std::max(1, requested_channels), 1};
    int n_try = (try_ch[0] > 1) ? 2 : 1;
    int last_err = -1;
    for (int i = 0; i < n_try; ++i) {
        const int ch = try_ch[i];
        PaStreamParameters params{};
        params.device = dev;
        params.channelCount = ch;
        params.sampleFormat = kPaFloat32;
        params.suggestedLatency = latency;
        params.hostApiSpecificStreamInfo = nullptr;

        int rc = pa_->IsFormatSupported(&params, nullptr, sample_rate_);
        if (rc != kPaFormatIsSupported) {
            last_err = rc;
            continue;
        }
        void* st = nullptr;
        rc = pa_->OpenStream(&st, &params, nullptr, sample_rate_,
                             static_cast<unsigned long>(blocksize_), 0, &pa_trampoline, this);
        if (rc != 0 || !st) {
            last_err = rc ? rc : -5;
            continue;
        }
        pa_stream_ = st;
        channels_ = ch;
        break;
    }
    if (!pa_stream_) {
        last_error_ = pa_->GetErrorText ? pa_->GetErrorText(last_err) : "Pa_OpenStream falló";
        return last_err;
    }

    aligned_free_bytes(ch_energy_);
    ch_energy_ = static_cast<double*>(aligned_alloc_bytes(
        static_cast<std::size_t>(channels_) * sizeof(double), kSimdAlign));
    std::memset(ch_energy_, 0, static_cast<std::size_t>(channels_) * sizeof(double));
    ch_selected_ = 0;
    ch_candidate_ = -1;
    ch_streak_ = 0;
    ch_decided_ = false;
    ch_count_ = 0;

    mono_cap_ = std::max<std::size_t>(blocksize_ ? blocksize_ : 4096, 65536);
    aligned_free_bytes(mono_buf_);
    mono_buf_ = static_cast<float*>(aligned_alloc_bytes(mono_cap_ * sizeof(float), kSimdAlign));

    const PaStreamInfo* sinfo = pa_->GetStreamInfo ? pa_->GetStreamInfo(pa_stream_) : nullptr;
    actual_fs_ = (sinfo && sinfo->sampleRate > 0.0) ? sinfo->sampleRate : sample_rate_;
    return 0;
}

int NativeAudioCapture::start() {
    if (!pa_stream_) return -1;
    if (is_active()) return 0;
    const int rc = pa_->StartStream(pa_stream_);
    if (rc < 0 && pa_->GetErrorText) last_error_ = pa_->GetErrorText(rc);
    return rc;
}

int NativeAudioCapture::stop() {
    if (!pa_stream_ || !is_active()) return 0;
    const int rc = pa_->StopStream(pa_stream_);
    if (rc < 0 && pa_->GetErrorText) last_error_ = pa_->GetErrorText(rc);
    return rc;
}

void NativeAudioCapture::close() {
    stop();
    if (pa_stream_ && pa_ && pa_->CloseStream) {
        pa_->CloseStream(pa_stream_);
        pa_stream_ = nullptr;
    }
    if (pa_initialized_ && pa_ && pa_->Terminate) {
        pa_->Terminate();
        pa_initialized_ = false;
    }
}

bool NativeAudioCapture::is_active() const noexcept {
    if (!pa_stream_ || !pa_ || !pa_->IsStreamActive) return false;
    return pa_->IsStreamActive(pa_stream_) == 1;
}

void NativeAudioCapture::stats(NativeCaptureStats* out) const noexcept {
    if (!out) return;
    out->callbacks = callbacks_.load(std::memory_order_relaxed);
    out->status_flags = status_flags_.load(std::memory_order_relaxed);
    out->callback_errors = callback_errors_.load(std::memory_order_relaxed);
    out->gil_wait_over_block = gil_over_block_.load(std::memory_order_relaxed);
    out->gil_wait_invalid = gil_invalid_.load(std::memory_order_relaxed);
    out->gil_n = gil_n_.load(std::memory_order_relaxed);
    for (int i = 0; i < kGilHistBuckets; ++i) {
        out->gil_hist[i] = gil_hist_[i].load(std::memory_order_relaxed);
    }
    out->actual_sample_rate = actual_fs_;
    out->max_callback_us =
        static_cast<double>(max_callback_ns_.load(std::memory_order_relaxed)) / 1e3;
    out->gil_wait_sum_us =
        static_cast<double>(gil_sum_ns_.load(std::memory_order_relaxed)) / 1e3;
    out->gil_wait_max_us =
        static_cast<double>(gil_max_ns_.load(std::memory_order_relaxed)) / 1e3;
    out->active = is_active() ? 1 : 0;
    out->channels = channels_;
}

int NativeAudioCapture::on_pa_callback(const float* input, unsigned long frames, const void* ti_void,
                                       unsigned long status_flags) noexcept {
    const double pa_now =
        (pa_ && pa_->GetStreamTime && pa_stream_) ? pa_->GetStreamTime(pa_stream_) : 0.0;
    const std::int64_t steady_t0 = steady_now_ns();
    const std::int64_t host_t0 = steady_t0 + (stream_ ? stream_->perf_offset_ns() : 0);
    const std::int64_t wall_t0 = wall_now_ns();
    const auto* ti = static_cast<const PaStreamCallbackTimeInfo*>(ti_void);

    callbacks_.fetch_add(1, std::memory_order_relaxed);
    if (status_flags != 0) status_flags_.fetch_add(1, std::memory_order_relaxed);

    if (!input || !stream_ || frames == 0) {
        if (!input || !stream_) callback_errors_.fetch_add(1, std::memory_order_relaxed);
    } else {
        std::int64_t adc_ns = 0;
        if (ti && ti->inputBufferAdcTime > 0.0) {
            adc_ns = std::llround(
                (ti->inputBufferAdcTime + static_cast<double>(frames) / sample_rate_) * 1e9);
        }
        const std::size_t n_frames = static_cast<std::size_t>(frames);
        if (channels_ <= 1) {
            stream_->write_f32(input, n_frames, host_t0, true, adc_ns, wall_t0);
        } else {
            channel_diff2_energy_f32(input, n_frames, static_cast<std::size_t>(channels_),
                                     ch_energy_);
            ch_count_ += n_frames;
            const int ch_use = ch_selected_;
            if (ch_count_ >= ch_window_) {
                int best = 0;
                for (int c = 1; c < channels_; ++c) {
                    if (ch_energy_[c] > ch_energy_[best]) best = c;
                }
                const double current = ch_energy_[ch_selected_];
                const double ratio = ch_decided_ ? ch_switch_ratio_ : ch_first_ratio_;
                const int need = ch_decided_ ? ch_confirm_ : 1;
                if (best != ch_selected_ && ch_energy_[best] > ratio * current) {
                    ch_streak_ = (best == ch_candidate_) ? (ch_streak_ + 1) : 1;
                    ch_candidate_ = best;
                    if (ch_streak_ >= need) {
                        ch_selected_ = best;
                        ch_candidate_ = -1;
                        ch_streak_ = 0;
                    }
                } else {
                    ch_candidate_ = -1;
                    ch_streak_ = 0;
                }
                ch_decided_ = true;
                std::memset(ch_energy_, 0, static_cast<std::size_t>(channels_) * sizeof(double));
                ch_count_ = 0;
            }
            const std::size_t stride = static_cast<std::size_t>(channels_);
            const std::size_t n_copy = std::min(n_frames, mono_cap_);
            for (std::size_t i = 0; i < n_copy; ++i) {
                mono_buf_[i] = input[i * stride + static_cast<std::size_t>(ch_use)];
            }
            stream_->write_f32(mono_buf_, n_copy, host_t0, true, adc_ns, wall_t0);
        }
    }

    const std::int64_t cb_dt = steady_now_ns() - steady_t0;
    atomic_max_i64(max_callback_ns_, cb_dt);

    const double cur_t = ti ? ti->currentTime : 0.0;
    if (pa_now > 0.0 && cur_t > 0.0) {
        double wait_us = (pa_now - cur_t) * 1e6;
        if (wait_us < 0.0) wait_us = 0.0;
        const std::int64_t wait_ns = std::llround(wait_us * 1e3);
        gil_n_.fetch_add(1, std::memory_order_relaxed);
        gil_sum_ns_.fetch_add(wait_ns, std::memory_order_relaxed);
        atomic_max_i64(gil_max_ns_, wait_ns);
        gil_hist_[wait_bucket(wait_us)].fetch_add(1, std::memory_order_relaxed);
        if (frames > 0 && wait_us > static_cast<double>(frames) * 1e6 / sample_rate_) {
            gil_over_block_.fetch_add(1, std::memory_order_relaxed);
        }
    } else {
        gil_invalid_.fetch_add(1, std::memory_order_relaxed);
    }

    return kPaContinue;
}

}  // namespace advtg::runtime
