"""Network utility helpers for GConnect.
Detects Tailscale, local LAN, and public network bindings for effortless GPU sharing.
"""

import logging
import os
import re
import socket
import subprocess
import sys
from typing import Dict, Optional

logger = logging.getLogger("connect.network")


def get_tailscale_ip() -> Optional[str]:
    """Detects Tailscale IPv4 address (100.x.x.x) via CLI or interface inspection."""
    # 1. Try running tailscale CLI
    try:
        res = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=1.5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if res.returncode == 0:
            ip = res.stdout.strip().splitlines()[0].strip()
            if ip.startswith("100."):
                return ip
    except Exception:
        pass

    # 2. Try psutil if available
    try:
        import psutil

        for iface_name, addrs in psutil.net_if_addrs().items():
            if "tailscale" in iface_name.lower():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address.startswith("100."):
                        return addr.address
    except Exception:
        pass

    # 3. Fallback: inspect all host interfaces for 100.64.0.0/10 Carrier Grade NAT (Tailscale range)
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip.startswith("100."):
                return ip
    except Exception:
        pass

    return None


def get_lan_ip() -> Optional[str]:
    """Detects primary local LAN IP (e.g. 192.168.x.x, 10.x.x.x, 172.16-31.x.x)."""
    try:
        # Connect to a public DNS IP without sending packets to find local routing interface
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        ips = [ip for ip in socket.gethostbyname_ex(hostname)[2] if not ip.startswith("127.")]
        if ips:
            return ips[0]
    except Exception:
        pass

    return "127.0.0.1"


def get_host_network_addresses() -> Dict[str, Optional[str]]:
    """Returns a dictionary of network identifiers for teammate connection."""
    hostname = socket.gethostname()
    ts_ip = get_tailscale_ip()
    lan_ip = get_lan_ip()

    # Recommended IP: Tailscale preferred for remote / VPN; LAN IP fallback
    recommended_ip = ts_ip if ts_ip else lan_ip

    return {
        "hostname": hostname,
        "tailscale_ip": ts_ip,
        "lan_ip": lan_ip,
        "recommended_ip": recommended_ip,
    }
