"""Conversión entre posición de la stage (mm) y retardo pump-probe (ps).

Equivale a la lógica de MotoMove.vi / CheckRange.vi:
  * retardo = 2 * passes * (x - t0_mm) / c        (ida y vuelta del haz en el retrorreflector)
  * 'reversed' invierte el sentido (el '/-' del Axis en Riko v5.ini)
  * passes = 2 para doble paso (el '/x2')
El LabVIEW original usaba c = 0.2998 mm/ps; aquí se usa el valor exacto (diferencia del 0.007 %).
"""
from __future__ import annotations

from dataclasses import dataclass

C_MM_PER_PS = 0.299792458


class OutOfRange(ValueError):
    pass


@dataclass
class DelayLine:
    t0_mm: float = 0.0          # posición de la stage (mm, tal como la da el XPS) que corresponde a t = 0
    reversed: bool = False
    passes: int = 1
    min_mm: float = -float("inf")
    max_mm: float = float("inf")
    c: float = C_MM_PER_PS

    @property
    def _k(self) -> float:
        """mm por ps de retardo (con signo)."""
        return (-1 if self.reversed else 1) * self.c / (2 * self.passes)

    def ps_to_mm(self, t_ps: float) -> float:
        return self.t0_mm + t_ps * self._k

    def mm_to_ps(self, x_mm: float) -> float:
        return (x_mm - self.t0_mm) / self._k

    def check(self, t_ps: float) -> float:
        x = self.ps_to_mm(t_ps)
        if not (self.min_mm <= x <= self.max_mm):
            raise OutOfRange(f"t = {t_ps:g} ps -> x = {x:.4f} mm fuera del recorrido "
                             f"[{self.min_mm:g}, {self.max_mm:g}] mm")
        return x

    def range_ps(self) -> tuple[float, float]:
        a, b = self.mm_to_ps(self.min_mm), self.mm_to_ps(self.max_mm)
        return min(a, b), max(a, b)
