"""Measurement core for adv_tg, rebuilt by layers.

* ``models``: data models shared with the GUI (``WatchConfig``, ``BeatEvent``, ...).
* ``sample_stream``: layer 1, ``SampleStream`` lock-free SPSC ring buffer of mono samples
  addressed by absolute sample index (ADC clock domain), plus per-block host timestamps.

* ``beat_types``: physical definitions (P1/P2/P3, t_lift, amplitude formula) and ``Beat``.
* ``beat_detector``: layer 2, ``BeatDetector`` (P1/P2/P3 onsets in absolute sample indices).

* ``metrics``: layer 3, rate / beat error / amplitude from the beats and parity templates
  (``compute_metrics``, ``MetricsTracker``).

* ``adc_clock``: layer 4, real ADC frequency (ppm +- sigma, confidence level) from the block
  log against the host clock (``ClockDriftTracker``) or from an external reference
  (``drift_from_ticks``, ``detect_pps_edges``).
* ``pps``: layer 4, GPS PPS reference: pulse detector (``PpsDetector``) and live calibrator
  (``PpsCalibrator``) of the ADC clock.
"""
