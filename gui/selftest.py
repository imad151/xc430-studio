"""
Built-in self-test (`XC430Studio --selftest [report.txt] [--screenshots DIR]`).

Runs the real app against the simulator for a few seconds and checks that every bundled
piece works: Qt platform, plots (pyqtgraph/numpy), gamepad (pygame), Dynamixel SDK, serial
port listing, motion, teleop. Meant for verifying a packaged build on a new machine.
Never touches real hardware or the user's workspace file.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import QTimer


def run_selftest(app, report_path: str, shot_dir: str | None) -> int:
    from .main_window import MainWindow

    lines: list[str] = []
    ok_all = [True]

    def check(cond, label):
        ok_all[0] &= bool(cond)
        lines.append(("PASS  " if cond else "FAIL  ") + label)

    # --- bundled modules
    lines.append(f"XC430 Studio {app.applicationVersion()} self-test")
    lines.append(f"{platform.platform()} | Python {platform.python_version()} | "
                 f"Qt platform: {app.platformName()} | frozen: {getattr(sys, 'frozen', False)}")
    try:
        import PySide6
        lines.append(f"PySide6 {PySide6.__version__}")
    except Exception:
        pass
    for mod in ("numpy", "pyqtgraph", "pygame", "dynamixel_sdk", "serial"):
        try:
            m = __import__(mod)
            check(True, f"module {mod} {getattr(m, '__version__', '')}".rstrip())
        except Exception as e:
            check(False, f"module {mod}: {e}")
    try:
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()]
        check(True, f"serial ports listed: {ports or 'none'}")
    except Exception as e:
        check(False, f"serial port listing: {e}")

    w = MainWindow(os.path.join(tempfile.mkdtemp(), "selftest_ws.json"))
    w.resize(1400, 900)
    w.show()
    m = w.m
    check(w.panel.plots if hasattr(w.panel, "plots") else False, "live plot widgets built")
    check(w.teleop.pad.ok, "gamepad support initialised")

    def shot(name):
        if shot_dir:
            os.makedirs(shot_dir, exist_ok=True)
            w.grab().save(os.path.join(shot_dir, f"{name}.png"))

    def wait(cond, timeout):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return cond()

    def steps():
        try:
            w.sim_chk.setChecked(True)
            w.connect_btn.click()
            check(wait(lambda: len(m.ws.motors) == 4, 5), "simulator connect + scan finds 4 motors")
            check(wait(lambda: all(m.is_online.get(c.id) for c in m.ws.motors), 3), "motors online")
            wait(lambda: False, 0.5)
            shot("1_dashboard")
            m.set_torque(1, True)
            check(wait(lambda: m.torque(1), 2), "torque on")
            m.send_goal(1, 1000)
            check(wait(lambda: abs(m.raw(1, "Present Position") - 1000) < 20, 4), "motor 1 moves to goal")
            w.tabs.setCurrentIndex(1)
            w.panel.tabs.setCurrentIndex(1)
            # Guard against a known class of bug: on Python < 3.12 a compiled dependency that
            # over-releases None/True makes their refcount collapse and Python crashes mid-session
            # (seen with the Linux wheels on Python 3.10). 3.12+ makes them immortal.
            r0 = sys.getrefcount(None), sys.getrefcount(True)
            for k in range(4):
                m.send_goal(1, 3000 if k % 2 == 0 else 1200)
                wait(lambda: False, 0.75)
            r1 = sys.getrefcount(None), sys.getrefcount(True)
            if sys.version_info < (3, 12):
                drop = max(r0[0] - r1[0], r0[1] - r1[1])
                check(drop < 300, f"no refcount leak of None/True while plotting (drop {drop})")
            c = list(w.panel.plots.values())[0][0].getData()[0]
            check(c is not None and len(c) > 10, f"live plot draws ({0 if c is None else len(c)} points)")
            shot("2_live_plot")
            w.panel.tabs.setCurrentIndex(0)
            wait(lambda: False, 0.3)
            shot("3_motor_control")
            w.tabs.setCurrentIndex(2)
            w.teleop.enable_btn.click()
            check(w.teleop.active, "teleop starts")
            wait(lambda: False, 0.3)
            shot("4_teleop")
            w._estop()
            check(wait(lambda: not any(m.torque(c.id) for c in m.ws.motors), 2), "STOP turns torque off")
        except Exception:
            check(False, "self-test crashed:\n" + traceback.format_exc())
        finally:
            w.close()
            app.quit()

    QTimer.singleShot(300, steps)
    QTimer.singleShot(30000, lambda: (check(False, "timed out"), app.quit()))
    app.exec()
    lines.append("RESULT: " + ("ALL PASSED" if ok_all[0] else "FAILED"))
    text = "\n".join(lines) + "\n"
    if report_path == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(text)
    return 0 if ok_all[0] else 1
