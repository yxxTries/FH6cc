# FH6 Cruise Control

Real-car cruise control for Forza Horizon 6 — with full **DS4 controller support**
(your analog steering/throttle/brake keep working; cruise only modulates the
throttle) and optional **offline voice commands**.

## How it works — output modes

**`keyboard` (default).** Your DS4 stays plugged
straight into the game, untouched. The app only *reads* it (non-exclusive,
for brake-cancel and override detection) and holds/pulses the in-game
accelerate key (`W`) with a PWM duty cycle matching the PID output. Forza
reads keyboard + controller simultaneously and applies the higher throttle,
so nothing collides. No virtual pad, no HidHide, no extra drivers.

```
DS4 ────────────────────────────────► FH6   (direct, untouched)
  └─(read only)─► this app ─► W-key pulses ─► FH6
                      ▲
   FH6 "Data Out" UDP telemetry (real speed)
```

**`vigem` (smoothest throttle).** The app forwards every DS4 input through a
virtual Xbox 360 pad and blends the right trigger with the PID output —
true analog cruise. Requires HidHide so the game sees only the virtual pad.

**`pad`** (throttle-only virtual pad next to the DS4) also exists, but FH6
ignores a second controller's trigger while the DS4 is active — confirmed
not to work; kept only for experimentation.

```
DS4 (hidden from the game by HidHide)
  └──► this app ──► virtual Xbox 360 pad (ViGEm) ──► FH6
            RT = max(your throttle, PID output)
```

Pick with `--output pad|keyboard|vigem` or `OUTPUT_MODE` in config.py.
No memory reading, no injection — only Forza's built-in telemetry output,
synthetic key events, and (optionally) the same virtual-controller driver
DS4Windows uses.

## Setup (once)

1. **Python 3.10+**, then:
   ```
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

2. **For `pad` and `vigem` modes** — ViGEmBus driver, bundled with vgamepad:
   `msiexec /i .venv\Lib\site-packages\vgamepad\win\vigem\install\x64\ViGEmBusSetup_x64.msi`

   **Additionally for `vigem` mode only**:
   - **[HidHide](https://github.com/nefarius/HidHide/releases)**: the game
     must see only the *virtual* pad, otherwise your DS4 inputs are doubled.
     In *HidHide Configuration Client*:
     - **Devices** tab → tick your DS4 ("Wireless Controller") → *Hide device*.
     - **Applications** tab → add the `.venv` `python.exe` so this app can
       still read the DS4.

3. **In FH6** → Settings → HUD and Gameplay → **Data Out**:
   - Data Out: **ON**
   - IP address: `127.0.0.1`
   - Port: `5300`

4. **Voice control (optional):**
   ```
   .\download_model.ps1
   ```
   Downloads the ~40 MB offline Vosk English model into `models/`.

## Run

```
.\.venv\Scripts\python main.py
```
Start it before or after the game; the DS4 is picked up automatically.
The app asks for **administrator rights** on launch (UAC prompt) — accept it.
Without admin, Windows blocks both the global hotkeys and the simulated
accelerate key whenever the game window has focus.

Flags: `--output pad|keyboard|vigem`, `--no-voice`, `--no-hud`, `--no-admin`,
`--port 5300`.

Keyboard-mode caveat: while cruise is engaged the app is pressing `W` for
whatever window has focus — disengage (tap brake) before alt-tabbing.

## Controls

| Input | Action |
|---|---|
| `x` | Engage at current speed (needs > 20 km/h) / cancel |
| `↑` / `↓` | Set speed ±5 km/h |
| `v` | Toggle voice recognition on/off |
| Throttle (R2) | Push past the cruise output → `MANUAL` override; release to resume |
| Brake (L2) | Cancels cruise (like a real car) |
| `ctrl+shift+q` | Quit |

### Voice commands
"**cruise on**" / "**engage**" · "**cruise off**" / "**cancel**" ·
"**resume**" · "**faster**" / "**slower**" ·
"**set speed one hundred twenty**"

### HUD buttons
The overlay includes clickable buttons:
- **Cruise:** toggle engage/disengage
- **Voice:** mute/unmute recognition (on/off indicator shown)

## HUD

A live telemetry panel in the top-left corner:
- **Status line:** grey `CRUISE OFF` · green `CRUISE 120 km/h` · orange `MANUAL` ·
  red `FAULT: ...` · `NO TELEMETRY` when the game isn't sending Data Out.
- **Speed:** current speed and target (e.g. `118 km/h → set 120`).
- **Live bars:** throttle and brake levels (0–100%), bright when cruise is
  commanding them, dimmed when the player is.
- **Buttons:** *Cruise: ON/OFF* and *Voice: ON/OFF* (reflects state; disabled if
  voice unavailable).
- **Shortcuts:** keyboard reference for engage, speed adjust, and voice toggle.
- Draggable by the status line. Overlays only show over **borderless/windowed fullscreen**.
  Cruise itself works in exclusive fullscreen too — you just won't see the panel.

## Tuning / troubleshooting

All knobs live in [config.py](config.py).

- **Speed hunts up/down:** lower `PID_KP` / `PID_KI`.
- **Slow to reach the set speed uphill:** raise `PID_KI` slightly.
- **`NO TELEMETRY`:** check Data Out is ON, IP `127.0.0.1`, port matches,
  and that nothing else (another telemetry app) is bound to the port.
- **FH6 changed the packet format:** set `SPEED_OFFSET_OVERRIDE` in
  config.py (FH4/FH5 use byte offset 256 in the 324-byte packet; the reader
  also auto-handles 311/331/232-byte Motorsport layouts).
- **Inputs doubled / steering fights itself (vigem mode only):** HidHide
  isn't hiding the DS4 from the game — recheck step 2. Or just use the
  default keyboard mode, which can't collide.
- **Cruise feels surge-y in keyboard mode:** raise `PWM_HZ` (e.g. 30) or
  switch to `--output vigem` + HidHide for true analog throttle.
- **Hotkeys dead while the game is focused:** the app must run elevated —
  accept the UAC prompt at launch (or start your terminal as administrator).
- **`x` clashes with a game keybind:** change `KEY_TOGGLE` in config.py.
