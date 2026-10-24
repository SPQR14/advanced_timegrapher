"""Audio capture layer for adv_tg (layer 1).

* ``capture``: ``AudioCapture``, a 1-channel float32 ``sounddevice.InputStream`` whose
  callback only writes into a ``SampleStream`` and counts events (in-process; used by
  ``capture_check``, the tests and the ``ADV_TG_CAPTURE_INPROC=1`` debugging mode).
* ``process_capture``: ``ProcessAudioCapture``, same interface, with the stream in a child
  process (``capture_process``) so the GUI's GIL never delays the audio callback. Default in
  the GUI since phase 1d.
* ``gil_stats``: GIL-wait histogram helpers shared by both.

``sounddevice`` is imported lazily, so this package imports fine without PortAudio.

Diagnostic CLIs: ``python -m adv_tg.tools.capture_check`` and ``python -m adv_tg.tools.gil_probe``.
"""
