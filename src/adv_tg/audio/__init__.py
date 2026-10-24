"""Audio capture layer for adv_tg (layer 1).

* ``capture``: ``AudioCapture``, a 1-channel float32 ``sounddevice.InputStream`` whose
  callback only writes into a ``SampleStream`` and counts events. ``sounddevice`` is imported
  lazily, so this package imports fine without PortAudio.

Diagnostic CLI: ``python -m adv_tg.tools.capture_check``.
"""
