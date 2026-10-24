# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the GConnect daemon sidecar.
# Build: python scripts/build_sidecar.py

import sys
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("docker")
    + collect_submodules("anyio")
    + [
        "aiofiles",
        "pynvml",
        "httpx",
        "backend",
        "backend.main",
        "backend.config",
        "backend.auth",
        "backend.docker_manager",
        "backend.file_manager",
        "backend.gpu_telemetry",
        "backend.cluster_manager",
        "backend.pty_bridge",
    ]
)

a = Analysis(
    ["backend_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[("frontend", "frontend")],  # self-contained web IDE in the sidecar
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
    icon="src-tauri/icons/icon.ico",
)
