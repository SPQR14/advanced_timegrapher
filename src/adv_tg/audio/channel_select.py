"""Choice of the input channel of a multichannel device (callback-safe, numpy only).

A live capture used to open one channel, i.e. the first of the device. With a two-input
interface and the microphone in the second input the application heard silence, and where
the host API mixes the channels down to one (PulseAudio/PipeWire, MME, DirectSound) the
watch lost 6 dB against a noise that only drops 3 dB, or was comb-filtered by the two
capsules of a stereo microphone. Files already use the channel with the largest energy
(``audio_files.select_channel``); ``ChannelSelector`` does the same on a live stream.

The device is opened with ``channels_to_open`` channels and every block goes through
``ChannelSelector.pick``, which returns the channel to keep:

* The level of a channel is the energy of its second difference, ``sum(diff(x, 2) ** 2)``:
  a DC offset does not count and mains hum counts ~90 dB less than the kHz content of a
  beat (an unconnected high-impedance input hums far louder than a microphone hears a
  watch).
* Levels are compared over windows of ``window_s``. The first window chooses the loudest
  channel if it is ``first_ratio_db`` above channel 0. Later the channel only changes when
  another one is ``switch_ratio_db`` louder for ``confirm`` windows in a row: the two
  capsules of a stereo microphone never trade places, a microphone plugged into another
  input after the start is found within a second.

A change of channel is a discontinuity of the signal (the detector sees it as a click); it
only happens when the current channel carries far less than another, i.e. when nothing was
being measured.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["MAX_CHANNELS", "ChannelSelector", "channels_to_open"]

#: Devices with more inputs than this are virtual mixers (PipeWire/JACK report 32 or 64).
MAX_CHANNELS = 8
#: Channels opened on such a device.
_VIRTUAL_CHANNELS = 2


def channels_to_open(max_input_channels: int) -> int:
    """Number of channels to open on a device that offers ``max_input_channels``."""
    n = int(max_input_channels)
    if n <= 1:
        return 1
    return n if n <= MAX_CHANNELS else _VIRTUAL_CHANNELS


class ChannelSelector:
    """Keeps the loudest channel of a multichannel input (see the module docstring).

    Args:
        channels: Channels of the blocks.
        sample_rate: Sample rate in Hz.
        window_s: Length of the comparison windows.
        first_ratio_db: Advantage over channel 0 that the first window needs.
        switch_ratio_db: Advantage over the current channel needed later.
        confirm: Consecutive windows that must agree before a later change.

    Attributes:
        channel: Channel in use.
        switches: Times the channel changed.
    """

    def __init__(
        self,
        channels: int,
        sample_rate: float,
        *,
        window_s: float = 0.25,
        first_ratio_db: float = 3.0,
        switch_ratio_db: float = 10.0,
        confirm: int = 2,
    ) -> None:
        if channels < 1 or not sample_rate > 0 or not window_s > 0 or confirm < 1:
            raise ValueError("parámetros del selector de canal no válidos")
        self.channels = int(channels)
        self.channel = 0
        self.switches = 0
        self._window = max(1, round(window_s * sample_rate))
        self._first = 10.0 ** (first_ratio_db / 10.0)
        self._switch = 10.0 ** (switch_ratio_db / 10.0)
        self._confirm = int(confirm)
        self._energy = np.zeros(self.channels)
        self._count = 0
        self._decided = False
        self._candidate = -1
        self._streak = 0

    def pick(self, block: Any) -> int:
        """Account for ``block`` (shape ``(frames, channels)``) and return the channel to keep.

        The block is judged with the channel chosen from the previous ones. Callback-safe:
        no logging, no locks, a few small temporaries.
        """
        if self.channels == 1:
            return 0
        d = block[2:] - 2.0 * block[1:-1] + block[:-2]
        self._energy += np.einsum("ij,ij->j", d, d)
        self._count += block.shape[0]
        if self._count >= self._window:
            self._decide()
        return self.channel

    def _decide(self) -> None:
        e = self._energy
        best = int(np.argmax(e))
        current = float(e[self.channel])
        ratio = self._switch if self._decided else self._first
        need = 1 if not self._decided else self._confirm
        if best != self.channel and float(e[best]) > ratio * current:
            self._streak = self._streak + 1 if best == self._candidate else 1
            self._candidate = best
            if self._streak >= need:
                self.channel = best
                self.switches += 1
                self._candidate, self._streak = -1, 0
        else:
            self._candidate, self._streak = -1, 0
        self._decided = True
        e[:] = 0.0
        self._count = 0
