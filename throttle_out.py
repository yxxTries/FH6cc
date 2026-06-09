"""Keyboard PWM throttle output — the no-virtual-pad alternative to ViGEm.

Holds/pulses the in-game accelerate key (default W, scancode 0x11) with a
duty cycle equal to the cruise controller's throttle demand. Forza reads
keyboard and controller at the same time and effectively applies the higher
throttle, so your DS4 keeps working untouched and there is nothing for the
game's input to collide with.

Keys are injected with SendInput + KEYEVENTF_SCANCODE (DirectInput games
ignore plain virtual-key events).
"""

import atexit
import ctypes
import threading
import time

import config

_user32 = ctypes.windll.user32
_winmm = ctypes.windll.winmm

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

_ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", _ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):  # only present to size the union correctly
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


def _send_key(scancode, up):
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp.union.ki = _KEYBDINPUT(0, scancode, flags, 0, None)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _panic_release():
    """Unconditional key-up for both game keys, no matter how we exit.

    Registered with atexit so even an unhandled crash elsewhere in the app
    cannot leave W or S held down in the game.
    """
    try:
        _send_key(config.THROTTLE_KEY_SCANCODE, up=True)
        _send_key(config.BRAKE_KEY_SCANCODE, up=True)
    except Exception:
        pass


atexit.register(_panic_release)


class KeyPWM(threading.Thread):
    """PWM thread: set_level(0..1) controls how much of each period a key is held."""

    def __init__(self, scancode, name="key-pwm"):
        super().__init__(name=name, daemon=True)
        self._duty = 0.0
        self._stop = threading.Event()
        self._held = False
        self._scan = scancode
        self._fed = time.monotonic()   # dead-man's switch timestamp

    def set_level(self, duty):
        self._duty = max(0.0, min(1.0, duty))
        self._fed = time.monotonic()

    def stop(self):
        self._stop.set()

    def _press(self):
        if not self._held:
            _send_key(self._scan, up=False)
            self._held = True

    def _release(self):
        if self._held:
            _send_key(self._scan, up=True)
            self._held = False

    def run(self):
        _winmm.timeBeginPeriod(1)  # 1 ms sleep resolution for clean pulses
        period = 1.0 / config.PWM_HZ
        try:
            while not self._stop.is_set():
                try:
                    duty = self._duty
                    # Dead-man's switch: if the control loop stops feeding us
                    # (crash, hang), never keep a key held down.
                    if duty > 0 and (time.monotonic() - self._fed
                                     > config.OUTPUT_DEADMAN_S):
                        duty = self._duty = 0.0
                    if duty >= 0.97:      # effectively full throttle: just hold
                        self._press()
                        time.sleep(period)
                    elif duty <= 0.02:    # effectively off: stay released
                        self._release()
                        time.sleep(period)
                    else:
                        self._press()
                        time.sleep(duty * period)
                        self._release()
                        time.sleep((1.0 - duty) * period)
                except Exception:
                    # A transient SendInput failure must not kill the thread
                    # (a dead thread can't release a held key). Force a key-up
                    # so the game is never left with the key stuck down.
                    try:
                        _send_key(self._scan, up=True)
                    except Exception:
                        pass
                    self._held = False
                    time.sleep(period)
        finally:
            self._release()
            _winmm.timeEndPeriod(1)


class KeyboardOutput:
    """Throttle + brake key channels (W / S by default)."""

    def __init__(self):
        self._throttle = KeyPWM(config.THROTTLE_KEY_SCANCODE, "throttle-pwm")
        self._brake = KeyPWM(config.BRAKE_KEY_SCANCODE, "brake-pwm")
        self._throttle.start()
        self._brake.start()

    def set_levels(self, throttle, brake):
        self._throttle.set_level(throttle)
        self._brake.set_level(brake)

    @property
    def alive(self):
        return self._throttle.is_alive() and self._brake.is_alive()

    def stop(self):
        self._throttle.stop()
        self._brake.stop()
