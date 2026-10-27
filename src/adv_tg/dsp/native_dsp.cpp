#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <cmath>
#include <vector>
#include <algorithm>
#include <numeric>
#include <cstdint>
#include <stdexcept>
#include <tuple>

#if __has_include("native_dsp_source_hash.h")
#include "native_dsp_source_hash.h"
#else
#define NATIVE_DSP_SOURCE_SHA256 ""
#endif

namespace py = pybind11;

namespace adv_tg_dsp {

// Noise gate for every division: a denominator (or a slope used as one) whose magnitude is
// below kGate counts as zero, so no division by ~0 can produce inf/NaN. The build uses
// -ffast-math (with -fno-finite-math-only, so NaN/inf keep their meaning).
constexpr double kGate = 1e-12;

// ----------------------------------------------------------------------
// Normalised coefficients of one second-order section (a0 gated)
// ----------------------------------------------------------------------
struct Section {
    double b0, b1, b2, a1, a2;
};

static std::vector<Section> load_sections(const double* sos, size_t n_sections) {
    std::vector<Section> out(n_sections);
    for (size_t s = 0; s < n_sections; ++s) {
        const double* sec = sos + s * 6;
        double a0 = sec[3];
        if (!(std::fabs(a0) > kGate)) {
            throw std::invalid_argument("sos: a0 es (casi) cero");
        }
        out[s] = {sec[0] / a0, sec[1] / a0, sec[2] / a0, sec[4] / a0, sec[5] / a0};
    }
    return out;
}

// In-place cascade of sections (Direct Form II transposed), initial state zi * x_scale.
static void sos_cascade_inplace(
    double* buf, size_t n, const std::vector<Section>& secs, const double* zi, double x_scale,
    bool reverse
) {
    for (size_t s = 0; s < secs.size(); ++s) {
        const Section& c = secs[s];
        double w1 = zi ? zi[s * 2 + 0] * x_scale : 0.0;
        double w2 = zi ? zi[s * 2 + 1] * x_scale : 0.0;
        for (size_t idx = 0; idx < n; ++idx) {
            size_t i = reverse ? n - 1 - idx : idx;
            double xi = buf[i];
            double yi = c.b0 * xi + w1;
            w1 = c.b1 * xi - c.a1 * yi + w2;
            w2 = c.b2 * xi - c.a2 * yi;
            buf[i] = yi;
        }
    }
}

// ----------------------------------------------------------------------
// Zero-phase filtering (scipy.signal.sosfiltfilt with odd extension of length `edge`)
// ----------------------------------------------------------------------
static void sos_filtfilt_internal(
    const double* x,
    size_t n,
    const std::vector<Section>& secs,
    const double* zi,
    size_t edge,
    double* ext_buf, // size >= n + 2 * edge
    double* out_y    // size >= n
) {
    size_t total_len = n + 2 * edge;

    // Odd extension at boundaries (matching scipy.signal._arraytools.odd_ext)
    double x_start = x[0];
    for (size_t i = 0; i < edge; ++i) {
        ext_buf[i] = 2.0 * x_start - x[edge - i];
    }
    for (size_t i = 0; i < n; ++i) {
        ext_buf[edge + i] = x[i];
    }
    double x_end = x[n - 1];
    for (size_t i = 0; i < edge; ++i) {
        ext_buf[edge + n + i] = 2.0 * x_end - x[n - 2 - i];
    }
    // Forward pass (state zi * first sample), then backward (state zi * last output)
    sos_cascade_inplace(ext_buf, total_len, secs, zi, ext_buf[0], false);
    sos_cascade_inplace(ext_buf, total_len, secs, zi, ext_buf[total_len - 1], true);
    for (size_t i = 0; i < n; ++i) {
        out_y[i] = ext_buf[edge + i];
    }
}

// ----------------------------------------------------------------------
// Hilbert FIR Convolution (optimized loop with zero inner branches)
// ----------------------------------------------------------------------
static void hilbert_fir_convolve_same(
    const double* y,
    size_t n,
    const double* hk,
    size_t hk_len,
    double* out_h
) {
    int64_t half = static_cast<int64_t>(hk_len / 2);
    int64_t n_int = static_cast<int64_t>(n);

    // Collect non-zero taps for cache efficiency
    std::vector<int64_t> offsets;
    std::vector<double> coefs;
    offsets.reserve(half / 2 + 1);
    coefs.reserve(half / 2 + 1);
    for (int64_t k = 1; k <= half; k += 2) {
        offsets.push_back(k);
        coefs.push_back(hk[half + k]);
    }
    size_t num_taps = offsets.size();

    // Prefix: i < half (boundary checks required)
    int64_t prefix_end = std::min(half, n_int);
    for (int64_t i = 0; i < prefix_end; ++i) {
        double sum = 0.0;
        for (size_t t = 0; t < num_taps; ++t) {
            int64_t k = offsets[t];
            double coef = coefs[t];
            double left = (i - k >= 0) ? y[i - k] : 0.0;
            double right = (i + k < n_int) ? y[i + k] : 0.0;
            sum += coef * (left - right);
        }
        out_h[i] = sum;
    }

    // Central body: half <= i < n - half (NO branches, compiler SIMD auto-vectorizable)
    int64_t body_end = std::max(prefix_end, n_int - half);
    for (int64_t i = prefix_end; i < body_end; ++i) {
        double sum = 0.0;
        #pragma clang loop vectorize(enable)
        for (size_t t = 0; t < num_taps; ++t) {
            int64_t k = offsets[t];
            double coef = coefs[t];
            sum += coef * (y[i - k] - y[i + k]);
        }
        out_h[i] = sum;
    }

    // Suffix: i >= n - half (boundary checks required)
    for (int64_t i = body_end; i < n_int; ++i) {
        double sum = 0.0;
        for (size_t t = 0; t < num_taps; ++t) {
            int64_t k = offsets[t];
            double coef = coefs[t];
            double left = (i - k >= 0) ? y[i - k] : 0.0;
            double right = (i + k < n_int) ? y[i + k] : 0.0;
            sum += coef * (left - right);
        }
        out_h[i] = sum;
    }
}

// ----------------------------------------------------------------------
// Moving average smoothing (box filter, mode="same")
// ----------------------------------------------------------------------
static void box_smooth_same(
    const double* e,
    size_t n,
    size_t sm_len,
    double* out_smoothed
) {
    int64_t half = static_cast<int64_t>(sm_len / 2);
    int64_t n_int = static_cast<int64_t>(n);
    double scale = 1.0 / static_cast<double>(sm_len);

    // Initial window sum for i = 0: covers j in [-half, half]
    double running_sum = 0.0;
    for (int64_t j = 0; j <= std::min(half, n_int - 1); ++j) {
        running_sum += e[j];
    }
    out_smoothed[0] = running_sum * scale;

    for (int64_t i = 1; i < n_int; ++i) {
        // Entering window at right: i + half
        int64_t in_idx = i + half;
        if (in_idx < n_int) {
            running_sum += e[in_idx];
        }
        // Leaving window at left: i - half - 1
        int64_t out_idx = i - half - 1;
        if (out_idx >= 0) {
            running_sum -= e[out_idx];
        }
        out_smoothed[i] = running_sum * scale;
    }
}

// ----------------------------------------------------------------------
// High-Level Envelope Computation
// ----------------------------------------------------------------------
py::array_t<double> compute_cell_envelope(
    py::array_t<double, py::array::c_style | py::array::forcecast> x_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> sos_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> zi_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> hk_arr,
    size_t smooth_len,
    size_t crop_start,
    size_t crop_end,
    size_t edge
) {
    py::buffer_info x_info = x_arr.request();
    py::buffer_info sos_info = sos_arr.request();
    py::buffer_info zi_info = zi_arr.request();
    py::buffer_info hk_info = hk_arr.request();

    size_t n = x_info.shape[0];
    size_t n_sections = sos_info.shape[0];
    size_t hk_len = hk_info.shape[0];

    if (crop_start >= crop_end || crop_end > n) {
        throw std::invalid_argument("crop indices fuera de límites");
    }
    if (smooth_len == 0) {
        throw std::invalid_argument("smooth_len debe ser >= 1");
    }

    size_t out_len = crop_end - crop_start;
    py::array_t<double> result(out_len);
    py::buffer_info res_info = result.request();
    double* res_ptr = static_cast<double*>(res_info.ptr);

    const double* x_ptr = static_cast<const double*>(x_info.ptr);
    const double* sos_ptr = static_cast<const double*>(sos_info.ptr);
    const double* zi_ptr = static_cast<const double*>(zi_info.ptr);
    const double* hk_ptr = static_cast<const double*>(hk_info.ptr);

    std::vector<Section> secs = load_sections(sos_ptr, n_sections);

    if (n <= edge + 1) {
        std::fill(res_ptr, res_ptr + out_len, 0.0);
        return result;
    }

    {
        // Release GIL during heavy DSP calculation
        py::gil_scoped_release release;

        // Use flat scratch vectors
        size_t total_pad = n + 2 * edge;
        std::vector<double> ext_buf(total_pad);
        std::vector<double> y(n);
        sos_filtfilt_internal(x_ptr, n, secs, zi_ptr, edge, ext_buf.data(), y.data());

        std::vector<double> h(n);
        hilbert_fir_convolve_same(y.data(), n, hk_ptr, hk_len, h.data());

        // Envelope magnitude: y[i]^2 + h[i]^2
        #pragma clang loop vectorize(enable)
        for (size_t i = 0; i < n; ++i) {
            y[i] = std::sqrt(y[i] * y[i] + h[i] * h[i]);
        }

        // Reuse h vector for smoothed envelope
        box_smooth_same(y.data(), n, smooth_len, h.data());

        // Copy cropped central portion to output
        for (size_t i = 0; i < out_len; ++i) {
            res_ptr[i] = h[crop_start + i];
        }
    }

    return result;
}

// ----------------------------------------------------------------------
// Sub-event Candidates Detection & Split
// ----------------------------------------------------------------------
std::tuple<std::vector<double>, std::vector<double>, std::vector<double>, std::vector<double>>
detect_cell_candidates(
    py::array_t<double, py::array::c_style | py::array::forcecast> env_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> f_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> d_arr,
    double threshold_hi,
    double threshold_lo,
    int64_t merge_samples,
    double split_ratio,
    double min_spacing_samples,
    double onset_fraction,
    int64_t cell_start_abs,
    int64_t cell_end_abs,
    int64_t lo_abs,
    int64_t run_start_abs
) {
    py::buffer_info e_info = env_arr.request();
    py::buffer_info f_info = f_arr.request();
    py::buffer_info d_info = d_arr.request();

    int64_t n = static_cast<int64_t>(e_info.shape[0]);
    if (n < 3) {
        return {{}, {}, {}, {}};
    }

    const double* e = static_cast<const double*>(e_info.ptr);
    const double* F = static_cast<const double*>(f_info.ptr);
    const double* D = static_cast<const double*>(d_info.ptr);

    std::vector<double> onsets;
    std::vector<double> peaks;
    std::vector<double> floors;
    std::vector<double> spreads;

    {
        // Release GIL during candidate detection
        py::gil_scoped_release release;

        // 1. Identify runs where e > thr_lo
        std::vector<int64_t> starts;
        std::vector<int64_t> ends;
        bool in_run = false;
        int64_t cur_start = 0;

        for (int64_t i = 0; i < n; ++i) {
            double thr_lo = F[i] + threshold_lo * D[i];
            if (e[i] > thr_lo) {
                if (!in_run) {
                    in_run = true;
                    cur_start = i;
                }
            } else {
                if (in_run) {
                    in_run = false;
                    starts.push_back(cur_start);
                    ends.push_back(i);
                }
            }
        }
        if (in_run) {
            starts.push_back(cur_start);
            ends.push_back(n);
        }

        if (starts.empty()) {
            return {{}, {}, {}, {}};
        }

        // 2. Merge runs separated by less than merge_samples
        std::vector<int64_t> merged_starts;
        std::vector<int64_t> merged_ends;
        merged_starts.push_back(starts[0]);
        merged_ends.push_back(ends[0]);

        for (size_t k = 1; k < starts.size(); ++k) {
            if (starts[k] - merged_ends.back() < merge_samples) {
                merged_ends.back() = std::max(merged_ends.back(), ends[k]);
            } else {
                merged_starts.push_back(starts[k]);
                merged_ends.push_back(ends[k]);
            }
        }

        // 3. For each merged run, check hysteresis and split
        for (size_t r = 0; r < merged_starts.size(); ++r) {
            int64_t s = merged_starts[r];
            int64_t t = merged_ends[r];

            // Hysteresis: must reach high threshold somewhere in the run
            bool reaches_hi = false;
            for (int64_t i = s; i < t; ++i) {
                if (e[i] > F[i] + threshold_hi * D[i]) {
                    reaches_hi = true;
                    break;
                }
            }
            if (!reaches_hi) continue;

            // Local maxima of e[s:t], same rule as scipy.signal.find_peaks
            // (_local_maxima_1d: a flat top counts once, at its middle sample)
            std::vector<int64_t> raw_peaks;
            bool any_peak = false;  // a local maximum exists (before the threshold)
            int64_t seg_len = t - s;
            {
                const double* seg = e + s;
                int64_t i = 1;
                int64_t i_max = seg_len - 1;
                while (i < i_max) {
                    if (seg[i - 1] < seg[i]) {
                        int64_t i_ahead = i + 1;
                        while (i_ahead < i_max && seg[i_ahead] == seg[i]) ++i_ahead;
                        if (seg[i_ahead] < seg[i]) {
                            int64_t mid = (i + i_ahead - 1) / 2;
                            any_peak = true;
                            if (seg[mid] > F[s + mid] + threshold_hi * D[s + mid]) {
                                raw_peaks.push_back(mid);
                            }
                            i = i_ahead;
                        }
                    }
                    ++i;
                }
            }
            if (!any_peak) {
                // No local maximum at all: use the argmax (as the Python code does)
                int64_t best_i = 0;
                double best_val = e[s];
                for (int64_t i = 1; i < seg_len; ++i) {
                    if (e[s + i] > best_val) {
                        best_val = e[s + i];
                        best_i = i;
                    }
                }
                if (best_val > F[s + best_i] + threshold_hi * D[s + best_i]) {
                    raw_peaks.push_back(best_i);
                }
            }
            if (raw_peaks.empty()) continue;

            // Group peaks and valleys
            struct Group {
                int64_t p;
                int64_t v_rel;
            };
            std::vector<Group> groups;

            for (int64_t p : raw_peaks) {
                if (groups.empty()) {
                    groups.push_back({p, -1});
                    continue;
                }
                Group& cur = groups.back();
                // Valley between cur.p and p
                int64_t v_rel = cur.p;
                double v_val = e[s + cur.p];
                for (int64_t vi = cur.p + 1; vi <= p; ++vi) {
                    if (e[s + vi] < v_val) {
                        v_val = e[s + vi];
                        v_rel = vi;
                    }
                }
                double valley = std::max(v_val, F[s + p]);
                if (e[s + p] >= split_ratio * valley && (p - cur.p) >= min_spacing_samples) {
                    groups.push_back({p, v_rel});
                } else if (e[s + p] > e[s + cur.p]) {
                    cur.p = p;
                }
            }

            // Sub-sample linear onset for each group
            for (const auto& grp : groups) {
                int64_t p = grp.p;
                int64_t v_rel = grp.v_rel;
                int64_t i = s + p;

                double base = (v_rel < 0) ? 0.0 : std::max(e[s + v_rel], F[i]);
                double level = base + onset_fraction * (e[i] - base);
                level = std::max(level, F[i] + threshold_lo * D[i]);

                int64_t j0 = (v_rel >= 0) ? (s + v_rel) : std::max<int64_t>(0, s - static_cast<int64_t>(min_spacing_samples));
                int64_t last_below = -1;
                for (int64_t idx = j0; idx < i; ++idx) {
                    if (e[idx] < level) {
                        last_below = idx;
                    }
                }

                double onset = 0.0;
                if (last_below < 0) {
                    onset = static_cast<double>(j0);
                } else {
                    int64_t j = last_below;
                    double d = e[j + 1] - e[j];
                    onset = static_cast<double>(j) + ((d > kGate) ? ((level - e[j]) / d) : 0.0);
                }

                double o_abs = static_cast<double>(lo_abs) + onset;
                if (o_abs >= static_cast<double>(cell_start_abs) &&
                    o_abs < static_cast<double>(cell_end_abs) &&
                    o_abs >= static_cast<double>(run_start_abs)) {
                    onsets.push_back(o_abs);
                    peaks.push_back(e[i]);
                    floors.push_back(F[i]);
                    spreads.push_back(D[i]);
                }
            }
        }
    }

    return std::make_tuple(onsets, peaks, floors, spreads);
}

// ----------------------------------------------------------------------
// Plain filters (replace scipy.signal.sosfilt / sosfiltfilt in the application)
// ----------------------------------------------------------------------
py::array_t<double> sosfilt(
    py::array_t<double, py::array::c_style | py::array::forcecast> x_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> sos_arr
) {
    py::buffer_info x_info = x_arr.request();
    py::buffer_info sos_info = sos_arr.request();
    if (sos_info.ndim != 2 || sos_info.shape[1] != 6) {
        throw std::invalid_argument("sos debe tener forma (n_secciones, 6)");
    }
    size_t n = x_info.shape[0];
    std::vector<Section> secs =
        load_sections(static_cast<const double*>(sos_info.ptr), sos_info.shape[0]);
    py::array_t<double> result(n);
    double* out = static_cast<double*>(result.request().ptr);
    const double* x = static_cast<const double*>(x_info.ptr);
    {
        py::gil_scoped_release release;
        std::copy(x, x + n, out);
        sos_cascade_inplace(out, n, secs, nullptr, 0.0, false);
    }
    return result;
}

py::array_t<double> sosfiltfilt(
    py::array_t<double, py::array::c_style | py::array::forcecast> x_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> sos_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> zi_arr,
    size_t edge
) {
    py::buffer_info x_info = x_arr.request();
    py::buffer_info sos_info = sos_arr.request();
    py::buffer_info zi_info = zi_arr.request();
    if (sos_info.ndim != 2 || sos_info.shape[1] != 6) {
        throw std::invalid_argument("sos debe tener forma (n_secciones, 6)");
    }
    size_t n = x_info.shape[0];
    if (n <= edge) {
        throw std::invalid_argument("la señal debe ser más larga que padlen");
    }
    std::vector<Section> secs =
        load_sections(static_cast<const double*>(sos_info.ptr), sos_info.shape[0]);
    py::array_t<double> result(n);
    double* out = static_cast<double*>(result.request().ptr);
    const double* x = static_cast<const double*>(x_info.ptr);
    const double* zi = static_cast<const double*>(zi_info.ptr);
    {
        py::gil_scoped_release release;
        std::vector<double> ext_buf(n + 2 * edge);
        sos_filtfilt_internal(x, n, secs, zi, edge, ext_buf.data(), out);
    }
    return result;
}

} // namespace adv_tg_dsp

PYBIND11_MODULE(adv_tg_dsp_native, m) {
    m.doc() = "Native C++ DSP accelerated routines for Advanced Timegrapher";

    m.def(
        "compute_cell_envelope",
        &adv_tg_dsp::compute_cell_envelope,
        py::arg("x"),
        py::arg("sos"),
        py::arg("zi"),
        py::arg("hk"),
        py::arg("smooth_len"),
        py::arg("crop_start"),
        py::arg("crop_end"),
        py::arg("edge"),
        "Computes zero-phase bandpass SOS filtering, Hilbert analytic envelope and box smoothing in C++."
    );

    m.def(
        "detect_cell_candidates",
        &adv_tg_dsp::detect_cell_candidates,
        py::arg("env"),
        py::arg("f"),
        py::arg("d"),
        py::arg("threshold_hi"),
        py::arg("threshold_lo"),
        py::arg("merge_samples"),
        py::arg("split_ratio"),
        py::arg("min_spacing_samples"),
        py::arg("onset_fraction"),
        py::arg("cell_start_abs"),
        py::arg("cell_end_abs"),
        py::arg("lo_abs"),
        py::arg("run_start_abs"),
        "Extracts sub-event burst candidates with hysteresis and sub-sample onsets in C++."
    );

    m.def(
        "sosfilt",
        &adv_tg_dsp::sosfilt,
        py::arg("x"),
        py::arg("sos"),
        "scipy.signal.sosfilt(sos, x) with zero initial state."
    );

    m.def(
        "sosfiltfilt",
        &adv_tg_dsp::sosfiltfilt,
        py::arg("x"),
        py::arg("sos"),
        py::arg("zi"),
        py::arg("edge"),
        "scipy.signal.sosfiltfilt(sos, x) with odd padding of length edge (zi = sosfilt_zi(sos))."
    );

    m.attr("SOURCE_SHA256") = NATIVE_DSP_SOURCE_SHA256;
}
