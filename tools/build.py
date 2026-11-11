#!/usr/bin/env python3
"""
Build the shareable Windows packages of XC430 Studio.

    .venv\\Scripts\\python tools\\build.py      (after: pip install -r requirements.txt pyinstaller)

Produces in release\\:
    XC430Studio.exe                    single-file program (just double-click)
    XC430Studio-<version>-windows.zip  folder version (starts faster)
    QUICKSTART.txt                     short guide for the people you share it with
Each build is checked with the app's own --selftest (simulator only, never real motors).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from main import __version__  # noqa: E402

EXE = "XC430Studio.exe"
REL = os.path.join(ROOT, "release")


def pyinstaller(onefile: bool) -> str:
    env = dict(os.environ, XC430_ONEFILE="1" if onefile else "0")
    kind = "onefile" if onefile else "onedir"
    dist = os.path.join(ROOT, "dist", kind)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--workpath", os.path.join(ROOT, "build", kind), "--distpath", dist,
                    os.path.join(ROOT, "xc430studio.spec")], check=True, cwd=ROOT, env=env)
    return dist


def selftest(exe_path: str, label: str) -> bool:
    report = os.path.join(ROOT, "build", f"selftest_{label}.txt")
    try:
        ok = subprocess.run([exe_path, "--selftest", report], timeout=120).returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    text = open(report, encoding="utf-8").read() if os.path.exists(report) else "(no report written)\n"
    print(f"--- self-test {label}: {'OK' if ok else 'FAILED'}\n{text}")
    return ok


def main():
    if sys.platform != "win32":
        sys.exit("This script builds the Windows version; run it on Windows.")
    os.makedirs(REL, exist_ok=True)
    ok = True

    print("== building folder version")
    folder = os.path.join(pyinstaller(onefile=False), "XC430Studio")
    shutil.copy(os.path.join(ROOT, "QUICKSTART.txt"), folder)
    ok &= selftest(os.path.join(folder, EXE), "onedir")
    zip_path = os.path.join(REL, f"XC430Studio-{__version__}-windows.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, _, files in os.walk(folder):
            for f in files:
                full = os.path.join(dp, f)
                z.write(full, os.path.join("XC430Studio", os.path.relpath(full, folder)))
    print("wrote", zip_path)

    print("== building single-file version")
    single = os.path.join(pyinstaller(onefile=True), EXE)
    ok &= selftest(single, "onefile")
    shutil.copy(single, os.path.join(REL, EXE))
    shutil.copy(os.path.join(ROOT, "QUICKSTART.txt"), REL)

    print("\n== release\\")
    for f in sorted(os.listdir(REL)):
        print(f"  {f:40} {os.path.getsize(os.path.join(REL, f)) / 1e6:7.1f} MB")
    print(f"\nBuild {'OK' if ok else 'FAILED (see self-test output above)'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
