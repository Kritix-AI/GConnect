"""
Connect - Windows System Tray Application
Runs the Connect Host Daemon in the background without a visible command console window,
and provides quick access to the Web IDE dashboard, Tailscale IP, and daemon controls.
"""

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw
import pystray


REPO_ROOT = Path(__file__).resolve().parent.parent
WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def create_tray_icon() -> Image.Image:
    """Programmatically generates a 64x64 GPU chip icon for the system tray."""
    size = (64, 64)
    img = Image.new("RGBA", size, color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer rounded chip base
    draw.rounded_rectangle([6, 6, 58, 58], radius=8, fill="#161b22", outline="#58a6ff", width=2)

    # Corner circuit pins
    pin_color = "#388bfd"
    # Top & bottom pins
    for x in [16, 26, 36, 46]:
        draw.line([(x, 2), (x, 6)], fill=pin_color, width=2)
        draw.line([(x, 58), (x, 62)], fill=pin_color, width=2)
    # Left & right pins
    for y in [16, 26, 36, 46]:
        draw.line([(2, y), (6, y)], fill=pin_color, width=2)
        draw.line([(58, y), (62, y)], fill=pin_color, width=2)

    # Inner processor core
    draw.rounded_rectangle([18, 18, 46, 46], radius=4, fill="#0d1117", outline="#2ea043", width=2)

    # Glowing center core
    draw.rectangle([25, 25, 39, 39], fill="#3fb950")

    return img


def copy_to_clipboard(text: str) -> bool:
    """Copies string to the system clipboard using Windows clip.exe or tkinter."""
    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
                creationflags=WIN_NO_WINDOW,
                shell=True
            )
            proc.communicate(text.encode("utf-8"))
            return proc.returncode == 0
        else:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()
            r.destroy()
            return True
    except Exception as e:
        print(f"Clipboard copy error: {e}")
        return False


def get_tailscale_ip() -> str | None:
    """Retrieves the host's Tailscale IPv4 address if Tailscale is running."""
    try:
        res = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=WIN_NO_WINDOW,
            shell=(sys.platform == "win32")
        )
        if res.returncode == 0:
            ip = res.stdout.strip().split("\n")[0].strip()
            if ip:
                return ip
    except Exception:
        pass
    return None


class ConnectTrayApp:
    def __init__(self):
        self.daemon_proc: subprocess.Popen | None = None
        self.icon: pystray.Icon | None = None

    def start_daemon(self):
        """Spawns the GConnect FastAPI host daemon in a hidden background
        process. Logs go to .gconnect/logs/tray-daemon.log (never discarded)."""
        if self.daemon_proc and self.daemon_proc.poll() is None:
            return

        # Honor the same env vars as the daemon (see .env.example).
        host = os.environ.get("CONNECT_HOST", "127.0.0.1")
        if os.environ.get("CONNECT_ALLOW_LAN", "").lower() == "true" and host == "127.0.0.1":
            host = "0.0.0.0"
        port = os.environ.get("CONNECT_PORT", "8000")

        python_exe = sys.executable
        cmd = [
            python_exe,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            host,
            "--port",
            port,
            "--log-level",
            "info",
        ]

        log_dir = REPO_ROOT / ".gconnect" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "tray-daemon.log", "ab")

        try:
            self.daemon_proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                creationflags=WIN_NO_WINDOW,
                stdout=log_file,
                stderr=subprocess.STDOUT
            )
        except Exception as e:
            print(f"Failed to start GConnect daemon: {e}")
        finally:
            # Popen duplicates the handle; closing ours is safe.
            log_file.close()

    def stop_daemon(self):
        """Terminates the host daemon and all child worker processes."""
        if self.daemon_proc and self.daemon_proc.poll() is None:
            pid = self.daemon_proc.pid
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        creationflags=WIN_NO_WINDOW,
                        capture_output=True
                    )
                else:
                    self.daemon_proc.terminate()
            except Exception as e:
                print(f"Failed to kill daemon process {pid}: {e}")
            finally:
                self.daemon_proc = None

    def on_open_dashboard(self, icon, item):
        webbrowser.open("http://localhost:" + os.environ.get("CONNECT_PORT", "8000"))

    def on_copy_tailscale(self, icon, item):
        ip = get_tailscale_ip()
        if ip:
            copy_to_clipboard(ip)
            if hasattr(icon, "notify"):
                icon.notify(f"Tailscale IP: {ip} copied to clipboard.", "GConnect Platform")
        else:
            if hasattr(icon, "notify"):
                icon.notify("Tailscale not detected or offline.", "GConnect Platform")

    def on_copy_dashboard_url(self, icon, item):
        url = "http://localhost:" + os.environ.get("CONNECT_PORT", "8000")
        copy_to_clipboard(url)
        if hasattr(icon, "notify"):
            icon.notify(f"{url} copied to clipboard.", "GConnect Platform")

    def on_restart_daemon(self, icon, item):
        self.stop_daemon()
        time.sleep(1)
        self.start_daemon()
        if hasattr(icon, "notify"):
            icon.notify("Host daemon restarted successfully.", "GConnect Platform")

    def on_quit(self, icon, item):
        self.stop_daemon()
        if self.icon:
            self.icon.stop()

    def run(self):
        self.start_daemon()

        menu = pystray.Menu(
            pystray.MenuItem("GConnect GPU Platform (Running)", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🌐 Open Web IDE", self.on_open_dashboard, default=True),
            pystray.MenuItem("📋 Copy Tailscale IP", self.on_copy_tailscale),
            pystray.MenuItem("📋 Copy Web IDE URL", self.on_copy_dashboard_url),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Restart Host Daemon", self.on_restart_daemon),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Quit GConnect", self.on_quit)
        )

        self.icon = pystray.Icon(
            "GConnectTray",
            create_tray_icon(),
            "GConnect - Remote GPU Platform",
            menu
        )

        try:
            self.icon.run()
        finally:
            self.stop_daemon()


if __name__ == "__main__":
    app = ConnectTrayApp()
    app.run()
