"""
Low-level bus access. Two implementations share one interface:

  * SdkBus  - real hardware through the official `dynamixel_sdk` (Protocol 2.0)
  * SimBus  - an in-memory simulator so you can use the GUI without motors

Only the BusWorker thread should call these methods (they are not re-entrant).
"""
from __future__ import annotations

import math
import random
import time

from . import control_table as ct


class BusError(Exception):
    pass


class BaseBus:
    def open(self, port: str, baud: int) -> None: ...
    def close(self) -> None: ...
    def set_baud(self, baud: int) -> None: ...
    def scan(self, id_range=range(0, 253)) -> list[tuple[int, int]]: ...
    def ping(self, dxl_id: int) -> int | None: ...
    def read(self, dxl_id: int, item: ct.Item) -> int: ...
    def write(self, dxl_id: int, item: ct.Item, value: int) -> None: ...
    def read_block(self, dxl_id: int, start: int, length: int) -> bytes: ...
    def sync_read(self, ids: list[int], start: int, length: int) -> dict[int, bytes]: ...
    def sync_write(self, item: ct.Item, values: dict[int, int]) -> None: ...
    def reboot(self, dxl_id: int) -> None: ...
    def factory_reset(self, dxl_id: int, option: int = 0x02) -> None: ...


# --------------------------------------------------------------------------- real hardware
class SdkBus(BaseBus):
    def __init__(self):
        import dynamixel_sdk as sdk  # imported lazily so the simulator works without it
        self.sdk = sdk
        self.port = None
        self.ph = sdk.PacketHandler(2.0)

    # helpers
    def _check(self, comm, err, what: str):
        sdk = self.sdk
        if comm != sdk.COMM_SUCCESS:
            raise BusError(f"{what}: {self.ph.getTxRxResult(comm)}")
        if err:
            # bit 7 = hardware alert; still a valid response, report but don't fail
            if err & 0x80 and (err & 0x7F) == 0:
                return "hw_alert"
            raise BusError(f"{what}: {self.ph.getRxPacketError(err)}")
        return None

    def open(self, port, baud):
        self.port = self.sdk.PortHandler(port)
        if not self.port.openPort():
            raise BusError(f"Could not open {port}")
        if not self.port.setBaudRate(baud):
            self.port.closePort()
            raise BusError(f"Could not set baud rate {baud}")

    def close(self):
        if self.port:
            self.port.closePort()
            self.port = None

    def set_baud(self, baud):
        if not self.port.setBaudRate(baud):
            raise BusError(f"Could not set baud rate {baud}")

    def scan(self, id_range=range(0, 253)):
        data, comm = self.ph.broadcastPing(self.port)
        if comm == self.sdk.COMM_SUCCESS:
            return sorted((i, v[0]) for i, v in data.items() if i in id_range)
        # fall back to individual pings (slow but robust)
        found = []
        for i in id_range:
            m = self.ping(i)
            if m is not None:
                found.append((i, m))
        return found

    def ping(self, dxl_id):
        model, comm, err = self.ph.ping(self.port, dxl_id)
        return model if comm == self.sdk.COMM_SUCCESS else None

    def read(self, dxl_id, item):
        fn = {1: self.ph.read1ByteTxRx, 2: self.ph.read2ByteTxRx, 4: self.ph.read4ByteTxRx}[item.size]
        val, comm, err = fn(self.port, dxl_id, item.addr)
        self._check(comm, err, f"read {item.name} [ID {dxl_id}]")
        return ct.to_signed(val, item.size) if item.signed else val

    def write(self, dxl_id, item, value):
        fn = {1: self.ph.write1ByteTxRx, 2: self.ph.write2ByteTxRx, 4: self.ph.write4ByteTxRx}[item.size]
        comm, err = fn(self.port, dxl_id, item.addr, ct.to_unsigned(int(value), item.size))
        self._check(comm, err, f"write {item.name}={value} [ID {dxl_id}]")

    def read_block(self, dxl_id, start, length):
        data, comm, err = self.ph.readTxRx(self.port, dxl_id, start, length)
        self._check(comm, err, f"read block {start}+{length} [ID {dxl_id}]")
        return bytes(data)

    def sync_read(self, ids, start, length):
        if not ids:
            return {}
        g = self.sdk.GroupSyncRead(self.port, self.ph, start, length)
        for i in ids:
            g.addParam(i)
        comm = g.txRxPacket()
        out = {}
        if comm == self.sdk.COMM_SUCCESS:
            for i in ids:
                if g.isAvailable(i, start, length):
                    out[i] = bytes(g.data_dict[i][:length])
            return out
        # Sync read fails as a whole if one motor is missing -> read individually
        for i in ids:
            try:
                out[i] = self.read_block(i, start, length)
            except BusError:
                pass
        return out

    def sync_write(self, item, values):
        if not values:
            return
        g = self.sdk.GroupSyncWrite(self.port, self.ph, item.addr, item.size)
        for i, v in values.items():
            g.addParam(i, list(ct.to_unsigned(int(v), item.size).to_bytes(item.size, "little")))
        comm = g.txPacket()
        if comm != self.sdk.COMM_SUCCESS:
            raise BusError(f"sync write {item.name}: {self.ph.getTxRxResult(comm)}")

    def reboot(self, dxl_id):
        comm, err = self.ph.reboot(self.port, dxl_id)
        self._check(comm, err, f"reboot [ID {dxl_id}]")

    def factory_reset(self, dxl_id, option=0x02):
        # 0xFF: reset all, 0x01: all except ID, 0x02: all except ID and baud
        comm, err = self.ph.factoryReset(self.port, dxl_id, option)
        self._check(comm, err, f"factory reset [ID {dxl_id}]")


# --------------------------------------------------------------------------- simulator
class _SimMotor:
    def __init__(self, dxl_id: int, model: int):
        self.mem = bytearray(147)
        self.t = time.monotonic()
        self.pos = random.uniform(1500, 2600)   # ticks (float)
        self.vel = 0.0                           # ticks/s
        self.temp = 28.0
        defaults = {
            "Model Number": model, "Firmware Version": 46, "ID": dxl_id, "Baud Rate": 1,
            "Return Delay Time": 250, "Operating Mode": 3, "Protocol Type": 2,
            "Moving Threshold": 10, "Temperature Limit": 80, "Max Voltage Limit": 160,
            "Min Voltage Limit": 60, "PWM Limit": 885, "Velocity Limit": 306 if model == 1070 else 199,
            "Max Position Limit": 4095, "Min Position Limit": 0, "Shutdown": 52,
            "Status Return Level": 2, "Velocity I Gain": 1920, "Velocity P Gain": 100,
            "Position P Gain": 640, "Goal Position": int(self.pos), "Present Input Voltage": 120,
            "Goal PWM": 885,   # like real motors: defaults to the PWM Limit
        }
        for k, v in defaults.items():
            self.set(ct.BY_NAME[k], v)

    def get(self, item):
        v = int.from_bytes(self.mem[item.addr:item.addr + item.size], "little")
        return ct.to_signed(v, item.size) if item.signed else v

    def set(self, item, value):
        self.mem[item.addr:item.addr + item.size] = ct.to_unsigned(int(value), item.size).to_bytes(item.size, "little")

    def step(self):
        now = time.monotonic()
        dt = min(now - self.t, 0.2)
        self.t = now
        g = lambda n: self.get(ct.BY_NAME[n])
        torque = g("Torque Enable")
        mode = g("Operating Mode")
        reverse = -1 if g("Drive Mode") & 1 else 1
        vmax = max(g("Velocity Limit"), 1) * ct.RPM_PER_UNIT / 60 * ct.TICKS_PER_REV   # ticks/s
        prof_v = g("Profile Velocity")
        if prof_v:
            vmax = min(vmax, prof_v * ct.RPM_PER_UNIT / 60 * ct.TICKS_PER_REV)
        target_v = 0.0
        offset = g("Homing Offset")
        if torque:
            if mode in (3, 4):
                goal = g("Goal Position")
                if mode == 3:
                    goal = max(g("Min Position Limit"), min(g("Max Position Limit"), goal))
                err = goal - (self.pos + offset)
                target_v = max(-vmax, min(vmax, err * 8))
            elif mode == 1:
                target_v = g("Goal Velocity") * ct.RPM_PER_UNIT / 60 * ct.TICKS_PER_REV * reverse
                target_v = max(-vmax, min(vmax, target_v))
            elif mode == 16:
                target_v = g("Goal PWM") / 885 * vmax * reverse
        # Goal PWM caps the output in every mode (0 = limp even with torque on, like the real motor)
        cap = max(0, min(g("Goal PWM") if mode != 16 else 885, g("PWM Limit"))) / 885 * vmax
        if mode != 16:
            target_v = max(-cap, min(cap, target_v))
        # first-order velocity response + gravity-ish sag when torque is off
        self.vel += (target_v - self.vel) * min(1.0, dt * 12)
        if not torque:
            self.vel *= 0.8
        self.pos += self.vel * dt
        if mode != 4 and not torque:
            self.pos %= ct.TICKS_PER_REV
        load = (target_v - self.vel) / max(vmax, 1) * 400 + random.gauss(0, 8) if torque else random.gauss(0, 2)
        self.temp += ((30 + abs(load) / 40) - self.temp) * dt * 0.05
        self.set(ct.BY_NAME["Present Position"], round(self.pos + offset))
        self.set(ct.BY_NAME["Position Trajectory"], round(self.pos + offset))
        self.set(ct.BY_NAME["Velocity Trajectory"], round(target_v / ct.TICKS_PER_REV * 60 / ct.RPM_PER_UNIT))
        self.set(ct.BY_NAME["Present Velocity"], round(self.vel / ct.TICKS_PER_REV * 60 / ct.RPM_PER_UNIT))
        self.set(ct.BY_NAME["Present Load"], round(max(-1000, min(1000, load))))
        self.set(ct.BY_NAME["Present PWM"], round(self.vel / max(vmax, 1) * 885 * 0.8))
        self.set(ct.BY_NAME["Present Temperature"], round(self.temp))
        self.set(ct.BY_NAME["Present Input Voltage"], 120 + round(random.gauss(0, 0.6)))
        self.set(ct.BY_NAME["Moving"], 1 if abs(self.vel) > 5 else 0)
        self.set(ct.BY_NAME["Realtime Tick"], int(now * 1000) % 32768)


class SimBus(BaseBus):
    """Pretends there are `n` XC430s on the bus with IDs 1..n."""

    def __init__(self, n: int = 4):
        self.motors = {i: _SimMotor(i, 1070 if i % 2 else 1080) for i in range(1, n + 1)}
        self.is_open = False

    def _m(self, dxl_id):
        m = self.motors.get(dxl_id)
        if m is None or not self.is_open:
            raise BusError(f"[TxRxResult] There is no status packet! (ID {dxl_id})")
        time.sleep(0.0005)
        m.step()
        return m

    def open(self, port, baud):
        self.is_open = True

    def close(self):
        self.is_open = False

    def set_baud(self, baud):
        pass

    def scan(self, id_range=range(0, 253)):
        return [(i, m.get(ct.BY_NAME["Model Number"])) for i, m in sorted(self.motors.items()) if i in id_range]

    def ping(self, dxl_id):
        m = self.motors.get(dxl_id)
        return m.get(ct.BY_NAME["Model Number"]) if m else None

    def read(self, dxl_id, item):
        return self._m(dxl_id).get(item)

    def write(self, dxl_id, item, value):
        m = self._m(dxl_id)
        if not item.writable:
            raise BusError(f"{item.name} is read-only")
        if item.eeprom and m.get(ct.BY_NAME["Torque Enable"]):
            raise BusError(f"write {item.name} [ID {dxl_id}]: [RxPacketError] Access error (torque is on)")
        if item.min is not None and not (item.min <= int(value) <= item.max):
            raise BusError(f"write {item.name}={value} [ID {dxl_id}]: [RxPacketError] Data range error")
        if item.name == "Operating Mode" and int(value) not in ct.OPERATING_MODES:
            raise BusError(f"[ID {dxl_id}] mode {value} not supported on XC430")
        m.set(item, value)
        if item.name == "ID" and int(value) != dxl_id:
            self.motors[int(value)] = self.motors.pop(dxl_id)
        if item.name == "Operating Mode":
            # Like real XC430s: a mode change resets the motion profile, but goals (incl. Goal PWM,
            # which caps output in every mode) keep their old values.
            m.set(ct.BY_NAME["Profile Velocity"], 0)
            m.set(ct.BY_NAME["Profile Acceleration"], 0)

    def read_block(self, dxl_id, start, length):
        return bytes(self._m(dxl_id).mem[start:start + length])

    def sync_read(self, ids, start, length):
        out = {}
        for i in ids:
            try:
                out[i] = self.read_block(i, start, length)
            except BusError:
                pass
        return out

    def sync_write(self, item, values):
        for i, v in values.items():
            if i in self.motors:
                self.motors[i].set(item, v)

    def reboot(self, dxl_id):
        m = self._m(dxl_id)
        m.set(ct.BY_NAME["Torque Enable"], 0)
        m.set(ct.BY_NAME["Hardware Error Status"], 0)

    def factory_reset(self, dxl_id, option=0x02):
        m = self._m(dxl_id)
        self.motors[dxl_id] = _SimMotor(dxl_id, m.get(ct.BY_NAME["Model Number"]))
