// CSV export with RFC-4180 quoting and CWE-1236 formula neutralization (ui/main_window.py).
#include "advtg.h"

#include <atomic>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

namespace fs = std::filesystem;

namespace {

fs::path u8_path(const char* utf8) {
    return fs::path(reinterpret_cast<const char8_t*>(utf8));
}

std::string sanitize_cell(const char* raw) {
    if (!raw || !*raw) return "";
    char buf[1024];
    const int32_t need = advtg_sanitize_csv_cell(raw, buf, sizeof buf);
    if (need < static_cast<int32_t>(sizeof buf)) return std::string(buf);
    std::string out(static_cast<std::size_t>(need) + 1, '\0');
    advtg_sanitize_csv_cell(raw, out.data(), need + 1);
    out.resize(static_cast<std::size_t>(need));
    return out;
}

void write_csv_field(std::ostream& os, const std::string& cell, bool trailing_comma) {
    const bool needs_quote = cell.find_first_of(",\"\n\r") != std::string::npos;
    if (!needs_quote) {
        os << cell;
    } else {
        os << '"';
        for (char c : cell) {
            if (c == '"') os << "\"\"";
            else os << c;
        }
        os << '"';
    }
    if (trailing_comma) os << ',';
}

template <class WriteBody>
int write_atomic_csv(const char* path_str, WriteBody&& body) {
    if (!path_str || !*path_str) return ADVTG_ERR_INVALID;
    const fs::path dst = u8_path(path_str);
    std::error_code ec;
    if (!dst.parent_path().empty()) fs::create_directories(dst.parent_path(), ec);
    static std::atomic<std::uint64_t> seq{0};
    auto u_name = dst.filename().u8string();
    const std::string tmp_name =
        "." + std::string(reinterpret_cast<const char*>(u_name.data()), u_name.size()) + "." +
        std::to_string(++seq) + ".tmp";
    const fs::path tmp =
        dst.parent_path().empty() ? u8_path(tmp_name.c_str()) : (dst.parent_path() / u8_path(tmp_name.c_str()));
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) return ADVTG_ERR_IO;
        body(f);
        f.flush();
        if (!f) {
            fs::remove(tmp, ec);
            return ADVTG_ERR_IO;
        }
    }
#if defined(_WIN32)
    if (!MoveFileExW(tmp.c_str(), dst.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        fs::remove(tmp, ec);
        return ADVTG_ERR_IO;
    }
#else
    fs::rename(tmp, dst, ec);
    if (ec) {
        fs::remove(tmp, ec);
        return ADVTG_ERR_IO;
    }
#endif
    return ADVTG_OK;
}

std::string fmt_lift(double la) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%.1f", la);
    return buf;
}

}  // namespace

extern "C" {

ADVTG_C_API int advtg_write_results_csv(const char* path, const advtg_csv_result_row* rows,
                                        size_t n_rows) {
    if (!rows && n_rows > 0) return ADVTG_ERR_INVALID;
    return write_atomic_csv(path, [&](std::ostream& os) {
        os << "test_name,caliber,bph,lift_angle,sample_rate,position,record_type,elapsed_s,"
              "rate_spd,beat_error_ms,amplitude_deg,confidence,snr_db\n";
        for (size_t i = 0; i < n_rows; ++i) {
            const auto& r = rows[i];
            write_csv_field(os, sanitize_cell(r.test_name), true);
            write_csv_field(os, sanitize_cell(r.caliber), true);
            write_csv_field(os, std::to_string(r.bph), true);
            write_csv_field(os, fmt_lift(r.lift_angle), true);
            write_csv_field(os, std::to_string(r.sample_rate), true);
            write_csv_field(os, sanitize_cell(r.position), true);
            write_csv_field(os, sanitize_cell(r.record_type), true);
            write_csv_field(os, sanitize_cell(r.elapsed_s), true);
            write_csv_field(os, sanitize_cell(r.rate_spd), true);
            write_csv_field(os, sanitize_cell(r.beat_error_ms), true);
            write_csv_field(os, sanitize_cell(r.amplitude_deg), true);
            write_csv_field(os, sanitize_cell(r.confidence), true);
            write_csv_field(os, sanitize_cell(r.snr_db), false);
            os << '\n';
        }
    });
}

ADVTG_C_API int advtg_write_global_stats_csv(const char* path, const advtg_csv_global_row* rows,
                                             size_t n_rows) {
    if (!rows && n_rows > 0) return ADVTG_ERR_INVALID;
    return write_atomic_csv(path, [&](std::ostream& os) {
        os << "test_name,caliber,bph,lift_angle,position,rate_spd,beat_error_ms,"
              "amplitude_deg,confidence,global_mean_spd,positional_delta_spd\n";
        for (size_t i = 0; i < n_rows; ++i) {
            const auto& r = rows[i];
            write_csv_field(os, sanitize_cell(r.test_name), true);
            write_csv_field(os, sanitize_cell(r.caliber), true);
            write_csv_field(os, std::to_string(r.bph), true);
            write_csv_field(os, fmt_lift(r.lift_angle), true);
            write_csv_field(os, sanitize_cell(r.position), true);
            write_csv_field(os, sanitize_cell(r.rate_spd), true);
            write_csv_field(os, sanitize_cell(r.beat_error_ms), true);
            write_csv_field(os, sanitize_cell(r.amplitude_deg), true);
            write_csv_field(os, sanitize_cell(r.confidence), true);
            write_csv_field(os, sanitize_cell(r.global_mean_spd), true);
            write_csv_field(os, sanitize_cell(r.positional_delta_spd), false);
            os << '\n';
        }
    });
}

ADVTG_C_API int advtg_write_traces_csv(const char* path, const double* even_x, const double* even_y,
                                       size_t n_even, const double* odd_x, const double* odd_y,
                                       size_t n_odd, const double* osc_x, const double* osc_y,
                                       size_t n_osc, const double* wav_x, const double* wav_y,
                                       size_t n_wav) {
    return write_atomic_csv(path, [&](std::ostream& os) {
        os << "trace_name,x,y\n";
        char line[128];
        if (even_x && even_y) {
            for (size_t i = 0; i < n_even; ++i) {
                std::snprintf(line, sizeof line, "paper_tape_even,%.6f,%.4f\n", even_x[i],
                              even_y[i]);
                os << line;
            }
        }
        if (odd_x && odd_y) {
            for (size_t i = 0; i < n_odd; ++i) {
                std::snprintf(line, sizeof line, "paper_tape_odd,%.6f,%.4f\n", odd_x[i], odd_y[i]);
                os << line;
            }
        }
        if (osc_x && osc_y) {
            for (size_t i = 0; i < n_osc; ++i) {
                std::snprintf(line, sizeof line, "oscilloscope,%.6f,%.6f\n", osc_x[i], osc_y[i]);
                os << line;
            }
        }
        if (wav_x && wav_y) {
            for (size_t i = 0; i < n_wav; ++i) {
                std::snprintf(line, sizeof line, "audio_waveform,%.6f,%.6f\n", wav_x[i], wav_y[i]);
                os << line;
            }
        }
    });
}

}  // extern "C"
