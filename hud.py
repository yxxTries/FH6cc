"""Always-on-top overlay: cruise status, live telemetry, and controls.

Shows in the top-left corner: the cruise status line, current speed / target,
live throttle and brake bars, and buttons to engage/cancel cruise and to
mute/unmute voice control.

Note: visible over borderless/windowed fullscreen. Exclusive fullscreen
hides any overlay; cruise itself still works, you just won't see the HUD.
"""

import tkinter as tk

import config


class _Bar:
    """A small labelled horizontal level meter (0..1)."""

    def __init__(self, parent, caption, fill):
        self._fill = fill
        row = tk.Frame(parent, bg="black")
        row.pack(fill="x", pady=1)
        tk.Label(row, text=caption, fg="#aaaaaa", bg="black",
                 font=("Segoe UI", 9), width=8, anchor="w").pack(side="left")
        self._canvas = tk.Canvas(row, width=140, height=12, bg="#222222",
                                  highlightthickness=0)
        self._canvas.pack(side="left")
        self._rect = self._canvas.create_rectangle(0, 0, 0, 12,
                                                    width=0, fill=fill)
        self._pct = tk.Label(row, text="0%", fg="#aaaaaa", bg="black",
                             font=("Segoe UI", 9), width=5, anchor="e")
        self._pct.pack(side="left")

    def set(self, value, auto):
        value = max(0.0, min(1.0, value or 0.0))
        self._canvas.coords(self._rect, 0, 0, int(140 * value), 12)
        # Brighter when cruise (auto) is driving it, dimmed for the player.
        self._canvas.itemconfigure(self._rect,
                                   fill=self._fill if auto else "#5a5a5a")
        self._pct.configure(text="%d%%" % round(value * 100),
                            fg="#dddddd" if value > 0.01 else "#777777")


class Hud:
    def __init__(self, get_data, stop_event, on_cruise=None,
                 on_voice=None, get_voice_enabled=None, keys=None):
        """get_data() -> telemetry dict (see CruiseControl.telemetry).

        on_cruise(): toggle cruise engage/cancel.
        on_voice(): toggle voice listening on/off.
        get_voice_enabled() -> bool | None (None = voice unavailable).
        keys: dict of hotkey labels to show (toggle/faster/slower/voice/step).
        stop_event ends the mainloop.
        """
        self._get_data = get_data
        self._stop = stop_event
        self._on_cruise = on_cruise
        self._on_voice = on_voice
        self._get_voice_enabled = get_voice_enabled
        self._keys = keys or {}

        self.root = tk.Tk()
        self.root.title("FH6 Cruise")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", config.HUD_ALPHA)
        self.root.configure(bg="black")

        outer = tk.Frame(self.root, bg="black", padx=12, pady=8)
        outer.pack()

        # Status line.
        self.status = tk.Label(outer, text="...", fg="#aaaaaa", bg="black",
                               font=("Segoe UI", 15, "bold"), anchor="w")
        self.status.pack(fill="x")

        # Speed / target line.
        self.speed = tk.Label(outer, text="", fg="#cccccc", bg="black",
                              font=("Segoe UI", 11), anchor="w")
        self.speed.pack(fill="x", pady=(0, 4))

        # Telemetry bars.
        self.throttle_bar = _Bar(outer, "Throttle", "#4cd964")
        self.brake_bar = _Bar(outer, "Brake", "#ff3b30")

        # Controls.
        controls = tk.Frame(outer, bg="black")
        controls.pack(fill="x", pady=(6, 0))
        self.cruise_btn = tk.Button(
            controls, text="Cruise", width=9, relief="flat",
            font=("Segoe UI", 9, "bold"), bg="#333333", fg="#ffffff",
            activebackground="#444444", activeforeground="#ffffff",
            command=self._cruise_clicked)
        self.cruise_btn.pack(side="left", padx=(0, 6))
        self.voice_btn = tk.Button(
            controls, text="Voice", width=9, relief="flat",
            font=("Segoe UI", 9, "bold"), bg="#333333", fg="#ffffff",
            activebackground="#444444", activeforeground="#ffffff",
            command=self._voice_clicked)
        self.voice_btn.pack(side="left")

        # Keyboard shortcuts reference.
        k = self._keys
        if k:
            shortcuts = ("%s  engage/cancel      %s / %s  ±%d km/h\n"
                         "%s  voice on/off" % (
                             k.get("toggle", "?"),
                             k.get("faster", "?"), k.get("slower", "?"),
                             k.get("step", 0), k.get("voice", "?")))
            tk.Label(outer, text=shortcuts, fg="#888888", bg="black",
                     font=("Consolas", 9), justify="left", anchor="w"
                     ).pack(fill="x", pady=(6, 0))

        self.root.update_idletasks()
        # Top-left corner, with a small margin.
        self.root.geometry("+%d+%d" % (config.HUD_X, config.HUD_Y))

        # Allow dragging the panel out of the way (from the status line).
        for w in (self.status, self.speed):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self._drag = (0, 0)

        self._tick()

    # ---- button handlers (don't let a callback error kill the HUD) -------

    def _cruise_clicked(self):
        if self._on_cruise:
            try:
                self._on_cruise()
            except Exception:
                pass

    def _voice_clicked(self):
        if self._on_voice:
            try:
                self._on_voice()
            except Exception:
                pass

    def _tick(self):
        if self._stop.is_set():
            self.root.destroy()
            return
        data = self._get_data()
        self.status.configure(text=data["text"], fg=data["color"])

        speed, target = data.get("speed"), data.get("target")
        if speed is None:
            self.speed.configure(text="— km/h")
        elif target is not None:
            self.speed.configure(text="%d km/h   →  set %d" %
                                 (round(speed), round(target)))
        else:
            self.speed.configure(text="%d km/h" % round(speed))

        self.throttle_bar.set(data.get("throttle", 0.0),
                              data.get("throttle_auto", False))
        self.brake_bar.set(data.get("brake", 0.0),
                           data.get("brake_auto", False))

        # Cruise button reflects engaged state.
        if data.get("engaged"):
            self.cruise_btn.configure(text="Cruise: ON", bg="#1f6f3f")
        else:
            self.cruise_btn.configure(text="Cruise: OFF", bg="#333333")

        # Voice button reflects mute state (or disabled if unavailable).
        ven = self._get_voice_enabled() if self._get_voice_enabled else None
        if ven is None:
            self.voice_btn.configure(text="Voice: n/a", bg="#333333",
                                     state="disabled")
        elif ven:
            self.voice_btn.configure(text="Voice: ON", bg="#1f6f3f",
                                     state="normal")
        else:
            self.voice_btn.configure(text="Voice: OFF", bg="#333333",
                                     state="normal")

        self.root.after(100, self._tick)

    def _drag_start(self, e):
        self._drag = (e.x, e.y)

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._drag[0]
        y = self.root.winfo_y() + e.y - self._drag[1]
        self.root.geometry("+%d+%d" % (x, y))

    def run(self):
        self.root.mainloop()
