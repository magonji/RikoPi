"""Driver for the Stanford Research SR810 lock-in amplifier (also works with the SR830).

Replaces the VIs SR810startup / SR810readX / SR810sens / SR810timeconst / SR810freq.
Communication over RS232 (pyserial, no National Instruments drivers).
"""
from __future__ import annotations

import threading
import time

# SR810 sensitivity table (command SENS i, i = 0..26), in volts
SENSITIVITIES_V = [
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3, 1.0,
]

# Time constant table (command OFLT i, i = 0..19), in seconds
TIME_CONSTANTS_S = [
    10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 3e-3, 10e-3, 30e-3, 100e-3, 300e-3,
    1, 3, 10, 30, 100, 300, 1e3, 3e3, 10e3, 30e3,
]


def sens_label(code: int) -> str:
    v = SENSITIVITIES_V[code]
    for unit, f in (("V", 1), ("mV", 1e-3), ("uV", 1e-6), ("nV", 1e-9)):
        if v >= f:
            return f"{v / f:g} {unit}"
    return f"{v:g} V"


SENS_LABELS = [sens_label(i) for i in range(len(SENSITIVITIES_V))]


def parse_sens(value) -> int:
    """Accepts a code (int) or a string such as '50 mV', '50mV', '100uV' and returns the SENS code."""
    if isinstance(value, int):
        if not 0 <= value < len(SENSITIVITIES_V):
            raise ValueError(f"sensitivity code out of range: {value}")
        return value
    s = str(value).replace(" ", "").replace("µ", "u").lower()
    for code, label in enumerate(SENS_LABELS):
        if label.replace(" ", "").lower() == s:
            return code
    raise ValueError(f"unrecognised sensitivity: {value!r} (options: {', '.join(SENS_LABELS)})")


class SerialTransport:
    """RS232 via pyserial. The SR810 accepts commands terminated by CR or LF and replies terminated by CR.
    The SR810 port is a DB25 wired as DCE: use a straight-through (not null-modem) cable to the adapter."""

    def __init__(self, port: str, baudrate: int = 9600, timeout_s: float = 3.0, rtscts: bool = False):
        import serial  # pyserial

        self.ser = serial.Serial(port, baudrate=baudrate, bytesize=serial.EIGHTBITS,
                                 parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                 timeout=timeout_s, rtscts=rtscts)
        self.port = port
        self.ser.write(b"\r")      # discard any half-received command in the SR810 buffer
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def write(self, cmd: str) -> None:
        self.ser.write((cmd + "\r").encode("ascii"))

    def query(self, cmd: str) -> str:
        self.ser.reset_input_buffer()
        self.write(cmd)
        reply = self.ser.read_until(b"\r")
        if not reply.endswith(b"\r"):
            raise TimeoutError(f"the SR810 does not reply to '{cmd}' on {self.port} "
                               f"(check the cable, the baud rate and that RS232 is selected on the front panel)")
        return reply.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self.ser.close()


class SR810:
    """SR810 lock-in over RS232. Create it with SR810.serial(port, baudrate)."""

    def __init__(self, transport, invert: bool = False):
        self.t = transport
        self.invert = invert
        self._lock = threading.Lock()
        self._sens_cache: int | None = None
        # OUTX 0: the SR810 replies over RS232 (not GPIB)
        self.write("OUTX 0")

    @classmethod
    def serial(cls, port: str, baudrate: int = 9600, rtscts: bool = False, timeout_s: float = 3.0,
               invert: bool = False) -> "SR810":
        return cls(SerialTransport(port, baudrate, timeout_s, rtscts), invert)

    # --- low level ---
    def write(self, cmd: str) -> None:
        with self._lock:
            self.t.write(cmd)

    def query(self, cmd: str) -> str:
        with self._lock:
            return self.t.query(cmd)

    def close(self) -> None:
        self.t.close()

    # --- high level ---
    def idn(self) -> str:
        return self.query("*IDN?")

    def read_x(self) -> float:
        """X output in volts (sign inverted if invert=True)."""
        x = float(self.query("OUTP? 1"))
        return -x if self.invert else x

    def get_sens(self) -> int:
        self._sens_cache = int(self.query("SENS?"))
        return self._sens_cache

    def set_sens(self, code: int) -> bool:
        """Changes the sensitivity. Returns True if it actually changed."""
        code = parse_sens(code)
        if self._sens_cache == code:
            return False
        self.write(f"SENS {code}")
        self._sens_cache = code
        return True

    def get_time_constant(self) -> float:
        return TIME_CONSTANTS_S[int(self.query("OFLT?"))]

    def get_freq(self) -> float:
        return float(self.query("FREQ?"))

    def set_freq(self, hz: float) -> None:
        """Only has an effect with the internal reference (e.g. if the lock-in drives the chopper)."""
        self.write(f"FREQ {hz:.4f}")


if __name__ == "__main__":
    # Quick connection test:  python -m rikopi.instruments.sr810 COM3 9600
    import sys

    if len(sys.argv) < 2:
        from serial.tools import list_ports

        print("Usage: python -m rikopi.instruments.sr810 PORT [BAUDRATE]\nAvailable serial ports:")
        for p in list_ports.comports():
            print(f"  {p.device:20s} {p.description}")
        sys.exit(1)
    lia = SR810.serial(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 9600)
    print("IDN :", lia.idn())
    print("SENS:", SENS_LABELS[lia.get_sens()])
    print("τ   :", lia.get_time_constant(), "s")
    print("FREQ:", lia.get_freq(), "Hz")
    print("X   :", lia.read_x() * 1e3, "mV")
    lia.close()
