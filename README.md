# XC430 Studio

A Windows desktop app for controlling several ROBOTIS DYNAMIXEL **XC430** motors (W150 / W240 / T150BB / T240BB, Protocol 2.0) from one window, with teleoperation built in. It's meant as a friendlier, use-case-focused alternative to Dynamixel Wizard.

## Download

Get **`XC430Studio.exe`** from the [Releases](../../releases/latest) page and double-click it. Nothing else needs installing.

- Windows may say *"Windows protected your PC"* because the program isn't code-signed. Click **More info → Run anyway**.
- The single `.exe` takes a few seconds to start. The `.zip` (folder version) starts instantly: unzip it, then run `XC430Studio.exe` inside.
- See `QUICKSTART.txt` (included with the download) for connecting your motors.

No motors? Tick **Simulator** in the toolbar and press **Connect** to try everything safely.

## Features

- **Dashboard**: one card per motor showing torque, operating mode, a goal slider (with a marker for the present position), velocity, load, voltage, temperature, and hardware-error badges with a reboot button.
- **Motor detail** tabs:
  - **Control**: torque, LED, operating mode, reverse direction, time-based profile, goal, motion profile, PID and feed-forward gains.
  - **Live plot**: position, velocity and load over time.
  - **Control table**: every register. Double-click a value to write it.
  - **Setup**: change ID or baud rate, limits, homing offset ("make current position the center"), shutdown flags, reboot, factory reset.
- **Teleop**:
  - Keyboard: hold-to-move with per-motor key bindings. Shift gives 3× speed.
  - Gamepad: map an axis to each motor, set deadzone and invert, assign an e-stop button.
  - Leader → follower mirroring: move one motor by hand and another copies it.
  - Poses: capture, reorder, go to, or play a sequence (optionally looped).
- **Safety**:
  - The STOP button turns torque off on every motor. Ctrl+E does the same from anywhere, and so do Esc or Space during teleop, or a gamepad button.
  - Turning torque on holds the current position, so motors don't jump.
  - EEPROM writes turn torque off automatically and say so in the log.
- **Workspace**: motor names, key bindings and poses are saved to `%USERPROFILE%\.xc430_studio.json` on exit. Use File → Save as to keep several setups.

Supported operating modes: Position, Extended Position (multi-turn), Velocity and PWM. The XC430 has no current sensor, so there are no current-based modes; Present Load is shown instead.

## Connecting

1. Plug the motors into a U2D2 (or OpenRB), power them (12 V), and plug the U2D2 into USB.
2. Pick the COM port (COM3, COM4, …) and baud rate (factory default **57,600**), then press **Connect**. The first time, it scans automatically.
3. Don't know the baud rate? Use **Scan → Scan all baud rates**.

Close Dynamixel Wizard first: only one program can use the port at a time.

Tips:
- For smoother updates, switch all motors to 1 Mbps (Setup tab), then reconnect at 1,000,000.
- Even faster polling: Device Manager → the U2D2's COM port → Port Settings → Advanced → Latency Timer = 1 ms.

## Run from source

Needs Python 3.10 or newer.

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

## Build the .exe

```powershell
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\python tools\build.py
```

This writes to `release\`:

| File | What it is |
|---|---|
| `XC430Studio.exe` | Single-file program |
| `XC430Studio-<version>-windows.zip` | Folder version |
| `QUICKSTART.txt` | Short guide for users |

Each build checks itself with the app's built-in self-test, which uses the simulator and never touches real motors. You can run the same check on any PC with `XC430Studio.exe --selftest report.txt`.

## Project layout

```
main.py                 entry point
core/control_table.py   XC430 control table (addresses, sizes, units, ranges)
core/bus.py             real hardware bus (dynamixel_sdk) + built-in simulator
core/worker.py          background thread that owns the serial port
core/config.py          workspace save/load
gui/                    the window, motor panel, teleop, widgets, theme, self-test
xc430studio.spec        PyInstaller recipe
tools/build.py          builds and self-tests the release files
```
