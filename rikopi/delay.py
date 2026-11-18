"""Conversion between stage position (mm) and pump-probe delay (ps).

Equivalent to the logic in MotoMove.vi / CheckRange.vi:
  * delay = 2 * passes * (x - t0_mm) / c        (the beam goes there and back in the retroreflector)
  * 'reversed' inverts the direction (the '/-' in Axis in Riko v5.ini)
  * passes = 2 for double pass (the '/x2')
The original LabVIEW used c = 0.2998 mm/ps; the exact value is used here (0.007 % difference).
"""
from __future__ import annotations

from dataclasses import dataclass

C_MM_PER_PS = 0.299792458


class OutOfRange(ValueError):
    pass


@dataclass
class DelayLine:
    t0_mm: float = 0.0          # stage position (mm, as reported by the XPS) corresponding to t = 0
    reversed: bool = False
    passes: int = 1
    min_mm: float = -float("inf")
    max_mm: float = float("inf")
    c: float = C_MM_PER_PS

    @property
    def _k(self) -> float:
        """mm per ps of delay (signed)."""
        return (-1 if self.reversed else 1) * self.c / (2 * self.passes)

    def ps_to_mm(self, t_ps: float) -> float:
        return self.t0_mm + t_ps * self._k

    def mm_to_ps(self, x_mm: float) -> float:
        return (x_mm - self.t0_mm) / self._k

    def check(self, t_ps: float) -> float:
        x = self.ps_to_mm(t_ps)
        if not (self.min_mm <= x <= self.max_mm):
            raise OutOfRange(f"t = {t_ps:g} ps -> x = {x:.4f} mm outside the travel range "
                             f"[{self.min_mm:g}, {self.max_mm:g}] mm")
        return x

    def range_ps(self) -> tuple[float, float]:
        a, b = self.mm_to_ps(self.min_mm), self.mm_to_ps(self.max_mm)
        return min(a, b), max(a, b)
