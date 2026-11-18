"""Punto de entrada:  python -m rikopi [--sim] [--config ruta/config.toml]"""
from __future__ import annotations

import argparse
import sys

from . import config


def main() -> int:
    ap = argparse.ArgumentParser(prog="rikopi", description="Adquisición OKE (SR810 + Newport XPS)")
    ap.add_argument("--sim", action="store_true", help="modo simulación, sin hardware")
    ap.add_argument("--config", help="ruta a config.toml (por defecto, el de la carpeta del programa)")
    args = ap.parse_args()
    cfg = config.load(args.config)
    from .gui import run
    return run(cfg, simulate=True if args.sim else None)


if __name__ == "__main__":
    sys.exit(main())
