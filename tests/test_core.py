import socket
import threading

import numpy as np
import pytest

from rikopi import datafile
from rikopi.delay import DelayLine, OutOfRange
from rikopi.fit import ac_sech2, fit_autocorrelation
from rikopi.instruments.sim import SimSR810, SimXPS
from rikopi.instruments.sr810 import parse_sens
from rikopi.instruments.xps import XPS, XPSError
from rikopi.scan import Scan, ScanSettings, SensStep, Zone, sens_for, time_grid


def test_time_grid_linear_and_merge():
    t = time_grid([Zone(-1, 0, 500), Zone(0, 0.02, 5)])
    assert np.allclose(t, [-1, -0.5, 0, 0.005, 0.01, 0.015, 0.02])


def test_time_grid_log():
    t = time_grid([Zone(1, 100, 100, log=True)])
    assert t[0] == 1 and t[-1] == 100
    assert np.isclose(t[1] - t[0], 0.1)
    assert np.all(np.diff(np.diff(t)[:-1]) > 0)  # increasing steps


def test_delay_roundtrip_and_direction():
    d = DelayLine(t0_mm=-121.2, reversed=True, passes=1, min_mm=-150, max_mm=150)
    for t in (-5, 0, 3.7, 150):
        assert np.isclose(d.mm_to_ps(d.ps_to_mm(t)), t)
    assert d.ps_to_mm(1) < d.t0_mm             # reversed: more delay = fewer mm
    assert np.isclose(d.t0_mm - d.ps_to_mm(1), 0.299792458 / 2)
    with pytest.raises(OutOfRange):
        d.check(1000)


def test_sens():
    assert parse_sens("50 mV") == 22 and parse_sens("100uV") == 14 and parse_sens(26) == 26
    steps = [SensStep(-1e9, "50 mV"), SensStep(1, "5 mV")]
    assert sens_for(0, steps) == 22 and sens_for(2, steps) == 19


def test_fit_recovers_pulse():
    t = np.linspace(-1, 1, 401)
    tau = 0.06
    y = ac_sech2(t, 30, 0.012, tau, 0.5) + np.random.default_rng(0).normal(0, 0.1, t.size)
    r = fit_autocorrelation(t, y, "sech2")
    assert abs(r.t0_ps - 0.012) < 0.002
    assert abs(r.pulse_fwhm_fs - 1.7627 * tau * 1000) < 3


def test_sim_scan_and_file_roundtrip(tmp_path):
    d = DelayLine(t0_mm=10, reversed=True, min_mm=-300, max_mm=300)
    stage = SimXPS(speed_mm_s=1e9)
    lia = SimSR810(stage, DelayLine(t0_mm=10, reversed=True), noise_mV=0.01)
    s = ScanSettings(zones=[Zone(-0.5, 0.5, 20), Zone(0.5, 5, 500)], reps=3, wait_ms=0,
                     sens_extra_wait_ms=0, jitter=True, sens=[SensStep(-1e9, "100 mV")])
    sc = Scan(stage, lia, d, s)
    data = sc.run()
    assert data.reps_done == 3 and np.isfinite(data.data_mV).all()
    assert abs(data.t_ps[np.argmax(data.average)]) < 0.03
    p = tmp_path / "x.dat"
    datafile.write(p, data, s, {"t0_mm": 10})
    t, reps, meta = datafile.read(p)
    assert np.allclose(t, data.t_ps) and reps.shape == (3, len(t))
    assert meta["settings"]["reps"] == 3
    assert datafile.unique_path(p).name == "x_2.dat"


def test_xps_protocol():
    """Fake server that mimics the XPS."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    log = []

    def serve():
        c, _ = srv.accept()
        while data := c.recv(4096):
            cmd = data.decode()
            log.append(cmd)
            if cmd.startswith("GroupPositionCurrentGet"):
                c.sendall(b"0,12.5,EndOfAPI")
            elif cmd.startswith("GroupStatusGet"):
                c.sendall(b"0,12,EndOfAPI")
            elif cmd.startswith("GroupMoveAbsolute(Group1.Pos,999"):
                c.sendall(b"-17,EndOfAPI")
            elif cmd.startswith("ErrorStringGet"):
                c.sendall(b"0,Parameter out of range or incorrect,EndOfAPI")
            else:
                c.sendall(b"0,EndOfAPI")

    threading.Thread(target=serve, daemon=True).start()
    x = XPS("127.0.0.1", port)
    x.initialize_and_home()          # already Ready -> does not initialise
    assert x.position() == 12.5
    x.move_abs(1.25)
    assert log[-1] == "GroupMoveAbsolute(Group1.Pos,1.250000)"
    with pytest.raises(XPSError, match="out of range"):
        x.move_abs(999)
    assert not any("GroupInitialize" in c for c in log)
    x.close()


class FakeSerial:
    """Mimics the SR810 serial port: answers queries terminated by CR."""

    def __init__(self):
        self.written = b""
        self.out = b""
        self.replies = {b"*IDN?": b"Stanford_Research_Systems,SR810,s/n12345,ver1.07",
                        b"OUTP? 1": b"-1.234e-3", b"SENS?": b"22", b"OFLT?": b"8", b"FREQ?": b"1000.000"}

    def write(self, data):
        self.written += data
        for cmd in data.split(b"\r"):
            if cmd in self.replies:
                self.out += self.replies[cmd] + b"\r"

    def read_until(self, term):
        r, self.out = self.out, b""
        return r

    def reset_input_buffer(self):
        self.out = b""

    def close(self):
        pass


def test_sr810_over_rs232():
    from rikopi.instruments.sr810 import SR810, SerialTransport

    t = SerialTransport.__new__(SerialTransport)
    t.ser, t.port = FakeSerial(), "COMtest"
    lia = SR810(t, invert=True)
    assert t.ser.written.startswith(b"OUTX 0\r")          # replies over RS232
    assert lia.idn().startswith("Stanford_Research_Systems,SR810")
    assert lia.read_x() == 1.234e-3                       # with inverted sign
    assert lia.get_sens() == 22 and lia.get_time_constant() == 0.1 and lia.get_freq() == 1000
    assert lia.set_sens("50 mV") is False and lia.set_sens("10 mV") is True
    assert t.ser.written.endswith(b"SENS 20\r")
    t.ser.replies.pop(b"*IDN?")
    with pytest.raises(TimeoutError):
        lia.idn()
