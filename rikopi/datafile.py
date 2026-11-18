"""Escritura/lectura de ficheros de datos (sustituye a FilePrep / xXYYheader / MakexXlgYYYarray).

Formato: texto plano, cabecera con líneas que empiezan por '#', después columnas separadas por
tabuladores:  t_fs  rep1  rep2 ...  repN  avg
Se lee directamente con numpy.loadtxt(path) o pandas.read_csv(path, sep='\\t', comment='#').
La penúltima línea de la cabecera contiene todos los ajustes en JSON (para recargar un barrido).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from .scan import ScanData, ScanSettings, sens_summary


def unique_path(path: str | os.PathLike) -> Path:
    """Si el fichero existe, añade _2, _3, ... (nunca se sobrescriben datos)."""
    p = Path(path)
    if not p.exists():
        return p
    i = 2
    while (q := p.with_name(f"{p.stem}_{i}{p.suffix}")).exists():
        i += 1
    return q


def write(path: str | os.PathLike, data: ScanData, settings: ScanSettings, extra: dict | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = data.reps_done
    meta = {"settings": settings.to_dict(), "reps_done": n, **(extra or {})}
    header = [
        "RikoPi OKE data",
        f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Reps: {n}/{settings.reps}   Wait: {settings.wait_ms:g} ms   Jitter: {settings.jitter}",
        "Zones: " + "; ".join(f"{z.start_ps:g}..{z.end_ps:g} ps step {z.step_fs:g} fs{' (log)' if z.log else ''}"
                              for z in settings.zones),
        "Sens: " + sens_summary(settings.sens),
        *(f"{k}: {v}" for k, v in (extra or {}).items()),
        "Notes: " + settings.notes.replace("\n", " / "),
        "JSON: " + json.dumps(meta),
        "\t".join(["t_fs"] + [f"rep{i + 1}" for i in range(n)] + ["avg"]),
    ]
    cols = [data.t_ps * 1000] + [data.data_mV[i] for i in range(n)] + [data.average]
    tmp = p.with_suffix(p.suffix + ".tmp")
    np.savetxt(tmp, np.column_stack(cols), fmt="%.6g", delimiter="\t",
               header="\n".join(header), comments="# ")
    os.replace(tmp, p)  # escritura atómica: el fichero nunca queda a medias


def read(path: str | os.PathLike) -> tuple[np.ndarray, np.ndarray, dict]:
    """Devuelve (t_ps, datos_mV[reps, n], meta)."""
    meta = {}
    with open(path) as f:
        for line in f:
            if not line.startswith("#"):
                break
            if line.startswith("# JSON: "):
                meta = json.loads(line[len("# JSON: "):])
    arr = np.loadtxt(path, comments="#", ndmin=2)
    return arr[:, 0] / 1000, arr[:, 1:-1].T, meta
