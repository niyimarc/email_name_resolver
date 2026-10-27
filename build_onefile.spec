# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Email Name Resolver: ONE standalone .exe (no _internal).

Build:  pyinstaller build_onefile.spec --clean -y
Output: dist/EmailNameResolver.exe   (a single file, roughly 50-80 MB)

Everything (Python, Kivy, SDL2/GLEW/ANGLE DLLs, data/, assets/) is packed
inside the .exe. On each start it unpacks itself to a temporary folder
(a _MEIxxxxxx folder in %TEMP%), which adds a few seconds to start-up and is deleted when
the app closes. Use build.spec instead for the faster-starting one-folder
version.

Based on Kivy's official PyInstaller packaging template
(https://kivy.org/doc/stable/guide/packaging-windows.html): the Kivy
hooks are registered and the SDL2 / GLEW (and ANGLE, when installed) DLL
folders from ``kivy_deps`` are added to the EXE with ``Tree``.

Equivalent CLI flags: --onefile --name EmailNameResolver --windowed
"""

from kivy_deps import glew, sdl2
from kivy.tools.packaging.pyinstaller_hooks import (
    get_deps_minimal,
    hookspath,
    runtime_hooks,
)

# NOTE: kivy[base] on Windows also installs kivy_deps.angle (an optional GL
# backend). Bundle it when present so either backend works; skip otherwise.
try:
    from kivy_deps import angle
    _angle_bins = list(angle.dep_bins)
except ImportError:
    _angle_bins = []

APP_NAME = "EmailNameResolver"

# Kivy picks its window/text/image/clipboard backends at runtime by name, so
# PyInstaller cannot find them on its own. get_deps_minimal() lists them as
# hidden imports (as in Kivy's official template). Video, audio, camera and
# spelling are not used by this app, so they are left out.
kivy_core_deps = get_deps_minimal(video=None, audio=None, camera=None, spelling=None)

# NOTE: get_deps_minimal() excludes tkinter by default, but the app uses
# tkinter as the fallback file dialog, so keep it.
kivy_excludes = [m for m in kivy_core_deps["excludes"] if "tkinter" not in m.lower()]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    # data/: US name lists + disposable domains. assets/: logo and icons.
    datas=[("data", "data"), ("assets", "assets")],
    hiddenimports=kivy_core_deps["hiddenimports"] + [
        # plyer picks its platform backend at runtime via importlib.
        "plyer.platforms.win.filechooser",
        # Loaded lazily inside FileDialogs as the fallback dialog.
        "tkinter",
        "tkinter.filedialog",
    ],
    hookspath=hookspath(),
    hooksconfig={},
    runtime_hooks=runtime_hooks(),
    excludes=kivy_excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# One-file mode: binaries, data and the Kivy dependency DLL folders all go
# into the EXE itself; there is no COLLECT step and no _internal folder.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    *[Tree(p) for p in (sdl2.dep_bins + glew.dep_bins + _angle_bins)],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # NOTE: UPX compression is off: compressed DLLs inside a one-file build
    # are a common cause of antivirus false positives and start-up crashes.
    upx=False,
    runtime_tmpdir=None,
    console=False,  # --windowed
    icon="assets/icon.ico",
    disable_windowed_traceback=False,
)
