// The live session of the engine, driven from C++ through the public C API (advtg.h), must
// reproduce the golden files of v2.0.1 (tests/golden/<take>/live.jsonl; protocol in
// tests/golden/protocol.py, mirrored here). Batch beats and PPS pulses are checked in C++ from
// phase 1, when their API is public.
#include <catch2/catch_test_macros.hpp>
#include <catch2/generators/catch_generators.hpp>
#include <catch2/generators/catch_generators_range.hpp>
#include <sndfile.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include "advtg.h"
#include "json_lite.hpp"

#ifndef ADVTG_SOURCE_DIR
#  error "ADVTG_SOURCE_DIR must point to the repository"
#endif

namespace {

const std::string kRoot = ADVTG_SOURCE_DIR;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

// ---------------------------------------------------------------- protocol (protocol.py)
constexpr int kBlock = 1024;
constexpr int kLiveSeconds = 60;
constexpr int kCapacitySeconds = 20;
constexpr std::int64_t kT0 = 1'700'000'000'000'000'000LL;
constexpr std::int64_t kStampPpm = 12;

std::int64_t stamp_ns(std::int64_t end, std::int64_t fs) {
    return kT0 + (end * 1'000'000'000LL) / fs - (end * kStampPpm * 1000) / fs;
}

// ---------------------------------------------------------------- tolerance (protocol.NATIVE)
bool close(const std::string& field, const jsonl::Value& want, double got) {
    if (want.kind == jsonl::Value::Null) return std::isnan(got);
    if (std::isnan(got)) return false;
    const double w = want.number();
    const bool rate = field == "rate_s_per_day";
    const bool be = field == "beat_error_ms" || field == "beat_error_p3_ms";
    const bool amp = field == "amplitude_deg" || field == "amplitude_tic_deg" ||
                     field == "amplitude_tac_deg";
    if (rate || be || amp) return std::fabs(got - w) <= 1e-6;
    if (want.kind == jsonl::Value::Int) return got == w;
    return std::fabs(got - w) <= 1e-9 + 1e-7 * std::fabs(w);
}

struct Checker {
    std::vector<std::string> errors;
    void num(const std::string& where, const jsonl::Value& obj, const char* key, double got) {
        auto it = obj.o.find(key);
        if (it == obj.o.end()) {
            errors.push_back(where + "." + key + ": missing in golden");
        } else if (!close(key, it->second, got)) {
            std::ostringstream m;
            m.precision(17);
            m << where << "." << key << ": " << got << " != "
              << (it->second.kind == jsonl::Value::Null ? kNaN : it->second.number());
            errors.push_back(m.str());
        }
    }
    void text(const std::string& where, const jsonl::Value& obj, const char* key,
              const std::string& got) {
        auto it = obj.o.find(key);
        if (it == obj.o.end() || it->second.s != got)
            errors.push_back(where + "." + key + ": '" + got + "' != '" +
                             (it == obj.o.end() ? "?" : it->second.s) + "'");
    }
    void flag(const std::string& where, const jsonl::Value& obj, const char* key, bool got) {
        auto it = obj.o.find(key);
        if (it == obj.o.end() || it->second.b != got) errors.push_back(where + "." + key);
    }
};

const char* be_point(int i) {
    static const char* names[] = {"\xE2\x80\x94", "p1", "p1_beat", "p1_fine", "t_fine"};  // "—"
    return (i >= 0 && i < 5) ? names[i] : names[0];
}
const char* amp_parity(int i) {
    static const char* names[] = {"both", "tic", "tac"};
    return (i >= 0 && i < 3) ? names[i] : names[0];
}

void check_snapshot(Checker& c, const std::string& where, const jsonl::Value& w,
                    const advtg_snapshot& s) {
    c.num(where, w, "audio_s", s.audio_s);
    c.num(where, w, "new_beats", static_cast<double>(s.new_beats));
    c.num(where, w, "beats_total", static_cast<double>(s.beats_total));
    c.flag(where, w, "locked", s.locked != 0);
    c.num(where, w, "saturated_beats", static_cast<double>(s.saturated_beats));
    c.num(where, w, "snr_db", s.snr_db);
    c.num(where, w, "detection_ratio", s.detection_ratio);
    c.num(where, w, "usable_ratio", s.usable_ratio);
    c.num(where, w, "real_bph", s.real_bph);
    c.num(where, w, "tape_len", static_cast<double>(s.tape_len));
    c.num(where, w, "waveform_len", static_cast<double>(s.waveform_len));
    const jsonl::Value& d = w.o.at("display");
    const std::string wd = where + ".display";
    c.num(wd, d, "rate_s_per_day", s.display.rate_s_per_day);
    c.num(wd, d, "beat_error_ms", s.display.beat_error_ms);
    c.num(wd, d, "beat_error_p3_ms", s.display.beat_error_p3_ms);
    c.num(wd, d, "amplitude_deg", s.display.amplitude_deg);
    c.num(wd, d, "amplitude_tic_deg", s.display.amplitude_tic_deg);
    c.num(wd, d, "amplitude_tac_deg", s.display.amplitude_tac_deg);
    c.num(wd, d, "confidence", s.display.confidence);
    c.num(wd, d, "timing_confidence", s.display.timing_confidence);
    const jsonl::Value& m = w.o.at("metrics");
    const std::string wm = where + ".metrics";
    const advtg_metrics& x = s.metrics;
    c.num(wm, m, "n_beats", static_cast<double>(x.n_beats));
    c.num(wm, m, "n_used_even", static_cast<double>(x.n_used_even));
    c.num(wm, m, "n_used_odd", static_cast<double>(x.n_used_odd));
    c.num(wm, m, "n_excluded", static_cast<double>(x.n_excluded));
    c.num(wm, m, "excluded_fraction", x.excluded_fraction);
    c.num(wm, m, "rate_s_per_day", x.rate_s_per_day);
    c.num(wm, m, "rate_sigma_s_per_day", x.rate_sigma_s_per_day);
    c.num(wm, m, "rate_uncertainty_s_per_day", x.rate_uncertainty_s_per_day);
    c.num(wm, m, "beat_error_ms", x.beat_error_ms);
    c.num(wm, m, "beat_error_uncertainty_ms", x.beat_error_uncertainty_ms);
    c.num(wm, m, "beat_error_p3_ms", x.beat_error_p3_ms);
    c.text(wm, m, "beat_error_point", be_point(x.beat_error_point));
    c.num(wm, m, "amplitude_deg", x.amplitude_deg);
    c.num(wm, m, "amplitude_tic_deg", x.amplitude_tic_deg);
    c.num(wm, m, "amplitude_tac_deg", x.amplitude_tac_deg);
    c.text(wm, m, "amplitude_parity", amp_parity(x.amplitude_parity));
    c.num(wm, m, "sigma_even_us", x.sigma_even_us);
    c.num(wm, m, "sigma_odd_us", x.sigma_odd_us);
    c.num(wm, m, "confidence", x.confidence);
    c.num(wm, m, "span_s", x.span_s);
}

std::string slurp(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

bool read_mono(const std::string& path, std::vector<double>& x, int& fs) {
    SF_INFO info;
    std::memset(&info, 0, sizeof info);
    SNDFILE* f = sf_open(path.c_str(), SFM_READ, &info);
    if (!f) return false;
    std::vector<double> frames(static_cast<std::size_t>(info.frames) * info.channels);
    const sf_count_t got = sf_readf_double(f, frames.data(), info.frames);
    sf_close(f);
    x.resize(static_cast<std::size_t>(got));
    for (sf_count_t i = 0; i < got; ++i)
        x[static_cast<std::size_t>(i)] = frames[static_cast<std::size_t>(i * info.channels)];
    fs = info.samplerate;
    return true;
}

}  // namespace

TEST_CASE("engine version and ABI through advtg.h", "[api]") {
    REQUIRE(advtg_abi_version() == 1);
    REQUIRE(std::strlen(advtg_version()) > 0);
}

TEST_CASE("live session reproduces the v2.0.1 golden files", "[golden]") {
    const jsonl::Value manifest = jsonl::parse(slurp(kRoot + "/tests/golden/manifest.json"));
    const auto& watches = manifest.o.at("watches").o;
    std::vector<std::string> names;
    for (const auto& kv : watches) names.push_back(kv.first);
    const std::string name = GENERATE_COPY(from_range(names));
    const jsonl::Value& spec = watches.at(name);  // [bph, lift angle | null, quartz]
    const std::string audio = kRoot + "/docs/samples/" + name;
    std::vector<double> x;
    int fs = 0;
    if (!read_mono(audio, x, fs)) SKIP("missing " << audio);
    const std::string stem = name.substr(0, name.rfind('.'));
    INFO("recording " << name);

    advtg_session_config cfg;
    advtg_session_config_default(&cfg, fs, spec.a[0].number());
    cfg.lift_angle_deg = spec.a[1].kind == jsonl::Value::Null ? kNaN : spec.a[1].number();
    cfg.quartz = spec.a[2].b ? 1 : 0;
    cfg.live_clock = 0;
    cfg.has_calibration = 1;  // AdcDrift.manual(12.0, 0.5)
    cfg.calibration.ppm = 12.0;
    cfg.calibration.sigma_ppm = 0.0;
    cfg.calibration.systematic_ppm = 0.5;
    cfg.calibration.span_s = 0.0;
    cfg.calibration.residual_us = kNaN;
    cfg.calibration.level = ADVTG_CLOCK_EXTERNAL;
    cfg.calibration.method = 3;  // manual
    void* stream = advtg_stream_create(static_cast<std::size_t>(kCapacitySeconds) * fs, fs, 8192, 0);
    REQUIRE(stream);
    advtg_session* s = advtg_session_create(&cfg, stream);
    REQUIRE(s);

    std::ifstream golden(kRoot + "/tests/golden/" + stem + "/live.jsonl");
    REQUIRE(golden.good());
    const std::size_t n = std::min<std::size_t>(x.size(), static_cast<std::size_t>(kLiveSeconds) * fs);
    std::vector<float> x32(x.begin(), x.begin() + static_cast<std::ptrdiff_t>(n));
    const int every = std::max(1, static_cast<int>(0.25 * fs) / kBlock);
    Checker c;
    int update = 0;
    std::string line;
    advtg_snapshot snap;
    for (std::size_t i = 0, a = 0; a < n; ++i, a += kBlock) {
        const std::size_t b = std::min(n, a + kBlock);
        const std::int64_t stamp = stamp_ns(static_cast<std::int64_t>(b), fs);
        advtg_stream_write_f32(stream, x32.data() + a, b - a, stamp, 1, 0, stamp);
        if ((i + 1) % static_cast<std::size_t>(every) == 0 || b == n) {
            REQUIRE(advtg_session_update(s, &snap) == 0);
            REQUIRE(std::getline(golden, line));
            check_snapshot(c, "update[" + std::to_string(update) + "]", jsonl::parse(line), snap);
            ++update;
        }
    }
    CHECK_FALSE(std::getline(golden, line));  // same number of updates
    advtg_session_destroy(s);
    advtg_stream_destroy(stream);
    std::string first;
    for (std::size_t k = 0; k < c.errors.size() && k < 8; ++k) first += c.errors[k] + "\n";
    INFO(c.errors.size() << " differences\n" << first);
    CHECK(c.errors.empty());
}
