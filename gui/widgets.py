"""Reusable widgets: goal control (adapts to operating mode) and dashboard motor card."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget)

from core import control_table as ct
from . import style

MODE_UNITS = {3: (ct.DEG_PER_TICK, "°"), 4: (ct.DEG_PER_TICK, "°"),
              1: (ct.RPM_PER_UNIT, "rpm"), 16: (ct.PWM_PCT_PER_UNIT, "%")}


class Dot(QLabel):
    def __init__(self, size=10):
        super().__init__()
        self.setFixedSize(size, size)
        self.set("grey")

    def set(self, state):
        color = {"ok": style.OK, "warn": style.WARN, "bad": style.BAD}.get(state, "#4a5160")
        self.setStyleSheet(f"background:{color}; border-radius:{self.width() // 2}px;")


class PresentSlider(QSlider):
    """Horizontal slider that also draws a marker for the present (measured) value."""

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.present = None

    def set_present(self, v):
        self.present = v
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.present is None or self.maximum() == self.minimum():
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        frac = (min(max(self.present, self.minimum()), self.maximum()) - self.minimum()) / (self.maximum() - self.minimum())
        x = groove.left() + 8 + frac * (groove.width() - 16)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(style.OK))
        y = groove.center().y() + 9
        p.drawPolygon(QPolygonF([QPointF(x, y - 5), QPointF(x - 5, y + 3), QPointF(x + 5, y + 3)]))


class GoalControl(QWidget):
    """Slider + spin box for the active goal (position / velocity / PWM), in physical units."""

    def __init__(self, model, dxl_id, compact=False):
        super().__init__()
        self.m, self.id = model, dxl_id
        self._mode = None
        self._range_key = None
        self._dragging = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.title = QLabel("Goal")
        self.title.setObjectName("muted")
        self.spin = QDoubleSpinBox()
        self.spin.setKeyboardTracking(False)
        self.spin.setDecimals(1)
        self.spin.setMinimumWidth(95)
        row.addWidget(self.title)
        row.addStretch()
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(lambda: self._send(0))
        row.addWidget(self.stop_btn)
        self.center_btn = QPushButton("Hold")
        self.center_btn.setToolTip("Set goal = present position")
        self.center_btn.clicked.connect(self._hold)
        row.addWidget(self.center_btn)
        row.addWidget(self.spin)
        lay.addLayout(row)
        self.slider = PresentSlider()
        lay.addWidget(self.slider)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderReleased.connect(self._released)
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)
        self.refresh()

    def _scale(self):
        return MODE_UNITS.get(self._mode, MODE_UNITS[3])

    def refresh(self):
        """Re-read mode/limits and current goal. Call on every state update."""
        i, m = self.id, self.m
        mode = m.mode(i)
        if mode == 1:
            lo, hi = -m.vel_limit(i), m.vel_limit(i)
        elif mode == 16:
            lo, hi = -m.pwm_limit(i), m.pwm_limit(i)
        elif mode == 4:
            lo, hi = -4096 * 5, 4096 * 5
        else:
            lo, hi = m.pos_limits(i)
        if (mode, lo, hi) != self._range_key:   # mode or EEPROM limits changed
            self._range_key = (mode, lo, hi)
            self._mode = mode
            k, unit = self._scale()
            self.slider.blockSignals(True)
            self.slider.setRange(int(lo), int(hi))
            self.slider.blockSignals(False)
            self.spin.blockSignals(True)
            slo, shi = (-1048575, 1048575) if mode == 4 else (lo, hi)
            self.spin.setRange(slo * k, shi * k)
            self.spin.setSuffix(f" {unit}")
            self.spin.blockSignals(False)
            self.title.setText({1: "Goal velocity", 16: "Goal PWM", 4: "Goal position (multi-turn)"}.get(mode, "Goal position"))
            self.stop_btn.setVisible(mode in (1, 16))
            self.center_btn.setVisible(mode in (3, 4))
        # present marker
        present = {1: "Present Velocity", 16: "Present PWM"}.get(mode, "Present Position")
        self.slider.set_present(m.raw(i, present))
        if not self._dragging and not self.spin.hasFocus():
            g = m.raw(i, m.goal_item(i))
            self.slider.blockSignals(True)
            self.slider.setValue(int(g))
            self.slider.blockSignals(False)
            self.spin.blockSignals(True)
            self.spin.setValue(g * self._scale()[0])
            self.spin.blockSignals(False)
        self.setEnabled(m.torque(i) and m.connected)

    def _slider_moved(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v * self._scale()[0])
        self.spin.blockSignals(False)
        self.m.send_goal(self.id, v)

    def _released(self):
        self._dragging = False
        self.m.send_goal(self.id, self.slider.value())

    def _spin_changed(self, v):
        raw = round(v / self._scale()[0])
        self._send(raw)

    def _send(self, raw):
        self.slider.blockSignals(True)
        self.slider.setValue(int(raw))
        self.slider.blockSignals(False)
        self.m.send_goal(self.id, raw)

    def _hold(self):
        self._send(self.m.raw(self.id, "Present Position"))


class MotorCard(QFrame):
    clicked = Signal(int)

    def __init__(self, model, dxl_id):
        super().__init__()
        self.setObjectName("card")
        self.m, self.id = model, dxl_id
        self.setMinimumWidth(300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.dot = Dot()
        self.name = QLabel()
        self.name.setObjectName("h1")
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.torque = QPushButton("Torque")
        self.torque.setObjectName("torque")
        self.torque.setCheckable(True)
        self.torque.clicked.connect(lambda on: self.m.set_torque(self.id, on))
        head.addWidget(self.dot)
        head.addWidget(self.name)
        head.addWidget(self.sub)
        head.addStretch()
        head.addWidget(self.torque)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.mode = QLabel()
        self.mode.setObjectName("badge")
        self.mode.setStyleSheet(f"background:{style.PANEL2}; color:{style.ACCENT};")
        self.err = QLabel()
        self.err.setObjectName("badge")
        self.err.setStyleSheet(f"background:#4a1f22; color:{style.BAD};")
        self.reboot = QPushButton("Reboot")
        self.reboot.setObjectName("danger")
        self.reboot.clicked.connect(lambda: self.m.worker.post("_do_reboot", self.id))
        row.addWidget(self.mode)
        row.addWidget(self.err)
        row.addStretch()
        row.addWidget(self.reboot)
        lay.addLayout(row)

        self.big = QLabel("—")
        self.big.setObjectName("big")
        lay.addWidget(self.big)

        self.goal = GoalControl(model, dxl_id)
        lay.addWidget(self.goal)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.vals = {}
        for col, (key, label) in enumerate([("vel", "Velocity"), ("load", "Load"), ("volt", "Voltage"), ("temp", "Temp")]):
            l = QLabel(label)
            l.setObjectName("muted")
            v = QLabel("—")
            v.setObjectName("value")
            grid.addWidget(l, 0, col)
            grid.addWidget(v, 1, col)
            self.vals[key] = v
        lay.addLayout(grid)
        self.refresh()

    def mousePressEvent(self, e):
        self.clicked.emit(self.id)
        super().mousePressEvent(e)

    def set_selected(self, sel):
        self.setProperty("selected", "true" if sel else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def refresh(self):
        m, i = self.m, self.id
        cfg = m.ws.motor(i)
        self.name.setText(cfg.name if cfg else f"ID {i}")
        model = ct.MODEL_NAMES.get(m.raw(i, "Model Number", cfg.model if cfg else 0), "")
        short = model.split(" /")[0]          # "XC430-W150 / T150BB" -> "XC430-W150" (cards are narrow)
        self.sub.setText(f"ID {i}" + (f" · {short}" if short else ""))
        self.sub.setToolTip(model)
        online = m.is_online.get(i, False) and m.connected
        err = m.raw(i, "Hardware Error Status")
        self.dot.set("bad" if err else ("ok" if online else "grey"))
        self.torque.blockSignals(True)
        self.torque.setChecked(m.torque(i))
        self.torque.setText("Torque ON" if m.torque(i) else "Torque OFF")
        self.torque.blockSignals(False)
        self.torque.setEnabled(online)
        self.mode.setText(ct.OPERATING_MODES.get(m.mode(i), f"Mode {m.mode(i)}"))
        self.err.setVisible(bool(err))
        self.reboot.setVisible(bool(err))
        self.err.setText("⚠ " + ct.hw_error_text(err))
        pos = m.raw(i, "Present Position")
        self.big.setText(f"{pos * ct.DEG_PER_TICK:8.1f}°" if online else "offline")
        temp = m.raw(i, "Present Temperature")
        self.vals["vel"].setText(f"{m.raw(i, 'Present Velocity') * ct.RPM_PER_UNIT:.1f} rpm")
        self.vals["load"].setText(f"{m.raw(i, 'Present Load') * 0.1:.1f} %")
        self.vals["volt"].setText(f"{m.raw(i, 'Present Input Voltage') * 0.1:.1f} V")
        self.vals["temp"].setText(f"{temp} °C")
        lim = m.raw(i, "Temperature Limit", 80)
        self.vals["temp"].setStyleSheet(f"color:{style.BAD if temp >= lim - 5 else style.TEXT}")
        self.goal.refresh()
