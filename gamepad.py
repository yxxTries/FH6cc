"""ViGEm passthrough output mode: DS4 -> virtual Xbox 360 pad.

The game should only see the ViGEm virtual pad. Every stick/button/trigger on
your physical DS4 is forwarded 1:1, except the right trigger, which is blended
with the cruise controller's throttle. Hide the physical DS4 from the game
with HidHide (see README) so inputs aren't doubled.

Prefer OUTPUT_MODE = "keyboard" (throttle_out.py) if you don't want a virtual
pad at all.
"""

import pygame
import vgamepad as vg

from ds4 import Ds4Reader

BUTTON_MAP = {
    pygame.CONTROLLER_BUTTON_A: vg.XUSB_BUTTON.XUSB_GAMEPAD_A,                # cross
    pygame.CONTROLLER_BUTTON_B: vg.XUSB_BUTTON.XUSB_GAMEPAD_B,                # circle
    pygame.CONTROLLER_BUTTON_X: vg.XUSB_BUTTON.XUSB_GAMEPAD_X,                # square
    pygame.CONTROLLER_BUTTON_Y: vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,                # triangle
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    pygame.CONTROLLER_BUTTON_BACK: vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,          # share
    pygame.CONTROLLER_BUTTON_START: vg.XUSB_BUTTON.XUSB_GAMEPAD_START,        # options
    pygame.CONTROLLER_BUTTON_GUIDE: vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,        # PS
    pygame.CONTROLLER_BUTTON_LEFTSTICK: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    pygame.CONTROLLER_BUTTON_RIGHTSTICK: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    pygame.CONTROLLER_BUTTON_DPAD_UP: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    pygame.CONTROLLER_BUTTON_DPAD_LEFT: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
}


class PadBridge(Ds4Reader):
    def __init__(self):
        super().__init__()
        self._virtual = vg.VX360Gamepad()
        self._pressed = set()

    def write(self, state, throttle, auto_brake=0.0):
        """Push the state to the virtual pad: `throttle` (0..1) on RT, and
        LT carries the player's brake or the cruise auto-brake, whichever
        is greater."""
        v = self._virtual
        lx, ly, rx, ry = state.sticks
        # SDL Y axes are down-positive, XInput is up-positive.
        v.left_joystick(x_value=lx, y_value=_flip(ly))
        v.right_joystick(x_value=rx, y_value=_flip(ry))
        brake = max(state.brake, auto_brake)
        v.left_trigger(value=int(max(0.0, min(1.0, brake)) * 255))
        v.right_trigger(value=int(max(0.0, min(1.0, throttle)) * 255))

        for sdl_btn, xusb in BUTTON_MAP.items():
            down = state.buttons.get(sdl_btn, False)
            if down and xusb not in self._pressed:
                v.press_button(button=xusb)
                self._pressed.add(xusb)
            elif not down and xusb in self._pressed:
                v.release_button(button=xusb)
                self._pressed.discard(xusb)
        v.update()

    def neutral(self):
        """Release everything on the virtual pad (used on exit)."""
        self._virtual.reset()
        self._virtual.update()
        self._pressed.clear()


def _flip(axis_value):
    return max(-32768, min(32767, -axis_value))
