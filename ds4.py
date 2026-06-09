"""Read-only DS4 (or any controller) input via SDL GameController.

Opening the pad here is non-exclusive: the game keeps receiving the DS4
directly. This module never sends anything to the game.
"""

import os

# Must be set before pygame is imported:
# - keep reading the DS4 while the game window has focus
# - no window/video needed
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
from pygame._sdl2 import controller as sdl2_controller  # noqa: E402

STICK_AXES = (
    pygame.CONTROLLER_AXIS_LEFTX,
    pygame.CONTROLLER_AXIS_LEFTY,
    pygame.CONTROLLER_AXIS_RIGHTX,
    pygame.CONTROLLER_AXIS_RIGHTY,
)

# All buttons forwarded in ViGEm passthrough mode (SDL constants).
SDL_BUTTONS = (
    pygame.CONTROLLER_BUTTON_A,
    pygame.CONTROLLER_BUTTON_B,
    pygame.CONTROLLER_BUTTON_X,
    pygame.CONTROLLER_BUTTON_Y,
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
    pygame.CONTROLLER_BUTTON_BACK,
    pygame.CONTROLLER_BUTTON_START,
    pygame.CONTROLLER_BUTTON_GUIDE,
    pygame.CONTROLLER_BUTTON_LEFTSTICK,
    pygame.CONTROLLER_BUTTON_RIGHTSTICK,
    pygame.CONTROLLER_BUTTON_DPAD_UP,
    pygame.CONTROLLER_BUTTON_DPAD_DOWN,
    pygame.CONTROLLER_BUTTON_DPAD_LEFT,
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
)


class InputState:
    __slots__ = ("sticks", "throttle", "brake", "buttons")

    def __init__(self):
        self.sticks = (0, 0, 0, 0)     # lx, ly, rx, ry  (-32768..32767)
        self.throttle = 0.0            # right trigger 0..1
        self.brake = 0.0               # left trigger 0..1
        self.buttons = {}


class Ds4Reader:
    def __init__(self):
        pygame.init()
        sdl2_controller.init()
        self._pad = None
        self._connect()

    def _connect(self):
        for i in range(sdl2_controller.get_count()):
            if sdl2_controller.is_controller(i):
                self._pad = sdl2_controller.Controller(i)
                print("[pad] using controller: %s" % self._pad.name)
                return True
        self._pad = None
        return False

    @property
    def connected(self):
        return self._pad is not None and self._pad.attached()

    def read(self):
        """Poll the physical controller. Returns InputState (neutral if absent)."""
        pygame.event.pump()
        state = InputState()
        if not self.connected and not self._connect():
            return state
        pad = self._pad
        try:
            state.sticks = tuple(pad.get_axis(a) for a in STICK_AXES)
            state.brake = pad.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT) / 32767.0
            state.throttle = pad.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT) / 32767.0
            state.buttons = {b: bool(pad.get_button(b)) for b in SDL_BUTTONS}
        except pygame.error:
            self._pad = None
        return state
