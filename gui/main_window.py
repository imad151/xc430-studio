from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from core import control_table as ct
from core.config import DEFAULT_PATH, Workspace
from . import style
from .model import Model
from .motor_panel import MotorPanel
from .teleop import TeleopPanel, is_text_input
from .widgets import MotorCard

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None


class KeyFilter(QObject):
    """App-wide key hook so teleop works regardless of which widget has focus."""

    def __init__(self, teleop: TeleopPanel):
        super().__init__()
        self.teleop = teleop
        self._last = None

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.KeyPress, QEvent.KeyRelease) and isinstance(obj, QWidget):
            sig = (e.type(), e.key(), e.timestamp(), e.isAutoRepeat())
            if sig == self._last:      # same event propagating to a parent widget
                return False
            self._last = sig
            if is_text_input(QApplication.focusWidget()) and e.key() != Qt.Key_Escape:
                return False   # let the user type into fields
            return self.teleop.handle_key(e)
        if e.type() == QEvent.ApplicationDeactivate:
            self.teleop.keys.clear()   # never leave a key "stuck" when the window loses focus
        return False


class MainWindow(QMainWindow):
    def __init__(self, ws_path=DEFAULT_PATH):
        super().__init__()
        self.ws_path = ws_path
        self.m = Model(Workspace.load(ws_path))
        self.setWindowTitle("XC430 Studio")
        self.resize(1400, 900)
        self.cards: dict[int, MotorCard] = {}
        self._dirty: set[int] = set()

        self._build_toolbar()
        self._build_motor_list()
        self._build_log()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard = QWidget()
        self.dash_grid = QGridLayout(self.dashboard)
        self.dash_grid.setSpacing(12)
        self.dash_grid.setContentsMargins(12, 12, 12, 12)
        self.dash_empty = QLabel("No motors yet.\n\nConnect to your U2D2 (or tick “Simulator”), then press Scan.")
        self.dash_empty.setAlignment(Qt.AlignCenter)
        self.dash_empty.setObjectName("muted")
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.dashboard)
        self.panel = MotorPanel(self.m)
        self.teleop = TeleopPanel(self.m)
        self.tabs.addTab(sc, "Dashboard")
        self.tabs.addTab(self.panel, "Motor detail")
        self.tabs.addTab(self.teleop, "Teleop")
        self.setCentralWidget(self.tabs)
        self.tabs.currentChanged.connect(lambda _i: self._dirty.update(self.cards))
        self._build_menu()

        self.stat_conn = QLabel("Disconnected")
        self.stat_hz = QLabel("")
        self.statusBar().addWidget(self.stat_conn)
        self.statusBar().addPermanentWidget(self.stat_hz)

        m = self.m
        m.motors_changed.connect(self._rebuild_motors)
        m.updated.connect(lambda ids: self._dirty.update(ids))
        m.online_changed.connect(lambda: self._dirty.update(self.cards))
        m.eeprom_updated.connect(lambda i: self._dirty.add(i))
        m.connection_changed.connect(self._on_connection)
        m.selected_changed.connect(self._on_selected)
        m.worker.log.connect(self.log)
        m.worker.stats.connect(lambda hz: self.stat_hz.setText(f"bus poll {hz:.0f} Hz"))
        self.teleop.active_changed.connect(lambda on: self.statusBar().showMessage(
            "Teleop ON — Esc / Space = emergency stop" if on else "Teleop off", 4000))

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_cards)
        self.refresh_timer.start(50)

        QShortcut(QKeySequence("Ctrl+E"), self, self.m.estop, context=Qt.ApplicationShortcut)
        self.keyfilter = KeyFilter(self.teleop)
        QApplication.instance().installEventFilter(self.keyfilter)

        m.worker.start()
        self._rebuild_motors()
        self.log("info", "Welcome! Pick your U2D2 port and baud rate, press Connect, then Scan. "
                         "No hardware? Tick “Simulator”.")

    # ------------------------------------------------------------------ UI construction
    def _build_toolbar(self):
        tb = self.addToolBar("Connection")
        tb.setMovable(False)
        ws = self.m.ws
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(200)
        self.port_combo.setToolTip("Serial port of your U2D2 / OpenRB / USB2Dynamixel")
        self._refresh_ports()
        if ws.port:
            self.port_combo.setCurrentText(ws.port)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Refresh serial ports")
        refresh.clicked.connect(self._refresh_ports)
        self.baud_combo = QComboBox()
        for b in ct.BAUD_RATES.values():
            self.baud_combo.addItem(f"{b:,}", b)
        self.baud_combo.setCurrentIndex(max(self.baud_combo.findData(ws.baud), 0))
        self.sim_chk = QCheckBox("Simulator")
        self.sim_chk.setChecked(ws.simulate)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setCheckable(True)
        self.connect_btn.clicked.connect(self._toggle_connect)

        self.scan_btn = QPushButton("Scan")
        scan_menu = QMenu(self)
        scan_menu.addAction("Scan at selected baud", lambda: self._scan(all_bauds=False))
        scan_menu.addAction("Scan all baud rates (slow)", lambda: self._scan(all_bauds=True))
        self.scan_btn.setMenu(scan_menu)
        self.add_btn = QPushButton("+ Add by ID")
        self.add_btn.clicked.connect(self._add_by_id)

        self.poll = QSpinBox()
        self.poll.setRange(1, 200)
        self.poll.setValue(int(ws.poll_hz))
        self.poll.setSuffix(" Hz")
        self.poll.setToolTip("Target polling rate. At 57600 bps expect ~10-20 Hz with several motors; "
                             "use 1 Mbps+ for faster updates.")
        self.poll.valueChanged.connect(self._set_poll)

        all_on = QPushButton("All torque ON")
        all_on.clicked.connect(lambda: [self.m.set_torque(c.id, True) for c in self.m.ws.motors])
        all_off = QPushButton("All torque OFF")
        all_off.clicked.connect(lambda: [self.m.set_torque(c.id, False) for c in self.m.ws.motors])
        estop = QPushButton("STOP")
        estop.setObjectName("estop")
        estop.setToolTip("Emergency stop: torque off on every motor (Ctrl+E, or Esc/Space during teleop)")
        estop.clicked.connect(self._estop)

        for w in [QLabel(" Port "), self.port_combo, refresh, QLabel(" Baud "), self.baud_combo,
                  self.sim_chk, self.connect_btn]:
            tb.addWidget(w)
        tb.addSeparator()
        for w in [self.scan_btn, self.add_btn]:
            tb.addWidget(w)
        tb.addSeparator()
        tb.addWidget(QLabel(" Poll "))
        tb.addWidget(self.poll)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        for w in [all_on, all_off, estop]:
            tb.addWidget(w)
        self._set_bus_controls(False)

    def _build_motor_list(self):
        dock = QDockWidget("MOTORS", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        self.motor_list = QListWidget()
        self.motor_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.motor_list.customContextMenuRequested.connect(self._list_menu)
        self.motor_list.currentItemChanged.connect(
            lambda cur, _p: cur and self.m.select(cur.data(Qt.UserRole)))
        self.motor_list.itemDoubleClicked.connect(lambda _it: self.tabs.setCurrentIndex(1))
        lay.addWidget(self.motor_list)
        hint = QLabel("Double-click: details\nRight-click: rename / remove")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        dock.setWidget(w)
        dock.setMinimumWidth(230)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_log(self):
        dock = QDockWidget("LOG", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 8)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        row = QHBoxLayout()
        self.debug_chk = QCheckBox("Show writes")
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log_view.clear)
        row.addWidget(self.debug_chk)
        row.addStretch()
        row.addWidget(clear)
        lay.addWidget(self.log_view)
        lay.addLayout(row)
        dock.setWidget(w)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [150], Qt.Vertical)
        self.log_dock = dock

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("Open workspace…", self._open_ws, QKeySequence.Open)
        f.addAction("Save workspace", self._save_ws, QKeySequence.Save)
        f.addAction("Save workspace as…", self._save_ws_as)
        f.addSeparator()
        f.addAction("Quit", self.close, QKeySequence.Quit)
        v = mb.addMenu("&View")
        v.addAction(self.log_dock.toggleViewAction())
        h = mb.addMenu("&Help")
        h.addAction("Shortcuts && tips", self._help)

    # ------------------------------------------------------------------ connection
    def _refresh_ports(self):
        cur = self.port_combo.currentText()
        self.port_combo.clear()
        ports = []
        if list_ports:
            for p in list_ports.comports():
                ports.append(p.device)
                self.port_combo.addItem(p.device)
                self.port_combo.setItemData(self.port_combo.count() - 1, p.description, Qt.ToolTipRole)
        if cur:
            self.port_combo.setCurrentText(cur)
        elif not ports:
            self.port_combo.setCurrentText("COM3" if sys.platform == "win32" else "/dev/ttyUSB0")

    def _toggle_connect(self, on):
        ws = self.m.ws
        if on:
            ws.port = self.port_combo.currentText().strip()
            ws.baud = self.baud_combo.currentData()
            ws.simulate = self.sim_chk.isChecked()
            self.connect_btn.setText("Connecting…")
            self.m.worker.connect_bus(ws.port, ws.baud, ws.simulate)
        else:
            self.teleop.set_active(False)
            self.m.worker.disconnect_bus()

    def _on_connection(self, ok, msg):
        self.connect_btn.setChecked(ok)
        self.connect_btn.setText("Disconnect" if ok else "Connect")
        self.stat_conn.setText(("● Connected: " if ok else "○ ") + msg)
        self.stat_conn.setStyleSheet(f"color:{style.OK if ok else style.MUTED}")
        self._set_bus_controls(ok)
        if ok:
            self.log("info", f"Connected ({msg})")
            if not self.m.ws.motors:
                self._scan(False)
        elif msg != "Disconnected":
            self.log("error", f"Connection failed: {msg}")
            QMessageBox.warning(self, "Connection failed",
                                f"{msg}\n\nTips:\n• Check the port name (Linux: /dev/ttyUSB0, Windows: COM3, macOS: /dev/tty.usbserial-*)\n"
                                "• On Linux add yourself to the 'dialout' group\n• Close Dynamixel Wizard if it's using the port")
        self._dirty.update(self.cards)

    def _set_bus_controls(self, connected):
        for w in [self.scan_btn, self.add_btn]:
            w.setEnabled(connected)
        for w in [self.port_combo, self.baud_combo, self.sim_chk]:
            w.setEnabled(not connected)

    def _scan(self, all_bauds):
        sel = self.baud_combo.currentData()
        bauds = [sel] + [b for b in ct.BAUD_RATES.values() if b != sel] if all_bauds else [sel]
        self.log("info", "Scanning" + (" all baud rates…" if all_bauds else f" at {sel:,} bps…"))
        self.m.worker.post("_do_scan", bauds, 0, 252)

    def _add_by_id(self):
        i, ok = QInputDialog.getInt(self, "Add motor", "Motor ID:", 1, 0, 252)
        if ok:
            self.m.worker.post("_do_add", i)

    def _set_poll(self, hz):
        self.m.ws.poll_hz = hz
        self.m.worker.poll_hz = hz

    def _estop(self):
        self.teleop.set_active(False)
        self.teleop.stop_playback()
        self.m.estop()

    # ------------------------------------------------------------------ motors
    def _rebuild_motors(self):
        cur = self.m.selected
        self.motor_list.blockSignals(True)
        self.motor_list.clear()
        for c in self.m.ws.motors:
            it = QListWidgetItem(c.label())
            it.setData(Qt.UserRole, c.id)
            self.motor_list.addItem(it)
            if c.id == cur:
                self.motor_list.setCurrentItem(it)
        self.motor_list.blockSignals(False)
        # dashboard cards
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        self.dash_empty.setParent(None)
        for c in self.m.ws.motors:
            card = MotorCard(self.m, c.id)
            card.clicked.connect(self.m.select)
            self.cards[c.id] = card
        self._layout_cards()
        if self.m.ws.motors and (cur is None or not self.m.ws.motor(cur)):
            self.m.select(self.m.ws.motors[0].id)
        elif cur is not None:
            self._on_selected(cur)

    def _layout_cards(self):
        while self.dash_grid.count():
            self.dash_grid.takeAt(0)
        if not self.cards:
            self.dash_grid.addWidget(self.dash_empty, 0, 0)
            return
        cols = max(1, (self.tabs.width() - 24) // 340)
        for k, card in enumerate(self.cards.values()):
            self.dash_grid.addWidget(card, k // cols, k % cols)
        for c in range(4):
            self.dash_grid.setColumnStretch(c, 1 if c < cols else 0)
        self.dash_grid.setRowStretch(len(self.cards) // cols + 1, 1)
        self._cols = cols

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = max(1, (self.tabs.width() - 24) // 340)
        if getattr(self, "_cols", None) != cols:
            self._layout_cards()

    def _refresh_cards(self):
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        if self.tabs.currentIndex() == 0:
            for i in dirty:
                if i in self.cards:
                    self.cards[i].refresh()
        for r in range(self.motor_list.count()):
            it = self.motor_list.item(r)
            i = it.data(Qt.UserRole)
            if i in dirty:
                online = self.m.is_online.get(i) and self.m.connected
                err = self.m.raw(i, "Hardware Error Status")
                mark = "⚠" if err else ("●" if online else "○")
                it.setText(f"{mark}  {self.m.label(i)}")
                it.setForeground(QColor(style.BAD if err else (style.TEXT if online else style.MUTED)))

    def _on_selected(self, i):
        for cid, card in self.cards.items():
            card.set_selected(cid == i)
        for r in range(self.motor_list.count()):
            if self.motor_list.item(r).data(Qt.UserRole) == i:
                self.motor_list.blockSignals(True)
                self.motor_list.setCurrentRow(r)
                self.motor_list.blockSignals(False)

    def _list_menu(self, pos):
        it = self.motor_list.itemAt(pos)
        if not it:
            return
        i = it.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Open details", lambda: (self.m.select(i), self.tabs.setCurrentIndex(1)))
        menu.addAction("Rename…", lambda: (self.m.select(i), self.panel._rename()))
        menu.addAction("Blink LED", lambda: self._blink(i))
        menu.addSeparator()
        menu.addAction("Remove from workspace", lambda: self.m.remove_motor(i))
        menu.exec(self.motor_list.mapToGlobal(pos))

    def _blink(self, i):
        for k in range(6):
            QTimer.singleShot(k * 250, lambda k=k: self.m.write(i, "LED", (k + 1) % 2))

    # ------------------------------------------------------------------ log
    def log(self, level, msg):
        if level == "debug" and not self.debug_chk.isChecked():
            return
        color = {"error": style.BAD, "warn": style.WARN, "debug": style.MUTED}.get(level, style.TEXT)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cur = self.log_view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        from datetime import datetime
        cur.insertText(f"{datetime.now():%H:%M:%S}  {msg}\n", fmt)
        self.log_view.setTextCursor(cur)
        self.log_view.ensureCursorVisible()
        if level == "error":
            self.statusBar().showMessage(msg, 5000)

    # ------------------------------------------------------------------ workspace files
    def _save_ws(self):
        self.m.ws.save(self.ws_path)
        self.statusBar().showMessage(f"Saved {self.ws_path}", 3000)

    def _save_ws_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save workspace", str(self.ws_path), "Workspace (*.json)")
        if path:
            self.ws_path = path
            self._save_ws()

    def _open_ws(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open workspace", str(self.ws_path), "Workspace (*.json)")
        if not path:
            return
        ws = Workspace.load(path)
        self.ws_path = path
        self.m.ws = ws
        self.m._sync_ids()
        self.port_combo.setCurrentText(ws.port)
        self.baud_combo.setCurrentIndex(max(self.baud_combo.findData(ws.baud), 0))
        self.sim_chk.setChecked(ws.simulate)
        self.m.motors_changed.emit()

    def _help(self):
        QMessageBox.information(self, "Shortcuts & tips", (
            "<b>Emergency stop</b>: STOP button, Ctrl+E anywhere, Esc/Space during teleop, or a gamepad button.<br><br>"
            "<b>Getting started</b>: choose the port & baud (factory default is 57,600), Connect, then Scan.<br><br>"
            "<b>EEPROM settings</b> (ID, baud, mode, limits, homing) need torque off — the app does this for you "
            "and tells you in the log.<br><br>"
            "<b>Teleop</b>: set key / axis bindings in the Teleop tab, press Start. Leader-follower copies one "
            "motor's position to another.<br><br>"
            "<b>Speed</b>: for smoother polling switch motors to 1 Mbps (Setup tab), then reconnect at 1 Mbps.<br><br>"
            "Your workspace (names, bindings, poses) is saved automatically on exit."))

    def closeEvent(self, e):
        self.teleop.set_active(False)
        try:
            self.m.ws.save(self.ws_path)
        except OSError:
            pass
        self.m.worker.stop()
        super().closeEvent(e)
