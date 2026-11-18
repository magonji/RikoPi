"""Configuration (replaces Riko v5.ini).

* config.toml : hardware, edited by hand; the program never overwrites it.
* state.json  : last scan settings, t0 and data path; saved by the interface on closing
                (as Riko did with the .ini). Its t0_mm takes precedence over the one in config.toml.
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
    raw: dict = field(default_factory=dict)   # remaining sections (e.g. chopper)

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
    """Opens the XPS stage and the SR810 lock-in. Returns (stage, lockin, delay_line, simulated)."""
    sim = cfg.simulate if simulate is None else simulate
    delay = cfg.delay_line()
    s, l = cfg.stage, cfg.lockin
    if sim:
        from .instruments.sim import SimSR810, SimXPS
        stage = SimXPS()
        # copy: the simulated 'physical' time zero does not move when the user changes t0
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
            raise ValueError("[lockin] port is missing in config.toml (serial port of the SR810, e.g. COM3)")
        lockin = SR810.serial(l["port"], l.get("baudrate", 9600), l.get("rtscts", False),
                              invert=l.get("invert", False))
        lockin.idn()  # checks communication (SR810startup.vi)
    lo, hi = stage.travel_limits()
    delay.min_mm = max(lo, s.get("min_mm", lo))
    delay.max_mm = min(hi, s.get("max_mm", hi))
    return stage, lockin, delay, sim
