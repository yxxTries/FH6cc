"""Analog throttle via a dedicated virtual Xbox pad — like a separate pedal set.

The virtual pad presses ONLY the right trigger; sticks and buttons stay
neutral. Your DS4 keeps talking to the game directly (no HidHide, nothing
intercepted), and the game merges the extra device's trigger the same way it
merges a standalone wheel/pedal unit. Requires only the ViGEmBus driver.
"""

import vgamepad as vg


class PadThrottle:
    def __init__(self):
        self._pad = vg.VX360Gamepad()
        self._last = None

    def set_levels(self, throttle, brake):
        """Analog throttle/brake 0..1 on the virtual pad's triggers."""
        t = int(max(0.0, min(1.0, throttle)) * 255)
        b = int(max(0.0, min(1.0, brake)) * 255)
        if (t, b) != self._last:
            self._pad.right_trigger(value=t)
            self._pad.left_trigger(value=b)
            self._pad.update()
            self._last = (t, b)

    def stop(self):
        self._pad.reset()
        self._pad.update()
        self._last = None
