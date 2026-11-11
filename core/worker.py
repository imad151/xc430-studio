"""
BusWorker: a single background thread that owns the serial port.

The GUI never touches the bus directly. It posts commands (queue) and receives
Qt signals with fresh motor state. High-rate goal streams (teleop, sliders) are
coalesced so the queue can never back up: only the newest goal per motor is sent.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from . import control_table as ct
from .bus import BaseBus, BusError, SdkBus, SimBus


class BusWorker(QThread):
    connected = Signal(bool, str)            # ok, message
    scanned = Signal(object)                 # [(id, model)]
    state_ready = Signal()                   # new state waiting: call take_state() (fast + periodic RAM)
    eeprom = Signal(int, object)               # id, {item_name: raw}
    online = Signal(object)                    # {id: bool}
    log = Signal(str, str)                   # level, message
    stats = Signal(float)                    # achieved poll Hz

    def __init__(self):
        super().__init__()
        self.bus: BaseBus | None = None
        self.cmds: queue.Queue = queue.Queue()
        self.ids: list[int] = []
        self.poll_hz = 30.0
        self.polling = True
        self._goal_lock = threading.Lock()
        self._goals: dict[str, dict[int, int]] = {}   # item name -> {id: value}
        self._running = True
        self._torque: dict[int, int] = {}
        self._fail: dict[int, int] = {}
        # Back-pressure for live state: at most one state_ready signal is in flight. New reads merge
        # into _pending until the GUI takes them, so a slow GUI gets fewer, fresher updates instead
        # of an ever-growing backlog of queued signals (which freezes the event loop).
        self._state_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._state_inflight = False

    # ------------------------------------------------------------------ public API (GUI thread)
    def post(self, fn_name: str, *args):
        self.cmds.put((fn_name, args))

    def connect_bus(self, port: str, baud: int, simulate: bool):
        self.post("_do_connect", port, baud, simulate)

    def disconnect_bus(self):
        self.post("_do_disconnect")

    def set_ids(self, ids):
        self.ids = sorted(set(ids))

    def write(self, dxl_id: int, name: str, value: int):
        self.post("_do_write", dxl_id, name, int(value))

    def stream_goal(self, name: str, values: dict[int, int]):
        """Coalesced high-rate write (sent with one sync-write per poll cycle)."""
        with self._goal_lock:
            self._goals.setdefault(name, {}).update({i: int(v) for i, v in values.items()})

    def estop(self):
        # jump the queue: clear pending commands and goals, then torque off everything
        with self._goal_lock:
            self._goals.clear()
        try:
            while True:
                self.cmds.get_nowait()
        except queue.Empty:
            pass
        self.post("_do_estop")

    def take_state(self) -> dict[int, dict]:
        """GUI thread: fetch all live state merged since the last call ({id: {item_name: raw}})."""
        with self._state_lock:
            d, self._pending = self._pending, {}
            self._state_inflight = False
        return d

    def _push_state(self, decoded: dict[int, dict]):
        with self._state_lock:
            for i, vals in decoded.items():
                self._pending.setdefault(i, {}).update(vals)
            notify = not self._state_inflight
            self._state_inflight = True
        if notify:
            self.state_ready.emit()

    def _torque_changed(self, dxl_id, on):
        """Record a torque write and tell the GUI right away (Torque Enable is only polled every 0.5 s)."""
        self._torque[dxl_id] = int(on)
        self._push_state({dxl_id: {"Torque Enable": int(on)}})

    def stop(self):
        self._running = False
        self.wait(2000)

    # ------------------------------------------------------------------ thread
    def run(self):
        last_ram = 0.0
        cycles, t_stats = 0, time.monotonic()
        while self._running:
            t0 = time.monotonic()
            # 1) commands
            try:
                while True:
                    name, args = self.cmds.get_nowait()
                    self._safe(getattr(self, name), *args)
            except queue.Empty:
                pass
            if not self.bus:
                time.sleep(0.05)
                continue
            # 2) coalesced goal streams
            with self._goal_lock:
                goals, self._goals = self._goals, {}
            for name, vals in goals.items():
                self._safe(self.bus.sync_write, ct.BY_NAME[name], vals)
            # 3) polling
            if self.polling and self.ids:
                now = time.monotonic()
                full = now - last_ram > 0.5
                start, length = ct.RAM_BLOCK if full else ct.FAST_BLOCK
                if full:
                    last_ram = now
                try:
                    data = self.bus.sync_read(self.ids, start, length)
                except BusError as e:
                    data = {}
                    self.log.emit("error", str(e))
                decoded = {i: ct.decode_block(start, b) for i, b in data.items()}
                for i, d in decoded.items():
                    if "Torque Enable" in d:
                        self._torque[i] = d["Torque Enable"]
                changed = {}
                for i in self.ids:
                    was = self._fail.get(i, 0) < 3
                    self._fail[i] = 0 if i in data else self._fail.get(i, 0) + 1
                    now_ok = self._fail[i] < 3
                    if was != now_ok or full:
                        changed[i] = now_ok
                if changed:
                    self.online.emit(changed)
                if decoded:
                    self._push_state(decoded)
                cycles += 1
            if time.monotonic() - t_stats > 1.0:
                self.stats.emit(cycles / (time.monotonic() - t_stats))
                cycles, t_stats = 0, time.monotonic()
            dt = time.monotonic() - t0
            time.sleep(max(0.001, 1.0 / self.poll_hz - dt))
        if self.bus:
            self.bus.close()

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except BusError as e:
            self.log.emit("error", str(e))
        except Exception as e:  # never let the bus thread die
            self.log.emit("error", f"{type(e).__name__}: {e}")
            traceback.print_exc()

    # ------------------------------------------------------------------ commands (bus thread)
    def _do_connect(self, port, baud, simulate):
        if self.bus:
            self.bus.close()
            self.bus = None
        try:
            bus = SimBus() if simulate else SdkBus()
            bus.open(port, baud)
        except ImportError:
            self.connected.emit(False, "dynamixel_sdk is not installed (pip install dynamixel-sdk)")
            return
        except BusError as e:
            self.connected.emit(False, str(e))
            return
        self.bus = bus
        self.connected.emit(True, "Simulator" if simulate else f"{port} @ {baud}")

    def _do_disconnect(self):
        if self.bus:
            self.bus.close()
        self.bus = None
        self.connected.emit(False, "Disconnected")

    def _do_scan(self, bauds: list[int], id_lo: int, id_hi: int):
        found = []
        current = None
        for b in bauds:
            self.bus.set_baud(b)
            current = b
            res = self.bus.scan(range(id_lo, id_hi + 1))
            if res:
                self.log.emit("info", f"Found {len(res)} motor(s) at {b} bps: " +
                              ", ".join(f"ID {i} ({ct.MODEL_NAMES.get(m, m)})" for i, m in res))
                found = res
                break
        if not found:
            self.log.emit("warn", "No motors found. Check power, wiring, baud rate and U2D2 port.")
        self.scanned.emit([(i, m, current) for i, m in found])

    def _do_add(self, dxl_id):
        model = self.bus.ping(dxl_id)
        if model is None:
            self.log.emit("warn", f"ID {dxl_id} did not respond to ping")
        self.scanned.emit([(dxl_id, model or 0, None)])

    def _do_write(self, dxl_id, name, value):
        item = ct.BY_NAME[name]
        if item.eeprom and self._torque.get(dxl_id):
            self.bus.write(dxl_id, ct.BY_NAME["Torque Enable"], 0)
            self._torque_changed(dxl_id, 0)
            self.log.emit("warn", f"ID {dxl_id}: torque disabled to write EEPROM item '{name}'")
        self.bus.write(dxl_id, item, value)
        if name == "Torque Enable":
            self._torque_changed(dxl_id, value)
        self.log.emit("debug", f"ID {dxl_id}: {name} <- {value}")
        if name == "ID":
            # report under the *old* id so the GUI can re-map the motor
            self.ids = sorted((set(self.ids) - {dxl_id}) | {value})
            self._do_read_eeprom(value, report_as=dxl_id)
            self.log.emit("info", f"Motor ID {dxl_id} -> {value}")
        elif item.eeprom:
            self._do_read_eeprom(dxl_id)

    def _do_read_eeprom(self, dxl_id, report_as=None):
        start, length = ct.EEPROM_BLOCK
        data = self.bus.read_block(dxl_id, start, length)
        self.eeprom.emit(dxl_id if report_as is None else report_as, ct.decode_block(start, data))

    def _do_estop(self):
        if not self.bus:
            return
        ids = self.ids or []
        try:
            self.bus.sync_write(ct.BY_NAME["Torque Enable"], {i: 0 for i in ids})
        except BusError:
            pass
        for i in ids:  # confirm individually
            try:
                self.bus.write(i, ct.BY_NAME["Torque Enable"], 0)
            except BusError:
                pass
            self._torque_changed(i, 0)
        self.log.emit("warn", "EMERGENCY STOP: torque disabled on all motors")

    def _do_set_mode(self, dxl_id, mode):
        # Drop streamed goals still queued for this motor: they belong to the old mode (e.g. a
        # "Goal PWM = 0" from a Stop click would otherwise land after the change and cap output at 0).
        with self._goal_lock:
            for vals in self._goals.values():
                vals.pop(dxl_id, None)
        was_on = self._torque.get(dxl_id, 0)
        self.bus.write(dxl_id, ct.BY_NAME["Torque Enable"], 0)
        self.bus.write(dxl_id, ct.BY_NAME["Operating Mode"], mode)
        self._torque_changed(dxl_id, 0)
        # Hold still after the mode change: real motors keep their old Goal Velocity / Goal PWM.
        # Careful: Goal PWM is also the PWM *cap* in position/velocity modes (0 = motor can't move),
        # so only zero it for PWM mode; otherwise open it up to the PWM Limit.
        self.bus.write(dxl_id, ct.BY_NAME["Goal Velocity"], 0)
        pwm_cap = 0 if mode == 16 else self.bus.read(dxl_id, ct.BY_NAME["PWM Limit"])
        self.bus.write(dxl_id, ct.BY_NAME["Goal PWM"], pwm_cap)
        pos = self.bus.read(dxl_id, ct.BY_NAME["Present Position"])
        if mode == 3:
            pos %= ct.TICKS_PER_REV
        if mode in (3, 4):
            self.bus.write(dxl_id, ct.BY_NAME["Goal Position"], pos)
        if was_on:
            self._do_torque_safe(dxl_id, True)
        self.log.emit("info", f"ID {dxl_id}: operating mode -> {ct.OPERATING_MODES.get(mode, mode)}")
        self._do_read_eeprom(dxl_id)

    def _do_change_id(self, old, new):
        self._do_write(old, "ID", new)

    def _do_change_baud(self, dxl_id, code):
        if self._torque.get(dxl_id):
            self.bus.write(dxl_id, ct.BY_NAME["Torque Enable"], 0)
            self._torque_changed(dxl_id, 0)
        self.bus.write(dxl_id, ct.BY_NAME["Baud Rate"], code)
        self.log.emit("warn", f"ID {dxl_id}: baud rate set to {ct.BAUD_RATES[code]}. "
                              f"Reconnect at that baud to talk to it again.")

    def _do_hold(self, ids):
        """Set goal = present position (prevents jumps when enabling torque)."""
        for i in ids:
            pos = self.bus.read(i, ct.BY_NAME["Present Position"])
            self.bus.write(i, ct.BY_NAME["Goal Position"], pos)

    def _do_torque_safe(self, dxl_id, on):
        if on:
            mode = self.bus.read(dxl_id, ct.BY_NAME["Operating Mode"])
            if mode in (3, 4):
                pos = self.bus.read(dxl_id, ct.BY_NAME["Present Position"])
                if mode == 3:
                    pos %= ct.TICKS_PER_REV
                self.bus.write(dxl_id, ct.BY_NAME["Goal Position"], pos)
            elif mode == 1:
                self.bus.write(dxl_id, ct.BY_NAME["Goal Velocity"], 0)
            elif mode == 16:
                self.bus.write(dxl_id, ct.BY_NAME["Goal PWM"], 0)
            if mode != 16:
                # Goal PWM caps the output in position/velocity modes; a leftover 0 from PWM mode
                # would leave the motor limp with torque "on".
                lim = self.bus.read(dxl_id, ct.BY_NAME["PWM Limit"])
                if self.bus.read(dxl_id, ct.BY_NAME["Goal PWM"]) < lim:
                    self.bus.write(dxl_id, ct.BY_NAME["Goal PWM"], lim)
        self.bus.write(dxl_id, ct.BY_NAME["Torque Enable"], 1 if on else 0)
        self._torque_changed(dxl_id, on)

    def _do_set_zero_here(self, dxl_id):
        """Adjust Homing Offset so the current physical position reads 2048 (center)."""
        if self._torque.get(dxl_id):
            self.bus.write(dxl_id, ct.BY_NAME["Torque Enable"], 0)
            self._torque_changed(dxl_id, 0)
        off = self.bus.read(dxl_id, ct.BY_NAME["Homing Offset"])
        pos = self.bus.read(dxl_id, ct.BY_NAME["Present Position"])
        new_off = off + (2048 - pos)
        self.bus.write(dxl_id, ct.BY_NAME["Homing Offset"], new_off)
        self.log.emit("info", f"ID {dxl_id}: homing offset {off} -> {new_off} (current position is now 180°)")
        self._do_read_eeprom(dxl_id)

    def _do_reboot(self, dxl_id):
        self.bus.reboot(dxl_id)
        self.log.emit("info", f"ID {dxl_id}: rebooted")

    def _do_factory_reset(self, dxl_id, option):
        self.bus.factory_reset(dxl_id, option)
        self.log.emit("warn", f"ID {dxl_id}: factory reset (option 0x{option:02X})")

    def _do_set_led_all(self, ids, on):
        self.bus.sync_write(ct.BY_NAME["LED"], {i: on for i in ids})
