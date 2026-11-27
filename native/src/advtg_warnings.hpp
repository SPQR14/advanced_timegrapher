// Internal bilingual warning codes and formatting helpers (advtg.h ADVTG_WARN_*).
#pragma once

#include <string>
#include <vector>

#include "advtg.h"

namespace advtg::warn {

inline advtg_warning make(int32_t code, int32_t param_int = 0, double a0 = 0.0, double a1 = 0.0,
                          double a2 = 0.0, double a3 = 0.0) {
    advtg_warning w{};
    w.code = code;
    w.param_int = param_int;
    w.args[0] = a0;
    w.args[1] = a1;
    w.args[2] = a2;
    w.args[3] = a3;
    return w;
}

std::string format_warning_str(const advtg_warning& w, const char* lang);

inline void append_unique_code(std::vector<advtg_warning>& codes,
                               std::vector<std::string>& texts, const advtg_warning& w) {
    for (const auto& existing : codes) {
        if (existing.code == w.code) return;
    }
    codes.push_back(w);
    texts.push_back(format_warning_str(w, "es"));
}

inline void append(std::vector<advtg_warning>& codes, std::vector<std::string>& texts,
                   const advtg_warning& w) {
    codes.push_back(w);
    texts.push_back(format_warning_str(w, "es"));
}

}  // namespace advtg::warn
