"""Configuración (sustituye a Riko v5.ini).

* config.toml : hardware, editado a mano, el programa nunca lo sobrescribe.
* state.json  : últimos ajustes de barrido, t0 y ruta de datos; lo guarda la interfaz al cerrar
                (como hacía Riko con el .ini). Su t0_mm tiene prioridad sobre el de config.toml.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .delay import DelayLine
from .scan import ScanSettings

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass
class Config:
    simulate: bool = False
    stage: dict = field(default_factory=dict)
    lockin: dict = field(default_factory=dict)
    data_dir: str = "~/OKEdata"
    path: Path = DEFAULT_CONFIG
    raw: dict = field(default_factory=dict)   # resto de secciones (p.ej. chopper)

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    @property
    def state_path(self) -> Path:
        return self.path.with_name("state.json")

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2))

    def scan_settings(self) -> ScanSettings:
        st = self.load_state().get("scan")
        return ScanSettings.from_dict(st) if st else ScanSettings()

    def delay_line(self) -> DelayLine:
        s = self.stage
        t0 = self.load_state().get("t0_mm", s.get("t0_mm", 0.0))
        return DelayLine(t0_mm=t0, reversed=s.get("reversed", False), passes=s.get("passes", 1))


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG
    raw = tomllib.loads(p.read_text()) if p.exists() else {}
    return Config(simulate=raw.get("simulate", False), stage=raw.get("stage", {}),
                  lockin=raw.get("lockin", {}), data_dir=raw.get("data_dir", "~/OKEdata"), path=p, raw=raw)


def connect(cfg: Config, simulate: bool | None = None):
    """Abre la stage XPS y el lock-in SR810. Devuelve (stage, lockin, delay_line, simulated)."""
    sim = cfg.simulate if simulate is None else simulate
    delay = cfg.delay_line()
    s, l = cfg.stage, cfg.lockin
    if sim:
        from .instruments.sim import SimSR810, SimXPS
        stage = SimXPS()
        # copia: el cero 'físico' simulado no cambia aunque el usuario mueva t0
        lockin = SimSR810(stage, replace(delay), invert=l.get("invert", False))
    else:
        from .instruments.xps import XPS
        stage = XPS(s.get("host", "192.168.0.254"), s.get("port", 5001),
                    s.get("group", "Group1"), s.get("positioner", "Group1.Pos"))
        stage.initialize_and_home()
        if "velocity" in s:
            stage.set_velocity(s["velocity"], s.get("acceleration", 4 * s["velocity"]))
        from .instruments.sr810 import SR810
        if "port" not in l:
            raise ValueError("falta [lockin] port en config.toml (puerto serie del SR810, p.ej. COM3)")
        lockin = SR810.serial(l["port"], l.get("baudrate", 9600), l.get("rtscts", False),
                              invert=l.get("invert", False))
        lockin.idn()  # comprueba la comunicación (SR810startup.vi)
    lo, hi = stage.travel_limits()
    delay.min_mm = max(lo, s.get("min_mm", lo))
    delay.max_mm = min(hi, s.get("max_mm", hi))
    return stage, lockin, delay, sim
