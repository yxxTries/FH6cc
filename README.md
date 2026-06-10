# FH6 Cruise Control

A real-car cruise control system for Forza Horizon 6. The program reads your game controller (e.g., DS4 or Xbox controller) and automatically pulses the in-game keyboard accelerate key (`W`) to maintain a target speed. 

Your controller continues working normally (you can still steer and brake). The cruise control only modulates the throttle via keyboard input, avoiding any input collisions with your controller. It also features a live HUD overlay and optional offline voice commands.

## Requirements

Before you begin, ensure you have the following:
- **Windows OS**: The program requires administrator rights to simulate keyboard presses while the game is focused.
- **Python 3.10 or newer**: Required to run the application.
- **Forza Horizon 6 (FH6)**: With Data Out telemetry enabled.
- **A Game Controller**: Such as a DualShock 4 or Xbox controller.

## Setup Guide

### 1. Install Python Dependencies
Open your terminal or command prompt in the project directory and install the required dependencies:
```powershell
pip install -r requirements.txt
```

### 2. Configure Forza Horizon 6 Telemetry
In Forza Horizon 6, navigate to **Settings** → **HUD and Gameplay** → **Data Out** and configure the following:
- **Data Out**: `ON`
- **IP address**: `127.0.0.1`
- **Port**: `5300`

### 3. Voice Control Model (Optional)
If you want to use offline voice commands, download the required ~40 MB Vosk model into the `models/` directory by running:
```powershell
.\download_model.ps1
```

## Running the Program

Start the script:
```powershell
python main.py
```
*Note: The app will ask for **administrator rights** via a UAC prompt on launch. Please accept it. Without admin privileges, Windows blocks the simulated `W` key presses and global hotkeys when the game window is in focus.*

You can start the program before or after launching the game.

### Command-line Flags
- `--no-voice`: Disable voice recognition.
- `--no-hud`: Disable the live telemetry overlay.
- `--port 5300`: Change the UDP port if you use a different one in-game.

*Caveat: While cruise control is engaged, the app presses `W` for the currently focused window. Be sure to disengage (tap the brake) before alt-tabbing out of the game.*

## Controls

| Input | Action |
|---|---|
| `x` | Engage at current speed (needs > 20 km/h) / cancel |
| `↑` / `↓` | Set target speed ±5 km/h |
| `v` | Toggle voice recognition on/off |
| Throttle (R2/RT) | Push past the cruise output to override (manual acceleration); release to resume cruise |
| Brake (L2/LT) | Cancels cruise completely (like a real car) |
| `ctrl+shift+q` | Quit the application |

### Voice Commands
If enabled, you can use these voice commands:
- "**cruise on**" / "**engage**"
- "**cruise off**" / "**cancel**"
- "**resume**"
- "**faster**" / "**slower**"
- "**set speed one hundred twenty**"

## HUD
A live telemetry panel appears in the top-left corner over **borderless/windowed fullscreen** mode:
- **Status line:** Shows current state (e.g., `CRUISE OFF`, `CRUISE 120 km/h`, `MANUAL`, `NO TELEMETRY`).
- **Speed:** Current speed and target speed.
- **Live bars:** Throttle and brake levels (0–100%).
- **Shortcuts:** Quick keyboard reference.
*(You can drag the HUD by clicking its status line)*

## Troubleshooting
If you need to tweak the behavior, check `config.py`.
- **Speed hunts up/down:** Lower `PID_KP` / `PID_KI`.
- **Slow to reach the set speed uphill:** Raise `PID_KI` slightly.
- **`NO TELEMETRY`:** Ensure Data Out is ON, IP is `127.0.0.1`, and port matches `5300`.
- **Hotkeys dead while game is focused:** Ensure the script was launched as an administrator.

## Optional: ViGEm Mode (Smoother Analog Throttle)
By default, the program simulates keyboard presses for the throttle. If you prefer a completely smooth, analog throttle output, you can use the `vigem` output mode. This mode forwards every controller input through a virtual Xbox 360 pad and blends the right trigger with the cruise control output.

**Setup for ViGEm Mode:**
1. **Install ViGEmBus Driver:** It's bundled with the `vgamepad` package you installed via pip. You can find the installer inside your Python site-packages directory (e.g., `site-packages\vgamepad\win\vigem\install\x64\ViGEmBusSetup_x64.msi`) and run it.
2. **Install [HidHide](https://github.com/nefarius/HidHide/releases):** You must hide your physical controller from the game, or your inputs will be doubled. 
   - Open *HidHide Configuration Client*.
   - In the **Devices** tab, check your physical controller and tick *Hide device*.
   - In the **Applications** tab, add `python.exe` so this app can still read the physical controller.
3. **Run with ViGEm Mode:** Start the app with the flag `--output vigem` or change `OUTPUT_MODE = "vigem"` in `config.py`.
