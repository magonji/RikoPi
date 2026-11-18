# RikoPi — OKE data acquisition in Python

Replaces **Riko v5.1** (LabVIEW): pump-probe delay scans using a **Newport XPS** stage (TCP/IP)
and an **SR810** lock-in amplifier (RS232).
No National Instruments drivers are needed.

## Installation (lab PC)

1. Python ≥ 3.11.
2. Network: the PC must be on the same subnet as the XPS (e.g. static IP `192.168.0.100`,
   netmask `255.255.255.0`; the XPS is at `192.168.0.254`).
3. SR810 over RS232: see [Connecting the SR810 (FTDI USB-RS232 adapter)](#connecting-the-sr810-ftdi-usb-rs232-adapter).
4. From this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate   (Linux/Mac)
   pip install -e .
   ```

## Connecting the SR810 (FTDI USB-RS232 adapter)

FTDI-based adapters are the most reliable choice. Before starting RikoPi:

1. **Driver**
   * **Windows 10/11:** the driver installs itself when the adapter is plugged in (it can take a
     minute). If no COM port appears, download the **VCP** driver from ftdichip.com.
   * **Mac / Linux:** the driver is already built into the operating system.

2. **Find the COM port**
   On Windows: *Device Manager* → *Ports (COM & LPT)* → "USB Serial Port (COM**x**)". Put that
   `COMx` in `config.toml`:
   ```toml
   [lockin]
   port = "COM4"
   ```
   Windows ties the number to the adapter *and* the USB socket, so **always plug it into the same
   USB socket**, otherwise the number may change. To fix the number: *Properties* →
   *Port Settings* → *Advanced* → *COM Port Number*.

3. **Optional: reduce the latency**
   In the same *Advanced* window, set the **Latency Timer** to **1–2 ms** (the default is 16 ms).
   The FTDI chip waits that long before passing each reply to the computer, so with 16 ms every
   lock-in reading takes ~15 ms longer. With a 200 ms wait per point it hardly matters, but it
   costs nothing.

4. **Cable**
   The SR810 has a **DB25** connector, and the adapter most likely ends in a **male DB9**.
   You need a **straight-through (not null-modem) female DB9 → male DB25** cable or adapter: the
   SR810 port is wired as DCE. With a null-modem cable the SR810 will never reply.

5. **On the SR810**
   Press **SETUP**, select the **RS232** interface and choose the baud rate (9600, or 19200), no
   parity. Set the same value as `baudrate` in `config.toml`.

6. **Test the connection** before starting the program:
   ```
   python -m rikopi.instruments.sr810           # lists the serial ports (the FTDI should appear)
   python -m rikopi.instruments.sr810 COM4 9600 # should reply Stanford_Research_Systems,SR810,...
   ```

If it does not reply, check in this order: the cable (straight-through vs null-modem), the baud
rate on the SR810 front panel, and the COM port number. If characters get lost or replies look
garbled, try 9600 baud or set `rtscts = true` in `config.toml`.

## Usage

```
rikopi           # with hardware (as set in config.toml)
rikopi --sim     # simulation, no hardware
```

If the instruments cannot be found at start-up, the program offers to open in simulation mode
(as Riko did).

* **Scan**: delay zones (start, end, step; *Log* = geometrically increasing step, where the step
  given is the first one), lock-in sensitivity table by delay range, repeats, wait per point and
  jitter. The whole file is saved at the end of every repeat (never overwritten: if it exists,
  `_2`, `_3`… is appended). *Fit autocorrelation* (sech² or Gaussian) gives t0 and the pulse
  duration, and *Use as t0* corrects time zero.
* **Stage / Scope**: position in mm and ps, move to a position, set t0, lock-in sensitivity,
  continuous reading of X, and back-and-forth stage motion for alignment.

On closing, the settings and t0 are saved in `state.json`.

## Configuration (`config.toml`)

Equivalences with `Riko v5.ini`: `XPSaddr → stage.host`, `Axis = Group1/- → group = "Group1"` +
`reversed = true`, `/x2 → passes = 2`. The SR810, which used to be on GPIB (`SR810addr = 12`), is
now on RS232 (`lockin.port`).

**Before measuring, check:**
- `positioner`: the positioner name on the XPS (see the XPS web interface). Usually `Group1.Pos`.
- `t0_mm`: **must be recalibrated** (the `t00` in the .ini cannot be converted reliably, as it is
  unclear how LabVIEW combined its sign with the `/-`). The easiest way: a short scan around the
  autocorrelation → *Fit* → *Use as t0*.
- The sensitivities in the .ini (`Sens0=8`…) were indices into a LabVIEW menu; in RikoPi they are
  chosen directly by value (`50 mV`, etc.).

## Chopper

`[chopper]` in `config.toml`, as in SR810slowChop.vi: 10 min after a scan finishes, the SR810
reference frequency is lowered to 120 Hz. When the next scan (or the scope) starts, the previous
frequency is restored and the program waits `spinup_s` for the chopper to get back up to speed.
It can be switched off with the tick box on the Scan tab.

## Data format

Plain text with a `#` header and tab-separated columns: `t_fs rep1 … repN avg` (mV).
```python
import numpy as np
d = np.loadtxt("file.dat")            # d[:,0] = t (fs), d[:,-1] = average
from rikopi import datafile
t_ps, reps, meta = datafile.read("file.dat")   # meta = all the scan settings
```

## Structure

| Module | Replaces |
|---|---|
| `rikopi/instruments/sr810.py` | SR810startup/readX/sens/timeconst/freq.vi |
| `rikopi/instruments/xps.py` | XPSstartup/init/home/getPosn, MotoMove, TCPwrtrd.vi |
| `rikopi/delay.py` | mm ↔ ps conversion from MotoMove/CheckRange.vi |
| `rikopi/scan.py` | Riko main loop + Smpl, Jitr, deJitr, zones |
| `rikopi/datafile.py` | FilePrep, xXYYheader, MakexXlgYYYarray.vi |
| `rikopi/fit.py` | CorrFit.vi |
| `rikopi/gui.py` | Riko front panel |

Tests: `pip install -e ".[test]" && pytest`.

## Not ported

OKEdoke (analysis). Nor the MGn2 Nanomover, the boxcar + NI-DAQ, the cameras (SXVR-H9, IMAQdx),
the ITC503 or the ASG, which are no longer used in the lab.
