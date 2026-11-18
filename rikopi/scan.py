"""Definition and execution of a pump-probe scan (equivalent to the 'Riko' mode of Riko v5 + Smpl.vi).

For each repeat, at each delay point:
  1. if required, change the lock-in sensitivity (and wait a little longer for it to settle)
  2. move the stage (with a small random offset if 'jitter' is on)
  3. wait 'wait_ms'
  4. read X from the lock-in (in mV)
At the end of each repeat the jitter is corrected by interpolation and the whole file is saved
(as in the original, so that no data are lost if the computer crashes).
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from .delay import DelayLine, OutOfRange
from .instruments.sr810 import SENS_LABELS, parse_sens


@dataclass
class Zone:
    start_ps: float
    end_ps: float
    step_fs: float
    log: bool = False   # if True: geometrically increasing step; step_fs is the first step (start_ps > 0)


@dataclass
class SensStep:
    from_ps: float      # this sensitivity is used from this delay onwards
    sens: str           # e.g. "50 mV"


@dataclass
class ScanSettings:
    zones: list[Zone] = field(default_factory=lambda: [
        Zone(-5, -0.15, 500), Zone(-0.15, 2, 5), Zone(2, 150, 100)])
    sens: list[SensStep] = field(default_factory=lambda: [SensStep(float("-inf"), "50 mV")])
    reps: int = 8
    wait_ms: float = 200
    sens_extra_wait_ms: float = 1000   # extra wait after changing the sensitivity
    jitter: bool = True
    jitter_nm: float = 400             # max. amplitude of the stage jitter (±), as in Jitr.vi
    pause_between_reps_s: float = 0
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "ScanSettings":
        d = dict(d)
        d["zones"] = [Zone(**z) for z in d.get("zones", [])]
        d["sens"] = [SensStep(**s) for s in d.get("sens", [])]
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


def time_grid(zones: list[Zone]) -> np.ndarray:
    """Array of delays (ps) built from the zones. Removes duplicate points at the boundaries."""
    pts: list[float] = []
    for z in zones:
        a, b = float(z.start_ps), float(z.end_ps)
        step = z.step_fs / 1000.0
        if b <= a or step <= 0:
            raise ValueError(f"invalid zone: {z}")
        if z.log:
            if a <= 0:
                raise ValueError(f"a logarithmic zone must start at t > 0: {z}")
            r = 1 + step / a
            n = math.ceil(math.log(b / a) / math.log(r))
            pts.extend(a * r ** np.arange(n))
        else:
            n = int(math.floor((b - a) / step + 1e-9))
            pts.extend(a + step * np.arange(n + 1))
        pts.append(b)
    t = np.unique(np.round(np.asarray(pts), 6))  # round to 1 as to merge duplicates
    return t


def sens_for(t_ps: float, steps: list[SensStep]) -> int:
    steps = sorted(steps, key=lambda s: s.from_ps)
    code = parse_sens(steps[0].sens)
    for s in steps:
        if t_ps >= s.from_ps:
            code = parse_sens(s.sens)
    return code


def estimate_duration_s(settings: ScanSettings, move_s: float = 0.05) -> float:
    n = len(time_grid(settings.zones))
    return settings.reps * (n * (settings.wait_ms / 1000 + move_s) + settings.pause_between_reps_s)


@dataclass
class ScanData:
    t_ps: np.ndarray                # nominal delays
    data_mV: np.ndarray             # (reps, n) with NaN where not yet measured
    reps_done: int = 0

    @property
    def average(self) -> np.ndarray:
        if self.reps_done == 0:
            return np.full_like(self.t_ps, np.nan)
        return np.nanmean(self.data_mV[: self.reps_done], axis=0)


class Scan:
    """Runs the scan. Meant to run in a thread; stopped with stop()."""

    def __init__(self, stage, lockin, delay: DelayLine, settings: ScanSettings,
                 on_point: Callable[[int, int, float, float], None] | None = None,
                 on_rep_done: Callable[[ScanData], None] | None = None):
        self.stage, self.lockin, self.delay, self.s = stage, lockin, delay, settings
        self.on_point = on_point or (lambda *a: None)
        self.on_rep_done = on_rep_done or (lambda d: None)
        self._stop = threading.Event()
        self.t = time_grid(settings.zones)
        for tt in (self.t[0], self.t[-1]):
            delay.check(tt)  # raises OutOfRange before starting (CheckRange.vi)
        self.data = ScanData(self.t, np.full((settings.reps, len(self.t)), np.nan))
        self.info: dict = {}

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def run(self) -> ScanData:
        s = self.s
        jitter_ps = 2 * self.delay.passes * (s.jitter_nm * 1e-6) / self.delay.c if s.jitter else 0.0
        self.info = {"time_constant_s": self.lockin.get_time_constant(),
                     "lockin_freq_Hz": self.lockin.get_freq(),
                     "start": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.lockin.get_sens()
        for rep in range(s.reps):
            actual_t = np.full(len(self.t), np.nan)
            for i, t in enumerate(self.t):
                if self.stopped:
                    return self.data
                changed = self.lockin.set_sens(sens_for(t, s.sens))
                tj = t + random.uniform(-jitter_ps, jitter_ps) if jitter_ps else t
                self.stage.move_abs(self.delay.check(tj))
                self._sleep((s.wait_ms + (s.sens_extra_wait_ms if changed else 0)) / 1000)
                if self.stopped:
                    return self.data
                v = self.lockin.read_x() * 1e3
                actual_t[i] = tj
                self.data.data_mV[rep, i] = v
                self.on_point(rep, i, t, v)
            if jitter_ps:  # deJitr.vi: re-interpolate onto the nominal delays
                self.data.data_mV[rep] = np.interp(self.t, actual_t, self.data.data_mV[rep])
            self.data.reps_done = rep + 1
            self.on_rep_done(self.data)
            if s.pause_between_reps_s and rep < s.reps - 1:
                self._sleep(s.pause_between_reps_s)
        return self.data


def sens_summary(steps: list[SensStep]) -> str:
    return "; ".join(f"t>={st.from_ps:g} ps: {SENS_LABELS[parse_sens(st.sens)]}"
                     for st in sorted(steps, key=lambda x: x.from_ps))
