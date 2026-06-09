"""Offline checks for the cruise logic (no game/controller needed)."""
import struct

from asr import words_to_number
from cruise import CruiseControl
from telemetry import TelemetryReader

# --- telemetry parse: fake FH5-style 324-byte packet, 50 m/s ---
pkt = bytearray(324)
struct.pack_into("<i", pkt, 0, 1)
struct.pack_into("<f", pkt, 256, 50.0)
race, ms = TelemetryReader._parse(bytes(pkt))
assert race and abs(ms - 50.0) < 1e-6, (race, ms)
print("telemetry parse OK: %.1f km/h" % (ms * 3.6))

# --- number words ---
for words, want in [(["one", "hundred", "twenty"], 120), (["eighty", "five"], 85),
                    (["two", "hundred"], 200), (["sixty"], 60)]:
    got = words_to_number(words)
    assert got == want, (words, got, want)
print("words_to_number OK")

# --- cruise sim: crude car model ---
# accel [km/h/s] = throttle*8 - brake*15 - drag - rolling + grade
DT = 1 / 60


def drive(c, speed, seconds, grade=0.0, user_throttle=0.0, user_brake=0.0):
    max_brake = 0.0
    for i in range(int(seconds / DT)):
        # Real telemetry jitters at the bit level every physics tick; a
        # perfectly converged sim value would (correctly!) trip the
        # frozen-telemetry monitor, so report a realistically noisy speed.
        meas = speed + 0.005 * (1 if i % 2 else -1)
        thr, brk = c.update(meas, user_throttle, user_brake, DT)
        max_brake = max(max_brake, brk)
        accel = thr * 8.0 - brk * 15.0 - 0.002 * speed - 0.5 + grade
        speed = max(0.0, speed + accel * DT)
    return speed, max_brake


c = CruiseControl()
print(c.toggle(10))                 # too slow -> refuses
assert not c.engaged

speed = 80.0
print(c.toggle(speed))              # engage at 80
speed, _ = drive(c, speed, 30)
print("hold 30s: speed=%.1f target=%.1f" % (speed, c.target_kmh))
assert abs(speed - c.target_kmh) < 2.0

# Big setpoint jump: ramped approach, bounded overshoot.
c.set_target(120, speed)
peak = 0.0
for i in range(int(40 / DT)):
    meas = speed + 0.005 * (1 if i % 2 else -1)
    thr, brk = c.update(meas, 0.0, 0.0, DT)
    accel = thr * 8.0 - brk * 15.0 - 0.002 * speed - 0.5
    speed = max(0.0, speed + accel * DT)
    peak = max(peak, speed)
print("approach 120: speed=%.1f peak=%.1f" % (speed, peak))
assert abs(speed - 120) < 2.0 and peak < 125

# Long downhill (containable grade): auto-brake holds the speed in check.
# (An uncontainable grade is the runaway-fault test further down.)
speed, max_brk = drive(c, speed, 40, grade=4.0)
print("downhill (+4 km/h/s grade): speed=%.1f max auto-brake=%.2f" % (speed, max_brk))
assert max_brk > 0.05, "auto-brake never engaged"
assert speed < 135, "ran away downhill"

# Back on the flat: returns to target and releases the brake.
speed, _ = drive(c, speed, 30)
thr, brk = c.update(speed, 0.0, 0.0, DT)
print("flat again: speed=%.1f brake=%.2f" % (speed, brk))
assert abs(speed - 120) < 2.5 and brk == 0.0

# Manual override: player floors it past the cruise output.
thr, brk = c.update(speed, 1.0, 0.0, DT)
assert c.manual and thr == 1.0 and brk == 0.0

# Player brake cancels.
thr, brk = c.update(speed, 0.0, 0.5, DT)
assert not c.engaged and brk == 0.0

print("resume:", c.resume(80))
assert c.engaged and c.target_kmh == 120
print("cruise state machine OK")

# --- telemetry plausibility: garbage packets are rejected ---
bad = bytearray(324)
struct.pack_into("<i", bad, 0, 1)
struct.pack_into("<f", bad, 256, 9e9)          # absurd speed
race, ms = TelemetryReader._parse(bytes(bad))  # parse still returns it...
assert ms > 1e9                                # (float32 round-trip, ~9e9)
import config
assert not (0.0 <= ms < config.MAX_PLAUSIBLE_SPEED_MS)  # ...reader drops it
print("telemetry plausibility guard OK")

# --- fault: telemetry lost while engaged -> instant safe disengage ---
c = CruiseControl()
c.engage(100)
thr, brk = c.update(None, 0.3, 0.0, DT)
assert not c.engaged and thr == 0.0 and brk == 0.0
assert c.fault == "telemetry lost", c.fault
print("fault: telemetry lost OK")

# --- fault: frozen telemetry (bit-identical speed) ---
c = CruiseControl()
c.engage(100)
for _ in range(int(2.5 / DT)):
    thr, brk = c.update(100.0, 0.0, 0.0, DT)   # never jitters = frozen
    if not c.engaged:
        break
assert c.fault == "telemetry frozen", c.fault
assert thr == 0.0 and brk == 0.0
print("fault: frozen telemetry OK")

# --- fault: runaway acceleration (speed climbs no matter what we do) ---
c = CruiseControl()
c.engage(80)
speed = 80.0
for _ in range(int(15 / DT)):
    thr, brk = c.update(speed, 0.0, 0.0, DT)
    if not c.engaged:
        break
    speed += 6.0 * DT                          # bugged: gains 6 km/h/s anyway
assert c.fault == "runaway acceleration", c.fault
print("fault: runaway acceleration OK (cut out at %.0f km/h)" % speed)

# --- fault: no throttle response (input not reaching the game) ---
c = CruiseControl()
c.engage(60)
c.set_target(120, 60)
speed, j = 60.0, 0.013
for i in range(int(20 / DT)):
    thr, brk = c.update(speed + (j if i % 2 else -j), 0.0, 0.0, DT)
    if not c.engaged:
        break
assert c.fault == "no throttle response", c.fault
print("fault: no throttle response OK")

# --- manual override is exempt: flooring it past target+25 must NOT fault ---
c = CruiseControl()
c.engage(80)
speed = 80.0
for _ in range(int(8 / DT)):
    thr, brk = c.update(speed, 1.0, 0.0, DT)   # player holds full throttle
    speed += 4.0 * DT
    assert thr == 1.0 and brk == 0.0
assert c.engaged and c.manual and c.fault is None, c.fault
print("fault: manual-override exemption OK")

# --- engaging again clears the fault ---
c.disengage()
print(c.engage(90))
assert c.engaged and c.fault is None
print("fault recovery OK")
