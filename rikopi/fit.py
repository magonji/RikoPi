"""Autocorrelation fit (equivalent to CorrFit.vi): gives time zero and the pulse duration."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit

# Ratio FWHM(autocorrelation)/FWHM(pulse)
DECONV = {"sech2": 1.5427, "gauss": 1.4142}
SECH_FWHM = 1.762747  # FWHM of sech^2(t/tau) in units of tau


def ac_sech2(t, A, t0, tau, B):
    """Intensity autocorrelation of a sech^2(t/tau) pulse."""
    x = np.asarray((t - t0) / tau, dtype=float)
    out = np.ones_like(x)
    m = np.abs(x) > 1e-3
    xm = np.clip(x[m], -300, 300)
    out[m] = 3 * (xm / np.tanh(xm) - 1) / np.sinh(xm) ** 2
    out[np.abs(x) > 300] = 0.0
    return A * out + B


def ac_gauss(t, A, t0, w, B):
    """Gaussian autocorrelation with FWHM w."""
    return A * np.exp(-4 * np.log(2) * ((t - t0) / w) ** 2) + B


@dataclass
class FitResult:
    model: str
    t0_ps: float
    ac_fwhm_fs: float
    pulse_fwhm_fs: float
    amplitude: float
    offset: float
    curve: tuple[np.ndarray, np.ndarray]

    def summary(self) -> str:
        return (f"{self.model}: t0 = {self.t0_ps * 1000:.1f} fs, FWHM AC = {self.ac_fwhm_fs:.1f} fs, "
                f"pulse = {self.pulse_fwhm_fs:.1f} fs")


def fit_autocorrelation(t_ps, y, model: str = "sech2", window_ps: float | None = 1.0) -> FitResult:
    t_ps, y = np.asarray(t_ps, float), np.asarray(y, float)
    ok = np.isfinite(y)
    t_ps, y = t_ps[ok], y[ok]
    i = int(np.argmax(np.abs(y)))
    tpk, A0 = t_ps[i], y[i]
    if window_ps:
        m = np.abs(t_ps - tpk) <= window_ps
        t_ps, y = t_ps[m], y[m]
    half = np.abs(y) >= abs(A0) / 2
    w0 = max(np.ptp(t_ps[half]) if half.sum() > 1 else 0.1, 0.01)
    if model == "sech2":
        p, _ = curve_fit(ac_sech2, t_ps, y, p0=[A0, tpk, w0 / 2.72, 0])
        A, t0, tau, B = p
        ac_fwhm = abs(tau) * SECH_FWHM * DECONV["sech2"]
        f = ac_sech2
    elif model == "gauss":
        p, _ = curve_fit(ac_gauss, t_ps, y, p0=[A0, tpk, w0, 0])
        A, t0, ac_fwhm, B = p
        ac_fwhm = abs(ac_fwhm)
        f = ac_gauss
    else:
        raise ValueError(model)
    tt = np.linspace(t_ps.min(), t_ps.max(), 500)
    return FitResult(model, t0, ac_fwhm * 1000, ac_fwhm / DECONV[model] * 1000, A, B, (tt, f(tt, *p)))
