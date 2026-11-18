"""Minimal driver for the Newport XPS controller over TCP/IP.

Replaces the VIs XPSstartup / XPSinit / XPShome / XPSgetPosn / MotoMove / TCPwrtrd.
The XPS accepts function calls as text (port 5001), e.g.
    GroupMoveAbsolute(Group1.Pos,12.5)
and replies with "<code>,<values...>,EndOfAPI". Code 0 = OK.
"""
from __future__ import annotations

import socket
import threading
import time


class XPSError(RuntimeError):
    pass


# Relevant GroupStatusGet states
READY_STATES = set(range(10, 19))  # 10..18 = Ready (from homing, motion, ...)
NOT_INIT_STATES = set(range(0, 10)) | {50, 63, 64, 65, 66, 67, 71, 72}
NOT_REFERENCED = 42


class XPS:
    def __init__(self, host: str = "192.168.0.254", port: int = 5001,
                 group: str = "Group1", positioner: str = "Group1.Pos", timeout_s: float = 30.0):
        self.group = group
        self.positioner = positioner
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(timeout_s)

    # --- protocol ---
    def _call(self, cmd: str) -> list[str]:
        with self._lock:
            self.sock.sendall(cmd.encode("ascii"))
            buf = b""
            while b"EndOfAPI" not in buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise XPSError(f"connection closed by the XPS during '{cmd}'")
                buf += chunk
        reply = buf.decode("ascii", errors="replace").split("EndOfAPI")[0].strip().rstrip(",")
        parts = [p.strip() for p in reply.split(",")]
        code = int(parts[0])
        if code != 0:
            raise XPSError(f"{cmd} -> error {code}: {self._error_string(code)}")
        return parts[1:]

    def _error_string(self, code: int) -> str:
        try:
            with self._lock:
                self.sock.sendall(f"ErrorStringGet({code},char *)".encode())
                buf = b""
                while b"EndOfAPI" not in buf:
                    buf += self.sock.recv(4096)
            return buf.decode(errors="replace").split(",", 1)[1].replace("EndOfAPI", "").strip(", ")
        except Exception:
            return "?"

    def close(self) -> None:
        self.sock.close()

    # --- functions ---
    def status(self) -> int:
        return int(self._call(f"GroupStatusGet({self.group},int *)")[0])

    def initialize_and_home(self) -> None:
        """Equivalent to XPSstartup: initialises and homes only if needed."""
        st = self.status()
        if st in READY_STATES:
            return
        if st in NOT_INIT_STATES:
            self._call(f"GroupInitialize({self.group})")
            st = self.status()
        if st == NOT_REFERENCED:
            self._call(f"GroupHomeSearch({self.group})")
        st = self.status()
        if st not in READY_STATES:
            raise XPSError(f"group {self.group} is not ready (state {st})")

    def position(self) -> float:
        return float(self._call(f"GroupPositionCurrentGet({self.positioner},double *)")[0])

    def move_abs(self, mm: float) -> None:
        """Blocking absolute move (the XPS replies when the move has finished)."""
        self._call(f"GroupMoveAbsolute({self.positioner},{mm:.6f})")

    def travel_limits(self) -> tuple[float, float]:
        lo, hi = self._call(f"PositionerUserTravelLimitsGet({self.positioner},double *,double *)")
        return float(lo), float(hi)

    def set_velocity(self, velocity: float, acceleration: float) -> None:
        """Equivalent to XPSsetparams (PositionerSGammaParametersSet)."""
        cur = self._call(f"PositionerSGammaParametersGet({self.positioner},double *,double *,double *,double *)")
        jmin, jmax = cur[2], cur[3]
        self._call(f"PositionerSGammaParametersSet({self.positioner},{velocity},{acceleration},{jmin},{jmax})")
