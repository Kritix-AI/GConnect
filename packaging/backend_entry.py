"""GConnect daemon entrypoint for the packaged desktop sidecar.

Runs uvicorn programmatically (no --reload, no access logs - query strings
carry the auth token). Used by PyInstaller to build binaries/gconnect-backend.
"""

import logging
import os
import sys
import time
from pathlib import Path

# When frozen by PyInstaller the repo layout is unavailable; the workspace
# defaults to a folder next to the executable unless overridden by env vars.
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
    os.environ.setdefault("CONNECT_BASE_DIR", str(_exe_dir))
    os.environ.setdefault("CONNECT_WORKSPACE_DIR", str(_exe_dir / "workspace"))
    os.environ.setdefault("CONNECT_RUNTIME_DIR", str(_exe_dir / ".gconnect"))


def _start_parent_watchdog() -> None:
    """Desktop mode: exit if the parent app process disappears, so a
    force-killed/crashed GConnect never leaves an orphaned daemon."""
    raw = os.environ.get("GCONNECT_PARENT_PID", "")
    if not raw.isdigit():
        return
    parent_pid = int(raw)

    def _alive(pid: int) -> bool:
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def _watch() -> None:
        while True:
            time.sleep(3)
            try:
                if not _alive(parent_pid):
                    os._exit(0)
            except Exception:
                os._exit(0)

    import threading

    thread = threading.Thread(target=_watch, name="parent-watchdog", daemon=True)
    thread.start()


_start_parent_watchdog()

import uvicorn

from backend.config import settings
from backend.main import app  # noqa: E402  (imports configure logging + dirs)


def main() -> None:
    uvicorn.run(
        app,
        host=settings.effective_host,
        port=settings.PORT,
        log_level="info",
        access_log=False,  # never log ?token= query strings
    )


if __name__ == "__main__":
    main()
