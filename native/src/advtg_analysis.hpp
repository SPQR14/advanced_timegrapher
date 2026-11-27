// Internal C++ declarations shared by gear_train.cpp and features.cpp.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "advtg.h"
#include "advtg_metrics.hpp"

namespace advtg::analysis {

struct GearSeriesData {
    double sample_rate = 48000.0;
    double bph = 21600.0;
    double duration_s = 0.0;
    std::int64_t beats_detected = 0;
    std::int64_t missing_beats = 0;
    std::int64_t outliers = 0;
    double rate_s_per_day = 0.0;
    double lift_angle_deg = 52.0;
    std::vector<std::int64_t> k;
    std::vector<double> t_s;
    std::vector<double> timing_residual_s;
    std::vector<double> energy_rel;
    std::vector<double> outlier_t_s;
    std::vector<double> p1_s;
    std::vector<double> p2_s;
    std::vector<double> p3_s;
    std::vector<double> e1;
    std::vector<double> e2;
    std::vector<double> e3;
    std::vector<double> pre_p1_db;
    std::vector<double> amplitude_deg;
};

struct GearSpectrumData {
    std::vector<double> freqs_hz;
    std::vector<double> ls_power;
    std::vector<double> fft_freqs_hz;
    std::vector<double> fft_amplitude;
};

struct GearReportData {
    GearSeriesData series;
    std::int32_t escape_teeth = 15;
    double escape_period_s = 0.0;
    GearSpectrumData timing;
    GearSpectrumData energy;
    advtg_gear_component components[4]{};
    std::uint32_t warning_mask = 0;
};

// Least-squares (amplitude, phase) of a sinusoid at f plus constant offset.
void sine_fit_cpp(const double* t, const double* y, std::size_t n, double f, double& amp_out,
                  double& phase_out);

// Window & recording features and rule-based diagnosis (analysis/features.py).
std::vector<advtg_feature_vector> window_features_cpp(const GearSeriesData& s, int32_t teeth,
                                                      double window_s);
advtg_feature_vector recording_features_cpp(const GearSeriesData& s, int32_t teeth);

}  // namespace advtg::analysis
