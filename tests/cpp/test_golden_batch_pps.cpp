// Batch BeatDetector/Metrics and GPS PPS golden parity tests through the public C API (advtg.h).
#include <catch2/catch_test_macros.hpp>
#include <catch2/generators/catch_generators.hpp>
#include <catch2/generators/catch_generators_range.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include "advtg.h"
#include "nlohmann/json.hpp"

#ifndef ADVTG_SOURCE_DIR
#  error "ADVTG_SOURCE_DIR must point to the repository"
#endif

namespace {

using json = nlohmann::json;
const std::string kRoot = ADVTG_SOURCE_DIR;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
constexpr std::size_t kBatchChunk = 4096;

std::string slurp(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

std::vector<std::vector<std::string>> read_csv(const std::string& path) {
    std::vector<std::vector<std::string>> rows;
    std::ifstream f(path);
    std::string line;
    bool first = true;
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (first) {
            first = false;
            continue;  // skip header
        }
        if (line.empty()) continue;
        std::vector<std::string> cols;
        std::size_t a = 0;
        while (a <= line.size()) {
            const std::size_t b = line.find(',', a);
            if (b == std::string::npos) {
                cols.push_back(line.substr(a));
                break;
            }
            cols.push_back(line.substr(a, b - a));
            a = b + 1;
        }
        rows.push_back(std::move(cols));
    }
    return rows;
}

double parse_cell(const std::string& s) {
    if (s.empty()) return kNaN;
    return std::strtod(s.c_str(), nullptr);
}

bool close_num(const std::string& field, double got, double want, bool is_int = false) {
    if (std::isnan(want)) return std::isnan(got);
    if (std::isnan(got)) return false;
    if (field == "p1" || field == "p2" || field == "p3" || field == "t_fine" ||
        field == "t_p1" || field == "t_p1_tpl" || field == "index") {
        return std::fabs(got - want) <= 1e-4;
    }
    if (field == "rate_s_per_day" || field == "beat_error_ms" || field == "beat_error_p3_ms" ||
        field == "amplitude_deg" || field == "amplitude_tic_deg" || field == "amplitude_tac_deg") {
        return std::fabs(got - want) <= 1e-6;
    }
    if (is_int) return got == want;
    return std::fabs(got - want) <= 1e-9 + 1e-7 * std::fabs(want);
}

bool close_json(const std::string& field, double got, const json& want) {
    if (want.is_null()) return std::isnan(got);
    return close_num(field, got, want.get<double>(), want.is_number_integer());
}

const char* be_point(int i) {
    static const char* names[] = {"\xE2\x80\x94", "p1", "p1_beat", "p1_fine", "t_fine"};
    return (i >= 0 && i < 5) ? names[i] : names[0];
}

const char* amp_parity(int i) {
    static const char* names[] = {"both", "tic", "tac"};
    return (i >= 0 && i < 3) ? names[i] : names[0];
}

bool load_mono_native(const std::string& path, std::vector<double>& out, int& fs) {
    advtg_audio_file_info info{};
    if (advtg_audio_info(path.c_str(), &info) != ADVTG_OK) return false;
    fs = info.sample_rate;
    out.resize(static_cast<std::size_t>(info.frames));
    advtg_audio_file_info rinfo{};
    return advtg_audio_read_mono(path.c_str(), -1, 0, out.data(), out.size(), &rinfo) == ADVTG_OK;
}

}  // namespace

TEST_CASE("batch detector and metrics reproduce the v2.0.1 golden files", "[golden][batch]") {
    const json manifest = json::parse(slurp(kRoot + "/tests/golden/manifest.json"));
    const auto& watches = manifest.at("watches");
    std::vector<std::string> names;
    for (auto it = watches.begin(); it != watches.end(); ++it) names.push_back(it.key());
    const std::string name = GENERATE_COPY(from_range(names));
    const json& spec = watches.at(name);
    const std::string audio = kRoot + "/docs/samples/" + name;

    std::vector<double> x;
    int fs = 0;
    if (!load_mono_native(audio, x, fs)) SKIP("missing or unreadable " << audio);
    const std::string stem = name.substr(0, name.rfind('.'));
    INFO("recording " << name);

    const double bph = spec.at(0).get<double>();
    const bool has_la = !spec.at(1).is_null();
    const double la = has_la ? spec.at(1).get<double>() : 52.0;
    const int quartz = spec.at(2).get<bool>() ? 1 : 0;

    advtg_detector_config dcfg{};
    advtg_detector_config_default(&dcfg, fs, bph);
    dcfg.lift_angle_deg = la;
    dcfg.quartz = quartz;
    advtg_detector* det = advtg_detector_create(&dcfg);
    REQUIRE(det != nullptr);

    std::vector<advtg_beat> beats;
    for (std::size_t a = 0; a < x.size(); a += kBatchChunk) {
        const std::size_t n = std::min(kBatchChunk, x.size() - a);
        const int64_t produced = advtg_detector_process(
            det, x.data() + a, n, static_cast<int64_t>(a), nullptr, 0);
        REQUIRE(produced >= 0);
        if (produced > 0) {
            const std::size_t old = beats.size();
            beats.resize(old + static_cast<std::size_t>(produced));
            REQUIRE(advtg_detector_last_beats(det, beats.data() + old, produced) == produced);
        }
    }
    const int64_t flushed = advtg_detector_flush(det, nullptr, 0);
    REQUIRE(flushed >= 0);
    if (flushed > 0) {
        const std::size_t old = beats.size();
        beats.resize(old + static_cast<std::size_t>(flushed));
        REQUIRE(advtg_detector_last_beats(det, beats.data() + old, flushed) == flushed);
    }

    const auto rows = read_csv(kRoot + "/tests/golden/" + stem + "/beats.csv");
    REQUIRE(beats.size() == rows.size());
    std::vector<std::string> errors;
    for (std::size_t i = 0; i < beats.size(); ++i) {
        const auto& b = beats[i];
        const auto& r = rows[i];
        REQUIRE(r.size() == 12);
        auto chk = [&](const char* f, double g, const std::string& cell, bool is_int = false) {
            if (!close_num(f, g, parse_cell(cell), is_int)) {
                errors.push_back("beat[" + std::to_string(i) + "]." + f + " got=" +
                                 std::to_string(g) + " want=" + cell);
            }
        };
        chk("k", static_cast<double>(b.k), r[0], true);
        chk("flags", static_cast<double>(b.flags), r[1], true);
        chk("n_subevents", static_cast<double>(b.n_subevents), r[2], true);
        chk("p1", b.p1, r[3]);
        chk("p2", b.p2, r[4]);
        chk("p3", b.p3, r[5]);
        chk("t_fine", b.t_fine, r[6]);
        chk("t_p1", b.t_p1, r[7]);
        chk("t_p1_tpl", b.t_p1_tpl, r[8]);
        chk("snr_db", b.snr_db, r[9]);
        chk("peak_dbfs", b.peak_dbfs, r[10]);
        chk("confidence", b.confidence, r[11]);
        if (errors.size() >= 10) break;
    }

    advtg_metrics_config mcfg{};
    advtg_metrics_config_default(&mcfg, fs, bph);
    mcfg.has_lift_angle = has_la ? 1 : 0;
    mcfg.lift_angle_deg = la;
    mcfg.quartz = quartz;
    advtg_metrics m{};
    REQUIRE(advtg_metrics_compute(det, beats.data(), beats.size(), &mcfg, &m) == ADVTG_OK);

    const json gm = json::parse(slurp(kRoot + "/tests/golden/" + stem + "/metrics.json"));
    auto mchk = [&](const char* f, double g) {
        if (!close_json(f, g, gm.at(f))) {
            errors.push_back(std::string("metrics.") + f + " got=" + std::to_string(g) +
                             " want=" + gm.at(f).dump());
        }
    };
    mchk("n_beats", static_cast<double>(m.n_beats));
    mchk("n_used_even", static_cast<double>(m.n_used_even));
    mchk("n_used_odd", static_cast<double>(m.n_used_odd));
    mchk("n_excluded", static_cast<double>(m.n_excluded));
    mchk("excluded_fraction", m.excluded_fraction);
    mchk("rate_s_per_day", m.rate_s_per_day);
    mchk("rate_sigma_s_per_day", m.rate_sigma_s_per_day);
    mchk("rate_uncertainty_s_per_day", m.rate_uncertainty_s_per_day);
    mchk("beat_error_ms", m.beat_error_ms);
    mchk("beat_error_uncertainty_ms", m.beat_error_uncertainty_ms);
    mchk("beat_error_p3_ms", m.beat_error_p3_ms);
    CHECK(gm.at("beat_error_point").get<std::string>() == be_point(m.beat_error_point));
    mchk("amplitude_deg", m.amplitude_deg);
    mchk("amplitude_tic_deg", m.amplitude_tic_deg);
    mchk("amplitude_tac_deg", m.amplitude_tac_deg);
    CHECK(gm.at("amplitude_parity").get<std::string>() == amp_parity(m.amplitude_parity));
    mchk("sigma_even_us", m.sigma_even_us);
    mchk("sigma_odd_us", m.sigma_odd_us);
    mchk("confidence", m.confidence);
    mchk("span_s", m.span_s);

    advtg_detector_destroy(det);
    INFO("first errors: " << (errors.empty() ? std::string("none") : errors[0]));
    CHECK(errors.empty());
}

TEST_CASE("PPS detector and calibrator reproduce the v2.0.1 golden files", "[golden][pps]") {
    const json manifest = json::parse(slurp(kRoot + "/tests/golden/manifest.json"));
    const auto& pps_list = manifest.at("pps");
    std::vector<std::string> names;
    for (const auto& el : pps_list) names.push_back(el.get<std::string>());
    const std::string name = GENERATE_COPY(from_range(names));
    const std::string audio = kRoot + "/docs/samples/" + name;

    std::vector<double> x;
    int fs = 0;
    if (!load_mono_native(audio, x, fs)) SKIP("missing or unreadable " << audio);
    const std::string stem = name.substr(0, name.rfind('.'));
    INFO("pps " << name);

    advtg_pps_detector* pdet = advtg_pps_detector_create(fs, 8.0);
    REQUIRE(pdet != nullptr);
    const std::size_t step = static_cast<std::size_t>(fs);
    std::vector<advtg_pps_pulse> pulses;
    for (std::size_t a = 0; a < x.size(); a += step) {
        const std::size_t n = std::min(step, x.size() - a);
        const int64_t got = advtg_pps_detector_process(
            pdet, x.data() + a, n, static_cast<int64_t>(a), nullptr, 0);
        REQUIRE(got >= 0);
        if (got > 0) {
            const std::size_t old = pulses.size();
            pulses.resize(old + static_cast<std::size_t>(got));
            REQUIRE(advtg_pps_detector_last_pulses(pdet, pulses.data() + old, got) == got);
        }
    }
    advtg_pps_detector_destroy(pdet);

    const auto rows = read_csv(kRoot + "/tests/golden/" + stem + "/pulses.csv");
    REQUIRE(pulses.size() == rows.size());
    for (std::size_t i = 0; i < pulses.size(); ++i) {
        REQUIRE(rows[i].size() == 3);
        CHECK(close_num("index", pulses[i].index, parse_cell(rows[i][0])));
        CHECK(close_num("snr_db", pulses[i].snr_db, parse_cell(rows[i][1])));
        CHECK(close_num("peak", pulses[i].peak, parse_cell(rows[i][2])));
    }

    advtg_pps_estimate est{};
    REQUIRE(advtg_pps_calibrate_samples(x.data(), x.size(), fs, 1.0, &est) == ADVTG_OK);
    CHECK(est.valid == 1);
    CHECK(est.ready == 1);
    CHECK(est.drift.level == ADVTG_CLOCK_EXTERNAL);
    CHECK(est.drift.method == 4);
    CHECK(std::isfinite(est.drift.ppm));
    CHECK(est.drift.sigma_ppm < 0.2);
}
