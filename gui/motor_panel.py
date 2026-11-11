"""Detail view for the selected motor: control, live plot, full control table, setup."""
from __future__ import annotations

import collections
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QGraphicsItem, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from core import control_table as ct
from . import style
from .widgets import Dot, GoalControl

try:
    import numpy as np
    import pyqtgraph as pg
except ImportError:   # plots are optional
    pg = None


def scroll(w):
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(w)
    return s


class IntField(QWidget):
    """Labelled spin box bound to one control-table item (writes on edit-finished)."""

    def __init__(self, panel, name, physical=True):
        super().__init__()
        self.panel, self.item, self.physical = panel, ct.BY_NAME[name], physical and ct.BY_NAME[name].unit
        it = self.item
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if self.physical:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(2 if it.unit < 1 else 0)
            self.spin.setRange((it.min or 0) * it.unit, (it.max if it.max is not None else 2**31) * it.unit)
            self.spin.setSuffix(f" {it.unit_name}")
            self.spin.setSingleStep(it.unit)
        else:
            self.spin = QSpinBox()
            self.spin.setRange(it.min if it.min is not None else 0, it.max if it.max is not None else 2**31 - 1)
        self.spin.setKeyboardTracking(False)
        self.spin.setToolTip(f"{it.name} (addr {it.addr}){' — EEPROM, torque will be turned off to write' if it.eeprom else ''}\n{it.desc}")
        self.spin.editingFinished.connect(self._commit)
        lay.addWidget(self.spin)
        self.raw_lbl = QLabel()
        self.raw_lbl.setObjectName("muted")
        self.raw_lbl.setMinimumWidth(60)
        lay.addWidget(self.raw_lbl)

    def raw(self):
        return round(self.spin.value() / self.item.unit) if self.physical else self.spin.value()

    def _commit(self):
        i = self.panel.id
        if i is None:
            return
        raw = self.raw()
        if raw != self.panel.m.raw(i, self.item.name, None):
            self.panel.m.write(i, self.item.name, raw)

    def refresh(self, model, i):
        if self.spin.hasFocus():
            return
        v = model.raw(i, self.item.name)
        self.spin.blockSignals(True)
        self.spin.setValue(v * self.item.unit if self.physical else v)
        self.spin.blockSignals(False)
        self.raw_lbl.setText(f"raw {v}")


class MotorPanel(QWidget):
    def __init__(self, model):
        super().__init__()
        self.m = model
        self.id: int | None = None
        self.fields: list[IntField] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        # header
        head = QHBoxLayout()
        self.dot = Dot(12)
        self.title = QLabel("Select a motor")
        self.title.setObjectName("h1")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        self.rename_btn = QPushButton("Rename")
        self.rename_btn.clicked.connect(self._rename)
        head.addWidget(self.dot)
        head.addWidget(self.title)
        head.addWidget(self.subtitle)
        head.addStretch()
        head.addWidget(self.rename_btn)
        lay.addLayout(head)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(scroll(self._build_control()), "Control")
        self.tabs.addTab(self._build_plot(), "Live plot")
        self.tabs.addTab(self._build_table(), "Control table")
        self.tabs.addTab(scroll(self._build_setup()), "Setup")
        self.tabs.currentChanged.connect(self._on_tab)

        self.goal: GoalControl | None = None
        model.selected_changed.connect(self.set_motor)
        model.updated.connect(self._on_update)
        model.eeprom_updated.connect(self._on_eeprom)
        model.online_changed.connect(self._refresh_all)
        model.connection_changed.connect(lambda *_: self._refresh_all())
        self.setEnabled(False)

    # ------------------------------------------------------------------ build: control tab
    def _build_control(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setSpacing(12)

        box = QGroupBox("Drive")
        f = QVBoxLayout(box)
        row = QHBoxLayout()
        self.torque_btn = QPushButton("Torque OFF")
        self.torque_btn.setObjectName("torque")
        self.torque_btn.setCheckable(True)
        self.torque_btn.clicked.connect(lambda on: self.m.set_torque(self.id, on))
        self.led_btn = QPushButton("LED")
        self.led_btn.setCheckable(True)
        self.led_btn.clicked.connect(lambda on: self.m.write(self.id, "LED", int(on)))
        self.mode_combo = QComboBox()
        for code, name in ct.OPERATING_MODES.items():
            self.mode_combo.addItem(name, code)
        self.mode_combo.activated.connect(self._mode_chosen)
        row.addWidget(self.torque_btn)
        row.addWidget(self.led_btn)
        row.addStretch()
        row.addWidget(QLabel("Mode"))
        row.addWidget(self.mode_combo)
        f.addLayout(row)
        self.goal_holder = QVBoxLayout()
        f.addLayout(self.goal_holder)
        row2 = QHBoxLayout()
        self.reverse_chk = QCheckBox("Reverse direction")
        self.reverse_chk.clicked.connect(lambda on: self._set_drive_bit(0, on))
        self.timeprof_chk = QCheckBox("Time-based profile")
        self.timeprof_chk.setToolTip("Profile Velocity/Acceleration are interpreted as times (ms) instead of speeds")
        self.timeprof_chk.clicked.connect(lambda on: self._set_drive_bit(2, on))
        row2.addWidget(self.reverse_chk)
        row2.addWidget(self.timeprof_chk)
        row2.addStretch()
        f.addLayout(row2)
        g.addWidget(box, 0, 0, 1, 2)

        live = QGroupBox("Live state")
        lf = QGridLayout(live)
        self.live = {}
        names = [("Present Position", "Position"), ("Present Velocity", "Velocity"), ("Present Load", "Load"),
                 ("Present PWM", "PWM"), ("Present Input Voltage", "Voltage"), ("Present Temperature", "Temperature"),
                 ("Moving", "Moving"), ("Hardware Error Status", "HW error")]
        for k, (item, label) in enumerate(names):
            l = QLabel(label)
            l.setObjectName("muted")
            v = QLabel("—")
            v.setObjectName("value")
            lf.addWidget(l, k // 2, (k % 2) * 2)
            lf.addWidget(v, k // 2, (k % 2) * 2 + 1)
            self.live[item] = v
        g.addWidget(live, 1, 0)

        prof = QGroupBox("Motion profile")
        pf = QFormLayout(prof)
        for name, label in [("Profile Velocity", "Profile velocity"), ("Profile Acceleration", "Profile accel")]:
            fld = IntField(self, name, physical=False)
            self.fields.append(fld)
            pf.addRow(label, fld)
        hint = QLabel("0 = unlimited. Velocity unit 0.229 rpm, accel unit 214.6 rev/min².")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        pf.addRow(hint)
        g.addWidget(prof, 1, 1)

        gains = QGroupBox("PID / feed-forward gains")
        gf = QGridLayout(gains)
        for k, (name, label) in enumerate([("Position P Gain", "Position P"), ("Position I Gain", "Position I"),
                                           ("Position D Gain", "Position D"), ("Velocity P Gain", "Velocity P"),
                                           ("Velocity I Gain", "Velocity I"), ("Feedforward 1st Gain", "FF 1st"),
                                           ("Feedforward 2nd Gain", "FF 2nd")]):
            fld = IntField(self, name, physical=False)
            self.fields.append(fld)
            gf.addWidget(QLabel(label), k // 2, (k % 2) * 2)
            gf.addWidget(fld, k // 2, (k % 2) * 2 + 1)
        g.addWidget(gains, 2, 0, 1, 2)
        g.setRowStretch(3, 1)
        return w

    # ------------------------------------------------------------------ build: plot tab
    def _build_plot(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.hist = collections.deque(maxlen=600)
        self.plot_paused = False
        self._plot_dirty = False
        if pg is None:
            lay.addWidget(QLabel("Install pyqtgraph for live plots:  pip install pyqtgraph"))
            return w
        bar = QHBoxLayout()
        self.window_spin = QSpinBox()
        self.window_spin.setRange(2, 60)
        self.window_spin.setValue(10)
        self.window_spin.setSuffix(" s window")
        self.window_spin.valueChanged.connect(self._set_window)
        pause = QPushButton("Pause")
        pause.setCheckable(True)
        pause.clicked.connect(lambda on: setattr(self, "plot_paused", on))
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_plot)
        bar.addWidget(self.window_spin)
        bar.addStretch()
        bar.addWidget(pause)
        bar.addWidget(clear)
        lay.addLayout(bar)
        pg.setConfigOptions(antialias=True, background=style.PANEL, foreground=style.MUTED)
        self.plots = {}
        specs = [("Position (°)", [("Present", style.OK), ("Goal", style.ACCENT)]),
                 ("Velocity (rpm)", [("Present", style.OK), ("Goal", style.ACCENT)]),
                 ("Load (%)", [("Load", style.WARN)])]
        prev = None
        for title, curves in specs:
            p = pg.PlotWidget()
            p.showGrid(x=True, y=True, alpha=0.15)
            p.setLabel("left", title)
            p.addLegend(offset=(10, 5))
            if prev:
                p.setXLink(prev)
            prev = p
            p.setClipToView(True)
            p.setMouseEnabled(x=False, y=True)
            p.setXRange(-self.window_spin.value(), 0, padding=0)
            # Cache each axis (and the grid it draws) in a pixmap. On some backing stores (WSLg/Wayland,
            # offscreen) drawing the grid directly costs ~20 ms per plot; cached it's a cheap blit.
            for ax in ("left", "bottom"):
                p.getAxis(ax).setCacheMode(QGraphicsItem.DeviceCoordinateCache)
            p.getAxis("left").setWidth(64)   # fixed: keeps plots aligned, avoids re-layout as labels change
            self.plots[title] = [p.plot(name=n, pen=pg.mkPen(c, width=2)) for n, c in curves]
            lay.addWidget(p)
        # Redraw on our own capped timer (not on every poll): repainting three plots can cost more
        # than one poll period, and drawing per update used to flood the GUI thread.
        self.plot_timer = QTimer(self)
        self.plot_timer.setInterval(50)   # 20 fps is plenty for telemetry
        self.plot_timer.timeout.connect(self._redraw_plot)
        return w

    def _set_window(self, win):
        # x-axes are linked, so setting one sets all
        next(iter(self.plots.values()))[0].getViewBox().setXRange(-win, 0, padding=0)
        self._plot_dirty = True
        self._redraw_plot()

    def _clear_plot(self):
        self.hist.clear()
        for curves in self.plots.values():
            for c in curves:
                c.setData([], [])

    def _on_tab(self, _idx=None):
        if pg is None:
            return
        if self.tabs.currentIndex() == 1 and self.isVisible():
            self.plot_timer.start()
            self._redraw_plot()
        else:
            self.plot_timer.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self._on_tab()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._on_tab()

    def _update_plot(self):
        """Record one sample (cheap; called on every state update). Drawing happens in _redraw_plot."""
        if pg is None or self.plot_paused or self.id is None:
            return
        m, i = self.m, self.id
        mode = m.mode(i)
        nan = float("nan")
        self.hist.append((time.monotonic(),
                          m.raw(i, "Present Position") * ct.DEG_PER_TICK,
                          m.raw(i, "Goal Position") * ct.DEG_PER_TICK if mode in (3, 4) else nan,
                          m.raw(i, "Present Velocity") * ct.RPM_PER_UNIT,
                          m.raw(i, "Goal Velocity") * ct.RPM_PER_UNIT if mode == 1 else nan,
                          m.raw(i, "Present Load") * 0.1))
        self._plot_dirty = True

    def _redraw_plot(self):
        if not self._plot_dirty or len(self.hist) < 2:
            return
        self._plot_dirty = False
        a = np.array(self.hist, dtype=float)
        a = a[a[:, 0] >= a[-1, 0] - self.window_spin.value()]
        t = a[:, 0] - a[-1, 0]
        (pp, pg_), (vp, vg), (ld,) = self.plots.values()
        pp.setData(t, a[:, 1])
        vp.setData(t, a[:, 3])
        ld.setData(t, a[:, 5])
        # goal curves: only finite points (all-NaN data upsets pyqtgraph's auto-range)
        for curve, col in ((pg_, 2), (vg, 4)):
            ok = np.isfinite(a[:, col])
            curve.setData(t[ok], a[ok, col])

    # ------------------------------------------------------------------ build: control table tab
    def _build_table(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter items…")
        self.filter.textChanged.connect(self._filter_table)
        refresh = QPushButton("Re-read EEPROM")
        refresh.clicked.connect(lambda: self.id is not None and self.m.worker.post("_do_read_eeprom", self.id))
        hint = QLabel("Double-click a value to edit")
        hint.setObjectName("muted")
        bar.addWidget(self.filter)
        bar.addWidget(hint)
        bar.addWidget(refresh)
        lay.addLayout(bar)
        self.table = QTableWidget(len(ct.ITEMS), 6)
        self.table.setHorizontalHeaderLabels(["Addr", "Item", "Area", "Access", "Raw", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for r, it in enumerate(ct.ITEMS):
            for c, txt in enumerate([str(it.addr), it.name, "EEPROM" if it.eeprom else "RAM", it.access, "", ""]):
                cell = QTableWidgetItem(txt)
                cell.setToolTip(it.desc)
                if c in (0, 2, 3):
                    cell.setForeground(Qt.gray)
                self.table.setItem(r, c, cell)
        self.table.cellDoubleClicked.connect(self._edit_cell)
        lay.addWidget(self.table)
        return w

    def _filter_table(self, text):
        for r, it in enumerate(ct.ITEMS):
            self.table.setRowHidden(r, text.lower() not in it.name.lower())

    def _edit_cell(self, row, _col):
        it = ct.ITEMS[row]
        if self.id is None or not it.writable:
            return
        cur = self.m.raw(self.id, it.name)
        lo = it.min if it.min is not None else -2**31
        hi = it.max if it.max is not None else 2**31 - 1
        extra = "\n\nThis is an EEPROM item: torque will be turned OFF to write it." if it.eeprom else ""
        v, ok = QInputDialog.getInt(self, f"Write {it.name}", f"{it.name} (addr {it.addr}, raw units)\n{it.desc}{extra}",
                                    cur, lo, hi)
        if ok:
            if it.name == "ID":
                self._change_id(v)
            else:
                self.m.write(self.id, it.name, v)

    def _refresh_table(self):
        if self.tabs.currentIndex() != 2:
            return
        st = self.m.state.get(self.id, {})
        for r, it in enumerate(ct.ITEMS):
            v = st.get(it.name)
            self.table.item(r, 4).setText("" if v is None else str(v))
            if v is None:
                txt = ""
            elif it.name == "Operating Mode":
                txt = ct.OPERATING_MODES.get(v, "?")
            elif it.name == "Baud Rate":
                txt = f"{ct.BAUD_RATES.get(v, '?')} bps"
            elif it.name == "Model Number":
                txt = ct.MODEL_NAMES.get(v, "")
            elif it.name == "Hardware Error Status":
                txt = ct.hw_error_text(v) or "OK"
            else:
                txt = ct.physical(it, v) if it.unit else ""
            self.table.item(r, 5).setText(txt)

    # ------------------------------------------------------------------ build: setup tab
    def _build_setup(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setSpacing(12)

        comm = QGroupBox("Communication")
        cf = QFormLayout(comm)
        self.id_btn = QPushButton("Change ID…")
        self.id_btn.clicked.connect(lambda: self._change_id())
        self.baud_combo = QComboBox()
        for code, b in ct.BAUD_RATES.items():
            self.baud_combo.addItem(f"{b:,} bps", code)
        self.baud_combo.activated.connect(self._change_baud)
        cf.addRow("ID", self.id_btn)
        cf.addRow("Baud rate", self.baud_combo)
        rd = IntField(self, "Return Delay Time")
        self.fields.append(rd)
        cf.addRow("Return delay", rd)
        g.addWidget(comm, 0, 0)

        lim = QGroupBox("Limits (EEPROM)")
        lf = QFormLayout(lim)
        for name, label in [("Min Position Limit", "Min position"), ("Max Position Limit", "Max position"),
                            ("Velocity Limit", "Velocity limit"), ("PWM Limit", "PWM limit"),
                            ("Temperature Limit", "Temperature limit"), ("Min Voltage Limit", "Min voltage"),
                            ("Max Voltage Limit", "Max voltage"), ("Moving Threshold", "Moving threshold")]:
            fld = IntField(self, name)
            self.fields.append(fld)
            lf.addRow(label, fld)
        g.addWidget(lim, 0, 1, 2, 1)

        home = QGroupBox("Homing")
        hf = QFormLayout(home)
        ho = IntField(self, "Homing Offset")
        self.fields.append(ho)
        hf.addRow("Homing offset", ho)
        zero = QPushButton("Make current position = 180° (center)")
        zero.clicked.connect(self._zero_here)
        hf.addRow(zero)
        g.addWidget(home, 1, 0)

        sd = QGroupBox("Shutdown on error (torque off when…)")
        sf = QVBoxLayout(sd)
        self.shutdown_chk = {}
        for bit, name in ct.HW_ERROR_BITS.items():
            c = QCheckBox(name)
            c.clicked.connect(self._shutdown_changed)
            sf.addWidget(c)
            self.shutdown_chk[bit] = c
        self.startup_torque = QCheckBox("Enable torque at power-up (Startup Configuration)")
        self.startup_torque.clicked.connect(lambda on: self.m.write(
            self.id, "Startup Configuration", (self.m.raw(self.id, "Startup Configuration") & ~1) | int(on)))
        sf.addWidget(self.startup_torque)
        g.addWidget(sd, 2, 0)

        danger = QGroupBox("Maintenance")
        df = QVBoxLayout(danger)
        rb = QPushButton("Reboot motor (clears hardware errors)")
        rb.clicked.connect(lambda: self.m.worker.post("_do_reboot", self.id))
        fr = QPushButton("Factory reset (keep ID & baud)…")
        fr.setObjectName("danger")
        fr.clicked.connect(self._factory_reset)
        df.addWidget(rb)
        df.addWidget(fr)
        df.addStretch()
        g.addWidget(danger, 2, 1)
        g.setRowStretch(3, 1)
        return w

    # ------------------------------------------------------------------ actions
    def _rename(self):
        cfg = self.m.ws.motor(self.id)
        name, ok = QInputDialog.getText(self, "Rename motor", "Name:", text=cfg.name)
        if ok:
            cfg.name = name.strip()
            self.m.motors_changed.emit()
            self._refresh_all()

    def _mode_chosen(self):
        mode = self.mode_combo.currentData()
        if mode == self.m.mode(self.id):
            return
        if self.m.torque(self.id):
            r = QMessageBox.question(self, "Change operating mode",
                                     "Torque will be briefly disabled to change the mode. Continue?")
            if r != QMessageBox.Yes:
                self._refresh_all()
                return
        self.m.set_mode(self.id, mode)

    def _set_drive_bit(self, bit, on):
        v = self.m.raw(self.id, "Drive Mode")
        v = (v | (1 << bit)) if on else (v & ~(1 << bit))
        self.m.write(self.id, "Drive Mode", v)

    def _shutdown_changed(self):
        v = sum(1 << b for b, c in self.shutdown_chk.items() if c.isChecked())
        self.m.write(self.id, "Shutdown", v)

    def _change_id(self, new=None):
        if new is None:
            new, ok = QInputDialog.getInt(self, "Change ID", f"New ID for motor {self.id}:", self.id, 0, 252)
            if not ok or new == self.id:
                return
        if self.m.ws.motor(new):
            QMessageBox.warning(self, "Change ID", f"ID {new} is already used by another motor in this workspace.")
            return
        self.m.worker.post("_do_change_id", self.id, new)

    def _change_baud(self):
        code = self.baud_combo.currentData()
        if code == self.m.raw(self.id, "Baud Rate"):
            return
        r = QMessageBox.warning(self, "Change baud rate",
                                f"Set motor {self.id} to {ct.BAUD_RATES[code]:,} bps?\n\n"
                                "You'll lose contact until you reconnect at the new baud rate. "
                                "All motors on one bus must use the same baud.",
                                QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            self.m.worker.post("_do_change_baud", self.id, code)
        else:
            self._refresh_all()

    def _zero_here(self):
        r = QMessageBox.question(self, "Set homing offset",
                                 "Torque will be turned off. Hold the joint at the pose you want to be the center (180°), "
                                 "then press Yes.")
        if r == QMessageBox.Yes:
            self.m.worker.post("_do_set_zero_here", self.id)

    def _factory_reset(self):
        r = QMessageBox.warning(self, "Factory reset",
                                f"Reset all settings of motor {self.id} to factory defaults (ID and baud are kept)?",
                                QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            self.m.worker.post("_do_factory_reset", self.id, 0x02)

    # ------------------------------------------------------------------ refresh
    def set_motor(self, i):
        self.id = i
        self._clear_plot() if pg else self.hist.clear()
        if self.goal:
            self.goal.setParent(None)
            self.goal.deleteLater()
            self.goal = None
        if i is not None and self.m.ws.motor(i):
            self.goal = GoalControl(self.m, i)
            self.goal_holder.addWidget(self.goal)
            self.setEnabled(True)
        else:
            self.setEnabled(False)
        self._refresh_all()

    def _on_update(self, ids):
        if self.id in ids:
            self._refresh_live()
            self._update_plot()

    def _on_eeprom(self, i):
        if i == self.id:
            self._refresh_all()

    def _refresh_live(self):
        m, i = self.m, self.id
        if i is None:
            return
        st = m.state.get(i, {})
        for item, lbl in self.live.items():
            v = st.get(item)
            if v is None:
                lbl.setText("—")
            elif item == "Hardware Error Status":
                lbl.setText(ct.hw_error_text(v) or "none")
                lbl.setStyleSheet(f"color:{style.BAD if v else style.TEXT}")
            elif item == "Moving":
                lbl.setText("yes" if v else "no")
            else:
                lbl.setText(ct.physical(ct.BY_NAME[item], v))
        on = m.torque(i)
        self.torque_btn.blockSignals(True)
        self.torque_btn.setChecked(on)
        self.torque_btn.setText("Torque ON" if on else "Torque OFF")
        self.torque_btn.blockSignals(False)
        self.led_btn.blockSignals(True)
        self.led_btn.setChecked(bool(m.raw(i, "LED")))
        self.led_btn.blockSignals(False)
        online = m.is_online.get(i, False) and m.connected
        self.dot.set("bad" if m.raw(i, "Hardware Error Status") else ("ok" if online else "grey"))
        if self.goal:
            self.goal.refresh()
        for f in self.fields:
            if not f.item.eeprom:
                f.refresh(m, i)
        self._refresh_table()

    def _refresh_all(self):
        m, i = self.m, self.id
        if i is None or not m.ws.motor(i):
            self.title.setText("Select a motor")
            self.subtitle.setText("")
            return
        cfg = m.ws.motor(i)
        self.title.setText(cfg.name or f"Motor {i}")
        model = m.raw(i, "Model Number", cfg.model)
        fw = m.raw(i, "Firmware Version", None)
        self.subtitle.setText(f"ID {i} · {ct.MODEL_NAMES.get(model, f'model {model}')}" + (f" · fw v{fw}" if fw else ""))
        idx = self.mode_combo.findData(m.mode(i))
        self.mode_combo.setCurrentIndex(max(idx, 0))
        dm = m.raw(i, "Drive Mode")
        self.reverse_chk.setChecked(bool(dm & 1))
        self.timeprof_chk.setChecked(bool(dm & 4))
        self.baud_combo.setCurrentIndex(max(self.baud_combo.findData(m.raw(i, "Baud Rate", 1)), 0))
        self.id_btn.setText(f"ID {i} — change…")
        sd = m.raw(i, "Shutdown", 52)
        for bit, c in self.shutdown_chk.items():
            c.setChecked(bool(sd & (1 << bit)))
        self.startup_torque.setChecked(bool(m.raw(i, "Startup Configuration") & 1))
        for f in self.fields:
            f.refresh(m, i)
        self._refresh_live()
