// Per-device ADC clock calibration store (utils/adc_calibration.py, core/adc_clock.py tags)
// backed by nlohmann/json (MIT, header-only, no Qt dependency).
#include "advtg.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include "nlohmann/json.hpp"

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

namespace fs = std::filesystem;
using nlohmann::json;

namespace {

constexpr double kCrystalAgingPpmPerYear = 1.0;
constexpr double kDaysPerYear = 365.25;
constexpr std::size_t kMaxHistory = 50;
constexpr int kFormatVersion = 1;
constexpr double kUncalibratedSigmaPpm = 30.0;

thread_local std::string g_cal_error;

void set_err(const std::string& msg) { g_cal_error = msg; }

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

std::string to_lower_utf8_basic(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string to_upper_ascii(std::string s) {
    for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

std::string collapse_spaces(const std::string& s) {
    std::string out;
    bool in_ws = true;
    for (unsigned char c : s) {
        if (std::isspace(c)) {
            if (!in_ws) {
                out.push_back(' ');
                in_ws = true;
            }
        } else {
            out.push_back(static_cast<char>(c));
            in_ws = false;
        }
    }
    if (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

bool same_device(const std::string& a, const std::string& b) {
    return to_lower_utf8_basic(trim(a)) == to_lower_utf8_basic(trim(b));
}

// Howard Hinnant's civil-date <-> epoch-days (pure UTC, DST-free on all OSes).
std::int64_t days_from_civil(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const std::int64_t era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + static_cast<std::int64_t>(doe) - 719468;
}

void civil_from_days(std::int64_t z, int& y, unsigned& m, unsigned& d) {
    z += 719468;
    const std::int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = static_cast<unsigned>(z - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    y = static_cast<int>(yoe) + static_cast<int>(era) * 400;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    d = doy - (153 * mp + 2) / 5 + 1;
    m = mp + (mp < 10 ? 3 : -9);
    y += (m <= 2);
}

std::string format_iso_utc(std::int64_t epoch_s) {
    std::int64_t days = epoch_s / 86400;
    std::int64_t rem = epoch_s % 86400;
    if (rem < 0) {
        rem += 86400;
        --days;
    }
    int y = 0;
    unsigned m = 0, d = 0;
    civil_from_days(days, y, m, d);
    const unsigned hh = static_cast<unsigned>(rem / 3600);
    const unsigned mm = static_cast<unsigned>((rem % 3600) / 60);
    const unsigned ss = static_cast<unsigned>(rem % 60);
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%04d-%02u-%02uT%02u:%02u:%02u+00:00", y, m, d, hh, mm, ss);
    return buf;
}

bool parse_uint_n(const char* p, std::size_t n, int& out) {
    int v = 0;
    for (std::size_t i = 0; i < n; ++i) {
        if (!std::isdigit(static_cast<unsigned char>(p[i]))) return false;
        v = v * 10 + (p[i] - '0');
    }
    out = v;
    return true;
}

bool parse_iso_utc(const std::string& raw, std::int64_t& epoch_out) {
    const std::string s = trim(raw);
    if (s.size() < 10) return false;
    int y = 0, mo = 0, d = 0;
    if (!parse_uint_n(s.data(), 4, y) || s[4] != '-' ||
        !parse_uint_n(s.data() + 5, 2, mo) || s[7] != '-' ||
        !parse_uint_n(s.data() + 8, 2, d)) {
        return false;
    }
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return false;
    int hh = 0, mm = 0, ss = 0;
    std::size_t pos = 10;
    if (pos < s.size()) {
        if (s[pos] != 'T' && s[pos] != 't' && s[pos] != ' ') return false;
        ++pos;
        if (pos + 5 > s.size() || !parse_uint_n(s.data() + pos, 2, hh) || s[pos + 2] != ':' ||
            !parse_uint_n(s.data() + pos + 3, 2, mm)) {
            return false;
        }
        pos += 5;
        if (pos < s.size() && s[pos] == ':') {
            ++pos;
            if (pos + 2 > s.size() || !parse_uint_n(s.data() + pos, 2, ss)) return false;
            pos += 2;
        }
        if (hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 59) return false;
        if (pos < s.size() && s[pos] == '.') {
            ++pos;
            if (pos >= s.size() || !std::isdigit(static_cast<unsigned char>(s[pos]))) return false;
            while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) ++pos;
        }
    }
    std::int64_t tz_offset_s = 0;
    if (pos < s.size()) {
        if (s[pos] == 'Z' || s[pos] == 'z') {
            ++pos;
            if (pos != s.size()) return false;
        } else if (s[pos] == '+' || s[pos] == '-') {
            const int sign = (s[pos] == '-') ? -1 : 1;
            ++pos;
            int tzh = 0, tzm = 0;
            if (pos + 2 > s.size() || !parse_uint_n(s.data() + pos, 2, tzh)) return false;
            pos += 2;
            if (pos < s.size()) {
                if (s[pos] == ':') ++pos;
                if (pos + 2 > s.size() || !parse_uint_n(s.data() + pos, 2, tzm)) return false;
                pos += 2;
            }
            if (pos != s.size() || tzh > 23 || tzm > 59) return false;
            tz_offset_s = sign * (tzh * 3600 + tzm * 60);
        } else {
            return false;
        }
    }
    const std::int64_t days = days_from_civil(y, static_cast<unsigned>(mo), static_cast<unsigned>(d));
    epoch_out = days * 86400LL + hh * 3600LL + mm * 60LL + ss - tz_offset_s;
    return true;
}

int method_from_str(const std::string& m) {
    if (m == "host-envelope") return 1;
    if (m == "reference") return 2;
    if (m == "manual") return 3;
    if (m == "pps") return 4;
    return 0;
}

const char* method_to_str(int m) {
    switch (m) {
        case 1: return "host-envelope";
        case 2: return "reference";
        case 3: return "manual";
        case 4: return "pps";
        default: return "none";
    }
}

std::string fmt_double(double v, int sig) {
    char fmt[16], buf[64];
    std::snprintf(fmt, sizeof(fmt), "%%.%dg", sig);
    std::snprintf(buf, sizeof(buf), fmt, v);
    return buf;
}

bool parse_double_strict(const std::string& s, double& out) {
    const std::string t = trim(s);
    if (t.empty()) return false;
    char* end = nullptr;
    const double v = std::strtod(t.c_str(), &end);
    if (end == t.c_str() || *end != '\0') return false;
    out = v;
    return true;
}

bool parse_int_strict(const std::string& s, int& out) {
    const std::string t = trim(s);
    if (t.empty()) return false;
    char* end = nullptr;
    const long v = std::strtol(t.c_str(), &end, 10);
    if (end == t.c_str() || *end != '\0') return false;
    out = static_cast<int>(v);
    return true;
}

struct CalRecord {
    std::string device;
    int32_t sample_rate = 48000;
    advtg_adc_drift drift{};
    std::int64_t measured_at_epoch_s = 0;
    std::string method_str = "none";
    std::string note;
};

bool validate_record(const CalRecord& r, std::string& err) {
    if (trim(r.device).empty()) {
        err = "el nombre del dispositivo está vacío";
        return false;
    }
    if (r.sample_rate <= 0) {
        err = "tasa de muestreo no válida: " + std::to_string(r.sample_rate);
        return false;
    }
    if (r.drift.level == ADVTG_CLOCK_UNCALIBRATED) {
        err = "no se guarda una calibración de nivel 0 (sin calibrar)";
        return false;
    }
    const double unc = std::hypot(r.drift.sigma_ppm, r.drift.systematic_ppm);
    if (!std::isfinite(r.drift.ppm) || !std::isfinite(unc)) {
        err = "ppm o incertidumbre no finitos";
        return false;
    }
    if (r.drift.sigma_ppm < 0.0 || r.drift.systematic_ppm < 0.0) {
        err = "las incertidumbres no pueden ser negativas";
        return false;
    }
    return true;
}

CalRecord from_c(const advtg_calibration& c) {
    CalRecord r;
    r.device = c.device;
    r.sample_rate = c.sample_rate;
    r.drift = c.drift;
    r.measured_at_epoch_s = c.measured_at_epoch_s;
    r.method_str = method_to_str(c.drift.method);
    r.note = c.note;
    return r;
}

advtg_calibration to_c(const CalRecord& r) {
    advtg_calibration c{};
    std::snprintf(c.device, sizeof(c.device), "%s", r.device.c_str());
    c.sample_rate = r.sample_rate;
    c.drift = r.drift;
    c.measured_at_epoch_s = r.measured_at_epoch_s;
    std::snprintf(c.note, sizeof(c.note), "%s", r.note.c_str());
    return c;
}

fs::path default_store_path(const char* store_path) {
    if (store_path && *store_path) return u8_path(store_path);
    char dir[1024];
    advtg_config_dir(dir, sizeof(dir));
    return u8_path(dir) / "adc_calibration.json";
}

bool load_store(const fs::path& path, std::vector<CalRecord>& out) {
    out.clear();
    std::error_code ec;
    if (!fs::exists(path, ec)) return true;
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        set_err("no se pudo leer el archivo de calibraciones '" + path_u8(path) + "'");
        return false;
    }
    try {
        json data = json::parse(f);
        if (!data.is_object() || !data.contains("calibrations") ||
            !data["calibrations"].is_array()) {
            throw std::runtime_error("falta la lista 'calibrations'");
        }
        int ver = 0;
        if (data.contains("version")) {
            if (!data["version"].is_number_integer()) throw std::runtime_error("versión no válida");
            ver = data["version"].get<int>();
        }
        if (ver > kFormatVersion) {
            throw std::runtime_error("versión " + std::to_string(ver) +
                                     " más nueva que la soportada");
        }
        for (const auto& item : data["calibrations"]) {
            if (!item.is_object()) throw std::runtime_error("registro no es objeto");
            CalRecord r;
            r.device = item.at("device").get<std::string>();
            r.sample_rate = item.at("sample_rate").get<int32_t>();
            const std::string ts = item.at("measured_at").get<std::string>();
            if (!parse_iso_utc(ts, r.measured_at_epoch_s)) {
                throw std::runtime_error("fecha no válida");
            }
            r.drift.ppm = item.at("ppm").get<double>();
            r.drift.sigma_ppm = item.at("sigma_ppm").get<double>();
            r.drift.systematic_ppm = item.at("systematic_ppm").get<double>();
            const int lvl = item.at("level").get<int>();
            if (lvl < ADVTG_CLOCK_UNCALIBRATED || lvl > ADVTG_CLOCK_EXTERNAL) {
                throw std::runtime_error("nivel no válido");
            }
            r.drift.level = lvl;
            r.method_str = item.at("method").get<std::string>();
            r.drift.method = method_from_str(r.method_str);
            r.drift.span_s = item.value("span_s", 0.0);
            r.drift.n_points = item.value("n_points", std::int64_t{0});
            r.drift.n_segments = item.value("n_segments", std::int64_t{0});
            if (item.contains("residual_us") && !item["residual_us"].is_null()) {
                r.drift.residual_us = item["residual_us"].get<double>();
            } else {
                r.drift.residual_us = std::numeric_limits<double>::quiet_NaN();
            }
            r.note = item.value("note", std::string());
            std::string verr;
            if (!validate_record(r, verr)) throw std::runtime_error(verr);
            out.push_back(std::move(r));
        }
        return true;
    } catch (const std::exception& e) {
        set_err("no se pudo leer el archivo de calibraciones '" + path_u8(path) + "': " +
                e.what() + ". Corrígelo o muévelo a otro sitio para empezar de cero.");
        return false;
    }
}

bool save_store(const fs::path& path, const std::vector<CalRecord>& records) {
    json arr = json::array();
    for (const auto& r : records) {
        json item = {
            {"device", r.device},
            {"sample_rate", r.sample_rate},
            {"measured_at", format_iso_utc(r.measured_at_epoch_s)},
            {"ppm", r.drift.ppm},
            {"sigma_ppm", r.drift.sigma_ppm},
            {"systematic_ppm", r.drift.systematic_ppm},
            {"level", r.drift.level},
            {"method", r.method_str},
            {"span_s", r.drift.span_s},
            {"n_points", r.drift.n_points},
            {"n_segments", r.drift.n_segments},
            {"residual_us", std::isfinite(r.drift.residual_us) ? json(r.drift.residual_us) : json(nullptr)},
            {"note", r.note},
        };
        arr.push_back(std::move(item));
    }
    json payload = {{"version", kFormatVersion}, {"calibrations", std::move(arr)}};
    const std::string text = payload.dump(2) + "\n";

    std::error_code ec;
    fs::path dir = path.parent_path();
    if (!dir.empty()) {
        fs::create_directories(dir, ec);
#if !defined(_WIN32)
        fs::permissions(dir, fs::perms::owner_all, fs::perm_options::replace, ec);
#endif
    }
    static std::atomic<std::uint64_t> seq{0};
    const std::string tmp_name = ".adc_calibration." + std::to_string(++seq) + ".json";
    fs::path tmp = dir.empty() ? u8_path(tmp_name.c_str()) : (dir / u8_path(tmp_name.c_str()));
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) {
            set_err("no se pudo crear el archivo temporal de calibración");
            return false;
        }
        f.write(text.data(), static_cast<std::streamsize>(text.size()));
        f.flush();
        if (!f) {
            set_err("error al escribir calibraciones");
            fs::remove(tmp, ec);
            return false;
        }
    }
#if defined(_WIN32)
    if (!MoveFileExW(tmp.c_str(), path.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        fs::remove(tmp, ec);
        set_err("no se pudo reemplazar el archivo de calibración");
        return false;
    }
#else
    fs::rename(tmp, path, ec);
    if (ec) {
        fs::remove(tmp, ec);
        set_err(ec.message());
        return false;
    }
#endif
    return true;
}

std::int64_t current_epoch_s() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

double effective_uncertainty(const CalRecord& r, std::int64_t now_s, double aging_ppm_per_year) {
    const double age_days = std::fabs(static_cast<double>(now_s - r.measured_at_epoch_s)) / 86400.0;
    const double aging = aging_ppm_per_year * age_days / kDaysPerYear;
    const double sys = (aging > 0.0) ? std::hypot(r.drift.systematic_ppm, aging)
                                     : r.drift.systematic_ppm;
    return std::hypot(r.drift.sigma_ppm, sys);
}

const CalRecord* pick_best_record(const std::vector<CalRecord>& recs, std::int64_t now_s,
                                    double aging_ppm_per_year) {
    if (recs.empty()) return nullptr;
    std::vector<const CalRecord*> ptrs;
    ptrs.reserve(recs.size());
    for (const auto& r : recs) ptrs.push_back(&r);
    std::stable_sort(ptrs.begin(), ptrs.end(), [](const CalRecord* a, const CalRecord* b) {
        return a->measured_at_epoch_s > b->measured_at_epoch_s;
    });
    const CalRecord* best = ptrs[0];
    double best_unc = effective_uncertainty(*best, now_s, aging_ppm_per_year);
    for (std::size_t i = 1; i < ptrs.size(); ++i) {
        const double u = effective_uncertainty(*ptrs[i], now_s, aging_ppm_per_year);
        if (u < best_unc) {
            best_unc = u;
            best = ptrs[i];
        }
    }
    return best;
}

}  // namespace

extern "C" {

ADVTG_C_API const char* advtg_cal_last_error(void) { return g_cal_error.c_str(); }

ADVTG_C_API int32_t advtg_cal_format_iso(int64_t epoch_s, char* buf, int32_t cap) {
    const std::string s = format_iso_utc(epoch_s);
    if (buf && cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(s.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, s.data(), n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(s.size());
}

ADVTG_C_API int advtg_cal_parse_iso(const char* iso, int64_t* epoch_s_out) {
    if (!iso || !epoch_s_out) return ADVTG_ERR_INVALID;
    return parse_iso_utc(iso, *epoch_s_out) ? ADVTG_OK : ADVTG_ERR_INVALID;
}

ADVTG_C_API double advtg_cal_age_days(int64_t measured_at_epoch_s, int64_t now_epoch_s) {
    const std::int64_t now = (now_epoch_s != 0) ? now_epoch_s : current_epoch_s();
    return std::fabs(static_cast<double>(now - measured_at_epoch_s)) / 86400.0;
}

ADVTG_C_API void advtg_cal_effective(const advtg_calibration* cal, int64_t now_epoch_s,
                                     double aging_ppm_per_year, advtg_adc_drift* drift_out) {
    if (!cal || !drift_out) return;
    *drift_out = cal->drift;
    const std::int64_t now = (now_epoch_s != 0) ? now_epoch_s : current_epoch_s();
    const double aging =
        aging_ppm_per_year * advtg_cal_age_days(cal->measured_at_epoch_s, now) / kDaysPerYear;
    if (aging > 0.0) {
        drift_out->systematic_ppm = std::hypot(cal->drift.systematic_ppm, aging);
    }
}

ADVTG_C_API int advtg_drift_to_tags(const advtg_adc_drift* drift, const char* note,
                                    const char* calibrated_at_iso, advtg_tag_cb cb, void* user) {
    if (!drift || !cb) return ADVTG_ERR_INVALID;
    cb("ADV_TG_ADC_DRIFT_PPM", fmt_double(drift->ppm, 9).c_str(), user);
    cb("ADV_TG_ADC_DRIFT_SIGMA_PPM", fmt_double(drift->sigma_ppm, 6).c_str(), user);
    cb("ADV_TG_ADC_DRIFT_SYSTEMATIC_PPM", fmt_double(drift->systematic_ppm, 6).c_str(), user);
    cb("ADV_TG_ADC_DRIFT_LEVEL", std::to_string(drift->level).c_str(), user);
    cb("ADV_TG_ADC_DRIFT_METHOD", method_to_str(drift->method), user);
    if (drift->span_s > 0.0) {
        cb("ADV_TG_ADC_DRIFT_SPAN_S", fmt_double(drift->span_s, 6).c_str(), user);
    }
    if (calibrated_at_iso && *calibrated_at_iso) {
        cb("ADV_TG_ADC_DRIFT_CALIBRATED_AT", calibrated_at_iso, user);
    }
    if (note && *note) {
        const std::string one_line = collapse_spaces(note);
        if (!one_line.empty()) cb("ADV_TG_ADC_DRIFT_NOTE", one_line.c_str(), user);
    }
    return ADVTG_OK;
}

ADVTG_C_API int advtg_drift_from_tags(const advtg_audio_tag* tags, size_t n_tags,
                                      advtg_adc_drift* drift_out, char* note_buf,
                                      int32_t note_cap) {
    if (!drift_out || (!tags && n_tags > 0)) return ADVTG_ERR_INVALID;
    std::vector<std::pair<std::string, std::string>> up;
    up.reserve(n_tags);
    auto find_tag = [&](const std::string& key) -> const std::string* {
        for (const auto& kv : up) {
            if (kv.first == key) return &kv.second;
        }
        return nullptr;
    };
    for (size_t i = 0; i < n_tags; ++i) {
        if (!tags[i].key || !tags[i].value) continue;
        std::string k = to_upper_ascii(tags[i].key);
        std::string v = tags[i].value;
        bool updated = false;
        for (auto& kv : up) {
            if (kv.first == k) {
                kv.second = v;
                updated = true;
                break;
            }
        }
        if (!updated) up.emplace_back(std::move(k), std::move(v));
    }
    const std::string* p_ppm = find_tag("ADV_TG_ADC_DRIFT_PPM");
    if (!p_ppm) return 0;

    double ppm = 0.0, sigma = 0.0, sys = 0.0, span = 0.0;
    int level = 0;
    if (!parse_double_strict(*p_ppm, ppm)) {
        set_err("etiquetas de calibración del reloj no válidas");
        return ADVTG_ERR_INVALID;
    }
    if (const auto* s = find_tag("ADV_TG_ADC_DRIFT_SIGMA_PPM")) {
        if (!parse_double_strict(*s, sigma)) return ADVTG_ERR_INVALID;
    }
    if (const auto* s = find_tag("ADV_TG_ADC_DRIFT_SYSTEMATIC_PPM")) {
        if (!parse_double_strict(*s, sys)) return ADVTG_ERR_INVALID;
    }
    if (const auto* s = find_tag("ADV_TG_ADC_DRIFT_SPAN_S")) {
        if (!parse_double_strict(*s, span)) return ADVTG_ERR_INVALID;
    }
    if (const auto* s = find_tag("ADV_TG_ADC_DRIFT_LEVEL")) {
        if (!parse_int_strict(*s, level) || level < ADVTG_CLOCK_UNCALIBRATED ||
            level > ADVTG_CLOCK_EXTERNAL) {
            set_err("etiquetas de calibración del reloj no válidas: nivel");
            return ADVTG_ERR_INVALID;
        }
    }
    if (!std::isfinite(ppm) || !std::isfinite(sigma) || !std::isfinite(sys) ||
        !std::isfinite(span) || std::min(sigma, sys) < 0.0) {
        set_err("etiquetas de calibración del reloj no válidas (valores no finitos)");
        return ADVTG_ERR_INVALID;
    }
    std::memset(drift_out, 0, sizeof *drift_out);
    drift_out->ppm = ppm;
    drift_out->sigma_ppm = sigma;
    drift_out->systematic_ppm = sys;
    drift_out->level = level;
    drift_out->span_s = span;
    drift_out->residual_us = std::numeric_limits<double>::quiet_NaN();
    if (const auto* m = find_tag("ADV_TG_ADC_DRIFT_METHOD")) {
        drift_out->method = method_from_str(*m);
    }
    if (note_buf && note_cap > 0) {
        const std::string note = find_tag("ADV_TG_ADC_DRIFT_NOTE") ? *find_tag("ADV_TG_ADC_DRIFT_NOTE") : "";
        const std::size_t n =
            std::min<std::size_t>(note.size(), static_cast<std::size_t>(note_cap - 1));
        if (n) std::memcpy(note_buf, note.data(), n);
        note_buf[n] = '\0';
    }
    return 1;
}

ADVTG_C_API int32_t advtg_cal_store_records(const char* store_path, const char* device_filter,
                                            int32_t sample_rate_filter, advtg_calibration* out,
                                            int32_t cap) {
    std::vector<CalRecord> all;
    if (!load_store(default_store_path(store_path), all)) return ADVTG_ERR_CORRUPT_STORE;
    std::vector<CalRecord> filtered;
    for (const auto& r : all) {
        if (device_filter && *device_filter && !same_device(r.device, device_filter)) continue;
        if (sample_rate_filter > 0 && r.sample_rate != sample_rate_filter) continue;
        filtered.push_back(r);
    }
    std::stable_sort(filtered.begin(), filtered.end(), [](const CalRecord& a, const CalRecord& b) {
        return a.measured_at_epoch_s < b.measured_at_epoch_s;
    });
    if (out && cap > 0) {
        const auto m = std::min<std::size_t>(filtered.size(), static_cast<std::size_t>(cap));
        for (std::size_t i = 0; i < m; ++i) out[i] = to_c(filtered[i]);
    }
    return static_cast<int32_t>(filtered.size());
}

ADVTG_C_API int advtg_cal_store_best(const char* store_path, const char* device,
                                     int32_t sample_rate, int64_t now_epoch_s,
                                     double aging_ppm_per_year, advtg_calibration* out) {
    if (!device || !out || sample_rate <= 0) return ADVTG_ERR_INVALID;
    std::vector<CalRecord> all;
    if (!load_store(default_store_path(store_path), all)) return ADVTG_ERR_CORRUPT_STORE;
    std::vector<CalRecord> filtered;
    for (const auto& r : all) {
        if (same_device(r.device, device) && r.sample_rate == sample_rate) filtered.push_back(r);
    }
    const std::int64_t now = (now_epoch_s != 0) ? now_epoch_s : current_epoch_s();
    const CalRecord* best = pick_best_record(filtered, now, aging_ppm_per_year);
    if (!best) return 0;
    *out = to_c(*best);
    return 1;
}

ADVTG_C_API int advtg_cal_store_add(const char* store_path, const advtg_calibration* cal) {
    if (!cal) return ADVTG_ERR_INVALID;
    CalRecord rec = from_c(*cal);
    std::string verr;
    if (!validate_record(rec, verr)) {
        set_err(verr);
        return ADVTG_ERR_INVALID;
    }
    const fs::path p = default_store_path(store_path);
    std::vector<CalRecord> all;
    if (!load_store(p, all)) return ADVTG_ERR_CORRUPT_STORE;
    std::string known;
    for (const auto& r : all) {
        if (same_device(r.device, rec.device)) {
            known = r.device;
            break;
        }
    }
    rec.device = known.empty() ? trim(rec.device) : known;
    all.push_back(rec);

    std::vector<std::size_t> mine;
    for (std::size_t i = 0; i < all.size(); ++i) {
        if (same_device(all[i].device, rec.device) && all[i].sample_rate == rec.sample_rate) {
            mine.push_back(i);
        }
    }
    std::vector<bool> keep(all.size(), true);
    if (mine.size() > kMaxHistory) {
        std::stable_sort(mine.begin(), mine.end(), [&](std::size_t a, std::size_t b) {
            return all[a].measured_at_epoch_s < all[b].measured_at_epoch_s;
        });
        const std::size_t drop_n = mine.size() - kMaxHistory;
        for (std::size_t i = 0; i < drop_n; ++i) keep[mine[i]] = false;
    }
    std::vector<CalRecord> next;
    next.reserve(all.size());
    for (std::size_t i = 0; i < all.size(); ++i) {
        if (keep[i]) next.push_back(std::move(all[i]));
    }
    return save_store(p, next) ? ADVTG_OK : ADVTG_ERR_IO;
}

ADVTG_C_API int32_t advtg_cal_store_forget(const char* store_path, const char* device,
                                           int32_t sample_rate_filter) {
    if (!device) return ADVTG_ERR_INVALID;
    const fs::path p = default_store_path(store_path);
    std::vector<CalRecord> all;
    if (!load_store(p, all)) return ADVTG_ERR_CORRUPT_STORE;
    std::vector<CalRecord> keep;
    keep.reserve(all.size());
    int32_t removed = 0;
    for (auto& r : all) {
        if (same_device(r.device, device) &&
            (sample_rate_filter <= 0 || r.sample_rate == sample_rate_filter)) {
            ++removed;
        } else {
            keep.push_back(std::move(r));
        }
    }
    if (removed > 0 && !save_store(p, keep)) return ADVTG_ERR_IO;
    return removed;
}

ADVTG_C_API int advtg_cal_recording_tags(const char* store_path, const char* device,
                                         int32_t sample_rate, int64_t now_epoch_s,
                                         advtg_tag_cb cb, void* user, int* has_cal_out,
                                         advtg_calibration* cal_out) {
    if (!cb || sample_rate <= 0) return ADVTG_ERR_INVALID;
    if (has_cal_out) *has_cal_out = 0;
    const std::int64_t now = (now_epoch_s != 0) ? now_epoch_s : current_epoch_s();
    const std::string now_iso = format_iso_utc(now);
    cb("ADV_TG_RECORDED_AT", now_iso.c_str(), user);
    cb("ADV_TG_SAMPLE_RATE", std::to_string(sample_rate).c_str(), user);
    if (device && *device) {
        const std::string clean = collapse_spaces(device);
        if (!clean.empty()) cb("ADV_TG_DEVICE", clean.c_str(), user);
    }
    advtg_calibration cal{};
    int has_cal = 0;
    if (device && *device) {
        const int rc = advtg_cal_store_best(store_path, device, sample_rate, now,
                                            kCrystalAgingPpmPerYear, &cal);
        if (rc < 0) return rc;
        has_cal = (rc > 0) ? 1 : 0;
    }
    if (!has_cal) {
        advtg_adc_drift uncal{};
        uncal.systematic_ppm = kUncalibratedSigmaPpm;
        uncal.level = ADVTG_CLOCK_UNCALIBRATED;
        uncal.method = 0;
        uncal.residual_us = std::numeric_limits<double>::quiet_NaN();
        advtg_drift_to_tags(&uncal, nullptr, nullptr, cb, user);
    } else {
        advtg_adc_drift eff{};
        advtg_cal_effective(&cal, now, kCrystalAgingPpmPerYear, &eff);
        const std::string cal_iso = format_iso_utc(cal.measured_at_epoch_s);
        advtg_drift_to_tags(&eff, cal.note, cal_iso.c_str(), cb, user);
        if (has_cal_out) *has_cal_out = 1;
        if (cal_out) *cal_out = cal;
    }
    return ADVTG_OK;
}

}  // extern "C"
