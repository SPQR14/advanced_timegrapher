# Watch Escape Analyzer EVO (`watch_escape_analyzerEVO`)

**Watch Escape Analyzer EVO** is a high-precision horological acoustic telemetry and escapement diagnostic suite engineered with a strictly decoupled architecture: a high-performance DSP Core (Python/C++ ready) paired with a real-time reactive UI built on **PyQt6** and **PyQtGraph**.

---

## Key Features

- **Decoupled Architecture**: Clean separation between the real-time audio acquisition (`wea.audio`), the mathematical signal processing and synthetic modeling (`wea.core`), machine learning inference (`wea.ml`), and the graphical interface (`wea.ui`).
- **Sub-Millisecond Swiss Lever Escapement Detection**: Dual-scale envelope extraction (global RMS beat detection + sub-millisecond Hilbert/fast-RMS micro-envelope) capable of isolating the three distinct acoustic impulses of a Swiss lever escapement:
  - **P1 (Unlock)**: Pallet stone unlocking from the escape wheel tooth.
  - **P2 (Impulse)**: Escape wheel tooth imparting energy to the pallet stone impulse plane.
  - **P3 (Drop / Banking)**: Escape wheel tooth locking on the opposite pallet stone and fork banking impact.
- **High-Precision Horological Telemetry**:
  - **Rate (`s/d`)**: Sub-tenth second per day rate deviation measurement across standard and high-beat frequencies (`18,000` to `36,000 A/h`).
  - **Beat Error (`ms`)**: Separation of alternating tick/tock intervals to quantify escapement symmetry with `< 0.1 ms` accuracy.
  - **Balance Amplitude (`deg`)**: Exact derivation of balance wheel oscillation amplitude using the lift angle ($\theta_{\text{lift}}$) and the temporal delta $\Delta t = t_{P3} - t_{P1}$ via the sinusoidal balance motion equation:
    $$\Phi = \frac{\theta_{\text{lift}}}{2 \sin(\pi f_0 \Delta t)}$$
- **Case Harmonics Filtering**: Cascaded Second-Order Sections (SOS) zero-phase Butterworth bandpass filtering (`4 kHz – 18 kHz`) to suppress low-frequency mechanical/ambient rumble and isolate escapement impulse transients.
- **Ground-Truth Synthetic Escape Generator**: Physics-based acoustic synthesizer (`SyntheticEscapeGenerator`) modeling damped multi-harmonic escapement impacts with configurable Rate, Beat Error, Amplitude, Lift Angle, and AWGN Signal-to-Noise Ratio (`snr_db`).
- **ML-Ready Pipeline**: Structured telemetry and waveform extraction ready for anomaly detection and escapement fault classification.
- **Bilingual Support (EN / ES)**: Built-in lightweight internationalization (`wea.i18n`) covering technical horological terminology in English and Spanish.

---

## Project Structure

```text
watch_escape_analyzerEVO/
├── pyproject.toml
├── README.md
├── src/
│   └── wea/
│       ├── __init__.py
│       ├── audio/
│       │   ├── __init__.py
│       │   └── ring_buffer.py      # Thread-safe NumPy lock-protected ring buffer
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py           # WatchConfig, BeatEvent, TelemetryResult
│       │   ├── synthetic.py        # Ground-Truth SyntheticEscapeGenerator
│       │   └── dsp_pipeline.py     # EscapeDSPPipeline (SOS filter, P1-P3, telemetry)
│       ├── i18n/
│       │   ├── __init__.py
│       │   └── translator.py       # Bilingual EN/ES horological dictionary
│       ├── ml/
│       │   └── __init__.py         # ML inference & feature extraction module
│       └── ui/
│           └── __init__.py         # PyQt6 + PyQtGraph real-time visualization
└── tests/
    └── test_dsp_accuracy.py        # Ground-truth validation suite
```

---

## Installation

Requires **Python >= 3.10**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**.
