# PyInstaller build recipe for XC430 Studio (Windows). Use tools\build.py rather than calling this directly.
#   XC430_ONEFILE=1 -> single .exe; otherwise a folder (one-dir) build.
import os
import sys

sys.path.insert(0, SPECPATH)
from main import __version__  # noqa: E402
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,  # noqa: E402
                                                 StringTable, VarFileInfo, VarStruct, VSVersionInfo)

ONEFILE = os.environ.get("XC430_ONEFILE") == "1"
NAME = "XC430Studio"

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    datas=[("gui/icons", "gui/icons")],
    hiddenimports=["gui.selftest", "serial.tools.list_ports"],
    excludes=[
        # not used; keeps the build small and stops pyqtgraph from probing other bindings
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "jupyter", "numba", "cupy", "h5py",
        "PyQt5", "PyQt6", "PySide2", "pyqtgraph.opengl", "OpenGL", "pyqtgraph.examples",
        "PIL", "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

v = tuple(int(x) for x in __version__.split(".")) + (0,)
version_info = VSVersionInfo(      # shown in Explorer -> Properties -> Details
    ffi=FixedFileInfo(filevers=v, prodvers=v),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "XC430 Studio - DYNAMIXEL XC430 control & teleop"),
            StringStruct("ProductName", "XC430 Studio"),
            StringStruct("FileVersion", __version__),
            StringStruct("ProductVersion", __version__),
            StringStruct("OriginalFilename", f"{NAME}.exe"),
            StringStruct("InternalName", NAME),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

exe_kw = dict(
    name=NAME,
    console=False,                 # windowed app (output goes to %USERPROFILE%\.xc430_studio.log)
    icon="gui/icons/app.ico",
    version=version_info,
    upx=False,
    strip=False,
    debug=False,
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kw)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kw)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False, strip=False)
