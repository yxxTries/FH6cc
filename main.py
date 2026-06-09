"""FH6 Cruise Control — entry point.

Usage:  python main.py [--no-voice] [--no-hud] [--port 5300]

Threads:
  - telemetry  : reads Forza Data Out UDP packets
  - control    : 60 Hz  DS4 -> cruise PID -> virtual Xbox pad
  - voice      : optional Vosk speech commands
  - main       : tkinter HUD overlay (or idle wait with --no-hud)
"""

import argparse
import ctypes
import os
import sys
import threading
import time

import config


def ensure_admin():
    """Relaunch elevated if needed.

    The global hotkey hook receives no keystrokes while a higher-privilege
    window (the game) is focused, so without admin the keybinds appear dead
    in-game.
    """
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
    except Exception:
        return
    print("Requesting administrator rights (needed for hotkeys to work "
          "while the game has focus)...")
    script = os.path.abspath(sys.argv[0])
    params = " ".join('"%s"' % a for a in [script] + sys.argv[1:])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, os.path.dirname(script), 1)
    if ret > 32:           # elevated copy launched; this one is done
        sys.exit(0)
    print("[warn] elevation declined — hotkeys may not respond in-game.")


def foreground_process():
    """Lowercase exe name of the focused window's process ('' if unknown)."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFO
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Forza Horizon cruise control")
    ap.add_argument("--no-voice", action="store_true", help="disable speech recognition")
    ap.add_argument("--no-hud", action="store_true", help="disable the overlay")
    ap.add_argument("--port", type=int, default=config.TELEMETRY_PORT,
                    help="Data Out UDP port (default %d)" % config.TELEMETRY_PORT)
    ap.add_argument("--output", choices=("pad", "keyboard", "vigem"),
                    default=config.OUTPUT_MODE,
                    help="throttle output: 'pad' = analog trigger on a "
                         "throttle-only virtual pad (DS4 stays native); "
                         "'keyboard' pulses the accelerate key; 'vigem' is "
                         "full controller passthrough (default %s)"
                         % config.OUTPUT_MODE)
    ap.add_argument("--no-admin", action="store_true",
                    help="skip the automatic elevation prompt")
    args = ap.parse_args()
    config.TELEMETRY_PORT = args.port
    vigem_mode = args.output == "vigem"

    if not args.no_admin:
        ensure_admin()

    import keyboard
    from cruise import CruiseControl
    from telemetry import TelemetryReader

    out = None
    if vigem_mode:
        # Imported here so a missing driver gives a clean message.
        try:
            from gamepad import PadBridge
            pad = PadBridge()
        except Exception as e:
            raise SystemExit(
                "Could not set up the virtual gamepad: %s\n"
                "Is the ViGEmBus driver installed? (see README, or run with "
                "--output keyboard)" % e)
    else:
        from ds4 import Ds4Reader
        pad = Ds4Reader()
        if args.output == "pad":
            try:
                from throttle_pad import PadThrottle
                out = PadThrottle()
            except Exception as e:
                raise SystemExit(
                    "Could not create the virtual throttle pad: %s\n"
                    "Is the ViGEmBus driver installed? (see README, or run "
                    "with --output keyboard)" % e)
        else:
            from throttle_out import KeyboardOutput
            out = KeyboardOutput()

    stop = threading.Event()
    telem = TelemetryReader()
    cruise = CruiseControl()

    if not pad.connected:
        print("[pad] no physical controller found yet — plug in the DS4, "
              "it will be picked up automatically.")

    # Voice control is created later; declared here so the hotkey/HUD
    # callbacks can toggle it once it exists.
    voice = None

    # ---- hotkeys ----------------------------------------------------
    def on_toggle():
        print("[key] %s" % cruise.toggle(telem.speed_kmh))

    def on_adjust(delta):
        t = cruise.adjust(delta)
        if t is not None:
            print("[key] target -> %d km/h" % t)

    def on_voice_toggle():
        if voice is not None:
            voice.set_enabled(not voice.enabled)
        else:
            print("[key] voice not available")

    keyboard.add_hotkey(config.KEY_TOGGLE, on_toggle)
    keyboard.add_hotkey(config.KEY_FASTER, lambda: on_adjust(config.STEP_KMH))
    keyboard.add_hotkey(config.KEY_SLOWER, lambda: on_adjust(-config.STEP_KMH))
    keyboard.add_hotkey(config.KEY_VOICE, on_voice_toggle)
    keyboard.add_hotkey(config.KEY_QUIT, stop.set)

    # ---- control loop ------------------------------------------------
    def release_outputs():
        try:
            if vigem_mode:
                pad.neutral()
            else:
                out.set_levels(0.0, 0.0)
        except Exception:
            pass

    def control_loop():
        period = 1.0 / config.LOOP_HZ
        last = time.perf_counter()
        errors = 0
        last_fault = None
        focused = True
        next_focus_check = 0.0
        focus_gate = config.GAME_FOCUS_ONLY and not vigem_mode
        try:
            while not stop.is_set():
                now = time.perf_counter()
                dt, last = now - last, now
                try:
                    # Output thread health: a dead PWM thread cannot release
                    # a key, so bail out instead of driving blind.
                    if not vigem_mode and hasattr(out, "alive") and not out.alive:
                        print("[safety] key output thread died — quitting")
                        release_outputs()
                        stop.set()
                        break

                    # Focus gate: never let cruise type W/S into another
                    # window. Disengage on alt-tab, like opening a car door.
                    if focus_gate and now >= next_focus_check:
                        next_focus_check = now + 0.5
                        name = foreground_process()
                        focused = config.GAME_PROCESS_HINT in name
                    if focus_gate and not focused and cruise.engaged:
                        cruise.disengage()
                        print("[safety] game window lost focus — cruise off")

                    state = pad.read()
                    throttle, auto_brake = cruise.update(telem.speed_kmh,
                                                         state.throttle,
                                                         state.brake, dt)
                    if cruise.fault and cruise.fault != last_fault:
                        last_fault = cruise.fault
                        print("[safety] FAULT: %s — cruise disengaged"
                              % cruise.fault)

                    if vigem_mode:
                        pad.write(state, throttle, auto_brake)
                    else:
                        # DS4 talks to the game directly; we only add throttle
                        # and downhill auto-brake while cruise is engaged.
                        if not cruise.engaged or not focused:
                            throttle = auto_brake = 0.0
                        out.set_levels(throttle, auto_brake)
                    errors = 0
                except Exception as e:
                    # One bad tick must never leave inputs applied.
                    errors += 1
                    print("[safety] control error (%d/5): %r" % (errors, e))
                    release_outputs()
                    try:
                        cruise.disengage()
                    except Exception:
                        pass
                    if errors >= 5:
                        print("[safety] repeated control errors — quitting")
                        stop.set()

                sleep = period - (time.perf_counter() - now)
                if sleep > 0:
                    time.sleep(sleep)
        finally:
            # Always leave the game with everything released.
            release_outputs()
            try:
                if not vigem_mode:
                    out.stop()
            except Exception:
                pass

    telem.start()
    ctl = threading.Thread(target=control_loop, name="control", daemon=True)
    ctl.start()

    if config.VOICE_ENABLED and not args.no_voice:
        from asr import VoiceControl
        voice = VoiceControl(cruise, lambda: telem.speed_kmh)
        voice.start()

    print("FH6 Cruise Control running (output: %s)." % args.output)
    if args.output == "pad":
        print("  pad mode: DS4 goes straight to the game; cruise drives the "
              "analog trigger of a separate virtual pedal pad.")
    elif args.output == "keyboard":
        print("  keyboard mode: DS4 goes straight to the game; cruise pulses "
              "the accelerate key. No HidHide needed.")
    print("  %s = engage/cancel   %s/%s = +/-%d km/h   %s = voice on/off   "
          "%s = quit"
          % (config.KEY_TOGGLE, config.KEY_FASTER, config.KEY_SLOWER,
             config.STEP_KMH, config.KEY_VOICE, config.KEY_QUIT))
    print("  In FH6: Settings > HUD > Data Out ON, IP 127.0.0.1, port %d"
          % config.TELEMETRY_PORT)

    def hud_voice_enabled():
        return voice.enabled if voice is not None else None

    # Hotkey labels shown in the HUD's shortcuts line.
    hud_keys = {
        "toggle": config.KEY_TOGGLE,
        "faster": config.KEY_FASTER,
        "slower": config.KEY_SLOWER,
        "voice": config.KEY_VOICE,
        "step": int(config.STEP_KMH),
    }

    try:
        if config.HUD_ENABLED and not args.no_hud:
            from hud import Hud
            Hud(lambda: cruise.telemetry(telem.speed_kmh), stop,
                on_cruise=on_toggle,
                on_voice=on_voice_toggle,
                get_voice_enabled=hud_voice_enabled,
                keys=hud_keys).run()
        else:
            while not stop.is_set():
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        telem.stop()
        if voice:
            voice.stop()
        ctl.join(timeout=2)
        keyboard.unhook_all()
        print("bye.")


if __name__ == "__main__":
    main()
