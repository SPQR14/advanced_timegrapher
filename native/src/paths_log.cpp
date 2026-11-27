// Cross-platform config/log directories and rotating file logger (utils/logger_setup.py,
// utils/adc_calibration.py).
#include "advtg.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr std::uintmax_t kMaxLogBytes = 5ULL * 1024ULL * 1024ULL;
constexpr int kBackupCount = 3;

std::string get_env_str(const char* name) {
#if defined(_WIN32)
    int wlen = MultiByteToWideChar(CP_UTF8, 0, name, -1, nullptr, 0);
    if (wlen <= 0) return "";
    std::wstring wn(static_cast<std::size_t>(wlen), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, name, -1, wn.data(), wlen);
    DWORD need = GetEnvironmentVariableW(wn.c_str(), nullptr, 0);
    if (need == 0) return "";
    std::wstring val(need, L'\0');
    DWORD got = GetEnvironmentVariableW(wn.c_str(), val.data(), need);
    if (got == 0) return "";
    val.resize(got);
    int ulen = WideCharToMultiByte(CP_UTF8, 0, val.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (ulen <= 1) return "";
    std::string out(static_cast<std::size_t>(ulen - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, val.c_str(), -1, out.data(), ulen, nullptr, nullptr);
    return out;
#else
    const char* v = std::getenv(name);
    return v ? std::string(v) : std::string();
#endif
}

std::string trim_copy(const std::string& s) {
    std::size_t a = 0;
    while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    std::size_t b = s.size();
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

fs::path home_dir() {
#if defined(_WIN32)
    std::string up = get_env_str("USERPROFILE");
    if (!up.empty()) return fs::path(reinterpret_cast<const char8_t*>(up.c_str()));
    std::string hd = get_env_str("HOMEDRIVE") + get_env_str("HOMEPATH");
    if (!hd.empty()) return fs::path(reinterpret_cast<const char8_t*>(hd.c_str()));
    return fs::path("C:\\");
#else
    std::string h = get_env_str("HOME");
    if (!h.empty()) return fs::path(reinterpret_cast<const char8_t*>(h.c_str()));
    return fs::path("/");
#endif
}

fs::path expand_user(const std::string& raw) {
    if (raw == "~") return home_dir();
    if (raw.size() >= 2 && raw[0] == '~' && (raw[1] == '/' || raw[1] == '\\')) {
        return home_dir() / fs::path(reinterpret_cast<const char8_t*>(raw.c_str() + 2));
    }
    return fs::path(reinterpret_cast<const char8_t*>(raw.c_str()));
}

std::string path_u8(const fs::path& p) {
    auto u = p.u8string();
    return std::string(reinterpret_cast<const char*>(u.data()), u.size());
}

int32_t copy_str(const std::string& s, char* buf, int32_t cap) {
    if (buf && cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(s.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, s.data(), n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(s.size());
}

std::string sanitize_log_name(const char* raw) {
    if (!raw) return "";
    std::string in = trim_copy(raw);
    std::string out;
    for (char c : in) {
        if (std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_') out.push_back(c);
    }
    return out;
}

struct LoggerState {
    std::mutex mu;
    bool initialized = false;
    fs::path log_dir;
    fs::path log_file;
};

LoggerState& logger_state() {
    static LoggerState st;
    return st;
}

void ensure_dir_private(const fs::path& d) {
    std::error_code ec;
    fs::create_directories(d, ec);
#if !defined(_WIN32)
    fs::permissions(d, fs::perms::owner_all, fs::perm_options::replace, ec);
#endif
}

void rotate_if_needed(const fs::path& file) {
    std::error_code ec;
    if (!fs::exists(file, ec)) return;
    const auto sz = fs::file_size(file, ec);
    if (ec || sz < kMaxLogBytes) return;
    for (int i = kBackupCount - 1; i >= 1; --i) {
        fs::path src = fs::path(reinterpret_cast<const char8_t*>(
            (path_u8(file) + "." + std::to_string(i)).c_str()));
        fs::path dst = fs::path(reinterpret_cast<const char8_t*>(
            (path_u8(file) + "." + std::to_string(i + 1)).c_str()));
        if (fs::exists(src, ec)) {
            fs::remove(dst, ec);
            fs::rename(src, dst, ec);
        }
    }
    fs::path dst1 = fs::path(reinterpret_cast<const char8_t*>((path_u8(file) + ".1").c_str()));
    fs::remove(dst1, ec);
    fs::rename(file, dst1, ec);
}

const char* level_name(int level) {
    if (level <= 10) return "DEBUG";
    if (level <= 20) return "INFO";
    if (level <= 30) return "WARNING";
    if (level <= 40) return "ERROR";
    return "CRITICAL";
}

}  // namespace

extern "C" {

ADVTG_C_API int32_t advtg_config_dir(char* buf, int32_t cap) {
    const std::string env = trim_copy(get_env_str("ADV_TG_CONFIG_DIR"));
    fs::path out;
    if (!env.empty()) {
        out = expand_user(env);
    } else {
#if defined(__APPLE__)
        out = home_dir() / "Library" / "Application Support" / "adv_tg";
#elif defined(_WIN32)
        const std::string base = get_env_str("APPDATA");
        out = (base.empty() ? (home_dir() / "AppData" / "Roaming")
                            : fs::path(reinterpret_cast<const char8_t*>(base.c_str()))) /
              "adv_tg";
#else
        const std::string base = get_env_str("XDG_CONFIG_HOME");
        out = (base.empty() ? (home_dir() / ".config")
                            : fs::path(reinterpret_cast<const char8_t*>(base.c_str()))) /
              "adv_tg";
#endif
    }
    return copy_str(path_u8(out), buf, cap);
}

ADVTG_C_API int32_t advtg_default_log_dir(char* buf, int32_t cap) {
    const std::string env = trim_copy(get_env_str("ADV_TG_LOG_DIR"));
    fs::path out;
    if (!env.empty()) {
        std::error_code ec;
        fs::path p = expand_user(env);
        fs::path abs = fs::weakly_canonical(p, ec);
        out = ec ? fs::absolute(p, ec) : abs;
    } else {
#if defined(__APPLE__)
        out = home_dir() / "Library" / "Logs" / "adv_tg";
#elif defined(_WIN32)
        const std::string base = get_env_str("LOCALAPPDATA");
        out = (base.empty() ? (home_dir() / "AppData" / "Local")
                            : fs::path(reinterpret_cast<const char8_t*>(base.c_str()))) /
              "adv_tg" / "logs";
#else
        const std::string base = get_env_str("XDG_STATE_HOME");
        out = (base.empty() ? (home_dir() / ".local" / "state")
                            : fs::path(reinterpret_cast<const char8_t*>(base.c_str()))) /
              "adv_tg" / "logs";
#endif
    }
    return copy_str(path_u8(out), buf, cap);
}

ADVTG_C_API int advtg_log_init(const char* log_dir, const char* log_name) {
    auto& st = logger_state();
    std::lock_guard<std::mutex> lk(st.mu);
    if (log_dir && *log_dir) {
        st.log_dir = fs::path(reinterpret_cast<const char8_t*>(log_dir));
    } else {
        char tmp[1024];
        advtg_default_log_dir(tmp, sizeof(tmp));
        st.log_dir = fs::path(reinterpret_cast<const char8_t*>(tmp));
    }
    ensure_dir_private(st.log_dir);
    std::string safe = sanitize_log_name(log_name);
    if (safe.empty()) safe = sanitize_log_name(get_env_str("ADV_TG_LOG_NAME").c_str());
    if (safe.empty()) safe = "adv_tg";
    st.log_file = st.log_dir / fs::path(reinterpret_cast<const char8_t*>((safe + ".log").c_str()));
    st.initialized = true;
    return ADVTG_OK;
}

ADVTG_C_API int advtg_log_write(int level, const char* component, const char* msg) {
    auto& st = logger_state();
    std::lock_guard<std::mutex> lk(st.mu);
    if (!st.initialized) {
        char tmp[1024];
        advtg_default_log_dir(tmp, sizeof(tmp));
        st.log_dir = fs::path(reinterpret_cast<const char8_t*>(tmp));
        ensure_dir_private(st.log_dir);
        std::string safe = sanitize_log_name(get_env_str("ADV_TG_LOG_NAME").c_str());
        if (safe.empty()) safe = "adv_tg";
        st.log_file =
            st.log_dir / fs::path(reinterpret_cast<const char8_t*>((safe + ".log").c_str()));
        st.initialized = true;
    }
    rotate_if_needed(st.log_file);
    const auto now = std::chrono::system_clock::now();
    const auto ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;
    const std::time_t tt = std::chrono::system_clock::to_time_t(now);
    std::tm tm_now{};
#if defined(_WIN32)
    localtime_s(&tm_now, &tt);
#else
    localtime_r(&tt, &tm_now);
#endif
    char ts[32];
    std::snprintf(ts, sizeof(ts), "%02d-%02d-%04d %02d:%02d:%02d.%03d", tm_now.tm_mday,
                  tm_now.tm_mon + 1, tm_now.tm_year + 1900, tm_now.tm_hour, tm_now.tm_min,
                  tm_now.tm_sec, static_cast<int>(ms.count()));
    std::ofstream f(st.log_file, std::ios::app | std::ios::binary);
    if (!f) return ADVTG_ERR_IO;
    f << ts << " - native - " << (component && *component ? component : "adv_tg") << " - "
      << level_name(level) << " - " << (msg ? msg : "") << '\n';
    return f.good() ? ADVTG_OK : ADVTG_ERR_IO;
}

ADVTG_C_API int32_t advtg_log_file_path(char* buf, int32_t cap) {
    auto& st = logger_state();
    std::lock_guard<std::mutex> lk(st.mu);
    if (!st.initialized) {
        char tmp[1024];
        advtg_default_log_dir(tmp, sizeof(tmp));
        std::string safe = sanitize_log_name(get_env_str("ADV_TG_LOG_NAME").c_str());
        if (safe.empty()) safe = "adv_tg";
        fs::path p = fs::path(reinterpret_cast<const char8_t*>(tmp)) /
                     fs::path(reinterpret_cast<const char8_t*>((safe + ".log").c_str()));
        return copy_str(path_u8(p), buf, cap);
    }
    return copy_str(path_u8(st.log_file), buf, cap);
}

}  // extern "C"
