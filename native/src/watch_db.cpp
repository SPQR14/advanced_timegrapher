// Watch caliber database CSV loader (utils/watch_db.py).
#include "advtg.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace {

std::string trim(const std::string& s) {
    std::size_t a = 0;
    while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    std::size_t b = s.size();
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

std::vector<std::string> parse_csv_line(const std::string& line) {
    std::vector<std::string> cells;
    std::string cur;
    bool in_quotes = false;
    for (std::size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < line.size() && line[i + 1] == '"') {
                    cur.push_back('"');
                    ++i;
                } else {
                    in_quotes = false;
                }
            } else {
                cur.push_back(c);
            }
        } else {
            if (c == '"') {
                in_quotes = true;
            } else if (c == ',') {
                cells.push_back(cur);
                cur.clear();
            } else {
                cur.push_back(c);
            }
        }
    }
    cells.push_back(cur);
    return cells;
}

bool parse_double_strict(const std::string& s, double& out) {
    const std::string t = trim(s);
    if (t.empty()) return false;
    char* end = nullptr;
    const double v = std::strtod(t.c_str(), &end);
    if (end == t.c_str() || *end != '\0' || !std::isfinite(v)) return false;
    out = v;
    return true;
}

advtg_watch_caliber make_cal(const std::string& name, int32_t bph, double lift_angle) {
    advtg_watch_caliber item{};
    std::snprintf(item.name, sizeof(item.name), "%s", name.c_str());
    item.bph = bph;
    item.lift_angle_deg = lift_angle;
    return item;
}

}  // namespace

extern "C" {

ADVTG_C_API int32_t advtg_load_watch_db(const char* csv_path, advtg_watch_caliber* out,
                                        int32_t cap) {
    std::vector<advtg_watch_caliber> list;
    auto upsert = [&](const std::string& name, int32_t bph, double la) {
        for (auto& existing : list) {
            if (name == existing.name) {
                existing.bph = bph;
                existing.lift_angle_deg = la;
                return;
            }
        }
        list.push_back(make_cal(name, bph, la));
    };

    if (csv_path && *csv_path) {
        std::filesystem::path p(reinterpret_cast<const char8_t*>(csv_path));
        std::error_code ec;
        if (std::filesystem::exists(p, ec)) {
            std::ifstream fh(p, std::ios::binary);
            if (fh) {
                std::string line;
                while (std::getline(fh, line)) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    if (trim(line).empty()) continue;
                    auto row = parse_csv_line(line);
                    bool all_empty = true;
                    for (const auto& c : row) {
                        if (!trim(c).empty()) {
                            all_empty = false;
                            break;
                        }
                    }
                    if (all_empty || row.size() < 3U) continue;
                    const std::string name = trim(row[0]);
                    double lift_angle = 0.0;
                    double bph_f = 0.0;
                    if (!parse_double_strict(row[1], lift_angle) ||
                        !parse_double_strict(row[2], bph_f)) {
                        continue;
                    }
                    const int32_t bph = static_cast<int32_t>(bph_f);
                    if (bph < 1000 || bph > 120000) continue;
                    if (!(lift_angle >= 10.0 && lift_angle <= 90.0)) continue;
                    upsert(name, bph, lift_angle);
                }
            }
        }
    }

    bool has_custom = false;
    for (const auto& item : list) {
        if (std::strcmp(item.name, "Custom") == 0) {
            has_custom = true;
            break;
        }
    }
    if (!has_custom) {
        list.insert(list.begin(), make_cal("Custom", 21600, 52.0));
    }

    if (out && cap > 0) {
        const auto m = std::min<std::size_t>(list.size(), static_cast<std::size_t>(cap));
        if (m) std::memcpy(out, list.data(), m * sizeof(advtg_watch_caliber));
    }
    return static_cast<int32_t>(list.size());
}

}  // extern "C"
