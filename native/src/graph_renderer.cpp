// Multi-threaded CPU 2D Graph Rasterizer (NativeGraphRenderer).
//
// Two dedicated CPU background workers:
//   - Thread 0 ("RenderWorker-Tape"): Paper Tape (tic #00e5ff, tac #ffab00 4 px anti-aliased discs)
//   - Thread 1 ("RenderWorker-WaveOsc"): Escapement Oscilloscope & Live Audio Waveform
//
// Each plot owns 3 pre-allocated 64-byte aligned ARGB32_Premultiplied framebuffers
// (front, ready, back) and blends sub-pixel coverage with bit-level SWAR premultiplied
// source-over compositing. Coordinate convention matches image row-major layout: row 0 = y1 (top),
// row h-1 = y0 (bottom), x0 -> col 0, x1 -> col w-1.
#include "advtg_runtime.hpp"

#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

namespace advtg::runtime {

namespace {

inline constexpr int kMaxW = 4096;
inline constexpr int kMaxH = 2160;

// Make premultiplied 0xAARRGGBB pixel from straight RGB + alpha [0..255].
inline std::uint32_t premul_rgba(std::uint8_t r, std::uint8_t g, std::uint8_t b,
                                 std::uint8_t a) noexcept {
    if (a == 255) {
        return (255u << 24) | (static_cast<std::uint32_t>(r) << 16) |
               (static_cast<std::uint32_t>(g) << 8) | static_cast<std::uint32_t>(b);
    }
    if (a == 0) return 0u;
    const std::uint32_t rp = (static_cast<std::uint32_t>(r) * a + 127u) / 255u;
    const std::uint32_t gp = (static_cast<std::uint32_t>(g) * a + 127u) / 255u;
    const std::uint32_t bp = (static_cast<std::uint32_t>(b) * a + 127u) / 255u;
    return (static_cast<std::uint32_t>(a) << 24) | (rp << 16) | (gp << 8) | bp;
}

// SWAR bit-level premultiplied source-over: out = src + dst * (1 - src.a).
inline std::uint32_t swar_over(std::uint32_t dst, std::uint32_t src_pm) noexcept {
    const std::uint32_t sa = src_pm >> 24;
    if (sa == 255u) return src_pm;
    if (sa == 0u) return dst;
    if (dst == 0u) return src_pm;
    const std::uint32_t inv_a = 255u - sa;
    const std::uint32_t rb = (dst & 0x00ff00ffu) * inv_a + 0x00800080u;
    const std::uint32_t ag = ((dst >> 8) & 0x00ff00ffu) * inv_a + 0x00800080u;
    const std::uint32_t dst_rb = ((rb + ((rb >> 8) & 0x00ff00ffu)) >> 8) & 0x00ff00ffu;
    const std::uint32_t dst_ag = ((ag + ((ag >> 8) & 0x00ff00ffu)) >> 8) & 0x00ff00ffu;
    const std::uint32_t dst_scaled = dst_rb | (dst_ag << 8);
    // Clamping byte addition in case of rounding: src_pm + dst_scaled <= 255 per channel almost
    // everywhere; clamp bytes cleanly without branches via saturation per byte.
    const std::uint32_t s0 = (src_pm & 0x00ff00ffu) + (dst_scaled & 0x00ff00ffu);
    const std::uint32_t s1 = ((src_pm >> 8) & 0x00ff00ffu) + ((dst_scaled >> 8) & 0x00ff00ffu);
    const std::uint32_t r0 =
        std::min<std::uint32_t>(s0 & 0xffffu, 255u) |
        (std::min<std::uint32_t>(s0 >> 16, 255u) << 16);
    const std::uint32_t r1 =
        std::min<std::uint32_t>(s1 & 0xffffu, 255u) |
        (std::min<std::uint32_t>(s1 >> 16, 255u) << 16);
    return r0 | (r1 << 8);
}

struct FrameBuf {
    std::uint8_t* pixels = nullptr;
    std::size_t cap_bytes = 0;
    int w = 0;
    int h = 0;
    int stride = 0;
    double x0 = 0.0, x1 = 1.0, y0 = -1.0, y1 = 1.0;
    std::uint64_t seq = 0;

    ~FrameBuf() { aligned_free_bytes(pixels); }

    void prepare(int new_w, int new_h, double nx0, double nx1, double ny0, double ny1) {
        w = std::clamp(new_w, 1, kMaxW);
        h = std::clamp(new_h, 1, kMaxH);
        stride = ((w * 4 + 63) / 64) * 64;
        const std::size_t need = static_cast<std::size_t>(stride) * static_cast<std::size_t>(h);
        if (need > cap_bytes) {
            aligned_free_bytes(pixels);
            pixels = static_cast<std::uint8_t*>(aligned_alloc_bytes(need, kSimdAlign));
            cap_bytes = need;
        }
        std::memset(pixels, 0, need);
        x0 = nx0;
        x1 = nx1;
        y0 = ny0;
        y1 = ny1;
    }

    inline std::uint32_t* row_ptr(int py) noexcept {
        return reinterpret_cast<std::uint32_t*>(pixels + static_cast<std::size_t>(py) * stride);
    }

    inline void blend_px(int px, int py, std::uint32_t src_pm) noexcept {
        if (static_cast<unsigned>(px) >= static_cast<unsigned>(w) ||
            static_cast<unsigned>(py) >= static_cast<unsigned>(h))
            return;
        std::uint32_t* p = row_ptr(py) + px;
        *p = swar_over(*p, src_pm);
    }
};

void draw_disc_aa(FrameBuf& fb, double cx, double cy, std::uint8_t r, std::uint8_t g,
                  std::uint8_t b, std::uint8_t base_a = 255) noexcept {
    constexpr double kRadius = 2.0;  // 4 px diameter
    if (!std::isfinite(cx) || !std::isfinite(cy)) return;
    if (cx < -3.0 || cx > fb.w + 2.0 || cy < -3.0 || cy > fb.h + 2.0) return;
    const int x_min = std::max(0, static_cast<int>(std::floor(cx - kRadius - 1.0)));
    const int x_max = std::min(fb.w - 1, static_cast<int>(std::ceil(cx + kRadius + 1.0)));
    const int y_min = std::max(0, static_cast<int>(std::floor(cy - kRadius - 1.0)));
    const int y_max = std::min(fb.h - 1, static_cast<int>(std::ceil(cy + kRadius + 1.0)));
    for (int py = y_min; py <= y_max; ++py) {
        const double dy = static_cast<double>(py) - cy;
        std::uint32_t* row = fb.row_ptr(py);
        for (int px = x_min; px <= x_max; ++px) {
            const double dx = static_cast<double>(px) - cx;
            const double dist = std::sqrt(dx * dx + dy * dy);
            const double cov = kRadius + 0.5 - dist;
            if (cov <= 0.0) continue;
            const double clamped = cov >= 1.0 ? 1.0 : cov;
            const std::uint8_t a =
                static_cast<std::uint8_t>(std::lround(clamped * static_cast<double>(base_a)));
            if (a > 0) row[px] = swar_over(row[px], premul_rgba(r, g, b, a));
        }
    }
}

void draw_segment_aa(FrameBuf& fb, double x0, double y0, double x1, double y1, std::uint8_t r,
                     std::uint8_t g, std::uint8_t b, std::uint8_t base_a = 255) noexcept {
    if (!std::isfinite(x0) || !std::isfinite(y0) || !std::isfinite(x1) || !std::isfinite(y1))
        return;
    const double dx = x1 - x0;
    const double dy = y1 - y0;
    const double len = std::hypot(dx, dy);
    if (len < 1e-6) {
        const int ix = static_cast<int>(std::llround(x0));
        const int iy = static_cast<int>(std::llround(y0));
        fb.blend_px(ix, iy, premul_rgba(r, g, b, base_a));
        return;
    }
    const int steps = std::min(std::max(1, static_cast<int>(std::ceil(len))), fb.w + fb.h);
    const std::uint32_t solid = premul_rgba(r, g, b, base_a);
    const std::uint32_t half = premul_rgba(r, g, b, static_cast<std::uint8_t>(base_a / 2));
    if (std::fabs(dx) >= std::fabs(dy)) {
        const double inv = 1.0 / static_cast<double>(steps);
        for (int s = 0; s <= steps; ++s) {
            const double t = static_cast<double>(s) * inv;
            const double fx = x0 + dx * t;
            const double fy = y0 + dy * t;
            const int px = static_cast<int>(std::llround(fx));
            const int iy = static_cast<int>(std::floor(fy));
            const double f = fy - static_cast<double>(iy);
            const std::uint8_t a0 =
                static_cast<std::uint8_t>(std::lround((1.0 - f) * static_cast<double>(base_a)));
            const std::uint8_t a1 =
                static_cast<std::uint8_t>(std::lround(f * static_cast<double>(base_a)));
            fb.blend_px(px, iy, premul_rgba(r, g, b, a0));
            fb.blend_px(px, iy + 1, premul_rgba(r, g, b, a1));
            if (s == 0 && base_a == 255) {
                (void)solid;
                (void)half;
            }
        }
    } else {
        const double inv = 1.0 / static_cast<double>(steps);
        for (int s = 0; s <= steps; ++s) {
            const double t = static_cast<double>(s) * inv;
            const double fx = x0 + dx * t;
            const double fy = y0 + dy * t;
            const int py = static_cast<int>(std::llround(fy));
            const int ix = static_cast<int>(std::floor(fx));
            const double f = fx - static_cast<double>(ix);
            const std::uint8_t a0 =
                static_cast<std::uint8_t>(std::lround((1.0 - f) * static_cast<double>(base_a)));
            const std::uint8_t a1 =
                static_cast<std::uint8_t>(std::lround(f * static_cast<double>(base_a)));
            fb.blend_px(ix, py, premul_rgba(r, g, b, a0));
            fb.blend_px(ix + 1, py, premul_rgba(r, g, b, a1));
        }
    }
}

void draw_series(FrameBuf& fb, const double* xs, const double* ys, std::size_t n, std::uint8_t r,
                 std::uint8_t g, std::uint8_t b, std::uint8_t alpha) {
    if (n == 0 || !xs || !ys) return;
    const double sx = static_cast<double>(fb.w - 1) / std::max(fb.x1 - fb.x0, 1e-12);
    const double sy = static_cast<double>(fb.h - 1) / std::max(fb.y1 - fb.y0, 1e-12);
    if (n <= static_cast<std::size_t>(fb.w) * 2) {
        double px_prev = (xs[0] - fb.x0) * sx;
        double py_prev = (fb.y1 - ys[0]) * sy;
        for (std::size_t i = 1; i < n; ++i) {
            const double px = (xs[i] - fb.x0) * sx;
            const double py = (fb.y1 - ys[i]) * sy;
            draw_segment_aa(fb, px_prev, py_prev, px, py, r, g, b, alpha);
            px_prev = px;
            py_prev = py;
        }
        return;
    }
    // Dense series: peak-preserving per-column [y_min, y_max] spans + inter-column bridge.
    std::vector<int> col_lo(static_cast<std::size_t>(fb.w), fb.h);
    std::vector<int> col_hi(static_cast<std::size_t>(fb.w), -1);
    for (std::size_t i = 0; i < n; ++i) {
        const double x = xs[i], y = ys[i];
        if (!std::isfinite(x) || !std::isfinite(y)) continue;
        const int px = static_cast<int>(std::llround((x - fb.x0) * sx));
        if (px < 0 || px >= fb.w) continue;
        const int py = std::clamp(static_cast<int>(std::llround((fb.y1 - y) * sy)), 0, fb.h - 1);
        col_lo[static_cast<std::size_t>(px)] = std::min(col_lo[static_cast<std::size_t>(px)], py);
        col_hi[static_cast<std::size_t>(px)] = std::max(col_hi[static_cast<std::size_t>(px)], py);
    }
    const std::uint32_t col = premul_rgba(r, g, b, alpha);
    int prev_lo = 0, prev_hi = -1;
    for (int px = 0; px < fb.w; ++px) {
        int lo = col_lo[static_cast<std::size_t>(px)];
        int hi = col_hi[static_cast<std::size_t>(px)];
        if (lo > hi) continue;
        if (prev_hi >= 0) {
            if (lo > prev_hi) lo = prev_hi;
            if (hi < prev_lo) hi = prev_lo;
        }
        for (int py = lo; py <= hi; ++py) fb.blend_px(px, py, col);
        prev_lo = col_lo[static_cast<std::size_t>(px)];
        prev_hi = col_hi[static_cast<std::size_t>(px)];
    }
}

struct SlotTriple {
    std::mutex mtx;
    FrameBuf bufs[3];
    int front = 0, ready = 1, back = 2;
    std::uint64_t published_seq = 0;

    FrameBuf& back_buf() { return bufs[back]; }

    std::uint64_t publish() {
        std::lock_guard<std::mutex> lk(mtx);
        bufs[back].seq = ++published_seq;
        std::swap(ready, back);
        return published_seq;
    }

    const std::uint8_t* acquire(int* out_w, int* out_h, int* out_stride, double* out_x0,
                                double* out_x1, double* out_y0, double* out_y1,
                                std::uint64_t* out_seq) {
        std::lock_guard<std::mutex> lk(mtx);
        if (bufs[ready].seq > bufs[front].seq) std::swap(front, ready);
        const FrameBuf& f = bufs[front];
        if (f.seq == 0 || !f.pixels) {
            if (out_seq) *out_seq = 0;
            return nullptr;
        }
        if (out_w) *out_w = f.w;
        if (out_h) *out_h = f.h;
        if (out_stride) *out_stride = f.stride;
        if (out_x0) *out_x0 = f.x0;
        if (out_x1) *out_x1 = f.x1;
        if (out_y0) *out_y0 = f.y0;
        if (out_y1) *out_y1 = f.y1;
        if (out_seq) *out_seq = f.seq;
        return f.pixels;
    }
};

struct TapeJob {
    bool pending = false;
    bool clear_only = false;
    int w = 1, h = 1;
    double x0 = 0.0, x1 = 1.0, y0 = -1.0, y1 = 1.0;
    std::vector<double> xe, ye, xo, yo;
};

struct WaveOscJob {
    bool pending = false;
    bool clear_only = false;
    bool is_osc = false;
    int w = 1, h = 1;
    double x0 = 0.0, x1 = 1.0, y0 = -1.0, y1 = 1.0;
    std::vector<double> t, wave, env;
};

}  // namespace

struct NativeGraphRenderer::Impl {
    SlotTriple slots[3];

    // Thread 0: Paper Tape (slot 0)
    std::mutex tape_mtx;
    std::condition_variable tape_cv;
    TapeJob tape_job;
    std::uint64_t tape_gen = 0;
    std::uint64_t tape_done = 0;

    // Thread 1: Osc (slot 1) + Wave (slot 2)
    std::mutex wo_mtx;
    std::condition_variable wo_cv;
    WaveOscJob osc_job;
    WaveOscJob wave_job;
    std::uint64_t wo_gen = 0;
    std::uint64_t wo_done = 0;

    std::condition_variable flush_cv;
    bool stop = false;
    std::thread th_tape;
    std::thread th_waveosc;

    Impl() {
        th_tape = std::thread([this] { run_tape(); });
        th_waveosc = std::thread([this] { run_waveosc(); });
    }

    ~Impl() {
        {
            std::lock_guard<std::mutex> l1(tape_mtx);
            std::lock_guard<std::mutex> l2(wo_mtx);
            stop = true;
        }
        tape_cv.notify_all();
        wo_cv.notify_all();
        if (th_tape.joinable()) th_tape.join();
        if (th_waveosc.joinable()) th_waveosc.join();
    }

    void run_tape() {
        TapeJob job;
        while (true) {
            std::uint64_t gen_seen = 0;
            {
                std::unique_lock<std::mutex> lk(tape_mtx);
                tape_cv.wait(lk, [&] { return stop || tape_job.pending; });
                if (stop) return;
                job = std::move(tape_job);
                tape_job.pending = false;
                gen_seen = tape_gen;
            }
            FrameBuf& fb = slots[0].back_buf();
            fb.prepare(job.w, job.h, job.x0, job.x1, job.y0, job.y1);
            if (!job.clear_only && job.x1 > job.x0 && job.y1 > job.y0) {
                const double sx = static_cast<double>(fb.w - 1) / (job.x1 - job.x0);
                const double sy = static_cast<double>(fb.h - 1) / (job.y1 - job.y0);
                for (std::size_t i = 0; i < job.xe.size(); ++i) {
                    const double px = (job.xe[i] - job.x0) * sx;
                    const double py = (job.y1 - job.ye[i]) * sy;
                    draw_disc_aa(fb, px, py, 0x00, 0xe5, 0xff, 255);
                }
                for (std::size_t i = 0; i < job.xo.size(); ++i) {
                    const double px = (job.xo[i] - job.x0) * sx;
                    const double py = (job.y1 - job.yo[i]) * sy;
                    draw_disc_aa(fb, px, py, 0xff, 0xab, 0x00, 255);
                }
            }
            slots[0].publish();
            {
                std::lock_guard<std::mutex> lk(tape_mtx);
                tape_done = std::max(tape_done, gen_seen);
            }
            flush_cv.notify_all();
        }
    }

    void run_waveosc() {
        WaveOscJob job;
        while (true) {
            std::uint64_t gen_seen = 0;
            int slot_idx = 1;
            {
                std::unique_lock<std::mutex> lk(wo_mtx);
                wo_cv.wait(lk, [&] { return stop || osc_job.pending || wave_job.pending; });
                if (stop) return;
                if (osc_job.pending) {
                    job = std::move(osc_job);
                    osc_job.pending = false;
                    slot_idx = 1;
                } else {
                    job = std::move(wave_job);
                    wave_job.pending = false;
                    slot_idx = 2;
                }
                gen_seen = wo_gen;
            }
            FrameBuf& fb = slots[slot_idx].back_buf();
            fb.prepare(job.w, job.h, job.x0, job.x1, job.y0, job.y1);
            if (!job.clear_only && job.x1 > job.x0 && job.y1 > job.y0) {
                if (slot_idx == 1) {
                    draw_series(fb, job.t.data(), job.wave.data(), job.t.size(), 0x00, 0xe5, 0xff, 255);
                    if (job.env.size() == job.t.size()) {
                        draw_series(fb, job.t.data(), job.env.data(), job.t.size(), 0xff, 0xab, 0x00,
                                    180);
                    }
                } else {
                    draw_series(fb, job.t.data(), job.wave.data(), job.t.size(), 0x00, 0xe6, 0x76, 255);
                }
            }
            slots[slot_idx].publish();
            {
                std::lock_guard<std::mutex> lk(wo_mtx);
                if (!osc_job.pending && !wave_job.pending) wo_done = std::max(wo_done, gen_seen);
            }
            flush_cv.notify_all();
        }
    }
};

NativeGraphRenderer::NativeGraphRenderer() : impl_(new Impl()) {}
NativeGraphRenderer::~NativeGraphRenderer() {
    delete impl_;
    impl_ = nullptr;
}

void NativeGraphRenderer::submit_tape(int w, int h, double x0, double x1, double y0, double y1,
                                      const double* xe, const double* ye, std::size_t ne,
                                      const double* xo, const double* yo, std::size_t no) {
    {
        std::lock_guard<std::mutex> lk(impl_->tape_mtx);
        impl_->tape_job.pending = true;
        impl_->tape_job.clear_only = false;
        impl_->tape_job.w = w;
        impl_->tape_job.h = h;
        impl_->tape_job.x0 = x0;
        impl_->tape_job.x1 = x1;
        impl_->tape_job.y0 = y0;
        impl_->tape_job.y1 = y1;
        impl_->tape_job.xe.assign(xe, xe + (xe && ne ? ne : 0));
        impl_->tape_job.ye.assign(ye, ye + (ye && ne ? ne : 0));
        impl_->tape_job.xo.assign(xo, xo + (xo && no ? no : 0));
        impl_->tape_job.yo.assign(yo, yo + (yo && no ? no : 0));
        ++impl_->tape_gen;
    }
    impl_->tape_cv.notify_one();
}

void NativeGraphRenderer::submit_osc(int w, int h, double x0, double x1, double y0, double y1,
                                     const double* t, const double* wave, const double* env,
                                     std::size_t n) {
    {
        std::lock_guard<std::mutex> lk(impl_->wo_mtx);
        impl_->osc_job.pending = true;
        impl_->osc_job.clear_only = false;
        impl_->osc_job.is_osc = true;
        impl_->osc_job.w = w;
        impl_->osc_job.h = h;
        impl_->osc_job.x0 = x0;
        impl_->osc_job.x1 = x1;
        impl_->osc_job.y0 = y0;
        impl_->osc_job.y1 = y1;
        const std::size_t m = (t && wave && n) ? n : 0;
        impl_->osc_job.t.assign(t, t + m);
        impl_->osc_job.wave.assign(wave, wave + m);
        if (env && m)
            impl_->osc_job.env.assign(env, env + m);
        else
            impl_->osc_job.env.clear();
        ++impl_->wo_gen;
    }
    impl_->wo_cv.notify_one();
}

void NativeGraphRenderer::submit_wave(int w, int h, double x0, double x1, double y0, double y1,
                                      const double* t, const double* wave, std::size_t n) {
    {
        std::lock_guard<std::mutex> lk(impl_->wo_mtx);
        impl_->wave_job.pending = true;
        impl_->wave_job.clear_only = false;
        impl_->wave_job.is_osc = false;
        impl_->wave_job.w = w;
        impl_->wave_job.h = h;
        impl_->wave_job.x0 = x0;
        impl_->wave_job.x1 = x1;
        impl_->wave_job.y0 = y0;
        impl_->wave_job.y1 = y1;
        const std::size_t m = (t && wave && n) ? n : 0;
        impl_->wave_job.t.assign(t, t + m);
        impl_->wave_job.wave.assign(wave, wave + m);
        impl_->wave_job.env.clear();
        ++impl_->wo_gen;
    }
    impl_->wo_cv.notify_one();
}

void NativeGraphRenderer::clear_slot(int slot) {
    if (slot == 0) {
        {
            std::lock_guard<std::mutex> lk(impl_->tape_mtx);
            impl_->tape_job.pending = true;
            impl_->tape_job.clear_only = true;
            impl_->tape_job.xe.clear();
            impl_->tape_job.ye.clear();
            impl_->tape_job.xo.clear();
            impl_->tape_job.yo.clear();
            ++impl_->tape_gen;
        }
        impl_->tape_cv.notify_one();
    } else if (slot == 1 || slot == 2) {
        {
            std::lock_guard<std::mutex> lk(impl_->wo_mtx);
            WaveOscJob& j = (slot == 1) ? impl_->osc_job : impl_->wave_job;
            j.pending = true;
            j.clear_only = true;
            j.t.clear();
            j.wave.clear();
            j.env.clear();
            ++impl_->wo_gen;
        }
        impl_->wo_cv.notify_one();
    }
}

void NativeGraphRenderer::flush() {
    {
        std::unique_lock<std::mutex> lk(impl_->tape_mtx);
        impl_->flush_cv.wait(lk, [&] { return !impl_->tape_job.pending && impl_->tape_done >= impl_->tape_gen; });
    }
    {
        std::unique_lock<std::mutex> lk(impl_->wo_mtx);
        impl_->flush_cv.wait(lk, [&] {
            return !impl_->osc_job.pending && !impl_->wave_job.pending && impl_->wo_done >= impl_->wo_gen;
        });
    }
}

const std::uint8_t* NativeGraphRenderer::acquire_frame(int slot, int* out_w, int* out_h,
                                                       int* out_stride, double* out_x0,
                                                       double* out_x1, double* out_y0,
                                                       double* out_y1, std::uint64_t* out_seq) {
    if (slot < 0 || slot > 2) return nullptr;
    return impl_->slots[slot].acquire(out_w, out_h, out_stride, out_x0, out_x1, out_y0, out_y1,
                                      out_seq);
}

}  // namespace advtg::runtime
