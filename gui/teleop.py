"""
Teleoperation: keyboard, gamepad, leader-follower mirroring, and pose record/playback.

Everything runs from one QTimer (default 50 Hz) that computes goals and hands them to the
bus worker as a coalesced stream, so the serial link never gets flooded.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication,QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
                               QSpinBox, QTableWidget, QTabWidget, QVBoxLayout, QWidget)

from core import control_table as ct
from . import style

# pygame is optional (gamepad support)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
except ImportError:
    pygame = None


class Gamepad(QObject):
    """Thin pygame wrapper; polled from the teleop timer."""

    def __init__(self):
        super().__init__()
        self.js = None
        self.ok = False
        if pygame:
            try:
                pygame.display.init()
                pygame.joystick.init()
                self.ok = True
            except Exception:
                self.ok = False

    def devices(self):
        if not self.ok:
            return []
        pygame.joystick.quit()
        pygame.joystick.init()
        return [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]

    def open(self, idx):
        self.js = None
        if self.ok and 0 <= idx < pygame.joystick.get_count():
            self.js = pygame.joystick.Joystick(idx)
            self.js.init()

    def poll(self):
        if not self.js:
            return [], []
        try:
            pygame.event.pump()
            axes = [self.js.get_axis(a) for a in range(self.js.get_numaxes())]
            btns = [self.js.get_button(b) for b in range(self.js.get_numbuttons())]
            hats = [self.js.get_hat(h) for h in range(self.js.get_numhats())]
            for hx, hy in hats:      # expose D-pad as two extra axes
                axes += [float(hx), float(-hy)]
            return axes, btns
        except Exception:
            self.js = None
            return [], []


class TeleopPanel(QWidget):
    active_changed = Signal(bool)

    def __init__(self, model):
        super().__init__()
        self.m = model
        self.active = False
        self.keys: set[str] = set()
        self.targets: dict[int, float] = {}
        self.last_sent: dict[int, int] = {}
        self.pad = Gamepad()
        self.axes, self.btns = [], []
        self._prev_btns = []
        self.pose_anim = None          # (start_t, duration, start{}, goal{}, then_callback)
        self.sequence: list[int] = []
        self.t_last = time.monotonic()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        # --- top bar
        top = QHBoxLayout()
        self.enable_btn = QPushButton("▶  Start teleop")
        self.enable_btn.setObjectName("primary")
        self.enable_btn.setCheckable(True)
        self.enable_btn.clicked.connect(self.set_active)
        self.src_keys = QCheckBox("Keyboard")
        self.src_keys.setChecked(True)
        self.src_pad = QCheckBox("Gamepad")
        self.src_pad.setChecked(True)
        self.src_follow = QCheckBox("Leader → follower")
        self.rate = QSpinBox()
        self.rate.setRange(5, 200)
        self.rate.setValue(50)
        self.rate.setSuffix(" Hz")
        self.rate.valueChanged.connect(lambda v: self.timer.setInterval(int(1000 / v)))
        self.status = QLabel("Teleop is off")
        self.status.setObjectName("muted")
        top.addWidget(self.enable_btn)
        top.addSpacing(12)
        top.addWidget(QLabel("Sources:"))
        top.addWidget(self.src_keys)
        top.addWidget(self.src_pad)
        top.addWidget(self.src_follow)
        top.addStretch()
        top.addWidget(QLabel("Loop"))
        top.addWidget(self.rate)
        lay.addLayout(top)
        lay.addWidget(self.status)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._build_keyboard(), "Keyboard")
        self.tabs.addTab(self._build_gamepad(), "Gamepad")
        self.tabs.addTab(self._build_follow(), "Leader-follower")
        self.tabs.addTab(self._build_poses(), "Poses")

        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.timer.start()   # always runs: drives gamepad monitor + pose playback
        model.motors_changed.connect(self.rebuild)
        self.rebuild()

    # ================================================================== keyboard tab
    def _build_keyboard(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel("Hold a key to move. <b>Shift</b> = 3× speed. <b>Esc</b> or <b>Space</b> = emergency stop. "
                      "Speed is °/s in position modes, rpm in velocity mode, % in PWM mode. "
                      "Click a key cell and press the key you want to bind.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.key_table = QTableWidget(0, 5)
        self.key_table.setHorizontalHeaderLabels(["Motor", "+ key", "− key", "Speed", "Target"])
        self.key_table.verticalHeader().setVisible(False)
        self.key_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.key_table)
        return w

    # ================================================================== gamepad tab
    def _build_gamepad(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        if not self.pad.ok:
            lay.addWidget(QLabel("Gamepad support needs pygame:  pip install pygame"))
        bar = QHBoxLayout()
        self.pad_combo = QComboBox()
        self.pad_combo.setMinimumWidth(260)
        self.pad_combo.currentIndexChanged.connect(lambda i: self.pad.open(i))
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._refresh_pads)
        self.deadzone = QDoubleSpinBox()
        self.deadzone.setRange(0, 0.9)
        self.deadzone.setSingleStep(0.02)
        self.deadzone.setValue(self.m.ws.deadzone)
        self.deadzone.valueChanged.connect(lambda v: setattr(self.m.ws, "deadzone", v))
        self.estop_btn_spin = QSpinBox()
        self.estop_btn_spin.setRange(-1, 31)
        self.estop_btn_spin.setSpecialValueText("none")
        self.estop_btn_spin.setValue(self.m.ws.estop_button)
        self.estop_btn_spin.valueChanged.connect(lambda v: setattr(self.m.ws, "estop_button", v))
        bar.addWidget(self.pad_combo)
        bar.addWidget(refresh)
        bar.addStretch()
        bar.addWidget(QLabel("Deadzone"))
        bar.addWidget(self.deadzone)
        bar.addWidget(QLabel("E-stop button"))
        bar.addWidget(self.estop_btn_spin)
        lay.addLayout(bar)

        mon = QGroupBox("Live input (move a stick to find its axis number)")
        self.mon_grid = QGridLayout(mon)
        self.axis_bars: list[QProgressBar] = []
        self.btn_label = QLabel("Buttons: —")
        self.btn_label.setObjectName("value")
        self.mon_grid.addWidget(self.btn_label, 99, 0, 1, 4)
        lay.addWidget(mon)

        self.pad_table = QTableWidget(0, 5)
        self.pad_table.setHorizontalHeaderLabels(["Motor", "Axis", "Invert", "Speed", "Target"])
        self.pad_table.verticalHeader().setVisible(False)
        self.pad_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.pad_table)
        self._refresh_pads()
        return w

    def _refresh_pads(self):
        self.pad_combo.blockSignals(True)
        self.pad_combo.clear()
        devs = self.pad.devices()
        self.pad_combo.addItems(devs or ["(no gamepad detected)"])
        self.pad_combo.blockSignals(False)
        self.pad.open(0 if devs else -1)

    def _update_pad_monitor(self):
        n = len(self.axes)
        while len(self.axis_bars) < n:
            k = len(self.axis_bars)
            bar = QProgressBar()
            bar.setRange(-100, 100)
            bar.setFormat(f"axis {k}: %v")
            self.mon_grid.addWidget(bar, k // 4, k % 4)
            self.axis_bars.append(bar)
        for k, bar in enumerate(self.axis_bars):
            bar.setVisible(k < n)
            if k < n:
                bar.setValue(int(self.axes[k] * 100))
        pressed = [str(b) for b, v in enumerate(self.btns) if v]
        self.btn_label.setText("Buttons pressed: " + (", ".join(pressed) or "none"))

    # ================================================================== leader-follower tab
    def _build_follow(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel("Each follower copies its leader's present position (position modes only). "
                      "Leaders should have torque OFF so you can move them by hand — e.g. a second, "
                      "passive arm used as a puppet controller.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        bar = QHBoxLayout()
        free = QPushButton("Disable torque on all leaders")
        free.clicked.connect(self._free_leaders)
        bar.addWidget(free)
        bar.addStretch()
        lay.addLayout(bar)
        self.follow_table = QTableWidget(0, 4)
        self.follow_table.setHorizontalHeaderLabels(["Follower", "Leader", "Mirror", "Offset (°)"])
        self.follow_table.verticalHeader().setVisible(False)
        self.follow_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.follow_table)
        return w

    def _free_leaders(self):
        for lid in {c.follow for c in self.m.ws.motors if c.follow >= 0}:
            self.m.set_torque(lid, False)

    # ================================================================== poses tab
    def _build_poses(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        self.pose_list = QListWidget()
        self.pose_list.itemDoubleClicked.connect(lambda it: self.goto_pose(self.pose_list.row(it)))
        left.addWidget(self.pose_list)
        lay.addLayout(left, 2)
        right = QVBoxLayout()
        for text, fn in [("● Capture current pose", self._capture), ("Go to selected", lambda: self.goto_pose(self.pose_list.currentRow())),
                         ("Rename", self._rename_pose), ("Move up", lambda: self._move_pose(-1)),
                         ("Move down", lambda: self._move_pose(1)), ("Delete", self._delete_pose)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            right.addWidget(b)
        right.addSpacing(10)
        right.addWidget(QLabel("Move duration"))
        self.pose_dur = QDoubleSpinBox()
        self.pose_dur.setRange(0.1, 30)
        self.pose_dur.setValue(1.5)
        self.pose_dur.setSuffix(" s")
        right.addWidget(self.pose_dur)
        self.play_btn = QPushButton("▶  Play all in order")
        self.play_btn.setObjectName("primary")
        self.play_btn.clicked.connect(self._play_sequence)
        self.loop_chk = QCheckBox("Loop")
        right.addWidget(self.play_btn)
        right.addWidget(self.loop_chk)
        stop = QPushButton("■  Stop playback")
        stop.clicked.connect(self.stop_playback)
        right.addWidget(stop)
        right.addStretch()
        lay.addLayout(right, 1)
        self._refresh_poses()
        return w

    def _refresh_poses(self):
        self.pose_list.clear()
        for p in self.m.ws.poses:
            desc = ", ".join(f"{self.m.label(i).split(' (')[0]}={v * ct.DEG_PER_TICK:.0f}°" for i, v in p["goals"].items())
            QListWidgetItem(f"{p['name']}   ·   {desc}", self.pose_list)

    def _capture(self):
        goals = {c.id: self.m.raw(c.id, "Present Position") for c in self.m.ws.motors
                 if self.m.mode(c.id) in (3, 4) and self.m.is_online.get(c.id)}
        if not goals:
            QMessageBox.information(self, "Capture pose", "No online motors in position mode.")
            return
        self.m.ws.poses.append({"name": f"Pose {len(self.m.ws.poses) + 1}", "goals": goals})
        self._refresh_poses()
        self.pose_list.setCurrentRow(len(self.m.ws.poses) - 1)

    def _rename_pose(self):
        r = self.pose_list.currentRow()
        if r < 0:
            return
        name, ok = QInputDialog.getText(self, "Rename pose", "Name:", text=self.m.ws.poses[r]["name"])
        if ok:
            self.m.ws.poses[r]["name"] = name
            self._refresh_poses()

    def _move_pose(self, d):
        r = self.pose_list.currentRow()
        p = self.m.ws.poses
        if 0 <= r < len(p) and 0 <= r + d < len(p):
            p[r], p[r + d] = p[r + d], p[r]
            self._refresh_poses()
            self.pose_list.setCurrentRow(r + d)

    def _delete_pose(self):
        r = self.pose_list.currentRow()
        if r >= 0:
            del self.m.ws.poses[r]
            self._refresh_poses()

    def goto_pose(self, idx, then=None):
        if not (0 <= idx < len(self.m.ws.poses)):
            return
        goals = {i: v for i, v in self.m.ws.poses[idx]["goals"].items()
                 if self.m.ws.motor(i) and self.m.torque(i) and self.m.mode(i) in (3, 4)}
        if not goals:
            self.status.setText("Pose skipped: enable torque on the motors (position mode) first")
            return
        start = {i: self.m.raw(i, "Present Position") for i in goals}
        self.pose_anim = (time.monotonic(), self.pose_dur.value(), start, goals, then)
        self.pose_list.setCurrentRow(idx)

    def _play_sequence(self):
        self.sequence = list(range(len(self.m.ws.poses)))
        self._next_in_sequence()

    def _next_in_sequence(self):
        if not self.sequence:
            if self.loop_chk.isChecked() and self.m.ws.poses:
                self.sequence = list(range(len(self.m.ws.poses)))
            else:
                return
        self.goto_pose(self.sequence.pop(0), then=self._next_in_sequence)

    def stop_playback(self):
        self.sequence = []
        self.pose_anim = None

    # ================================================================== tables
    def rebuild(self):
        motors = self.m.ws.motors
        # keyboard
        t = self.key_table
        t.setRowCount(len(motors))
        for r, c in enumerate(motors):
            t.setCellWidget(r, 0, QLabel("  " + c.label()))
            for col, attr in [(1, "key_plus"), (2, "key_minus")]:
                e = KeyEdit(getattr(c, attr))
                e.changed.connect(lambda k, c=c, a=attr: setattr(c, a, k))
                t.setCellWidget(r, col, e)
            t.setCellWidget(r, 3, self._spin(c, "key_speed", 1, 1000))
            t.setCellWidget(r, 4, QLabel("—"))
        # gamepad
        t = self.pad_table
        t.setRowCount(len(motors))
        for r, c in enumerate(motors):
            t.setCellWidget(r, 0, QLabel("  " + c.label()))
            ax = QSpinBox()
            ax.setRange(-1, 15)
            ax.setSpecialValueText("none")
            ax.setValue(c.axis)
            ax.valueChanged.connect(lambda v, c=c: setattr(c, "axis", v))
            t.setCellWidget(r, 1, ax)
            t.setCellWidget(r, 2, self._check(c, "axis_invert"))
            t.setCellWidget(r, 3, self._spin(c, "axis_speed", 1, 1000))
            t.setCellWidget(r, 4, QLabel("—"))
        # follower
        t = self.follow_table
        t.setRowCount(len(motors))
        for r, c in enumerate(motors):
            t.setCellWidget(r, 0, QLabel("  " + c.label()))
            cb = QComboBox()
            cb.addItem("— none —", -1)
            for o in motors:
                if o.id != c.id:
                    cb.addItem(o.label(), o.id)
            cb.setCurrentIndex(max(cb.findData(c.follow), 0))
            cb.currentIndexChanged.connect(lambda _i, c=c, cb=cb: setattr(c, "follow", cb.currentData()))
            t.setCellWidget(r, 1, cb)
            t.setCellWidget(r, 2, self._check(c, "follow_invert"))
            t.setCellWidget(r, 3, self._spin(c, "follow_offset", -360, 360))
        self._refresh_poses()

    def _spin(self, cfg, attr, lo, hi):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setValue(getattr(cfg, attr))
        s.valueChanged.connect(lambda v: setattr(cfg, attr, v))
        return s

    def _check(self, cfg, attr):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(8, 0, 0, 0)
        c = QCheckBox()
        c.setChecked(getattr(cfg, attr))
        c.toggled.connect(lambda v: setattr(cfg, attr, v))
        l.addWidget(c)
        return w

    # ================================================================== activation & keys
    def set_active(self, on):
        if on and not self.m.connected:
            QMessageBox.information(self, "Teleop", "Connect to the bus first.")
            on = False
        self.active = bool(on)
        if self.active:
            fw = QApplication.focusWidget()
            if fw:
                fw.clearFocus()   # so key presses go to teleop, not a spin box
        self.enable_btn.setChecked(self.active)
        self.enable_btn.setText("■  Stop teleop" if self.active else "▶  Start teleop")
        self.keys.clear()
        self.targets = {c.id: float(self.m.raw(c.id, "Present Position")) for c in self.m.ws.motors}
        self.last_sent.clear()
        if not self.active:  # bring velocity / PWM motors to rest
            stop = {c.id: 0 for c in self.m.ws.motors if self.m.mode(c.id) in (1, 16) and self.m.torque(c.id)}
            if stop:
                self.m.send_goals(stop)
        self.active_changed.emit(self.active)

    def handle_key(self, event) -> bool:
        """Called by the app-wide event filter. Returns True if the key was consumed."""
        if event.isAutoRepeat():
            return self.active
        key = key_name(event)
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Escape, Qt.Key_Space) and self.active:
            self.m.estop()
            self.set_active(False)
            return True
        if not self.active:
            return False
        bound = {c.key_plus for c in self.m.ws.motors} | {c.key_minus for c in self.m.ws.motors} | {"Shift"}
        if key not in bound:
            return False
        if event.type() == QEvent.KeyPress:
            self.keys.add(key)
        else:
            self.keys.discard(key)
        return True

    # ================================================================== main loop
    def _tick(self):
        now = time.monotonic()
        dt = min(now - self.t_last, 0.1)
        self.t_last = now

        if self.pad.js:
            self.axes, self.btns = self.pad.poll()
            if self.tabs.currentIndex() == 1:
                self._update_pad_monitor()
            eb = self.m.ws.estop_button
            if 0 <= eb < len(self.btns) and self.btns[eb] and not (eb < len(self._prev_btns) and self._prev_btns[eb]):
                self.m.estop()
                self.set_active(False)
            self._prev_btns = list(self.btns)

        if self.pose_anim:
            self._tick_pose(now)
            return
        if not self.active or not self.m.connected:
            return

        goals: dict[int, float] = {}
        shift = "Shift" in self.keys
        dz = self.m.ws.deadzone
        for c in self.m.ws.motors:
            i = c.id
            if not self.m.torque(i):
                self.targets[i] = float(self.m.raw(i, "Present Position"))
                continue
            mode = self.m.mode(i)
            # --- leader-follower has priority
            if self.src_follow.isChecked() and c.follow >= 0 and mode in (3, 4) and self.m.mode(c.follow) in (3, 4):
                lead = self.m.raw(c.follow, "Present Position")
                g = (4096 - lead) if c.follow_invert else lead
                goals[i] = g + c.follow_offset / ct.DEG_PER_TICK
                self.targets[i] = goals[i]
                continue
            # --- combine keyboard + gamepad into a command in [-1..1] * speed
            cmd = 0.0
            if self.src_keys.isChecked():
                d = (c.key_plus in self.keys) - (c.key_minus in self.keys)
                cmd += d * c.key_speed * (3 if shift else 1)
            if self.src_pad.isChecked() and 0 <= c.axis < len(self.axes):
                a = self.axes[c.axis]
                a = 0.0 if abs(a) < dz else (a - dz * (1 if a > 0 else -1)) / (1 - dz)
                cmd += (-a if c.axis_invert else a) * c.axis_speed
            if mode in (3, 4):
                if cmd:
                    lo, hi = self.m.pos_limits(i)
                    self.targets[i] = max(lo, min(hi, self.targets.get(i, 2048) + cmd * dt / ct.DEG_PER_TICK))
                    goals[i] = self.targets[i]
            elif mode == 1:
                goals[i] = cmd / ct.RPM_PER_UNIT
            elif mode == 16:
                goals[i] = cmd / ct.PWM_PCT_PER_UNIT
        # only send what changed (velocity/PWM zero is sent once on release)
        out = {i: v for i, v in goals.items() if self.last_sent.get(i) != round(v)}
        if out:
            self.m.send_goals(out)
            self.last_sent.update({i: round(v) for i, v in out.items()})
        self._update_target_labels(goals)
        held = "+".join(sorted(self.keys)) or "—"
        self.status.setText(f"Teleop active · keys: {held} · gamepad: {'yes' if self.pad.js else 'no'}")

    def _tick_pose(self, now):
        t0, dur, start, goals, then = self.pose_anim
        s = min(1.0, (now - t0) / dur)
        s = s * s * (3 - 2 * s)  # smoothstep
        self.m.send_goals({i: start[i] + (goals[i] - start[i]) * s for i in goals})
        self.status.setText(f"Moving to pose… {int(s * 100)}%")
        if now - t0 >= dur:
            self.pose_anim = None
            self.targets.update({i: float(v) for i, v in goals.items()})
            self.status.setText("Pose reached")
            if then:
                QTimer.singleShot(250, then)

    def _update_target_labels(self, goals):
        for table, col in [(self.key_table, 4), (self.pad_table, 4)]:
            for r, c in enumerate(self.m.ws.motors):
                lbl = table.cellWidget(r, col)
                if not isinstance(lbl, QLabel):
                    continue
                mode = self.m.mode(c.id)
                if mode in (3, 4):
                    lbl.setText(f"  {self.targets.get(c.id, 0) * ct.DEG_PER_TICK:.1f}°")
                elif mode == 1:
                    lbl.setText(f"  {goals.get(c.id, 0) * ct.RPM_PER_UNIT:.1f} rpm")
                else:
                    lbl.setText(f"  {goals.get(c.id, 0) * ct.PWM_PCT_PER_UNIT:.1f} %")


def key_name(event) -> str:
    k = event.key()
    if k == Qt.Key_Shift:
        return "Shift"
    return QKeySequence(k).toString()


class KeyEdit(QLineEdit):
    """Click, then press a key to bind it."""
    changed = Signal(str)

    def __init__(self, key):
        super().__init__(key)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setPlaceholderText("press key")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            self.setText("")
        elif e.key() not in (Qt.Key_Escape, Qt.Key_Space, Qt.Key_Shift, Qt.Key_Tab):
            self.setText(key_name(e))
        else:
            return super().keyPressEvent(e)
        self.changed.emit(self.text())
        self.clearFocus()


def is_text_input(w) -> bool:
    return isinstance(w, (QAbstractSpinBox, KeyEdit)) or (isinstance(w, QLineEdit) and not w.isReadOnly())
