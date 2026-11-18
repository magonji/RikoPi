"""Entry point:  python -m rikopi [--sim] [--config path/config.toml]"""
from __future__ import annotations

import argparse
import sys

from . import config


def main() -> int:
    ap = argparse.ArgumentParser(prog="rikopi", description="OKE data acquisition (SR810 + Newport XPS)")
    ap.add_argument("--sim", action="store_true", help="simulation mode, no hardware")
    ap.add_argument("--config", help="path to config.toml (default: the one in the program folder)")
    args = ap.parse_args()
    cfg = config.load(args.config)
    from .gui import run
    return run(cfg, simulate=True if args.sim else None)


if __name__ == "__main__":
    sys.exit(main())
