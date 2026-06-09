"""Reads Forza Horizon "Data Out" UDP telemetry and exposes the car speed.

Enable in game: Settings -> HUD and Gameplay -> Data Out -> ON,
IP 127.0.0.1, port 5300 (see config.py).
"""

import socket
import struct
import threading
import time

import config


class TelemetryReader(threading.Thread):
    """Background thread that keeps the latest speed reading available."""

    def __init__(self):
        super().__init__(name="telemetry", daemon=True)
        self._lock = threading.Lock()
        self._speed_kmh = 0.0
        self._race_on = False
        self._last_packet = 0.0
        self._stop = threading.Event()

    # ---- public API -------------------------------------------------

    @property
    def speed_kmh(self):
        """Current speed in km/h, or None if telemetry is stale/absent."""
        with self._lock:
            if time.monotonic() - self._last_packet > config.TELEMETRY_STALE_AFTER:
                return None
            if not self._race_on:
                return None
            return self._speed_kmh

    def stop(self):
        self._stop.set()

    # ---- internals --------------------------------------------------

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((config.TELEMETRY_IP, config.TELEMETRY_PORT))
        sock.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    data, _ = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                parsed = self._parse(data)
                if parsed is None:
                    continue
                race_on, speed_ms = parsed
                # Plausibility guard: a wrong offset / corrupt packet decodes
                # to garbage floats — never let those reach the controller.
                if not (0.0 <= speed_ms < config.MAX_PLAUSIBLE_SPEED_MS) \
                        or speed_ms != speed_ms:   # NaN check
                    continue
                with self._lock:
                    self._race_on = race_on
                    self._speed_kmh = speed_ms * 3.6
                    self._last_packet = time.monotonic()
        finally:
            sock.close()

    @staticmethod
    def _parse(data):
        """Returns (is_race_on, speed_m_per_s) or None for unknown packets."""
        n = len(data)
        race_on = bool(struct.unpack_from("<i", data, 0)[0]) if n >= 4 else False

        if config.SPEED_OFFSET_OVERRIDE is not None:
            off = config.SPEED_OFFSET_OVERRIDE
            if n >= off + 4:
                return race_on, struct.unpack_from("<f", data, off)[0]
            return None

        if n == 324:                      # FH4 / FH5 (and expected FH6) layout
            return race_on, struct.unpack_from("<f", data, 256)[0]
        if n in (311, 331):               # FM7 / FM(2023) "Dash" layout
            return race_on, struct.unpack_from("<f", data, 244)[0]
        if n == 232:                      # "Sled" layout: no speed scalar,
            vx, vy, vz = struct.unpack_from("<fff", data, 32)
            return race_on, (vx * vx + vy * vy + vz * vz) ** 0.5
        return None
