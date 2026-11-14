"""PyInstaller runtime hook (Linux): make ``sounddevice`` load the bundled PortAudio.

``sounddevice`` locates PortAudio with ``ctypes.util.find_library("portaudio")``, which on
Linux only looks at the system (``ldconfig``, ``gcc``, ``ld``) and never at the bundle: the
packaged application had ``libportaudio.so.2`` inside and still started without audio input
on any machine without the distribution's PortAudio package. The macOS and Windows wheels of
``sounddevice`` carry their own PortAudio and do not go through this path.
"""

import ctypes.util
import os
import sys

if sys.platform.startswith("linux"):
    _bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "libportaudio.so.2")
    if os.path.isfile(_bundled):
        _find_library = ctypes.util.find_library

        def _find_library_bundled(name):
            if name == "portaudio":
                return _bundled
            return _find_library(name)

        ctypes.util.find_library = _find_library_bundled
