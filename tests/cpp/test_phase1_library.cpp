// Unit tests for Phase 1 C++ library modules through the public C API (advtg.h).
#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "advtg.h"
#include "miniz.h"
#include "nlohmann/json.hpp"

#ifndef ADVTG_SOURCE_DIR
#  error "ADVTG_SOURCE_DIR must point to the repository"
#endif

namespace fs = std::filesystem;

namespace {

const fs::path kRoot = ADVTG_SOURCE_DIR;

struct TempDir {
    fs::path path;
    explicit TempDir(const std::string& tag) {
        path = fs::temp_directory_path() / ("advtg_test_" + tag);
        std::error_code ec;
        fs::remove_all(path, ec);
        fs::create_directories(path, ec);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

void collect_tag_cb(const char* k, const char* v, void* user) {
    auto* m = static_cast<std::map<std::string, std::string>*>(user);
    if (k && v) (*m)[k] = v;
}

std::string slurp_bytes(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}

}  // namespace

TEST_CASE("audio file I/O WAV and FLAC round-trip with tags", "[phase1][audio]") {
    TempDir tmp("audio");
    const fs::path wav = tmp.path / "test.wav";
    const fs::path flac = tmp.path / "test.flac";

    constexpr int32_t fs_hz = 48000;
    constexpr int64_t frames = 4800;
    std::vector<double> stereo(static_cast<std::size_t>(frames * 2));
    for (int64_t i = 0; i < frames; ++i) {
        const double t = static_cast<double>(i) / fs_hz;
        stereo[static_cast<std::size_t>(2 * i)] = 0.05 * std::sin(2.0 * M_PI * 440.0 * t);
        stereo[static_cast<std::size_t>(2 * i + 1)] = 0.6 * std::sin(2.0 * M_PI * 880.0 * t);
    }

    const advtg_audio_tag tags[] = {{"ADV_TG_DEVICE", "CM18 USB"}, {"ADV_TG_NOTE", "toma 1"}};
    int64_t clipped = 0;
    REQUIRE(advtg_audio_write_f64(wav.string().c_str(), stereo.data(), frames, 2, fs_hz,
                                  "FLOAT", "hola \xC3\xB1", tags, 2, &clipped) == ADVTG_OK);
    REQUIRE(clipped == 0);

    advtg_audio_file_info info{};
    REQUIRE(advtg_audio_info(wav.string().c_str(), &info) == ADVTG_OK);
    CHECK(info.frames == frames);
    CHECK(info.sample_rate == fs_hz);
    CHECK(info.channels == 2);
    CHECK(std::string(info.format) == "WAV");

    std::vector<double> mono(static_cast<std::size_t>(frames));
    advtg_audio_file_info rinfo{};
    REQUIRE(advtg_audio_read_mono(wav.string().c_str(), -1, 0, mono.data(), mono.size(),
                                  &rinfo) == ADVTG_OK);
    double max_err = 0.0;
    for (int64_t i = 0; i < frames; ++i) {
        max_err = std::max(max_err, std::fabs(mono[static_cast<std::size_t>(i)] -
                                              stereo[static_cast<std::size_t>(2 * i + 1)]));
    }
    CHECK(max_err < 1e-6);

    std::map<std::string, std::string> got_tags;
    REQUIRE(advtg_audio_read_tags(wav.string().c_str(), collect_tag_cb, &got_tags) == ADVTG_OK);
    CHECK(got_tags["ADV_TG_DEVICE"] == "CM18 USB");
    CHECK(got_tags["ADV_TG_NOTE"] == "toma 1");

    // FLAC write + streaming read + atomic tag rewrite preserves PCM samples bit-for-bit
    std::vector<double> mono_src(static_cast<std::size_t>(frames));
    for (int64_t i = 0; i < frames; ++i) mono_src[static_cast<std::size_t>(i)] = stereo[2 * i + 1];
    advtg_audio_writer* w =
        advtg_audio_writer_open(flac.string().c_str(), fs_hz, 1, "PCM_24", nullptr, tags, 2);
    REQUIRE(w != nullptr);
    REQUIRE(advtg_audio_writer_write(w, mono_src.data(), frames / 2, 1, &clipped) == ADVTG_OK);
    REQUIRE(advtg_audio_writer_write(w, mono_src.data() + frames / 2, frames / 2, 1, &clipped) ==
            ADVTG_OK);
    int64_t wrote_frames = 0;
    REQUIRE(advtg_audio_writer_close(w, 0, &wrote_frames, &clipped) == ADVTG_OK);
    CHECK(wrote_frames == frames);

    std::vector<double> before(static_cast<std::size_t>(frames));
    REQUIRE(advtg_audio_read_f64(flac.string().c_str(), 0, before.data(), before.size(), &rinfo,
                                 nullptr, 0) == ADVTG_OK);
    const advtg_audio_tag upd[] = {{"ADV_TG_NOTE", nullptr}, {"ADV_TG_EXTRA", "ok"}};
    REQUIRE(advtg_audio_update_tags(flac.string().c_str(), upd, 2) == ADVTG_OK);

    got_tags.clear();
    REQUIRE(advtg_audio_read_tags(flac.string().c_str(), collect_tag_cb, &got_tags) == ADVTG_OK);
    CHECK(got_tags.count("ADV_TG_NOTE") == 0);
    CHECK(got_tags["ADV_TG_EXTRA"] == "ok");

    std::vector<double> after(static_cast<std::size_t>(frames));
    REQUIRE(advtg_audio_read_f64(flac.string().c_str(), 0, after.data(), after.size(), &rinfo,
                                 nullptr, 0) == ADVTG_OK);
    CHECK(before == after);
}

TEST_CASE("calibration store persistence, aging, history cap, and tags", "[phase1][cal]") {
    TempDir tmp("cal");
    const std::string store = (tmp.path / "adc_calibration.json").string();

    advtg_calibration c1{};
    std::snprintf(c1.device, sizeof c1.device, "CM18 USB");
    c1.sample_rate = 48000;
    c1.drift.ppm = 12.5;
    c1.drift.sigma_ppm = 0.05;
    c1.drift.systematic_ppm = 0.01;
    c1.drift.level = ADVTG_CLOCK_EXTERNAL;
    c1.drift.method = 4;  // pps
    c1.measured_at_epoch_s = 1700000000LL;
    std::snprintf(c1.note, sizeof c1.note, "pps antiguo");

    REQUIRE(advtg_cal_store_add(store.c_str(), &c1) == ADVTG_OK);
    for (int i = 0; i < 55; ++i) {
        advtg_calibration ci = c1;
        ci.measured_at_epoch_s = 1700000100LL + i * 10;
        ci.drift.ppm = 12.0 + 0.01 * i;
        REQUIRE(advtg_cal_store_add(store.c_str(), &ci) == ADVTG_OK);
    }

    std::vector<advtg_calibration> recs(64);
    const int32_t n = advtg_cal_store_records(store.c_str(), "cm18 usb", 48000, recs.data(), 64);
    REQUIRE(n == 50);

    advtg_calibration best{};
    REQUIRE(advtg_cal_store_best(store.c_str(), "CM18 USB", 48000, 1700001000LL, 1.0, &best) == 1);
    CHECK(best.drift.method == 4);

    advtg_adc_drift aged{};
    advtg_cal_effective(&c1, 1700000000LL + 365LL * 86400LL, 1.0, &aged);
    CHECK(aged.systematic_ppm > 0.99);

    std::map<std::string, std::string> rec_tags;
    int has_cal = 0;
    advtg_calibration used{};
    REQUIRE(advtg_cal_recording_tags(store.c_str(), "CM18 USB", 48000, 1700001000LL,
                                     collect_tag_cb, &rec_tags, &has_cal, &used) == ADVTG_OK);
    CHECK(has_cal == 1);
    CHECK(rec_tags["ADV_TG_ADC_DRIFT_METHOD"] == "pps");

    std::vector<advtg_audio_tag> tag_vec;
    for (const auto& kv : rec_tags) tag_vec.push_back({kv.first.c_str(), kv.second.c_str()});
    advtg_adc_drift parsed{};
    char note_buf[128]{};
    REQUIRE(advtg_drift_from_tags(tag_vec.data(), tag_vec.size(), &parsed, note_buf,
                                  sizeof note_buf) == 1);
    CHECK(parsed.method == 4);
    CHECK(std::fabs(parsed.ppm - best.drift.ppm) < 1e-6);

    REQUIRE(advtg_cal_store_forget(store.c_str(), "CM18 USB", 48000) == 50);

    // Corrupt store never silently overwritten
    const fs::path corrupt = tmp.path / "bad.json";
    {
        std::ofstream f(corrupt);
        f << "{not valid json";
    }
    CHECK(advtg_cal_store_records(corrupt.string().c_str(), nullptr, 0, recs.data(), 64) ==
          ADVTG_ERR_CORRUPT_STORE);
}

TEST_CASE("gear train, features, diagnosis, and synth_watch C API", "[phase1][gear]") {
    advtg_synth_config sc{};
    advtg_synth_config_default(&sc);
    sc.bph = 21600.0;
    sc.seconds = 45.0;
    sc.snr_db = 40.0;
    sc.phase_mod_period_s[0] = advtg_escape_wheel_period_s(21600.0, 15);
    sc.phase_mod_amp_us[0] = 150.0;

    std::vector<double> sig(static_cast<std::size_t>(sc.seconds * sc.sample_rate));
    const int64_t n = advtg_synth_watch(&sc, sig.data(), sig.size());
    REQUIRE(n == static_cast<int64_t>(sig.size()));

    int32_t err = 0;
    int64_t beats_found = 0;
    advtg_gear_analysis* h = advtg_gear_extract(sig.data(), sig.size(), sc.sample_rate, 21600.0,
                                                52.0, 15, &err, &beats_found);
    REQUIRE(h != nullptr);
    CHECK(beats_found >= 240);

    advtg_gear_summary sum{};
    REQUIRE(advtg_gear_get_summary(h, &sum) == ADVTG_OK);
    CHECK(sum.n_series >= 240);
    CHECK(std::fabs(sum.escape_period_s - 5.0) < 1e-9);

    advtg_gear_component comps[4]{};
    REQUIRE(advtg_gear_get_components(h, comps) == ADVTG_OK);
    CHECK(comps[0].name == ADVTG_GEAR_COMP_ESCAPE);
    CHECK(comps[0].resolvable == 1);
    CHECK(comps[0].significant == 1);

    int64_t ls_len = 0, fft_len = 0;
    advtg_gear_spectra_len(h, &ls_len, &fft_len);
    REQUIRE(ls_len > 10);
    REQUIRE(fft_len > 10);

    advtg_feature_vector whole{};
    REQUIRE(advtg_gear_recording_features(h, 15, &whole) == ADVTG_OK);
    CHECK(whole.whole == 1);
    CHECK(whole.escape_amp_us > 80.0);

    const int32_t nw = advtg_gear_window_features(h, 15, 20.0, nullptr, 0);
    REQUIRE(nw >= 1);
    std::vector<advtg_feature_vector> wins(static_cast<std::size_t>(nw));
    REQUIRE(advtg_gear_window_features(h, 15, 20.0, wins.data(), nw) == nw);

    const advtg_positional_record pos_recs[] = {
        {"DU", 12.0, 0.2, 290.0},
        {"CU", 2.0, 0.6, 260.0},
    };
    advtg_positional_deltas pd{};
    REQUIRE(advtg_compute_positional_deltas(pos_recs, 2, &pd) == 1);
    CHECK(pd.valid == 1);
    CHECK(std::fabs(pd.delta_rate_hv_spd - 10.0) < 1e-9);

    advtg_diagnostic_finding findings[8]{};
    const int32_t nf = advtg_diagnose(&whole, wins.data(), wins.size(), &pd, comps, 4, findings, 8);
    REQUIRE(nf >= 1);
    bool saw_escape = false;
    for (int32_t i = 0; i < nf; ++i) {
        if (findings[i].code == ADVTG_FINDING_ESCAPE_WHEEL) saw_escape = true;
    }
    CHECK(saw_escape);
    advtg_gear_destroy(h);
}

TEST_CASE("security, watch_db, csv, paths, log, report zip, and bilingual warnings",
          "[phase1][utils]") {
    TempDir tmp("utils");

    // Security
    char buf[512];
    REQUIRE(advtg_sanitize_csv_cell("=CMD()", buf, sizeof buf) > 0);
    CHECK(std::string(buf) == "'=CMD()");
    REQUIRE(advtg_sanitize_csv_cell("-1.25", buf, sizeof buf) > 0);
    CHECK(std::string(buf) == "-1.25");
    REQUIRE(advtg_safe_filename_base("../../bad:name", "fallback", buf, sizeof buf) > 0);
    CHECK(std::string(buf) == "badname");
    REQUIRE(advtg_anonymize_text("/home/alice/logs/a.log for alice", "alice", "/home/alice",
                                 buf, sizeof buf) > 0);
    CHECK(std::string(buf).find("alice") == std::string::npos);

    // Watch DB
    std::vector<advtg_watch_caliber> db(64);
    const int32_t ndb =
        advtg_load_watch_db((kRoot / "src/adv_tg/utils/db/watch_db.csv").string().c_str(),
                            db.data(), 64);
    REQUIRE(ndb >= 5);
    CHECK(std::string(db[0].name) == "Custom");

    // CSV exports
    const advtg_csv_result_row rrow{"=bad", "NH35", 21600, 53.0, 48000, "DU",
                                    "sample", "10.00", "+1.20", "0.20", "280.0", "0.950", "30.0"};
    const fs::path rcsv = tmp.path / "res.csv";
    REQUIRE(advtg_write_results_csv(rcsv.string().c_str(), &rrow, 1) == ADVTG_OK);
    CHECK(slurp_bytes(rcsv).find("'=bad") != std::string::npos);

    const double xs[2] = {0.0, 1.0};
    const double ys[2] = {0.5, -0.5};
    const fs::path tcsv = tmp.path / "traces.csv";
    REQUIRE(advtg_write_traces_csv(tcsv.string().c_str(), xs, ys, 2, nullptr, nullptr, 0,
                                   nullptr, nullptr, 0, nullptr, nullptr, 0) == ADVTG_OK);
    CHECK(slurp_bytes(tcsv).find("paper_tape_even") != std::string::npos);

    // Paths & logger
    REQUIRE(advtg_config_dir(buf, sizeof buf) > 0);
    REQUIRE(advtg_default_log_dir(buf, sizeof buf) > 0);
    const fs::path ldir = tmp.path / "logs";
    REQUIRE(advtg_log_init(ldir.string().c_str(), "testlog") == ADVTG_OK);
    REQUIRE(advtg_log_write(0, "test", "hello /home/alice/x") == ADVTG_OK);
    REQUIRE(advtg_log_file_path(buf, sizeof buf) > 0);
    CHECK(fs::exists(buf));

    // Report zip + miniz reader check
    const fs::path calp = tmp.path / "adc_calibration.json";
    {
        std::ofstream f(calp);
        f << "{\"version\":1,\"calibrations\":[]}\n";
    }
    const fs::path recp = tmp.path / "rec.flac";
    double pulse[480]{};
    pulse[100] = 0.5;
    REQUIRE(advtg_audio_write_f64(recp.string().c_str(), pulse, 480, 1, 48000, "PCM_24",
                                  nullptr, nullptr, 0, nullptr) == ADVTG_OK);

    REQUIRE(advtg_report_default_name("2.0.1", 1700000000LL, buf, sizeof buf) > 0);
    CHECK(std::string(buf).find("adv_tg_report_2.0.1_") == 0);

    const advtg_audio_tag settings[] = {{"bph", "21600"}, {"quartz", "False"}};
    const fs::path zpath = tmp.path / "report.zip";
    char notes[512]{};
    REQUIRE(advtg_report_build(zpath.string().c_str(), "2.0.1", "Linux", "Engine", "Bug desc",
                               settings, 2, recp.string().c_str(), ldir.string().c_str(),
                               calp.string().c_str(), "alice", "/home/alice", 1024 * 1024,
                               1024 * 1024, notes, sizeof notes) == ADVTG_OK);

    const std::string zbytes = slurp_bytes(zpath);
    mz_zip_archive za{};
    REQUIRE(mz_zip_reader_init_mem(&za, zbytes.data(), zbytes.size(), 0) == MZ_TRUE);
    CHECK(mz_zip_reader_locate_file(&za, "report.txt", nullptr, 0) >= 0);
    CHECK(mz_zip_reader_locate_file(&za, "logs/testlog.log", nullptr, 0) >= 0);
    CHECK(mz_zip_reader_locate_file(&za, "adc_calibration.json", nullptr, 0) >= 0);
    CHECK(mz_zip_reader_locate_file(&za, "recording.flac", nullptr, 0) >= 0);
    std::size_t rlen = 0;
    void* rtxt = mz_zip_reader_extract_file_to_heap(&za, "report.txt", &rlen, 0);
    REQUIRE(rtxt != nullptr);
    const std::string rstr(static_cast<const char*>(rtxt), rlen);
    mz_free(rtxt);
    mz_zip_reader_end(&za);
    CHECK(rstr.find("bph: 21600") != std::string::npos);

    // Bilingual warnings EN vs ES
    for (int code = 1; code <= 13; ++code) {
        advtg_warning w{};
        w.code = code;
        w.param_int = 10;
        w.args[0] = 4.5;
        w.args[1] = 21600.0;
        w.args[2] = 0.25;
        w.args[3] = 4.2;
        char es[512]{}, en[512]{};
        REQUIRE(advtg_format_warning(&w, "es", es, sizeof es) > 0);
        REQUIRE(advtg_format_warning(&w, "en", en, sizeof en) > 0);
        CHECK(std::string(es) != std::string(en));
    }

    // Mock primitives
    CHECK(advtg_mock_beat_interval_s(21600, 0.0) == 3600.0 / 21600.0);
    CHECK(advtg_mock_beat_delta_t_s(270.0, 52.0, 21600) > 0.0);
}
