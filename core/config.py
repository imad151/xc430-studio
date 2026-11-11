"""Save / load the workspace (motors, names, teleop mappings, poses) as JSON."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = Path.home() / ".xc430_studio.json"

DEFAULT_KEYS = [("Q", "A"), ("W", "S"), ("E", "D"), ("R", "F"), ("T", "G"), ("Y", "H"),
                ("U", "J"), ("I", "K"), ("O", "L"), ("P", ";")]


@dataclass
class MotorCfg:
    id: int
    name: str = ""
    model: int = 0
    key_plus: str = ""
    key_minus: str = ""
    key_speed: float = 60.0         # deg/s (position) or rpm (velocity) or % (PWM)
    axis: int = -1                  # gamepad axis index, -1 = none
    axis_invert: bool = False
    axis_speed: float = 90.0
    follow: int = -1                # leader motor id for leader-follower, -1 = none
    follow_invert: bool = False
    follow_offset: float = 0.0      # degrees

    def label(self):
        return f"{self.name or 'Motor'} (ID {self.id})"


@dataclass
class Workspace:
    port: str = ""
    baud: int = 57600
    simulate: bool = False
    poll_hz: float = 30.0
    motors: list[MotorCfg] = field(default_factory=list)
    poses: list[dict] = field(default_factory=list)   # [{"name": str, "goals": {id: ticks}, "duration": s}]
    estop_button: int = -1
    deadzone: float = 0.12

    def motor(self, dxl_id) -> MotorCfg | None:
        return next((m for m in self.motors if m.id == dxl_id), None)

    def add_motor(self, dxl_id, model=0) -> MotorCfg:
        m = self.motor(dxl_id)
        if m:
            if model:
                m.model = model
            return m
        k = DEFAULT_KEYS[len(self.motors) % len(DEFAULT_KEYS)]
        m = MotorCfg(dxl_id, f"Joint {len(self.motors) + 1}", model, k[0], k[1])
        self.motors.append(m)
        self.motors.sort(key=lambda x: x.id)
        return m

    def save(self, path: Path = DEFAULT_PATH):
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Workspace":
        try:
            d = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return cls()
        known = set(MotorCfg.__dataclass_fields__)
        motors = [MotorCfg(**{k: v for k, v in m.items() if k in known}) for m in d.pop("motors", [])]
        ws = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        ws.motors = motors
        for p in ws.poses:  # JSON keys are strings
            p["goals"] = {int(k): v for k, v in p.get("goals", {}).items()}
        return ws
