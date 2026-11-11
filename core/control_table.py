"""
Control table for ROBOTIS DYNAMIXEL XC430 (W150 / W240 / T150BB / T240BB), Protocol 2.0.

Reference: https://emanual.robotis.com/docs/en/dxl/x/xc430-w150/
Note: the XC430 has no current sensor, so there is no Goal Current / Current Limit and
current-based operating modes are not available. "Present Load" (addr 126) is reported instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Item:
    name: str
    addr: int
    size: int
    access: str            # "R" or "RW"
    eeprom: bool
    unit: Optional[float] = None   # multiply raw by this to get physical value
    unit_name: str = ""
    signed: bool = False
    min: Optional[int] = None
    max: Optional[int] = None
    desc: str = ""

    @property
    def writable(self) -> bool:
        return "W" in self.access


ITEMS: list[Item] = [
    # ---------------- EEPROM (torque must be OFF to write) ----------------
    Item("Model Number", 0, 2, "R", True, desc="Model number"),
    Item("Model Information", 2, 4, "R", True),
    Item("Firmware Version", 6, 1, "R", True),
    Item("ID", 7, 1, "RW", True, min=0, max=252, desc="Motor ID (0-252)"),
    Item("Baud Rate", 8, 1, "RW", True, min=0, max=7, desc="0:9600 1:57600 2:115200 3:1M 4:2M 5:3M 6:4M 7:4.5M"),
    Item("Return Delay Time", 9, 1, "RW", True, 2.0, "us", min=0, max=254),
    Item("Drive Mode", 10, 1, "RW", True, min=0, max=13, desc="bit0: reverse direction, bit2: time-based profile"),
    Item("Operating Mode", 11, 1, "RW", True, min=0, max=16, desc="1:Velocity 3:Position 4:Extended Position 16:PWM"),
    Item("Secondary(Shadow) ID", 12, 1, "RW", True, min=0, max=255),
    Item("Protocol Type", 13, 1, "RW", True, min=1, max=2),
    Item("Homing Offset", 20, 4, "RW", True, 0.088, "deg", True, -1044479, 1044479),
    Item("Moving Threshold", 24, 4, "RW", True, 0.229, "rpm", min=0, max=1023),
    Item("Temperature Limit", 31, 1, "RW", True, 1.0, "C", min=0, max=100),
    Item("Max Voltage Limit", 32, 2, "RW", True, 0.1, "V", min=60, max=160),
    Item("Min Voltage Limit", 34, 2, "RW", True, 0.1, "V", min=60, max=160),
    Item("PWM Limit", 36, 2, "RW", True, 0.113, "%", min=0, max=885),
    Item("Velocity Limit", 44, 4, "RW", True, 0.229, "rpm", min=0, max=1023),
    Item("Max Position Limit", 48, 4, "RW", True, 0.088, "deg", min=0, max=4095),
    Item("Min Position Limit", 52, 4, "RW", True, 0.088, "deg", min=0, max=4095),
    Item("Startup Configuration", 60, 1, "RW", True, min=0, max=3, desc="bit0: torque on at boot, bit1: RAM restore"),
    Item("Shutdown", 63, 1, "RW", True, min=0, max=255, desc="Error bits that trigger torque-off"),
    # ---------------- RAM ----------------
    Item("Torque Enable", 64, 1, "RW", False, min=0, max=1),
    Item("LED", 65, 1, "RW", False, min=0, max=1),
    Item("Status Return Level", 68, 1, "RW", False, min=0, max=2),
    Item("Registered Instruction", 69, 1, "R", False),
    Item("Hardware Error Status", 70, 1, "R", False),
    Item("Velocity I Gain", 76, 2, "RW", False, min=0, max=16383),
    Item("Velocity P Gain", 78, 2, "RW", False, min=0, max=16383),
    Item("Position D Gain", 80, 2, "RW", False, min=0, max=16383),
    Item("Position I Gain", 82, 2, "RW", False, min=0, max=16383),
    Item("Position P Gain", 84, 2, "RW", False, min=0, max=16383),
    Item("Feedforward 2nd Gain", 88, 2, "RW", False, min=0, max=16383),
    Item("Feedforward 1st Gain", 90, 2, "RW", False, min=0, max=16383),
    Item("Bus Watchdog", 98, 1, "RW", False, 20.0, "ms", min=0, max=127),
    Item("Goal PWM", 100, 2, "RW", False, 0.113, "%", True, -885, 885),
    Item("Goal Velocity", 104, 4, "RW", False, 0.229, "rpm", True, -1023, 1023),
    Item("Profile Acceleration", 108, 4, "RW", False, 214.577, "rev/min2", min=0, max=32767),
    Item("Profile Velocity", 112, 4, "RW", False, 0.229, "rpm", min=0, max=32767),
    Item("Goal Position", 116, 4, "RW", False, 0.088, "deg", True, -1048575, 1048575),
    Item("Realtime Tick", 120, 2, "R", False, 1.0, "ms"),
    Item("Moving", 122, 1, "R", False),
    Item("Moving Status", 123, 1, "R", False),
    Item("Present PWM", 124, 2, "R", False, 0.113, "%", True),
    Item("Present Load", 126, 2, "R", False, 0.1, "%", True),
    Item("Present Velocity", 128, 4, "R", False, 0.229, "rpm", True),
    Item("Present Position", 132, 4, "R", False, 0.088, "deg", True),
    Item("Velocity Trajectory", 136, 4, "R", False, 0.229, "rpm", True),
    Item("Position Trajectory", 140, 4, "R", False, 0.088, "deg", True),
    Item("Present Input Voltage", 144, 2, "R", False, 0.1, "V"),
    Item("Present Temperature", 146, 1, "R", False, 1.0, "C"),
]

BY_NAME: dict[str, Item] = {i.name: i for i in ITEMS}
EEPROM_ITEMS = [i for i in ITEMS if i.eeprom]
RAM_ITEMS = [i for i in ITEMS if not i.eeprom]

# Memory blocks used for polling
EEPROM_BLOCK = (0, 64)            # start, length -> addr 0..63
RAM_BLOCK = (64, 147 - 64)        # addr 64..146
FAST_BLOCK = (116, 147 - 116)     # goal position .. temperature (cheap, polled every cycle)

OPERATING_MODES = {1: "Velocity", 3: "Position", 4: "Extended Position", 16: "PWM"}
BAUD_RATES = {0: 9600, 1: 57600, 2: 115200, 3: 1_000_000, 4: 2_000_000,
              5: 3_000_000, 6: 4_000_000, 7: 4_500_000}
HW_ERROR_BITS = {0: "Input voltage", 2: "Overheating", 3: "Motor encoder",
                 4: "Electrical shock", 5: "Overload"}
MODEL_NAMES = {1070: "XC430-W150 / T150BB", 1080: "XC430-W240 / T240BB",
               1160: "2XC430-W250", 1060: "XL430-W250", 1090: "2XL430-W250",
               1200: "XM430-W350", 1020: "XM430-W210"}

TICKS_PER_REV = 4096
DEG_PER_TICK = 0.088
RPM_PER_UNIT = 0.229
PWM_PCT_PER_UNIT = 0.113


def to_signed(value: int, size: int) -> int:
    bits = size * 8
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


def to_unsigned(value: int, size: int) -> int:
    return value & ((1 << (size * 8)) - 1)


def decode_block(start: int, data: bytes, items=ITEMS) -> dict[str, int]:
    """Decode every item that lies completely inside the raw byte block."""
    out: dict[str, int] = {}
    end = start + len(data)
    for it in items:
        if it.addr >= start and it.addr + it.size <= end:
            off = it.addr - start
            v = int.from_bytes(data[off:off + it.size], "little")
            out[it.name] = to_signed(v, it.size) if it.signed else v
    return out


def physical(item: Item, raw: int) -> str:
    if item.unit is None:
        return str(raw)
    v = raw * item.unit
    return f"{v:.1f} {item.unit_name}" if abs(v) < 1e5 else f"{v:.0f} {item.unit_name}"


def hw_error_text(bits: int) -> str:
    if not bits:
        return ""
    return ", ".join(name for b, name in HW_ERROR_BITS.items() if bits & (1 << b))
