"""Central configuration for FH6 Cruise Control."""

# ---- Telemetry (Forza "Data Out" UDP) ----
TELEMETRY_IP = "127.0.0.1"
TELEMETRY_PORT = 5300
# If FH6 changes the packet layout, force the speed float offset here (bytes).
# Leave as None to auto-detect from packet length (FH4/FH5 layout = offset 256).
SPEED_OFFSET_OVERRIDE = None
TELEMETRY_STALE_AFTER = 1.0  # seconds without a packet -> "NO TELEMETRY"

# ---- Output mode ----
# "keyboard": pulses the in-game accelerate key (PWM). Works alongside the
#             DS4 because Forza merges keyboard + controller (it does NOT
#             merge two controllers). No drivers needed.
# "vigem":    full passthrough — every DS4 input is forwarded through one
#             virtual Xbox pad, with true analog cruise throttle. Requires
#             HidHide so the game doesn't see two controllers at once.
# "pad":      throttle-only virtual pad next to the DS4. Confirmed NOT to
#             work in FH6 with a controller active (the game ignores the
#             second pad's trigger) — kept only for experimentation.
OUTPUT_MODE = "keyboard"
THROTTLE_KEY_SCANCODE = 0x11   # W = Forza's default accelerate key
BRAKE_KEY_SCANCODE = 0x1F      # S = Forza's default brake key
PWM_HZ = 30                    # key pulse rate in keyboard mode

# ---- Cruise behaviour ----
MIN_ENGAGE_KMH = 20.0        # cannot engage below this speed
STEP_KMH = 5.0               # up/down arrow / "faster"/"slower" increment
MIN_TARGET_KMH = 20.0
MAX_TARGET_KMH = 400.0
BRAKE_CANCEL_THRESHOLD = 0.12   # left-trigger travel (0..1) that cancels cruise
MANUAL_OVERRIDE_MARGIN = 0.03   # user throttle must exceed PID output by this

# ---- Controller (two-loop, modeled on real ACC systems) ----
# Outer loop: setpoint changes are ramped at a comfort acceleration limit so
# the throttle glides instead of slamming (real systems command bounded accel).
ACCEL_LIMIT_KMH_S = 8.0      # max commanded acceleration (km/h per second)
DECEL_LIMIT_KMH_S = 6.0      # max commanded slowdown of the reference

# Inner loop: PI on the ramped reference + acceleration damping.
PID_KP = 0.060
PID_KI = 0.025
PID_KD = 0.030               # damping against measured acceleration
PID_I_MAX = 0.80             # max throttle contribution from the integral term
SPEED_FILTER_TC = 0.15       # seconds, low-pass on telemetry speed

# Throttle/coast/brake arbitration (hysteresis bands, km/h over target).
# Like a real car: slightly over -> lift off and coast on engine drag;
# well over (downhill) -> gentle proportional braking.
COAST_BAND = 1.5             # over target where throttle fully lifts
BRAKE_ON_BAND = 8.0          # over target where auto-brake starts
BRAKE_OFF_BAND = 3.0         # auto-brake releases once back under this
BRAKE_KP = 0.05              # brake per km/h beyond BRAKE_OFF_BAND
MAX_AUTO_BRAKE = 0.40        # never brake harder than this (0..1)

# Actuator feel: max throttle change per second (a foot, not a square wave).
THROTTLE_SLEW_UP = 1.2
THROTTLE_SLEW_DOWN = 2.5

# ---- Safety / fault monitors (set a time to 0 to disable that monitor) ----
# Telemetry plausibility: packets with speeds outside this are discarded
# (protects against a wrong byte offset if FH6 changes the packet format).
MAX_PLAUSIBLE_SPEED_MS = 250.0
# Frozen telemetry: identical raw speed for this long while engaged -> fault
# (real speed jitters every physics tick; a frozen value means stale data).
FROZEN_TELEM_TIME_S = 1.5
# Runaway: this far past target AND still accelerating for this long -> fault
# (auto-brake should contain a downhill; if speed keeps climbing anyway,
# something is bugged — cut out instead of fighting it).
RUNAWAY_OVER_KMH = 25.0
RUNAWAY_ACCEL = 1.0          # km/h/s still gaining despite containment
RUNAWAY_TIME_S = 1.5
# No response: high throttle, no acceleration, far below target for this
# long -> the game isn't receiving our input (wrong keybind, stuck against
# a wall, gear trouble) -> fault instead of pinning the throttle forever.
NO_RESPONSE_THROTTLE = 0.6
NO_RESPONSE_BELOW_KMH = 15.0
NO_RESPONSE_TIME_S = 6.0
# Dead-man's switch: PWM key output zeroes itself if the control loop stops
# feeding it for this long (loop crash can never leave a key held down).
OUTPUT_DEADMAN_S = 0.5
# Only inject keys while the game window is focused; cruise disengages on
# alt-tab so W/S presses can never leak into another window.
GAME_FOCUS_ONLY = True
GAME_PROCESS_HINT = "forza"  # substring of the game's process name (lowercase)
FAULT_DISPLAY_S = 5.0        # how long the HUD shows a fault message

# ---- Control loop ----
LOOP_HZ = 60

# ---- Hotkeys (https://github.com/boppreh/keyboard syntax) ----
KEY_TOGGLE = "x"
KEY_FASTER = "up"
KEY_SLOWER = "down"
KEY_VOICE = "v"              # mute / unmute voice recognition
KEY_QUIT = "ctrl+shift+q"

# ---- HUD overlay ----
HUD_ENABLED = True
HUD_X = 20                   # pixels from left edge of screen (top-left anchor)
HUD_Y = 20                   # pixels from top of screen
HUD_ALPHA = 0.85

# ---- Voice control (Vosk, offline) ----
VOICE_ENABLED = True
# Models are tried in order; the first one present on disk is used. The
# lgraph model (~128 MB) is far better at distinguishing spoken numbers
# (one vs. three vs. nine) at essentially the same speed for our tiny
# grammar. Falls back to the small model if lgraph isn't downloaded.
# Run download_model.ps1 to fetch the recommended one.
VOSK_MODEL_DIRS = [
    "models/vosk-model-en-us-0.22-lgraph",
    "models/vosk-model-small-en-us-0.15",
]
# Back-compat: single-path override still honoured if set to a real dir.
VOSK_MODEL_DIR = "models/vosk-model-small-en-us-0.15"
MIC_SAMPLE_RATE = 16000
# Audio chunk size in frames. Smaller = lower latency, more CPU.
# 1000 frames @ 16 kHz = ~62 ms per partial-result poll.
MIC_BLOCKSIZE = 1000
# Fire single-word commands the instant they appear in a partial result,
# instead of waiting for the end-of-utterance silence. Big latency win.
VOICE_PARTIAL_HOTWORDS = True
# Ignore a repeat of the same command within this many seconds (debounce,
# so a partial-result fire isn't re-fired by the final result).
VOICE_DEBOUNCE_S = 0.6

# ---- Whisper number recognition (hybrid) ----
# Vosk handles the instant commands (on/off/faster/slower/resume). For
# "set <number>", spoken digits are where small models fail ("one hundred"
# heard as "three hundred"). When enabled, faster-whisper transcribes the
# number instead — it's dramatically better at digits. Falls back to Vosk's
# own number parsing if faster-whisper isn't installed.
WHISPER_NUMBERS = True
WHISPER_MODEL = "base.en"        # tiny.en / base.en / small.en (bigger = better, slower)
WHISPER_DEVICE = "cpu"           # "cuda" if you have an NVIDIA GPU + CUDA
WHISPER_COMPUTE = "int8"         # int8 (cpu) / float16 (cuda)
# A rolling buffer always holds the last WHISPER_PREROLL_S seconds of audio,
# so "set one hundred" said in one breath is captured retroactively — no need
# to pause after "set". After the trigger we keep collecting until either
# WHISPER_POSTROLL_S of trailing audio or ~0.4s of silence, whichever first.
WHISPER_PREROLL_S = 1.2          # audio kept *before* the trigger fires
WHISPER_POSTROLL_S = 1.0         # max audio collected *after* the trigger
WHISPER_SILENCE_S = 0.4          # stop early after this much trailing quiet
WHISPER_SILENCE_RMS = 350        # int16 RMS below this counts as silence
