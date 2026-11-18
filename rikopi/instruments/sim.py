"""Instrumentos simulados, para probar el programa sin hardware (como el modo 'Simulate' de Riko)."""
from __future__ import annotations

import math
import random
import time

from .sr810 import SENSITIVITIES_V


class SimXPS:
    def __init__(self, limits=(-300.0, 300.0), speed_mm_s: float = 200.0):
        self._pos = 0.0
        self._limits = limits
        self.speed = speed_mm_s

    def initialize_and_home(self):
        pass

    def status(self):
        return 12

    def position(self):
        return self._pos

    def move_abs(self, mm):
        time.sleep(min(abs(mm - self._pos) / self.speed, 0.5))
        self._pos = mm

    def travel_limits(self):
        return self._limits

    def set_velocity(self, velocity, acceleration):
        pass

    def close(self):
        pass


class SimSR810:
    """Devuelve una señal OKE sintética (pico de autocorrelación + respuesta nuclear) según la posición
    de la stage simulada."""

    def __init__(self, stage: SimXPS, delay, noise_mV: float = 0.05, invert: bool = False):
        self.stage = stage
        self.delay = delay  # rikopi.delay.DelayLine (para convertir mm -> ps)
        self.noise = noise_mV
        self.invert = invert
        self._sens = 22
        self._freq = 1000.0

    @staticmethod
    def signal_mV(t: float) -> float:
        tau = 0.06  # ps, parámetro sech de un pulso de ~100 fs
        x = t / tau
        ac = 1.0 if abs(x) < 1e-3 else 3 * (x / math.tanh(x) - 1) / math.sinh(x) ** 2 if abs(x) < 300 else 0.0
        nuclear = (t > 0) * (1 - math.exp(-2 * t)) * math.exp(-((abs(t) / 10) ** 0.5)) / 3
        return 40 * ac + 10 * nuclear

    def idn(self):
        return "Stanford_Research_Systems,SR810,SIM,1.0"

    def read_x(self):
        t = self.delay.mm_to_ps(self.stage.position())
        v = (self.signal_mV(t) + random.gauss(0, self.noise)) * 1e-3
        v = max(-SENSITIVITIES_V[self._sens], min(SENSITIVITIES_V[self._sens], v))  # saturación
        return -v if self.invert else v

    def get_sens(self):
        return self._sens

    def set_sens(self, code):
        changed = code != self._sens
        self._sens = code
        return changed

    def get_time_constant(self):
        return 0.1

    def get_freq(self):
        return self._freq

    def set_freq(self, hz):
        self._freq = hz

    def close(self):
        pass
