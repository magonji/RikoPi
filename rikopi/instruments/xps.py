"""Driver mínimo para el controlador Newport XPS por TCP/IP.

Sustituye a los VIs XPSstartup / XPSinit / XPShome / XPSgetPosn / MotoMove / TCPwrtrd.
El XPS acepta llamadas de función en texto (puerto 5001), p.ej.
    GroupMoveAbsolute(Group1.Pos,12.5)
y contesta con "<código>,<valores...>,EndOfAPI". Código 0 = OK.
"""
from __future__ import annotations

import socket
import threading
import time


class XPSError(RuntimeError):
    pass


# Estados de GroupStatusGet relevantes
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

    # --- protocolo ---
    def _call(self, cmd: str) -> list[str]:
        with self._lock:
            self.sock.sendall(cmd.encode("ascii"))
            buf = b""
            while b"EndOfAPI" not in buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise XPSError(f"conexión cerrada por el XPS durante '{cmd}'")
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

    # --- funciones ---
    def status(self) -> int:
        return int(self._call(f"GroupStatusGet({self.group},int *)")[0])

    def initialize_and_home(self) -> None:
        """Equivalente a XPSstartup: inicializa y hace homing sólo si hace falta."""
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
            raise XPSError(f"el grupo {self.group} no está listo (estado {st})")

    def position(self) -> float:
        return float(self._call(f"GroupPositionCurrentGet({self.positioner},double *)")[0])

    def move_abs(self, mm: float) -> None:
        """Movimiento absoluto bloqueante (el XPS contesta al terminar el movimiento)."""
        self._call(f"GroupMoveAbsolute({self.positioner},{mm:.6f})")

    def travel_limits(self) -> tuple[float, float]:
        lo, hi = self._call(f"PositionerUserTravelLimitsGet({self.positioner},double *,double *)")
        return float(lo), float(hi)

    def set_velocity(self, velocity: float, acceleration: float) -> None:
        """Equivalente a XPSsetparams (PositionerSGammaParametersSet)."""
        cur = self._call(f"PositionerSGammaParametersGet({self.positioner},double *,double *,double *,double *)")
        jmin, jmax = cur[2], cur[3]
        self._call(f"PositionerSGammaParametersSet({self.positioner},{velocity},{acceleration},{jmin},{jmax})")
