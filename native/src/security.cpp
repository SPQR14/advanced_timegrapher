// Security helpers: CSV formula neutralization, safe filenames, PII scrubbing (utils/security.py).
#include "advtg.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

namespace {

int32_t copy_str(const std::string& s, char* out, int32_t cap) {
    if (out && cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(s.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(out, s.data(), n);
        out[n] = '\0';
    }
    return static_cast<int32_t>(s.size());
}

bool is_finite_number_str(const std::string& s) {
    if (s.empty()) return false;
    const char* p = s.c_str();
    char* end = nullptr;
    const double v = std::strtod(p, &end);
    if (end == p) return false;
    while (*end != '\0') {
        if (!std::isspace(static_cast<unsigned char>(*end))) return false;
        ++end;
    }
    return std::isfinite(v);
}

bool is_unicode_alnum(std::uint32_t cp) {
    if (cp < 0x80U) {
        return std::isalnum(static_cast<unsigned char>(cp)) != 0;
    }
    if (cp >= 0xC0U && cp <= 0x2AFU) {
        return cp != 0xD7U && cp != 0xF7U;
    }
    if (cp >= 0x370U && cp <= 0x1FFFU) return true;
    if (cp >= 0x2C00U && cp <= 0xD7AFU) return true;
    if (cp >= 0xF900U && cp <= 0x10FFFFU) return true;
    return false;
}

bool is_word_or_path_char(unsigned char c) {
    return std::isalnum(c) != 0 || c == '_' || c == '.' || c == '-' || c >= 0x80U;
}

bool iequals_ascii_at(const std::string& text, std::size_t pos, const std::string& needle) {
    if (pos + needle.size() > text.size()) return false;
    for (std::size_t i = 0; i < needle.size(); ++i) {
        if (std::tolower(static_cast<unsigned char>(text[pos + i])) !=
            std::tolower(static_cast<unsigned char>(needle[i]))) {
            return false;
        }
    }
    return true;
}

std::string to_lower_ascii(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string current_username() {
#if defined(_WIN32)
    DWORD len = 0;
    GetUserNameW(nullptr, &len);
    if (len > 1) {
        std::wstring ws(len, L'\0');
        if (GetUserNameW(ws.data(), &len) && len > 1) {
            ws.resize(len - 1);
            int ulen = WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), -1, nullptr, 0, nullptr, nullptr);
            if (ulen > 1) {
                std::string out(static_cast<std::size_t>(ulen - 1), '\0');
                WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), -1, out.data(), ulen, nullptr, nullptr);
                return out;
            }
        }
    }
#endif
    for (const char* k : {"LOGNAME", "USER", "LNAME", "USERNAME"}) {
        if (const char* v = std::getenv(k); v && *v) return v;
    }
    return "";
}

std::vector<std::string> default_home_variants() {
    std::vector<std::string> out;
    auto add = [&](const std::string& s) {
        if (s.size() > 1 && s != "/" && std::find(out.begin(), out.end(), s) == out.end()) {
            out.push_back(s);
        }
    };
    const char* h = std::getenv("HOME");
    if (!h || !*h) h = std::getenv("USERPROFILE");
    if (h && *h) {
        std::filesystem::path p(reinterpret_cast<const char8_t*>(h));
        auto u = p.u8string();
        add(std::string(reinterpret_cast<const char*>(u.data()), u.size()));
        auto g = p.generic_u8string();
        add(std::string(reinterpret_cast<const char*>(g.data()), g.size()));
        std::error_code ec;
        auto r = std::filesystem::weakly_canonical(p, ec);
        if (!ec) {
            auto ru = r.u8string();
            add(std::string(reinterpret_cast<const char*>(ru.data()), ru.size()));
            auto rg = r.generic_u8string();
            add(std::string(reinterpret_cast<const char*>(rg.data()), rg.size()));
        }
    }
    std::sort(out.begin(), out.end(),
              [](const std::string& a, const std::string& b) { return a.size() > b.size(); });
    return out;
}

void replace_all(std::string& s, const std::string& from, const std::string& to) {
    if (from.empty()) return;
    std::size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
}

}  // namespace

extern "C" {

ADVTG_C_API int32_t advtg_sanitize_csv_cell(const char* in, char* out, int32_t cap) {
    if (!in) return copy_str("", out, cap);
    const std::string raw(in);
    std::size_t i = 0;
    while (i < raw.size() && raw[i] == ' ') ++i;
    const std::string s = raw.substr(i);
    if (!s.empty()) {
        const char c = s[0];
        if (c == '=' || c == '+' || c == '-' || c == '@' || c == '\t' || c == '\r') {
            if (!is_finite_number_str(s)) {
                return copy_str("'" + raw, out, cap);
            }
        }
    }
    return copy_str(raw, out, cap);
}

ADVTG_C_API int32_t advtg_safe_filename_base(const char* name, const char* fallback, char* out,
                                             int32_t cap) {
    const std::string fb = (fallback && *fallback) ? fallback : "watch_test";
    if (!name) return copy_str(fb, out, cap);
    const std::string in(name);
    std::string filtered;
    filtered.reserve(in.size());
    for (std::size_t i = 0; i < in.size();) {
        const unsigned char b = static_cast<unsigned char>(in[i]);
        if (b < 0x80U) {
            if (std::isalnum(b) || b == '-' || b == '_' || b == ' ') {
                filtered.push_back(static_cast<char>(b));
            }
            ++i;
            continue;
        }
        std::uint32_t cp = 0;
        std::size_t adv = 1;
        if ((b & 0xE0U) == 0xC0U && i + 1 < in.size()) {
            cp = ((b & 0x1FU) << 6) | (static_cast<unsigned char>(in[i + 1]) & 0x3FU);
            adv = 2;
        } else if ((b & 0xF0U) == 0xE0U && i + 2 < in.size()) {
            cp = ((b & 0x0FU) << 12) | ((static_cast<unsigned char>(in[i + 1]) & 0x3FU) << 6) |
                 (static_cast<unsigned char>(in[i + 2]) & 0x3FU);
            adv = 3;
        } else if ((b & 0xF8U) == 0xF0U && i + 3 < in.size()) {
            cp = ((b & 0x07U) << 18) | ((static_cast<unsigned char>(in[i + 1]) & 0x3FU) << 12) |
                 ((static_cast<unsigned char>(in[i + 2]) & 0x3FU) << 6) |
                 (static_cast<unsigned char>(in[i + 3]) & 0x3FU);
            adv = 4;
        }
        if (is_unicode_alnum(cp)) {
            filtered.append(in, i, adv);
        }
        i += adv;
    }
    std::size_t a = 0;
    while (a < filtered.size() && std::isspace(static_cast<unsigned char>(filtered[a]))) ++a;
    std::size_t b = filtered.size();
    while (b > a && std::isspace(static_cast<unsigned char>(filtered[b - 1]))) --b;
    const std::string trimmed = filtered.substr(a, b - a);
    return copy_str(trimmed.empty() ? fb : trimmed, out, cap);
}

ADVTG_C_API int32_t advtg_anonymize_text(const char* text, const char* username,
                                         const char* home_variants_nl, char* out, int32_t cap) {
    if (!text) return copy_str("", out, cap);
    std::string s(text);
    if (s.empty()) return copy_str(s, out, cap);

    std::vector<std::string> homes;
    if (home_variants_nl) {
        std::size_t start = 0;
        const std::string hv(home_variants_nl);
        while (start <= hv.size()) {
            std::size_t nl = hv.find('\n', start);
            if (nl == std::string::npos) nl = hv.size();
            const std::string item = hv.substr(start, nl - start);
            if (item.size() > 1 && item != "/") homes.push_back(item);
            if (nl == hv.size()) break;
            start = nl + 1;
        }
        std::sort(homes.begin(), homes.end(),
                  [](const std::string& a, const std::string& b) { return a.size() > b.size(); });
    } else {
        homes = default_home_variants();
    }
    for (const auto& h : homes) replace_all(s, h, "~");

    const std::string user = username ? std::string(username) : current_username();
    const std::string user_low = to_lower_ascii(user);
    if (user.size() < 2 || user_low == "root" || user_low == "nobody") {
        return copy_str(s, out, cap);
    }

    auto subst = [&](bool require_slash) {
        std::string res;
        res.reserve(s.size());
        for (std::size_t i = 0; i < s.size();) {
            if (iequals_ascii_at(s, i, user)) {
                const bool prev_ok =
                    require_slash
                        ? (i > 0 && (s[i - 1] == '/' || s[i - 1] == '\\'))
                        : (i == 0 || !is_word_or_path_char(static_cast<unsigned char>(s[i - 1])));
                const std::size_t after = i + user.size();
                const bool next_ok =
                    (after >= s.size() || !is_word_or_path_char(static_cast<unsigned char>(s[after])));
                if (prev_ok && next_ok) {
                    res += "<USER>";
                    i = after;
                    continue;
                }
            }
            res.push_back(s[i]);
            ++i;
        }
        s = std::move(res);
    };

    subst(true);
    if (user.size() >= 4) subst(false);
    return copy_str(s, out, cap);
}

}  // extern "C"
