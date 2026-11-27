// Advanced Timegrapher 3.0, C++ application: phase 0 skeleton (docs/PLAN_MIGRACION_CPP.md).
//
// What it already does: opens a Qt 6 window with the engine information, and implements the
// command-line contract of the 2.x application that CI and scripts/baseline.py rely on:
//   --version           print the version and exit
//   --smoke-test        run a synthetic measurement through the public C API (advtg.h) and
//                       exit 0 when the rate is finite, 1 otherwise
//   ADV_TG_BENCH=startup  print "ADV_TG_BENCH {...}" once the window is shown, then quit
//   ADV_TG_BENCH_OUT=<file>  also append that line to <file> (windowed builds)
#include <QApplication>
#include <QElapsedTimer>
#include <QFile>
#include <QLabel>
#include <QMainWindow>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "advtg.h"

#ifndef ADVTG_APP_VERSION
#  define ADVTG_APP_VERSION "3.0.0-dev"
#endif

namespace {

void emit_bench(const QByteArray& json) {
    const QByteArray line = "ADV_TG_BENCH " + json + "\n";
    std::fwrite(line.constData(), 1, static_cast<std::size_t>(line.size()), stdout);
    std::fflush(stdout);
    const QByteArray out = qgetenv("ADV_TG_BENCH_OUT");
    if (!out.isEmpty()) {
        QFile f(QString::fromLocal8Bit(out));
        if (f.open(QIODevice::Append)) f.write(line);
    }
}

// 20 s of a synthetic 21600 BPH watch (three decaying bursts per beat) through the session.
int smoke_test() {
    constexpr double fs = 48000.0, bph = 21600.0;
    const std::size_t n = static_cast<std::size_t>(20.0 * fs);
    // background noise (deterministic LCG, ~-60 dBFS): the detector thresholds in noise sigmas
    std::vector<float> x(n);
    std::uint32_t lcg = 12345u;
    for (auto& v : x) {
        lcg = lcg * 1664525u + 1013904223u;
        v = static_cast<float>((static_cast<double>(lcg >> 8) / 16777216.0 - 0.5) * 2e-3);
    }
    const double period = 3600.0 / bph * fs;
    const double offsets_ms[3] = {0.0, 4.0, 8.5};
    const double amps[3] = {0.25, 0.4, 0.8};
    const double pi = 3.14159265358979323846;
    for (int k = 0; k < static_cast<int>(n / period); ++k) {
        for (int e = 0; e < 3; ++e) {
            const std::size_t a = static_cast<std::size_t>(k * period + offsets_ms[e] * 48.0);
            for (std::size_t i = 0; i < 240 && a + i < n; ++i)
                x[a + i] += static_cast<float>(amps[e] * std::sin(2.0 * pi * 4000.0 * i / fs) *
                                               std::exp(-static_cast<double>(i) / 30.0));
        }
    }
    advtg_session_config cfg;
    advtg_session_config_default(&cfg, fs, bph);
    cfg.lift_angle_deg = 52.0;
    advtg_session* s = advtg_session_create(&cfg, nullptr);
    if (!s) {
        std::fprintf(stderr, "smoke test: %s\n", advtg_session_last_error(nullptr));
        return 1;
    }
    void* stream = advtg_session_stream(s);
    advtg_snapshot snap;
    std::memset(&snap, 0, sizeof snap);
    for (std::size_t a = 0; a < n; a += 960) {
        const std::size_t m = std::min<std::size_t>(960, n - a);
        advtg_stream_write_f32(stream, x.data() + a, m, 0, 0, 0, 0);
        if ((a / 960) % 12 == 11 || a + m >= n) advtg_session_update(s, &snap);
    }
    advtg_session_destroy(s);
    const bool ok = std::isfinite(snap.metrics.rate_s_per_day) && snap.beats_total > 100;
    std::printf("smoke test: %s beats=%lld rate=%+.3f s/d engine=%s [%s]\n", ok ? "OK" : "FAILED",
                static_cast<long long>(snap.beats_total), snap.metrics.rate_s_per_day,
                advtg_version(), advtg_simd_name());
    return ok ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    QElapsedTimer since_main;
    since_main.start();
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--version") || !std::strcmp(argv[i], "-v")) {
            std::printf("Advanced Timegrapher %s (C++, engine %s)\n", ADVTG_APP_VERSION,
                        advtg_version());
            return 0;
        }
        if (!std::strcmp(argv[i], "--smoke-test")) return smoke_test();
    }

    QApplication app(argc, argv);
    QApplication::setApplicationName("Advanced Timegrapher");
    QApplication::setOrganizationName("AdvancedTimegrapher");

    QMainWindow window;
    window.setWindowTitle(QStringLiteral("Advanced Timegrapher %1 (C++)").arg(ADVTG_APP_VERSION));
    auto* central = new QWidget(&window);
    auto* layout = new QVBoxLayout(central);
    layout->addWidget(new QLabel(
        QStringLiteral("Esqueleto de la aplicación C++ (fase 0).\nMotor %1 — SIMD %2 — ABI %3")
            .arg(advtg_version())
            .arg(advtg_simd_name())
            .arg(advtg_abi_version()),
        central));
    window.setCentralWidget(central);
    window.resize(900, 600);
    window.show();

    if (qgetenv("ADV_TG_BENCH") == "startup") {
        QTimer::singleShot(0, &app, [&] {
            emit_bench(QByteArrayLiteral("{\"mode\": \"startup\", \"window_shown_ms\": ") +
                       QByteArray::number(since_main.nsecsElapsed() / 1e6, 'f', 3) + "}");
            QApplication::quit();
        });
    }
    return QApplication::exec();
}
