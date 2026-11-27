// Atomic bug/result .zip report bundle built with miniz (utils/report.py).
#include "advtg.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include "miniz.h"

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr int64_t kDefaultMaxLogsBytes = 30LL * 1024LL * 1024LL;
constexpr int64_t kDefaultMaxRecordingBytes = 20LL * 1024LL * 1024LL;

fs::path u8_path(const char* utf8) {
    return fs::path(reinterpret_cast<const char8_t*>(utf8));
}

std::string path_u8(const fs::path& p) {
    auto u = p.u8string();
    return std::string(reinterpret_cast<const char*>(u.data()), u.size());
}

std::string trim(const std::string& s) {
    std::size_t a = 0;
    while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    std::size_t b = s.size();
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

std::string anonymize_str(const std::string& text, const char* username,
                          const char* home_variants_nl) {
    if (text.empty()) return text;
    std::string out(text.size() + 256, '\0');
    int32_t need = advtg_anonymize_text(text.c_str(), username, home_variants_nl, out.data(),
                                        static_cast<int32_t>(out.size()));
    if (need >= static_cast<int32_t>(out.size())) {
        out.resize(static_cast<std::size_t>(need) + 1);
        advtg_anonymize_text(text.c_str(), username, home_variants_nl, out.data(),
                             static_cast<int32_t>(out.size()));
    }
    out.resize(static_cast<std::size_t>(std::max<int32_t>(0, need)));
    return out;
}

std::string format_local_datetime(std::int64_t epoch_s) {
    const std::time_t tt = static_cast<std::time_t>(epoch_s);
    std::tm tm_loc{};
    std::tm tm_utc{};
#if defined(_WIN32)
    localtime_s(&tm_loc, &tt);
    gmtime_s(&tm_utc, &tt);
#else
    localtime_r(&tt, &tm_loc);
    gmtime_r(&tt, &tm_utc);
#endif
    char dt[32];
    std::snprintf(dt, sizeof dt, "%04d-%02d-%02d %02d:%02d:%02d", tm_loc.tm_year + 1900,
                  tm_loc.tm_mon + 1, tm_loc.tm_mday, tm_loc.tm_hour, tm_loc.tm_min, tm_loc.tm_sec);
    std::tm loc_copy = tm_loc;
    std::tm utc_copy = tm_utc;
    loc_copy.tm_isdst = 0;
    utc_copy.tm_isdst = 0;
    const double diff = std::difftime(std::mktime(&loc_copy), std::mktime(&utc_copy));
    const int offset_min = static_cast<int>(diff / 60.0);
    const char sign = offset_min >= 0 ? '+' : '-';
    const int abs_m = std::abs(offset_min);
    char out[48];
    std::snprintf(out, sizeof out, "%s %c%02d%02d", dt, sign, abs_m / 60, abs_m % 60);
    return out;
}

struct FileWriteCtx {
    std::ofstream* out = nullptr;
};

size_t mz_write_cb(void* opaque, mz_uint64 file_ofs, const void* pBuf, size_t n) {
    auto* ctx = static_cast<FileWriteCtx*>(opaque);
    if (!ctx || !ctx->out) return 0;
    ctx->out->seekp(static_cast<std::streamoff>(file_ofs), std::ios::beg);
    ctx->out->write(static_cast<const char*>(pBuf), static_cast<std::streamsize>(n));
    return ctx->out->good() ? n : 0;
}

struct FileReadCtx {
    std::ifstream* in = nullptr;
};

size_t mz_read_cb(void* opaque, mz_uint64 file_ofs, void* pBuf, size_t n) {
    auto* ctx = static_cast<FileReadCtx*>(opaque);
    if (!ctx || !ctx->in) return 0;
    ctx->in->clear();
    ctx->in->seekg(static_cast<std::streamoff>(file_ofs), std::ios::beg);
    ctx->in->read(static_cast<char*>(pBuf), static_cast<std::streamsize>(n));
    return static_cast<size_t>(ctx->in->gcount());
}

std::string audio_devices_summary() {
    int32_t need = advtg_audio_devices_text(nullptr, 0);
    if (need <= 0) return "(PortAudio not available)";
    std::string buf(static_cast<std::size_t>(need) + 1, '\0');
    advtg_audio_devices_text(buf.data(), need + 1);
    buf.resize(static_cast<std::size_t>(need));
    return buf;
}

}  // namespace

extern "C" {

ADVTG_C_API int32_t advtg_report_default_name(const char* version, int64_t now_epoch_s, char* buf,
                                              int32_t cap) {
    const std::int64_t t =
        (now_epoch_s != 0)
            ? now_epoch_s
            : std::chrono::duration_cast<std::chrono::seconds>(
                  std::chrono::system_clock::now().time_since_epoch())
                  .count();
    const std::time_t tt = static_cast<std::time_t>(t);
    std::tm tm_loc{};
#if defined(_WIN32)
    localtime_s(&tm_loc, &tt);
#else
    localtime_r(&tt, &tm_loc);
#endif
    const char* ver = (version && *version) ? version : advtg_version();
    char name[128];
    std::snprintf(name, sizeof name, "adv_tg_report_%s_%04d%02d%02d_%02d%02d%02d.zip", ver,
                  tm_loc.tm_year + 1900, tm_loc.tm_mon + 1, tm_loc.tm_mday, tm_loc.tm_hour,
                  tm_loc.tm_min, tm_loc.tm_sec);
    const std::size_t len = std::strlen(name);
    if (buf && cap > 0) {
        const std::size_t n = std::min<std::size_t>(len, static_cast<std::size_t>(cap - 1));
        std::memcpy(buf, name, n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(len);
}

ADVTG_C_API int advtg_report_build(const char* dest_path, const char* version,
                                   const char* system_info, const char* runtime_info,
                                   const char* description,
                                   const advtg_audio_tag* settings, size_t n_settings,
                                   const char* recording_path, const char* log_dir,
                                   const char* calibrations_path, const char* username,
                                   const char* home_variants_nl, int64_t max_logs_bytes,
                                   int64_t max_recording_bytes, char* notes_buf,
                                   int32_t notes_cap) {
    if (!dest_path || !*dest_path) return ADVTG_ERR_INVALID;
    fs::path dest = u8_path(dest_path);
    std::string ext = path_u8(dest.extension());
    for (char& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (ext != ".zip") dest += ".zip";

    fs::path ldir;
    if (log_dir && *log_dir) {
        ldir = u8_path(log_dir);
    } else {
        char tmp[1024];
        advtg_default_log_dir(tmp, sizeof tmp);
        ldir = u8_path(tmp);
    }

    fs::path cal_file;
    if (calibrations_path && *calibrations_path) {
        cal_file = u8_path(calibrations_path);
    } else {
        char tmp[1024];
        advtg_config_dir(tmp, sizeof tmp);
        cal_file = u8_path(tmp) / "adc_calibration.json";
    }

    const int64_t max_logs = max_logs_bytes > 0 ? max_logs_bytes : kDefaultMaxLogsBytes;
    const int64_t max_rec = max_recording_bytes > 0 ? max_recording_bytes : kDefaultMaxRecordingBytes;
    const std::int64_t now_s = std::chrono::duration_cast<std::chrono::seconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count();

    std::vector<std::string> lines;
    const char* ver = (version && *version) ? version : advtg_version();
    lines.push_back(std::string("Advanced Timegrapher ") + ver);
    lines.push_back("Created: " + format_local_datetime(now_s));
    if (system_info && *system_info) {
        lines.push_back(std::string("System: ") + system_info);
    } else {
#if defined(_WIN32)
        lines.push_back("System: Windows");
#elif defined(__APPLE__)
        lines.push_back("System: macOS");
#else
        lines.push_back("System: Linux");
#endif
    }
    if (runtime_info && *runtime_info) {
        lines.push_back(runtime_info);
    } else {
        lines.push_back(std::string("Engine: ") + advtg_version() + " (" + advtg_simd_name() + ")");
    }
    lines.push_back("");
    lines.push_back("== Description ==");
    const std::string desc_trim = description ? trim(description) : "";
    lines.push_back(desc_trim.empty() ? "(empty)" : desc_trim);
    lines.push_back("");
    lines.push_back("== Settings ==");
    if (settings && n_settings > 0) {
        for (size_t i = 0; i < n_settings; ++i) {
            const std::string k = settings[i].key ? settings[i].key : "";
            const std::string v = settings[i].value ? settings[i].value : "";
            lines.push_back(k + ": " + v);
        }
    } else {
        lines.push_back("(none)");
    }
    lines.push_back("");
    lines.push_back("== Audio devices ==");
    lines.push_back(audio_devices_summary());
    lines.push_back("");

    // Collect log files newest first within max_logs
    struct LogEntry {
        fs::path path;
        fs::file_time_type mtime;
        std::uintmax_t size;
    };
    std::vector<LogEntry> logs;
    std::error_code ec;
    if (fs::is_directory(ldir, ec)) {
        std::vector<LogEntry> candidates;
        for (const auto& ent : fs::directory_iterator(ldir, ec)) {
            if (!ent.is_regular_file(ec)) continue;
            const std::string fname = path_u8(ent.path().filename());
            if (fname.find(".log") == std::string::npos) continue;
            candidates.push_back({ent.path(), ent.last_write_time(ec), ent.file_size(ec)});
        }
        std::sort(candidates.begin(), candidates.end(),
                  [](const LogEntry& a, const LogEntry& b) { return a.mtime > b.mtime; });
        std::int64_t total = 0;
        for (const auto& c : candidates) {
            if (total + static_cast<std::int64_t>(c.size) > max_logs) continue;
            logs.push_back(c);
            total += static_cast<std::int64_t>(c.size);
        }
    }

    std::vector<std::string> notes;
    if (logs.empty()) {
        notes.push_back("no se encontraron registros en " + path_u8(ldir));
    }

    bool rec_ok = false;
    fs::path rec_path;
    std::uintmax_t rec_size = 0;
    if (recording_path && *recording_path) {
        rec_path = u8_path(recording_path);
        if (!fs::is_regular_file(rec_path, ec)) {
            notes.push_back("la grabación de la última sesión ya no existe");
        } else {
            rec_size = fs::file_size(rec_path, ec);
            if (static_cast<std::int64_t>(rec_size) > max_rec) {
                char msg[256];
                std::snprintf(
                    msg, sizeof msg,
                    "la grabación (%.0f MB) es demasiado grande para GitHub: "
                    "guárdala con Grabar Sonido y compártela aparte",
                    static_cast<double>(rec_size) / 1e6);
                notes.push_back(msg);
            } else {
                rec_ok = true;
            }
        }
    }

    lines.push_back("== Notes ==");
    if (notes.empty()) {
        lines.push_back("(none)");
    } else {
        for (const auto& n : notes) lines.push_back(n);
    }

    std::string report_raw;
    for (const auto& ln : lines) {
        report_raw += ln;
        report_raw.push_back('\n');
    }
    const std::string report_text = anonymize_str(report_raw, username, home_variants_nl);

    if (!dest.parent_path().empty()) fs::create_directories(dest.parent_path(), ec);
    static std::atomic<std::uint64_t> seq{0};
    const std::string tmp_name = "." + path_u8(dest.filename()) + "." + std::to_string(++seq) + ".tmp";
    const fs::path tmp =
        dest.parent_path().empty() ? u8_path(tmp_name.c_str()) : (dest.parent_path() / u8_path(tmp_name.c_str()));

    bool ok = false;
    {
        std::ofstream out_stream(tmp, std::ios::binary | std::ios::trunc);
        if (!out_stream) return ADVTG_ERR_IO;
        FileWriteCtx wctx{&out_stream};
        mz_zip_archive zip{};
        zip.m_pWrite = mz_write_cb;
        zip.m_pIO_opaque = &wctx;
        if (mz_zip_writer_init(&zip, 0)) {
            bool zip_ok = mz_zip_writer_add_mem(&zip, "report.txt", report_text.data(),
                                                report_text.size(), MZ_DEFAULT_COMPRESSION);
            for (const auto& lg : logs) {
                if (!zip_ok) break;
                std::ifstream lf(lg.path, std::ios::binary);
                if (!lf) continue;
                std::string raw((std::istreambuf_iterator<char>(lf)),
                                std::istreambuf_iterator<char>());
                const std::string anon = anonymize_str(raw, username, home_variants_nl);
                const std::string arc = "logs/" + path_u8(lg.path.filename());
                zip_ok = mz_zip_writer_add_mem(&zip, arc.c_str(), anon.data(), anon.size(),
                                               MZ_DEFAULT_COMPRESSION);
            }
            if (zip_ok && fs::is_regular_file(cal_file, ec)) {
                std::ifstream cf(cal_file, std::ios::binary);
                if (cf) {
                    std::string raw((std::istreambuf_iterator<char>(cf)),
                                    std::istreambuf_iterator<char>());
                    zip_ok = mz_zip_writer_add_mem(&zip, "adc_calibration.json", raw.data(),
                                                   raw.size(), MZ_DEFAULT_COMPRESSION);
                }
            }
            if (zip_ok && rec_ok) {
                std::ifstream rf(rec_path, std::ios::binary);
                if (rf) {
                    FileReadCtx rctx{&rf};
                    const MZ_TIME_T mt = static_cast<MZ_TIME_T>(now_s);
                    zip_ok = mz_zip_writer_add_read_buf_callback(
                        &zip, "recording.flac", mz_read_cb, &rctx,
                        static_cast<mz_uint64>(rec_size), &mt, nullptr, 0, MZ_NO_COMPRESSION,
                        nullptr, 0, nullptr, 0);
                }
            }
            if (zip_ok) zip_ok = mz_zip_writer_finalize_archive(&zip);
            mz_zip_writer_end(&zip);
            out_stream.flush();
            ok = zip_ok && out_stream.good();
        }
    }
    if (!ok) {
        fs::remove(tmp, ec);
        return ADVTG_ERR_IO;
    }
#if defined(_WIN32)
    if (!MoveFileExW(tmp.c_str(), dest.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        fs::remove(tmp, ec);
        return ADVTG_ERR_IO;
    }
#else
    fs::rename(tmp, dest, ec);
    if (ec) {
        fs::remove(tmp, ec);
        return ADVTG_ERR_IO;
    }
#endif

    if (notes_buf && notes_cap > 0) {
        std::string joined;
        for (std::size_t i = 0; i < notes.size(); ++i) {
            if (i) joined.push_back('\n');
            joined += notes[i];
        }
        const std::size_t n =
            std::min<std::size_t>(joined.size(), static_cast<std::size_t>(notes_cap - 1));
        if (n) std::memcpy(notes_buf, joined.data(), n);
        notes_buf[n] = '\0';
    }
    return ADVTG_OK;
}

}  // extern "C"
