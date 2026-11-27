// C API exports for advtg::core::BeatDetector and advtg::core::PpsDetector.
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <string>
#include <vector>

#include "advtg_api.hpp"
#include "advtg_detector.hpp"

using advtg::core::BeatDetector;
using advtg::core::NativeBeat;
using advtg::core::NativeDetectorConfig;
using advtg::core::NativeDetectorStats;
using advtg::core::NativePpsPulse;
using advtg::core::NativeTemplateHeader;
using advtg::core::PpsDetector;

ADVTG_API void* advtg_bd_create(const NativeDetectorConfig* cfg) {
    if (!cfg) return nullptr;
    try {
        return new BeatDetector(*cfg);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_bd_destroy(void* h) { delete static_cast<BeatDetector*>(h); }

ADVTG_API void advtg_bd_reset(void* h) { static_cast<BeatDetector*>(h)->reset(); }

ADVTG_API int advtg_bd_stats(void* h, NativeDetectorStats* out) {
    if (!h || !out) return -1;
    *out = static_cast<const BeatDetector*>(h)->stats();
    return 0;
}

ADVTG_API std::int32_t advtg_bd_warnings(void* h, char* buf, std::int32_t cap) {
    if (!h) return -1;
    const auto& ws = static_cast<const BeatDetector*>(h)->warnings();
    std::string joined;
    for (std::size_t i = 0; i < ws.size(); ++i) {
        if (i) joined.push_back('\n');
        joined += ws[i];
    }
    if (buf && cap > 0) {
        const std::size_t n = std::min<std::size_t>(joined.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, joined.data(), n);
        buf[n] = '\0';
    }
    return static_cast<std::int32_t>(joined.size());
}

ADVTG_API int advtg_bd_templates(void* h, NativeTemplateHeader* hdr0, double* env0,
                                 std::size_t cap0, NativeTemplateHeader* hdr1, double* env1,
                                 std::size_t cap1) {
    if (!h || !hdr0 || !hdr1) return -1;
    try {
        thread_local std::vector<double> e0, e1;
        static_cast<const BeatDetector*>(h)->templates(*hdr0, e0, *hdr1, e1);
        if (hdr0->valid && env0) {
            if (e0.size() > cap0) return -2;
            std::memcpy(env0, e0.data(), e0.size() * sizeof(double));
        }
        if (hdr1->valid && env1) {
            if (e1.size() > cap1) return -2;
            std::memcpy(env1, e1.data(), e1.size() * sizeof(double));
        }
        return 0;
    } catch (...) {
        return -1;
    }
}

ADVTG_API void advtg_bd_quartz_timing(void* h, std::int32_t* q0, std::int32_t* q1) {
    if (!h || !q0 || !q1) return;
    static_cast<const BeatDetector*>(h)->quartz_timing(*q0, *q1);
}

ADVTG_API std::int64_t advtg_bd_stream_start_hint(void* h) {
    return h ? static_cast<const BeatDetector*>(h)->stream_start_hint() : 0;
}

// Note: when process/flush returns -2 - m because caller cap was too small, caller cannot
// re-call process cleanly (state already advanced!). So export a drain helper that copies
// thread_local beats from the failed-cap call on retry!
namespace {
thread_local std::vector<NativeBeat> g_overflow_beats;
}  // namespace

ADVTG_API std::int64_t advtg_bd_process_buffered(void* h, const double* chunk, std::size_t n,
                                                 std::int64_t start_index, NativeBeat* out_beats,
                                                 std::size_t cap) {
    try {
        auto* bd = static_cast<BeatDetector*>(h);
        g_overflow_beats.clear();
        bd->process(chunk, n, start_index, g_overflow_beats);
        const std::size_t m = g_overflow_beats.size();
        if (m <= cap) {
            if (m) std::memcpy(out_beats, g_overflow_beats.data(), m * sizeof(NativeBeat));
            return static_cast<std::int64_t>(m);
        }
        return -2 - static_cast<std::int64_t>(m);
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::int64_t advtg_bd_flush_buffered(void* h, NativeBeat* out_beats, std::size_t cap) {
    try {
        auto* bd = static_cast<BeatDetector*>(h);
        g_overflow_beats.clear();
        bd->flush(g_overflow_beats);
        const std::size_t m = g_overflow_beats.size();
        if (m <= cap) {
            if (m) std::memcpy(out_beats, g_overflow_beats.data(), m * sizeof(NativeBeat));
            return static_cast<std::int64_t>(m);
        }
        return -2 - static_cast<std::int64_t>(m);
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::int64_t advtg_bd_take_overflow(NativeBeat* out_beats, std::size_t cap) {
    const std::size_t m = std::min(cap, g_overflow_beats.size());
    if (m) std::memcpy(out_beats, g_overflow_beats.data(), m * sizeof(NativeBeat));
    g_overflow_beats.clear();
    return static_cast<std::int64_t>(m);
}

// ------------------------------------------------------------------ PpsDetector
namespace {
thread_local std::vector<NativePpsPulse> g_overflow_pulses;
}

ADVTG_API void* advtg_pps_create(double sample_rate, double threshold_sigma) {
    try {
        return new PpsDetector(sample_rate, threshold_sigma);
    } catch (...) {
        return nullptr;
    }
}

ADVTG_API void advtg_pps_destroy(void* h) { delete static_cast<PpsDetector*>(h); }

ADVTG_API void advtg_pps_reset(void* h, std::int64_t start_index) {
    static_cast<PpsDetector*>(h)->reset(start_index);
}

ADVTG_API std::int64_t advtg_pps_process(void* h, const double* chunk, std::size_t n,
                                         std::int64_t start_index, NativePpsPulse* out,
                                         std::size_t cap) {
    try {
        g_overflow_pulses.clear();
        static_cast<PpsDetector*>(h)->process(chunk, n, start_index, g_overflow_pulses);
        const std::size_t m = g_overflow_pulses.size();
        if (m <= cap) {
            if (m) std::memcpy(out, g_overflow_pulses.data(), m * sizeof(NativePpsPulse));
            return static_cast<std::int64_t>(m);
        }
        return -2 - static_cast<std::int64_t>(m);
    } catch (...) {
        return -1;
    }
}

ADVTG_API std::int64_t advtg_pps_take_overflow(NativePpsPulse* out, std::size_t cap) {
    const std::size_t m = std::min(cap, g_overflow_pulses.size());
    if (m) std::memcpy(out, g_overflow_pulses.data(), m * sizeof(NativePpsPulse));
    g_overflow_pulses.clear();
    return static_cast<std::int64_t>(m);
}

ADVTG_API std::int64_t advtg_pps_rejected(void* h) {
    return h ? static_cast<const PpsDetector*>(h)->rejected() : 0;
}
