#!/usr/bin/env python3
"""XC430 Studio - multi-motor control & teleoperation GUI for DYNAMIXEL XC430."""
import argparse
import os
import sys

__version__ = "1.0.0"
FROZEN = getattr(sys, "frozen", False)          # True inside the PyInstaller build
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")   # never let pyqtgraph pick another Qt binding

LOG_PATH = os.path.join(os.path.expanduser("~"), ".xc430_studio.log")


def _redirect_output():
    """A windowed .exe has no console (stdout/stderr are None) - send output to a log file."""
    if sys.stdout is None or sys.stderr is None:
        try:
            f = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        except OSError:
            f = open(os.devnull, "w")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


_redirect_output()

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import DEFAULT_PATH  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.style import apply_theme  # noqa: E402

ICON = os.path.join(APP_DIR, "gui", "icons", "app.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", default=str(DEFAULT_PATH), help="workspace JSON file")
    ap.add_argument("--selftest", metavar="REPORT", nargs="?", const="-",
                    help="run a quick automated check with the simulator, write a report (file or '-'), exit")
    ap.add_argument("--screenshots", metavar="DIR", help="with --selftest: save a screenshot of each tab")
    ap.add_argument("--version", action="version", version=f"XC430 Studio {__version__}")
    args, qt_args = ap.parse_known_args()

    if sys.platform == "win32":
        try:   # own taskbar group + icon instead of the generic Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("XC430Studio.App")
        except Exception:
            pass

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("XC430 Studio")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(ICON))
    apply_theme(app)

    if args.selftest:
        from gui.selftest import run_selftest
        sys.exit(run_selftest(app, args.selftest, args.screenshots))

    win = MainWindow(args.workspace)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
