# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the GConnect daemon sidecar.
# Build: python scripts/build_sidecar.py
#
# All paths are derived from SPECPATH (the directory holding this spec) so
# the build works no matter which working directory PyInstaller is invoked
# from. SPECPATH = <repo>/packaging -> repo root is one level up.

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("docker")
    + collect_submodules("anyio")
    + collect_submodules("pyflakes")
    + [
        "aiofiles",
        "pynvml",
        "psutil",
        "httpx",
        "backend",
        "backend.main",
        "backend.config",
        "backend.auth",
        "backend.docker_manager",
        "backend.file_manager",
        "backend.gpu_telemetry",
        "backend.cluster_manager",
        "backend.access_tokens",
        "backend.job_manager",
        "backend.linter",
        "backend.pty_bridge",
    ]
)

a = Analysis(
    [os.path.join(ROOT, "packaging", "backend_entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "frontend"), "frontend")],  # self-contained web IDE
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gconnect-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # output is drained by the Tauri shell plugin
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "src-tauri", "icons", "icon.ico"),
)
