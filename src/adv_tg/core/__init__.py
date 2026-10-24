"""Measurement core for adv_tg, rebuilt by layers.

* ``models``: data models shared with the GUI (``WatchConfig``, ``BeatEvent``, ...).
* ``sample_stream``: layer 1, ``SampleStream`` lock-free SPSC ring buffer of mono samples
  addressed by absolute sample index (ADC clock domain), plus per-block host timestamps.

* ``beat_types``: physical definitions (P1/P2/P3, t_lift, amplitude formula) and ``Beat``.
* ``beat_detector``: layer 2, ``BeatDetector`` (P1/P2/P3 onsets in absolute sample indices).

Metrics and clock-drift estimation (layers 3-4) are not implemented yet.
"""
