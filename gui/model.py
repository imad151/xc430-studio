"""Shared application model: workspace config + live motor state + command helpers."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core import control_table as ct
from core.config import Workspace
from core.worker import BusWorker


class Model(QObject):
    updated = Signal(object)          # ids with new live data
    eeprom_updated = Signal(int)
    motors_changed = Signal()
    online_changed = Signal()
    connection_changed = Signal(bool, str)
    selected_changed = Signal(int)

    def __init__(self, ws: Workspace):
        super().__init__()
        self.ws = ws
        self.worker = BusWorker()
        self.worker.poll_hz = ws.poll_hz
        self.state: dict[int, dict] = {}
        self.eeprom: dict[int, dict] = {}
        self.is_online: dict[int, bool] = {}
        self.connected = False
        self.selected: int | None = None
        w = self.worker
        w.state_ready.connect(self._on_state)
        w.eeprom.connect(self._on_eeprom)
        w.online.connect(self._on_online)
        w.scanned.connect(self._on_scanned)
        w.connected.connect(self._on_connected)
        self._sync_ids()

    # ------------------------------------------------------------------ signals from worker
    def _on_state(self):
        # ignore ids no longer in the workspace (e.g. a read that raced an ID change)
        d = {i: v for i, v in self.worker.take_state().items() if self.ws.motor(i)}
        if not d:
            return
        for i, vals in d.items():
            self.state.setdefault(i, {}).update(vals)
        self.updated.emit(set(d))

    def _on_eeprom(self, i, vals):
        self.eeprom[i] = vals
        self.state.setdefault(i, {}).update(vals)
        new_id = vals.get("ID", i)
        if new_id != i:   # ID changed
            m = self.ws.motor(i)
            if m:
                m.id = new_id
                self.eeprom[new_id] = self.eeprom.pop(i)
                self.state[new_id] = self.state.pop(i)
                self._sync_ids()
                self.motors_changed.emit()
                self.select(new_id)
        self.eeprom_updated.emit(new_id)

    def _on_online(self, d):
        self.is_online.update(d)
        self.online_changed.emit()

    def _on_scanned(self, found):
        for i, model, baud in found:
            self.ws.add_motor(i, model)
        if found and found[0][2]:
            self.ws.baud = found[0][2]
        self._sync_ids()
        self.motors_changed.emit()
        for i, _, _ in found:
            self.worker.post("_do_read_eeprom", i)

    def _on_connected(self, ok, msg):
        self.connected = ok
        if ok:
            for m in self.ws.motors:
                self.worker.post("_do_read_eeprom", m.id)
        self.connection_changed.emit(ok, msg)

    # ------------------------------------------------------------------ motors
    def _sync_ids(self):
        self.worker.set_ids([m.id for m in self.ws.motors])

    def remove_motor(self, i):
        self.ws.motors = [m for m in self.ws.motors if m.id != i]
        self.state.pop(i, None)
        self._sync_ids()
        self.motors_changed.emit()

    def select(self, i):
        self.selected = i
        self.selected_changed.emit(i)

    # ------------------------------------------------------------------ getters
    def raw(self, i, name, default=0):
        return self.state.get(i, {}).get(name, default)

    def mode(self, i):
        return self.raw(i, "Operating Mode", 3)

    def torque(self, i):
        return bool(self.raw(i, "Torque Enable"))

    def pos_limits(self, i):
        """Returns (min_ticks, max_ticks) valid for goal position in the current mode."""
        if self.mode(i) == 4:
            return -1048575, 1048575
        lo = self.raw(i, "Min Position Limit", 0)
        hi = self.raw(i, "Max Position Limit", 4095)
        return (lo, hi) if lo < hi else (0, 4095)

    def vel_limit(self, i):
        return self.raw(i, "Velocity Limit", 306) or 306

    def pwm_limit(self, i):
        return self.raw(i, "PWM Limit", 885) or 885

    # ------------------------------------------------------------------ commands
    def goal_item(self, i):
        return {1: "Goal Velocity", 16: "Goal PWM"}.get(self.mode(i), "Goal Position")

    def clamp_goal(self, i, raw):
        m = self.mode(i)
        if m == 1:
            lim = self.vel_limit(i)
            return max(-lim, min(lim, raw))
        if m == 16:
            lim = self.pwm_limit(i)
            return max(-lim, min(lim, raw))
        lo, hi = self.pos_limits(i)
        return max(lo, min(hi, raw))

    def send_goal(self, i, raw, stream=True):
        raw = int(round(self.clamp_goal(i, raw)))
        item = self.goal_item(i)
        self.state.setdefault(i, {})[item] = raw
        if stream:
            self.worker.stream_goal(item, {i: raw})
        else:
            self.worker.write(i, item, raw)

    def send_goals(self, goals: dict[int, float]):
        """Group goals by item and sync-write them in one go."""
        by_item: dict[str, dict[int, int]] = {}
        for i, raw in goals.items():
            raw = int(round(self.clamp_goal(i, raw)))
            item = self.goal_item(i)
            self.state.setdefault(i, {})[item] = raw
            by_item.setdefault(item, {})[i] = raw
        for item, vals in by_item.items():
            self.worker.stream_goal(item, vals)

    def set_torque(self, i, on):
        self.worker.post("_do_torque_safe", i, bool(on))

    def set_mode(self, i, mode):
        self.worker.post("_do_set_mode", i, mode)

    def write(self, i, name, value):
        self.worker.write(i, name, value)

    def estop(self):
        self.worker.estop()

    def label(self, i):
        m = self.ws.motor(i)
        return m.label() if m else f"ID {i}"
