import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("connect.tracker")


class ConnectionTracker:
    """Thread-safe tracker for devices and client sessions connected to GConnect.

    Accurately identifies distinct physical devices using client IDs, IP addresses,
    and user agents, tracking live WebSockets (telemetry, terminal) and HTTP activity.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # client_key -> dict of device details
        self._clients: Dict[str, Dict[str, Any]] = {}
        # socket_id (int) -> client_key
        self._socket_map: Dict[int, str] = {}

    def register_client(
        self,
        ip: str,
        user_agent: str = "",
        client_id: Optional[str] = None,
        channel: str = "http",
        socket_obj: Optional[Any] = None,
    ) -> str:
        """Registers or updates an active client connection."""
        now = time.time()
        clean_ip = (ip or "").strip()
        is_local = clean_ip in ("127.0.0.1", "::1", "localhost", "testclient")

        # Determine grouping key: prefer client_id from browser/app, fallback to IP
        if client_id and client_id.strip():
            key = f"id-{client_id.strip()}"
        else:
            key = f"ip-{clean_ip}"

        with self._lock:
            if key not in self._clients:
                self._clients[key] = {
                    "id": key,
                    "ip": clean_ip,
                    "user_agent": user_agent or "",
                    "name": self._parse_device_name(user_agent or "", is_local),
                    "is_local": is_local,
                    "connected_at": now,
                    "last_seen": now,
                    "channels": {},
                    "active_sockets": 0,
                }

            client = self._clients[key]
            client["last_seen"] = now
            if clean_ip and clean_ip != "unknown":
                client["ip"] = clean_ip
            if user_agent:
                client["user_agent"] = user_agent
                client["name"] = self._parse_device_name(user_agent, is_local)

            client["channels"][channel] = client["channels"].get(channel, 0) + 1
            if socket_obj is not None:
                client["active_sockets"] += 1
                self._socket_map[id(socket_obj)] = key

        return key

    def unregister_socket(self, socket_obj: Any, channel: str = ""):
        """Called when a WebSocket disconnects to update channel and socket counts."""
        sock_id = id(socket_obj)
        with self._lock:
            key = self._socket_map.pop(sock_id, None)
            if key and key in self._clients:
                client = self._clients[key]
                client["active_sockets"] = max(0, client["active_sockets"] - 1)
                if channel and channel in client["channels"]:
                    client["channels"][channel] = max(0, client["channels"][channel] - 1)
                    if client["channels"][channel] == 0:
                        del client["channels"][channel]
                client["last_seen"] = time.time()

    def get_connected_devices(self, timeout_seconds: float = 60.0) -> Dict[str, Any]:
        """Returns currently active connected devices with remote vs local breakdown."""
        now = time.time()
        devices: List[Dict[str, Any]] = []
        remote_count = 0
        local_count = 0

        with self._lock:
            stale_keys = []
            for key, client in list(self._clients.items()):
                has_sockets = client.get("active_sockets", 0) > 0
                age = now - client.get("last_seen", 0)

                # Prune clients without active sockets that haven't been seen recently
                if not has_sockets and age > timeout_seconds:
                    stale_keys.append(key)
                else:
                    is_active = has_sockets or age < 45.0
                    if is_active:
                        channels_list = list(client.get("channels", {}).keys())
                        if not channels_list:
                            channels_list = ["active"]
                        device_info = {
                            "id": client["id"],
                            "ip": client["ip"],
                            "name": client["name"],
                            "is_local": client["is_local"],
                            "connected_at": client["connected_at"],
                            "last_seen": client["last_seen"],
                            "channels": sorted(channels_list),
                            "active": True,
                        }
                        devices.append(device_info)
                        if client["is_local"]:
                            local_count += 1
                        else:
                            remote_count += 1

            for sk in stale_keys:
                del self._clients[sk]

        # Sort remote devices first, then by connected time descending
        devices.sort(key=lambda d: (d["is_local"], -d["last_seen"]))

        return {
            "total_devices": len(devices),
            "remote_devices": remote_count,
            "local_devices": local_count,
            "devices": devices,
        }

    def _parse_device_name(self, ua: str, is_local: bool) -> str:
        if is_local:
            return "This PC (Host)"
        if not ua:
            return "Remote Device"

        ua_lower = ua.lower()

        # Identify operating system / platform
        os_str = "Device"
        if "windows" in ua_lower:
            os_str = "Windows PC"
        elif "macintosh" in ua_lower or "mac os" in ua_lower:
            os_str = "Mac"
        elif "iphone" in ua_lower:
            os_str = "iPhone"
        elif "ipad" in ua_lower:
            os_str = "iPad"
        elif "android" in ua_lower:
            os_str = "Android"
        elif "linux" in ua_lower:
            os_str = "Linux PC"

        # Identify client / browser
        client_str = ""
        if "tauri" in ua_lower or "gconnect" in ua_lower:
            client_str = "GConnect App"
        elif "edg" in ua_lower:
            client_str = "Edge"
        elif "chrome" in ua_lower:
            client_str = "Chrome"
        elif "firefox" in ua_lower:
            client_str = "Firefox"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            client_str = "Safari"
        elif "python" in ua_lower:
            client_str = "Python SDK"

        if client_str:
            return f"{os_str} ({client_str})"
        return os_str


connection_tracker = ConnectionTracker()
