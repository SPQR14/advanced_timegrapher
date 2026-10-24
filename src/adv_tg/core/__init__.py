"""Measurement core for adv_tg, rebuilt by layers.

* ``models``: data models shared with the GUI (``WatchConfig``, ``BeatEvent``, ...).
* ``sample_stream``: layer 1, ``SampleStream`` lock-free SPSC ring buffer of mono samples
  addressed by absolute sample index (ADC clock domain), plus per-block host timestamps.

Beat detection, metrics and clock-drift estimation (layers 2-4) are not implemented yet.
"""
