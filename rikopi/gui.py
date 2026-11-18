"""Graphical user interface (replaces the Riko v5 front panel).

'Scan' tab: step zones, sensitivities, repeats, start/stop, autocorrelation fit.
'Stage / Scope' tab: move the stage, set t0, continuous lock-in reading (Scope mode) with the
option of moving the stage back and forth for alignment.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from . import datafile
from .config import Config, connect
from .fit import fit_autocorrelation
from .instruments.sr810 import SENS_LABELS
from .scan import Scan, ScanData, ScanSettings, SensStep, Zone, estimate_duration_s, time_grid


class Signals(QtCore.QObject):
    point = QtCore.Signal(int, int, float, float)
    rep_done = QtCore.Signal(object)
    finished = QtCore.Signal(object, str)    # (data, error message or "")
    scope = QtCore.Signal(float, float, float)  # (time, position mm, signal mV)
    scope_finished = QtCore.Signal(str)


def dspin(lo, hi, val, dec=3, step=1.0, suffix=""):
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    if suffix:
        w.setSuffix(" " + suffix)
    return w


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, cfg: Config, simulate: bool | None = None):
        super().__init__()
        self.cfg = cfg
        self.stage, self.lockin, self.delay, self.sim = connect(cfg, simulate)
        self.chopper_cfg = cfg.section("chopper")
        self._chopper_prev_freq: float | None = None
        self.chopper_timer = QtCore.QTimer(self, singleShot=True, timeout=self._slow_chopper)
        self.state = cfg.load_state()
        self.scan: Scan | None = None
        self.scan_path: Path | None = None
        self._scope_stop = threading.Event()
        self._scope_thread: threading.Thread | None = None
        self.sig = Signals()
        self.sig.point.connect(self._on_point)
        self.sig.rep_done.connect(self._on_rep_done)
        self.sig.finished.connect(self._on_finished)
        self.sig.scope.connect(self._on_scope)
        self.sig.scope_finished.connect(self._on_scope_finished)

        self.setWindowTitle("RikoPi" + ("  [SIMULATION]" if self.sim else ""))
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_scan_tab(), "Scan")
        tabs.addTab(self._build_stage_tab(), "Stage / Scope")
        self.tabs = tabs
        tabs.setMaximumWidth(460)

        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "delay", "ps")
        self.plot.setLabel("left", "signal", "mV")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend()
        self.curve_reps: list[pg.PlotDataItem] = []
        self.curve_avg = self.plot.plot([], [], pen=pg.mkPen("k", width=2), name="average")
        self.curve_cur = self.plot.plot([], [], pen=pg.mkPen((220, 50, 50), width=1.5), name="current")
        self.curve_fit = self.plot.plot([], [], pen=pg.mkPen((30, 120, 220), width=2, style=Qt.DashLine),
                                        name="fit")
        self.scope_plot = pg.PlotWidget(background="w")
        self.scope_plot.setLabel("bottom", "time", "s")
        self.scope_plot.setLabel("left", "X", "mV")
        self.scope_curve = self.scope_plot.plot([], [], pen=pg.mkPen((220, 50, 50), width=1.5))
        self.plots = QtWidgets.QStackedWidget()
        self.plots.addWidget(self.plot)
        self.plots.addWidget(self.scope_plot)
        tabs.currentChanged.connect(lambda i: self.plots.setCurrentIndex(i))

        split = QtWidgets.QSplitter()
        split.addWidget(tabs)
        split.addWidget(self.plots)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)
        self.resize(1300, 800)

        self.pos_timer = QtCore.QTimer(self, interval=1000, timeout=self._refresh_position)
        self.pos_timer.start()
        self._refresh_position()
        self._update_estimate()
        self.status(f"Connected ({'simulation' if self.sim else 'hardware'}). "
                    f"Travel range: {self.delay.range_ps()[0]:.1f} … {self.delay.range_ps()[1]:.1f} ps")

    # ------------------------------------------------------------------ layout
    def _build_scan_tab(self) -> QtWidgets.QWidget:
        s = self.cfg.scan_settings()
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        f = QtWidgets.QHBoxLayout()
        default = self.state.get("last_path") or str(
            Path(self.cfg.data_dir).expanduser() / time.strftime("%y%m%d") / "oke.dat")
        self.path_edit = QtWidgets.QLineEdit(default)
        b = QtWidgets.QPushButton("…")
        b.clicked.connect(self._browse)
        f.addWidget(QtWidgets.QLabel("File"))
        f.addWidget(self.path_edit)
        f.addWidget(b)
        lay.addLayout(f)
        self.notes = QtWidgets.QLineEdit(s.notes)
        self.notes.setPlaceholderText("Notes (sample, temperature, …) — saved in the file header")
        lay.addWidget(self.notes)

        # Zones
        lay.addWidget(QtWidgets.QLabel("<b>Zones</b> (log: increasing step; the step given is the first one)"))
        self.zones = QtWidgets.QTableWidget(0, 4)
        self.zones.setHorizontalHeaderLabels(["Start (ps)", "End (ps)", "Step (fs)", "Log"])
        self.zones.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        for z in s.zones:
            self._add_zone(z)
        self.zones.itemChanged.connect(self._update_estimate)
        lay.addWidget(self.zones)
        lay.addLayout(self._row_buttons(lambda: self._add_zone(None), self.zones))

        # Sensitivities
        lay.addWidget(QtWidgets.QLabel("<b>Lock-in sensitivity</b> (from each delay onwards)"))
        self.sens = QtWidgets.QTableWidget(0, 2)
        self.sens.setHorizontalHeaderLabels(["From (ps)", "Sensitivity"])
        self.sens.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        for st in s.sens:
            self._add_sens(st)
        lay.addWidget(self.sens)
        lay.addLayout(self._row_buttons(lambda: self._add_sens(None), self.sens))

        form = QtWidgets.QFormLayout()
        self.reps = QtWidgets.QSpinBox()
        self.reps.setRange(1, 10000)
        self.reps.setValue(s.reps)
        self.wait = dspin(0, 1e5, s.wait_ms, 0, 50, "ms")
        self.extra_wait = dspin(0, 1e5, s.sens_extra_wait_ms, 0, 100, "ms")
        self.pause = dspin(0, 1e5, s.pause_between_reps_s, 1, 1, "s")
        self.jitter = QtWidgets.QCheckBox("±")
        self.jitter.setChecked(s.jitter)
        self.jitter_nm = dspin(0, 1e5, s.jitter_nm, 0, 50, "nm")
        jl = QtWidgets.QHBoxLayout()
        jl.addWidget(self.jitter)
        jl.addWidget(self.jitter_nm)
        form.addRow("Repeats", self.reps)
        form.addRow("Wait per point", self.wait)
        form.addRow("Extra wait on sens. change", self.extra_wait)
        form.addRow("Pause between repeats", self.pause)
        form.addRow("Stage jitter", jl)
        for x in (self.reps, self.wait, self.pause):
            x.valueChanged.connect(self._update_estimate)
        lay.addLayout(form)
        c = self.chopper_cfg
        self.chk_slow_chop = QtWidgets.QCheckBox(
            f"Slow the chopper to {c.get('slow_Hz', 120):g} Hz {c.get('delay_min', 10):g} min after finishing")
        self.chk_slow_chop.setChecked(bool(c.get("slow_after_scan", False)))
        lay.addWidget(self.chk_slow_chop)
        self.estimate = QtWidgets.QLabel()
        lay.addWidget(self.estimate)

        bl = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("▶  Start")
        self.btn_stop = QtWidgets.QPushButton("■  Stop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_scan)
        self.btn_stop.clicked.connect(self.stop_scan)
        bl.addWidget(self.btn_start)
        bl.addWidget(self.btn_stop)
        lay.addLayout(bl)
        self.progress = QtWidgets.QProgressBar()
        lay.addWidget(self.progress)

        fl = QtWidgets.QHBoxLayout()
        self.fit_model = QtWidgets.QComboBox()
        self.fit_model.addItems(["sech2", "gauss"])
        bf = QtWidgets.QPushButton("Fit autocorrelation")
        bf.clicked.connect(self.do_fit)
        self.btn_t0_from_fit = QtWidgets.QPushButton("Use as t0")
        self.btn_t0_from_fit.setEnabled(False)
        self.btn_t0_from_fit.clicked.connect(self._t0_from_fit)
        fl.addWidget(self.fit_model)
        fl.addWidget(bf)
        fl.addWidget(self.btn_t0_from_fit)
        lay.addLayout(fl)
        self.fit_label = QtWidgets.QLabel()
        self.fit_label.setWordWrap(True)
        lay.addWidget(self.fit_label)
        bo = QtWidgets.QPushButton("Open data file…")
        bo.clicked.connect(self.open_data)
        lay.addWidget(bo)
        lay.addStretch()
        return w

    def _build_stage_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        g = QtWidgets.QGroupBox("Stage")
        form = QtWidgets.QFormLayout(g)
        self.pos_label = QtWidgets.QLabel("—")
        self.pos_label.setStyleSheet("font-size: 16px; font-weight: bold")
        form.addRow("Position", self.pos_label)
        self.goto_ps = dspin(-1e5, 1e5, 0.0, 3, 0.1, "ps")
        b = QtWidgets.QPushButton("Go")
        b.clicked.connect(lambda: self._move(self.delay.check(self.goto_ps.value())))
        form.addRow(self._hbox(self.goto_ps, b))
        self.goto_mm = dspin(-1e4, 1e4, 0.0, 4, 1.0, "mm")
        b = QtWidgets.QPushButton("Go")
        b.clicked.connect(lambda: self._move(self.goto_mm.value()))
        form.addRow(self._hbox(self.goto_mm, b))
        self.t0_spin = dspin(-1e4, 1e4, self.delay.t0_mm, 4, 0.01, "mm")
        self.t0_spin.valueChanged.connect(self._set_t0)
        form.addRow("t0 (position of t = 0)", self.t0_spin)
        b = QtWidgets.QPushButton("Set current position as t0")
        b.clicked.connect(lambda: self.t0_spin.setValue(self.stage.position()))
        form.addRow(b)
        lay.addWidget(g)

        g = QtWidgets.QGroupBox("Lock-in")
        form = QtWidgets.QFormLayout(g)
        self.sens_combo = QtWidgets.QComboBox()
        self.sens_combo.addItems(SENS_LABELS)
        try:
            self.sens_combo.setCurrentIndex(self.lockin.get_sens())
        except Exception:
            pass
        self.sens_combo.activated.connect(lambda i: self.lockin.set_sens(i))
        form.addRow("Sensitivity", self.sens_combo)
        self.lia_info = QtWidgets.QLabel()
        form.addRow(self.lia_info)
        b = QtWidgets.QPushButton("Read settings")
        b.clicked.connect(self._read_lia)
        form.addRow(b)
        lay.addWidget(g)

        g = QtWidgets.QGroupBox("Scope (continuous reading)")
        form = QtWidgets.QFormLayout(g)
        self.scope_wait = dspin(10, 1e5, 100, 0, 50, "ms")
        form.addRow("Interval", self.scope_wait)
        self.scope_len = QtWidgets.QSpinBox()
        self.scope_len.setRange(10, 100000)
        self.scope_len.setValue(600)
        form.addRow("Points shown", self.scope_len)
        self.align = QtWidgets.QCheckBox("Move stage back and forth (alignment)")
        form.addRow(self.align)
        self.align_a = dspin(-1e4, 1e4, self.delay.ps_to_mm(-1), 4, 1, "mm")
        self.align_b = dspin(-1e4, 1e4, self.delay.ps_to_mm(1), 4, 1, "mm")
        form.addRow("Between", self._hbox(self.align_a, QtWidgets.QLabel("and"), self.align_b))
        self.btn_scope = QtWidgets.QPushButton("▶  Scope")
        self.btn_scope.setCheckable(True)
        self.btn_scope.toggled.connect(self._toggle_scope)
        form.addRow(self.btn_scope)
        self.scope_value = QtWidgets.QLabel("—")
        self.scope_value.setStyleSheet("font-size: 22px; font-weight: bold")
        form.addRow("X", self.scope_value)
        lay.addWidget(g)
        lay.addStretch()
        return w

    @staticmethod
    def _hbox(*widgets):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            h.addWidget(x)
        return w

    def _row_buttons(self, add, table):
        h = QtWidgets.QHBoxLayout()
        a, r = QtWidgets.QPushButton("+"), QtWidgets.QPushButton("−")
        a.clicked.connect(add)
        a.clicked.connect(self._update_estimate)
        r.clicked.connect(lambda: table.removeRow(table.currentRow() if table.currentRow() >= 0
                                                  else table.rowCount() - 1))
        r.clicked.connect(self._update_estimate)
        h.addWidget(a)
        h.addWidget(r)
        h.addStretch()
        return h

    def _add_zone(self, z: Zone | None):
        t = self.zones
        if z is None:
            last = float(t.item(t.rowCount() - 1, 1).text()) if t.rowCount() else 0.0
            z = Zone(last, last + 10, 100)
        r = t.rowCount()
        t.blockSignals(True)
        t.insertRow(r)
        for c, v in enumerate((z.start_ps, z.end_ps, z.step_fs)):
            t.setItem(r, c, QtWidgets.QTableWidgetItem(f"{v:g}"))
        it = QtWidgets.QTableWidgetItem()
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        it.setCheckState(Qt.Checked if z.log else Qt.Unchecked)
        t.setItem(r, 3, it)
        t.blockSignals(False)

    def _add_sens(self, st: SensStep | None):
        st = st or SensStep(0.0, "10 mV")
        r = self.sens.rowCount()
        self.sens.insertRow(r)
        self.sens.setItem(r, 0, QtWidgets.QTableWidgetItem(f"{st.from_ps:g}"))
        cb = QtWidgets.QComboBox()
        cb.addItems(SENS_LABELS)
        cb.setCurrentText(st.sens if st.sens in SENS_LABELS else "50 mV")
        self.sens.setCellWidget(r, 1, cb)

    # ------------------------------------------------------------------ settings
    def settings(self) -> ScanSettings:
        zones = []
        for r in range(self.zones.rowCount()):
            v = [float(self.zones.item(r, c).text()) for c in range(3)]
            zones.append(Zone(*v, log=self.zones.item(r, 3).checkState() == Qt.Checked))
        sens = [SensStep(float(self.sens.item(r, 0).text()), self.sens.cellWidget(r, 1).currentText())
                for r in range(self.sens.rowCount())]
        if not sens:
            raise ValueError("add at least one sensitivity")
        return ScanSettings(zones=zones, sens=sens, reps=self.reps.value(), wait_ms=self.wait.value(),
                            sens_extra_wait_ms=self.extra_wait.value(), jitter=self.jitter.isChecked(),
                            jitter_nm=self.jitter_nm.value(), pause_between_reps_s=self.pause.value(),
                            notes=self.notes.text())

    def _update_estimate(self, *_):
        try:
            s = self.settings()
            n = len(time_grid(s.zones))
            d = estimate_duration_s(s)
            lo, hi = self.delay.range_ps()
            rng = time_grid(s.zones)[[0, -1]]
            warn = "" if lo <= rng[0] and rng[1] <= hi else \
                f"  <span style='color:#c00'>outside the travel range ({lo:.1f}…{hi:.1f} ps)!</span>"
            self.estimate.setText(f"{n} points/repeat · estimated duration ≈ {d / 60:.0f} min{warn}")
        except Exception as e:
            self.estimate.setText(f"<span style='color:#c00'>{e}</span>")

    def _browse(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Data file", self.path_edit.text(),
                                                     "Data (*.dat *.txt);;All files (*)")
        if p:
            self.path_edit.setText(p)

    def _set_t0(self, v):
        self.delay.t0_mm = v
        self._update_estimate()

    def status(self, msg: str):
        self.statusBar().showMessage(msg)

    def error(self, msg: str):
        QtWidgets.QMessageBox.critical(self, "RikoPi", msg)

    # ------------------------------------------------------------------ stage / lock-in
    def _busy(self) -> bool:
        return self.scan is not None or (self._scope_thread is not None and self._scope_thread.is_alive())

    def _refresh_position(self):
        if self._busy():
            return
        try:
            x = self.stage.position()
            self.pos_label.setText(f"{x:.4f} mm   =   {self.delay.mm_to_ps(x):.3f} ps")
        except Exception as e:
            self.pos_label.setText(f"error: {e}")

    def _move(self, mm: float):
        if self._busy():
            return
        try:
            self.stage.move_abs(mm)
        except Exception as e:
            self.error(str(e))
        self._refresh_position()

    def _read_lia(self):
        try:
            self.sens_combo.setCurrentIndex(self.lockin.get_sens())
            self.lia_info.setText(f"τ = {self.lockin.get_time_constant():g} s   ·   "
                                  f"f = {self.lockin.get_freq():.2f} Hz")
        except Exception as e:
            self.error(str(e))

    # ------------------------------------------------------------------ scan
    def start_scan(self):
        if self._busy():
            return
        try:
            s = self.settings()
            path = datafile.unique_path(Path(self.path_edit.text()).expanduser())
            self.scan = Scan(self.stage, self.lockin, self.delay, s,
                             on_point=lambda *a: self.sig.point.emit(*a),
                             on_rep_done=lambda d: self.sig.rep_done.emit(d))
        except Exception as e:
            self.scan = None
            self.error(str(e))
            return
        self.scan_path = path
        self.path_edit.setText(str(path))
        for c in self.curve_reps:
            self.plot.removeItem(c)
        self.curve_reps.clear()
        for c in (self.curve_avg, self.curve_cur, self.curve_fit):
            c.setData([], [])
        self.progress.setMaximum(s.reps * len(self.scan.t))
        self.progress.setValue(0)
        self._t_start = time.time()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.tabs.widget(1).setEnabled(False)
        self.status(f"Measuring → {path}")
        self.chopper_timer.stop()
        restore = self._chopper_prev_freq if self.chopper_cfg.get("restore_on_start", True) else None
        self._chopper_prev_freq = None

        def run():
            err = ""
            try:
                if restore:
                    self.lockin.set_freq(restore)
                    self.scan._sleep(self.chopper_cfg.get("spinup_s", 10))
                self.scan.run()
            except Exception as e:
                err = f"{e}\n\n{traceback.format_exc()}"
            self.sig.finished.emit(self.scan.data, err)

        threading.Thread(target=run, daemon=True).start()

    def stop_scan(self):
        if self.scan:
            self.scan.stop()
            self.status("Stopping… (only complete repeats are saved)")

    def _on_point(self, rep, i, t, v):
        d = self.scan.data
        self.curve_cur.setData(d.t_ps[: i + 1], d.data_mV[rep, : i + 1])
        done = rep * len(d.t_ps) + i + 1
        self.progress.setValue(done)
        el = time.time() - self._t_start
        rest = el / done * (self.progress.maximum() - done)
        self.status(f"Rep {rep + 1}/{self.scan.s.reps} · t = {t:.3f} ps · X = {v:.4g} mV · "
                    f"≈ {rest / 60:.0f} min left → {self.scan_path.name}")

    def _on_rep_done(self, d: ScanData):
        i = d.reps_done - 1
        c = self.plot.plot(d.t_ps, d.data_mV[i], pen=pg.mkPen((150, 150, 150, 120), width=1))
        self.curve_reps.append(c)
        self.curve_avg.setData(d.t_ps, d.average)
        try:
            datafile.write(self.scan_path, d, self.scan.s, self._extra_info())
        except Exception as e:
            self.error(f"Could not save the file: {e}")

    def _extra_info(self) -> dict:
        return {**self.scan.info, "t0_mm": self.delay.t0_mm, "reversed": self.delay.reversed,
                "passes": self.delay.passes, "simulated": self.sim}

    def _on_finished(self, data: ScanData, err: str):
        stopped = self.scan.stopped
        self.scan = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.tabs.widget(1).setEnabled(True)
        self.curve_cur.setData([], [])
        if err:
            self.error("The scan was interrupted by an error:\n" + err)
        saved = f"{data.reps_done} repeat(s) saved in {self.scan_path}" if data.reps_done else "nothing saved"
        self.status(("Stopped: " if stopped or err else "Finished: ") + saved)
        self.data_for_fit = data
        if self.chk_slow_chop.isChecked():
            self.chopper_timer.start(int(self.chopper_cfg.get("delay_min", 10) * 60_000))

    def open_data(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open data", str(Path(self.path_edit.text()).parent),
                                                     "Data (*.dat *.txt);;All files (*)")
        if not p:
            return
        try:
            t, reps, meta = datafile.read(p)
        except Exception as e:
            self.error(str(e))
            return
        for c in self.curve_reps:
            self.plot.removeItem(c)
        self.curve_reps = [self.plot.plot(t, y, pen=pg.mkPen((150, 150, 150, 120))) for y in reps]
        d = ScanData(t, reps, len(reps))
        self.curve_avg.setData(t, d.average)
        self.curve_fit.setData([], [])
        self.data_for_fit = d
        self.status(f"Loaded {p}")

    def do_fit(self):
        d = getattr(self, "data_for_fit", None) or (self.scan.data if self.scan else None)
        if d is None or d.reps_done == 0:
            self.error("There are no complete data to fit")
            return
        try:
            r = fit_autocorrelation(d.t_ps, d.average, self.fit_model.currentText())
        except Exception as e:
            self.error(f"The fit failed: {e}")
            return
        self.curve_fit.setData(*r.curve)
        self.fit_label.setText(r.summary())
        self._last_fit = r
        self.btn_t0_from_fit.setEnabled(True)

    def _t0_from_fit(self):
        r = self._last_fit
        new = self.delay.ps_to_mm(r.t0_ps)
        if QtWidgets.QMessageBox.question(
                self, "RikoPi", f"Change t0 from {self.delay.t0_mm:.4f} mm to {new:.4f} mm "
                               f"(shift of {r.t0_ps * 1000:.1f} fs)?") == QtWidgets.QMessageBox.Yes:
            self.t0_spin.setValue(new)
            self.btn_t0_from_fit.setEnabled(False)

    # ------------------------------------------------------------------ scope
    def _toggle_scope(self, on: bool):
        if on:
            if self.scan is not None:
                self.btn_scope.setChecked(False)
                return
            self.chopper_timer.stop()
            if self._chopper_prev_freq:   # if the chopper had been slowed down, restore it
                try:
                    self.lockin.set_freq(self._chopper_prev_freq)
                    self._chopper_prev_freq = None
                except Exception as e:
                    self.status(f"Could not restore the chopper frequency: {e}")
            self._scope_buf = deque(maxlen=self.scope_len.value())
            self._scope_stop.clear()
            self.btn_scope.setText("■  Stop scope")
            args = (self.scope_wait.value() / 1000, self.align.isChecked(),
                    self.align_a.value(), self.align_b.value())
            self._scope_thread = threading.Thread(target=self._scope_loop, args=args, daemon=True)
            self._scope_thread.start()
        else:
            self._scope_stop.set()

    def _scope_loop(self, dt, align, a, b):
        err = ""
        t0 = time.time()
        target = b
        try:
            while not self._scope_stop.is_set():
                if align:
                    self.stage.move_abs(target)
                    target = a if target == b else b
                self._scope_stop.wait(dt)
                v = self.lockin.read_x() * 1e3
                self.sig.scope.emit(time.time() - t0, self.stage.position() if align else float("nan"), v)
        except Exception as e:
            err = str(e)
        self.sig.scope_finished.emit(err)

    def _on_scope(self, t, x, v):
        self._scope_buf.append((t, v))
        arr = np.array(self._scope_buf)
        self.scope_curve.setData(arr[:, 0], arr[:, 1])
        self.scope_value.setText(f"{v:.4g} mV")

    def _on_scope_finished(self, err):
        self.btn_scope.blockSignals(True)
        self.btn_scope.setChecked(False)
        self.btn_scope.blockSignals(False)
        self.btn_scope.setText("▶  Scope")
        self._refresh_position()
        if err:
            self.error(err)

    # ------------------------------------------------------------------ chopper (SR810slowChop)
    def _slow_chopper(self):
        if self._busy():
            return
        try:
            self._chopper_prev_freq = self.lockin.get_freq()
            self.lockin.set_freq(self.chopper_cfg.get("slow_Hz", 120))
            self.status(f"Chopper slowed to {self.chopper_cfg.get('slow_Hz', 120):g} Hz "
                        f"(was {self._chopper_prev_freq:g} Hz; restored when the next scan starts)")
        except Exception as e:
            self.status(f"Could not slow the chopper: {e}")

    # ------------------------------------------------------------------ closing
    def closeEvent(self, ev):
        if self.scan is not None:
            if QtWidgets.QMessageBox.question(self, "RikoPi", "A scan is running. Stop it and quit?") \
                    != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
            self.scan.stop()
        self._scope_stop.set()
        try:
            st = self.settings().to_dict()
        except Exception:
            st = self.state.get("scan")
        self.cfg.save_state({"scan": st, "t0_mm": self.delay.t0_mm, "last_path": self.path_edit.text()})
        time.sleep(0.3)
        for dev in (self.lockin, self.stage):
            try:
                dev.close()
            except Exception:
                pass
        ev.accept()


def run(cfg: Config, simulate: bool | None = None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pg.setConfigOptions(antialias=True)
    try:
        win = MainWindow(cfg, simulate)
    except Exception as e:
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Critical, "RikoPi",
                                    f"Could not connect to the hardware:\n\n{e}\n\nOpen in simulation mode?",
                                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if box.exec() != QtWidgets.QMessageBox.Yes:
            return 1
        win = MainWindow(cfg, simulate=True)
    win.show()
    return app.exec()
