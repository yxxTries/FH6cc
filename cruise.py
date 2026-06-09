"""Cruise control modeled on real automotive ACC architecture.

Two-loop hierarchical design (as used in production cruise systems):

  Outer loop   speed error -> bounded acceleration demand. Implemented as a
               reference ramp: the internal setpoint glides toward the target
               at a comfort accel/decel limit, so a "set speed 150" command
               produces a smooth pull, not a throttle slam.

  Inner loop   PI tracking of the ramped reference on low-pass-filtered
               speed, plus damping against measured acceleration
               (derivative-on-measurement: no setpoint kick).

  Arbitration  throttle / coast / brake zones with hysteresis, like a real
               car: slightly over target -> lift off and engine-brake;
               well over (long downhill) -> gentle proportional auto-brake
               that releases once speed falls back.

  Feel         the throttle command is slew-rate limited (a foot, not a
               square wave) and the integral term doubles as a learned
               steady-state throttle trim that persists across engagements,
               so re-engaging settles instantly.

States: OFF -> ENGAGED (sub-states: throttle / coast / braking / MANUAL
override while the player's throttle exceeds ours). Player brake always
cancels, like a real car.

Fault monitors (like a real ECU's sensor-fault limp mode) auto-disengage and
zero the outputs when something is wrong rather than fighting it:
  telemetry lost     no fresh Data Out packets while engaged
  telemetry frozen   bit-identical speed for too long (stale/paused data)
  runaway            far past target and still accelerating despite braking
  no response        high throttle, no acceleration, far below target
                     (input not reaching the game / car stuck)
"""

import threading
import time

import config


class CruiseControl:
    def __init__(self):
        self._lock = threading.Lock()
        self.engaged = False
        self.manual = False
        self.target_kmh = 0.0
        self._last_target = None       # remembered for voice "resume"

        # Outer loop: ramped reference.
        self._ref = 0.0
        # Filtered measurements.
        self._v_f = None               # filtered speed, km/h
        self._a_f = 0.0                # filtered acceleration, km/h per s
        # Inner loop.
        self._integral = 0.0           # persistent throttle trim
        # Actuator state.
        self._out = 0.0                # slew-limited throttle command
        self._braking = False          # brake-zone hysteresis latch
        self._brake_out = 0.0
        # Last live inputs, exposed to the HUD telemetry panel.
        self._user_throttle = 0.0      # player's right-trigger, 0..1
        self._user_brake = 0.0         # player's left-trigger, 0..1
        # Fault monitoring.
        self.fault = None              # last fault reason (sticky until engage)
        self._fault_time = 0.0
        self._frozen_t = 0.0
        self._runaway_t = 0.0
        self._nores_t = 0.0
        self._last_raw = None          # last raw speed, for the frozen check

    # ---- commands (called from hotkey / voice threads) ---------------

    def toggle(self, speed_kmh):
        with self._lock:
            if self.engaged:
                self._disengage_locked()
                return "cruise off"
            return self._engage_locked(speed_kmh, speed_kmh)

    def engage(self, speed_kmh, target_kmh=None):
        with self._lock:
            return self._engage_locked(speed_kmh, target_kmh or speed_kmh)

    def resume(self, speed_kmh):
        with self._lock:
            if self._last_target is None:
                return "nothing to resume"
            return self._engage_locked(speed_kmh, self._last_target)

    def disengage(self):
        with self._lock:
            self._disengage_locked()

    def adjust(self, delta_kmh):
        with self._lock:
            if not self.engaged:
                return None
            self.target_kmh = self._clamp_target(self.target_kmh + delta_kmh)
            self._last_target = self.target_kmh
            return self.target_kmh

    def set_target(self, kmh, speed_kmh):
        """Voice 'set N': retarget if engaged, otherwise engage at N."""
        with self._lock:
            kmh = self._clamp_target(kmh)
            if self.engaged:
                self.target_kmh = kmh
                self._last_target = kmh
                return "set %d" % kmh
            return self._engage_locked(speed_kmh, kmh)

    # ---- control loop -------------------------------------------------

    def update(self, speed_kmh, user_throttle, user_brake, dt):
        """One tick. Returns (throttle 0..1, auto_brake 0..1)."""
        with self._lock:
            dt = min(max(dt, 1e-3), 0.1)   # guard against clock hiccups
            self._user_throttle = user_throttle
            self._user_brake = user_brake

            if self.engaged:
                fault = self._monitor_locked(speed_kmh, dt)
                if fault:
                    self._fault_locked(fault)
                    return 0.0, 0.0        # everything released, no carryover

            if speed_kmh is not None:
                self._filter(speed_kmh, dt)

            if self.engaged and user_brake > config.BRAKE_CANCEL_THRESHOLD:
                self._disengage_locked()

            if not self.engaged or self._v_f is None:
                self._out = user_throttle    # passthrough; slew resumes here
                self._brake_out = 0.0
                return user_throttle, 0.0

            self._ramp_reference(dt)
            u = self._arbitrate(dt)

            # Manual override: the player's foot wins while it asks for more.
            if user_throttle > u + config.MANUAL_OVERRIDE_MARGIN:
                self.manual = True
                self._out = user_throttle    # resume slewing from their level
                self._brake_out = 0.0
                return user_throttle, 0.0
            self.manual = False

            self._out = _slew(self._out, u, dt)
            return self._out, self._brake_out

    # ---- HUD ------------------------------------------------------------

    def status(self, speed_kmh):
        with self._lock:
            return self._status_locked(speed_kmh)

    def _status_locked(self, speed_kmh):
        if self.fault and \
                time.monotonic() - self._fault_time < config.FAULT_DISPLAY_S:
            return ("FAULT: %s — cruise off" % self.fault, "#ff3b30")
        if speed_kmh is None:
            return ("NO TELEMETRY", "#888888")
        if not self.engaged:
            return ("CRUISE OFF  •  %d km/h" % speed_kmh, "#aaaaaa")
        if self.manual:
            return ("MANUAL  (set %d)" % self.target_kmh, "#ffb347")
        if self._braking:
            return ("CRUISE %d  •  braking" % self.target_kmh, "#ff6961")
        return ("CRUISE %d km/h" % self.target_kmh, "#4cd964")

    def telemetry(self, speed_kmh):
        """Live data snapshot for the HUD panel.

        Returns a dict the HUD renders directly. 'throttle' / 'brake' are the
        commands actually being applied (cruise output while engaged, otherwise
        the player's own pedals), each 0..1; 'throttle_auto'/'brake_auto' flag
        whether cruise (rather than the player) is the source.
        """
        with self._lock:
            text, color = self._status_locked(speed_kmh)
            engaged = self.engaged and not self.manual
            if engaged:
                throttle, throttle_auto = self._out, True
                brake, brake_auto = self._brake_out, True
            else:
                throttle, throttle_auto = self._user_throttle, False
                brake, brake_auto = self._user_brake, False
            return {
                "text": text,
                "color": color,
                "speed": speed_kmh,
                "target": self.target_kmh if self.engaged else None,
                "engaged": self.engaged,
                "manual": self.manual,
                "throttle": throttle,
                "throttle_auto": throttle_auto,
                "brake": brake,
                "brake_auto": brake_auto,
            }

    # ---- internals (caller holds the lock) ------------------------------

    def _engage_locked(self, speed_kmh, target_kmh):
        if speed_kmh is None:
            return "no telemetry"
        if speed_kmh < config.MIN_ENGAGE_KMH:
            return "too slow to engage (< %d km/h)" % config.MIN_ENGAGE_KMH
        self.target_kmh = self._clamp_target(target_kmh)
        self._last_target = self.target_kmh
        self.engaged = True
        self.manual = False
        self._braking = False
        self._brake_out = 0.0
        # Start the reference at the actual speed and the throttle at the
        # learned trim, so the pickup is seamless.
        self._ref = self._v_f if self._v_f is not None else speed_kmh
        self._out = self._integral
        # Fresh engagement: clear fault state and monitor timers.
        self.fault = None
        self._frozen_t = self._runaway_t = self._nores_t = 0.0
        self._last_raw = None
        return "cruise set %d km/h" % self.target_kmh

    def _disengage_locked(self):
        # Keep self._integral: it is the learned steady-state trim and makes
        # the next engagement settle instantly.
        self.engaged = False
        self.manual = False
        self._braking = False
        self._brake_out = 0.0

    def _fault_locked(self, reason):
        """Disengage and zero everything; remember why for the HUD/console."""
        self._disengage_locked()
        self.fault = reason
        self._fault_time = time.monotonic()
        self._out = 0.0

    def _monitor_locked(self, speed_kmh, dt):
        """Plausibility checks while engaged. Returns a fault reason or None.

        Runs on the previous tick's filtered state, before this tick's
        filtering, so a bad sample can't poison its own check.
        """
        # 1. Telemetry lost (None means the staleness grace already elapsed).
        if speed_kmh is None:
            return "telemetry lost"

        # 2. Frozen telemetry: real speed jitters every physics tick, so a
        # bit-identical value for this long means stale/paused data.
        if config.FROZEN_TELEM_TIME_S > 0:
            if speed_kmh == self._last_raw:
                self._frozen_t += dt
                if self._frozen_t > config.FROZEN_TELEM_TIME_S:
                    return "telemetry frozen"
            else:
                self._frozen_t = 0.0
                self._last_raw = speed_kmh

        if self._v_f is None:
            return None

        # 3. Runaway: way past target and *still* gaining speed despite the
        # coast/brake zones — something is bugged, stop fighting it. The
        # player intentionally speeding (manual override) is not a fault.
        if config.RUNAWAY_TIME_S > 0 and not self.manual:
            if (self._v_f - self.target_kmh > config.RUNAWAY_OVER_KMH
                    and self._a_f > config.RUNAWAY_ACCEL):
                self._runaway_t += dt
                if self._runaway_t > config.RUNAWAY_TIME_S:
                    return "runaway acceleration"
            else:
                self._runaway_t = 0.0

        # 4. No response: throttle high, car not accelerating, far below
        # target. Input isn't reaching the game (wrong keybind, alt-tab,
        # stuck against a wall) — don't pin the throttle forever.
        if config.NO_RESPONSE_TIME_S > 0 and not self.manual:
            if (self._out > config.NO_RESPONSE_THROTTLE
                    and self._a_f < 0.1
                    and self.target_kmh - self._v_f > config.NO_RESPONSE_BELOW_KMH):
                self._nores_t += dt
                if self._nores_t > config.NO_RESPONSE_TIME_S:
                    return "no throttle response"
            else:
                self._nores_t = 0.0
        return None

    @staticmethod
    def _clamp_target(kmh):
        return max(config.MIN_TARGET_KMH, min(config.MAX_TARGET_KMH, kmh))

    def _filter(self, speed_kmh, dt):
        """Low-pass speed and estimate acceleration from the filtered value."""
        if self._v_f is None or dt <= 0:
            self._v_f = speed_kmh
            self._a_f = 0.0
            return
        alpha = dt / (config.SPEED_FILTER_TC + dt)
        v_new = self._v_f + alpha * (speed_kmh - self._v_f)
        a_raw = (v_new - self._v_f) / dt
        self._a_f += alpha * (a_raw - self._a_f)
        self._v_f = v_new

    def _ramp_reference(self, dt):
        """Outer loop: glide the reference toward the target at comfort rates."""
        gap = self.target_kmh - self._ref
        step = (config.ACCEL_LIMIT_KMH_S if gap > 0
                else config.DECEL_LIMIT_KMH_S) * dt
        self._ref += max(-step, min(step, gap))

    def _arbitrate(self, dt):
        """Inner loop + throttle/coast/brake switching. Returns throttle cmd."""
        over = self._v_f - self.target_kmh

        # Brake-zone hysteresis: latch on well over target, release near it.
        if self._braking:
            if over < config.BRAKE_OFF_BAND:
                self._braking = False
        elif over > config.BRAKE_ON_BAND:
            self._braking = True

        if self._braking:
            self._brake_out = min(config.MAX_AUTO_BRAKE,
                                  config.BRAKE_KP * (over - config.BRAKE_OFF_BAND))
            self._ref = self._v_f        # re-approach from here when done
            return 0.0
        self._brake_out = 0.0

        if over > config.COAST_BAND:     # slightly over: lift off, coast
            self._ref = self._v_f
            # Bleed the learned trim while coasting so the throttle doesn't
            # push the car back over target as soon as it re-enters the band.
            self._integral = max(0.0, self._integral
                                 - config.PID_KI * over * dt)
            return 0.0

        # Throttle zone: PI on the ramped reference + acceleration damping.
        err = self._ref - self._v_f
        p = config.PID_KP * err
        d = -config.PID_KD * self._a_f
        u_unclamped = p + self._integral + d
        # Conditional integration (anti-windup): only wind while not saturated
        # in the direction of the error.
        if not (u_unclamped >= 1.0 and err > 0) and \
           not (u_unclamped <= 0.0 and err < 0):
            self._integral += config.PID_KI * err * dt
            self._integral = max(0.0, min(config.PID_I_MAX, self._integral))
        return max(0.0, min(1.0, p + self._integral + d))


def _slew(current, desired, dt):
    """Rate-limit throttle motion so it moves like a foot on a pedal."""
    if desired > current:
        return min(desired, current + config.THROTTLE_SLEW_UP * dt)
    return max(desired, current - config.THROTTLE_SLEW_DOWN * dt)
