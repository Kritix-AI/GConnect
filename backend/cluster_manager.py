"""GConnect GPU cluster registry.

Lets one GConnect daemon track other GConnect daemons ("nodes") running on
other PCs (over Tailscale/LAN). Each node exposes its real NVML telemetry and
sandbox status; this manager polls them and aggregates pooled VRAM.

Tokens are stored in .gconnect/nodes.json (owner-only permissions) and are
NEVER returned by the API.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.config import settings
from backend.gpu_telemetry import gpu_telemetry_service

logger = logging.getLogger("connect.cluster")

NODES_FILE_NAME = "nodes.json"
REMOTE_TIMEOUT_SECONDS = 3.0


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Node URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("Node URL has no host")
    return url


class ClusterManager:
    def __init__(self):
        self._nodes: List[Dict[str, Any]] = []
        self.local_jobs_enabled = True
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @property
    def nodes_file(self) -> Path:
        return settings.RUNTIME_DIR / NODES_FILE_NAME

    def _load(self) -> None:
        try:
            if self.nodes_file.exists():
                data = json.loads(self.nodes_file.read_text(encoding="utf-8"))
                self.local_jobs_enabled = bool(data.get("local_jobs_enabled", True))
                self._nodes = [
                    n for n in data.get("nodes", [])
                    if isinstance(n, dict) and n.get("name") and n.get("url")
                ]
        except Exception as e:
            logger.warning(f"Could not load nodes registry: {e}")
            self._nodes = []

    def _save(self) -> None:
        self.nodes_file.parent.mkdir(parents=True, exist_ok=True)
        self.nodes_file.write_text(
            json.dumps({"nodes": self._nodes, "local_jobs_enabled": self.local_jobs_enabled}, indent=2), encoding="utf-8"
        )
        try:
            import os

            os.chmod(self.nodes_file, 0o600)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Registry operations
    # ------------------------------------------------------------------

    def add_node(self, name: str, url: str, token: str, jobs_enabled: bool = False) -> Dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Node name is required.")
        if any(n["name"] == name for n in self._nodes):
            raise ValueError(f"A node named '{name}' already exists.")
        url = _normalize_url(url)
        if not token or not token.strip():
            raise ValueError("Node token is required (the remote daemon's auth token).")

        node = {
            "name": name,
            "url": url,
            "token": token.strip(),
            "added_at": time.time(),
            "jobs_enabled": bool(jobs_enabled),
        }
        self._nodes.append(node)
        self._save()
        logger.info(f"Added cluster node '{name}' -> {url}")
        return self._public_view(node)

    def remove_node(self, name: str) -> bool:
        before = len(self._nodes)
        self._nodes = [n for n in self._nodes if n["name"] != name]
        if len(self._nodes) == before:
            return False
        self._save()
        logger.info(f"Removed cluster node '{name}'")
        return True

    def set_jobs_enabled(self, name: str, enabled: bool) -> bool:
        for node in self._nodes:
            if node["name"] == name:
                node["jobs_enabled"] = bool(enabled)
                self._save()
                return True
        return False

    def set_local_jobs_enabled(self, enabled: bool) -> None:
        self.local_jobs_enabled = bool(enabled)
        self._save()

    def list_nodes(self) -> List[Dict[str, Any]]:
        """Node list WITHOUT tokens (safe for the UI)."""
        return [self._public_view(n) for n in self._nodes]

    @staticmethod
    def _public_view(node: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": node["name"],
            "url": node["url"],
            "added_at": node.get("added_at", 0),
            "jobs_enabled": bool(node.get("jobs_enabled", False)),
        }

    # ------------------------------------------------------------------
    # Cluster status (local + remotes)
    # ------------------------------------------------------------------

    def _local_node_status(self) -> Dict[str, Any]:
        snap = gpu_telemetry_service.get_snapshot()
        from backend.docker_manager import docker_manager

        container = docker_manager.get_status()
        return {
            "name": "this-pc",
            "url": "local",
            "local": True,
            "jobs_enabled": self.local_jobs_enabled,
            "online": True,
            "gpu_name": snap["gpu_name"],
            "gpu_count": snap["gpu_count"],
            "gpus": snap["gpus"],
            "vram_total_mb": snap["vram_total_mb"],
            "vram_used_mb": snap["vram_used_mb"],
            "vram_percent": snap["vram_percent"],
            "gpu_utilization_percent": snap["gpu_utilization_percent"],
            "temperature_c": snap["temperature_c"],
            "telemetry_is_mock": snap["is_mock"],
            "host_platform": snap.get("host_platform", "unknown"),
            "telemetry_degraded": snap.get("degraded", False),
            "host": snap.get("host", {}),
            "container_status": container.get("container_status"),
            "gpu_passthrough": container.get("gpu_passthrough"),
        }

    def _remote_node_status(self, node: Dict[str, Any]) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "name": node["name"],
            "url": node["url"],
            "local": False,
            "jobs_enabled": bool(node.get("jobs_enabled", False)),
            "online": False,
            "gpu_name": "unreachable",
            "gpu_count": 0,
            "gpus": [],
            "vram_total_mb": 0.0,
            "vram_used_mb": 0.0,
            "vram_percent": 0.0,
            "gpu_utilization_percent": 0,
            "temperature_c": 0,
            "telemetry_is_mock": True,
            "telemetry_degraded": False,
            "host": {},
            "container_status": "unknown",
            "gpu_passthrough": False,
            "provider_jobs_enabled": False,
            "error": None,
        }
        try:
            import httpx

            headers = {"Authorization": f"Bearer {node['token']}"}
            with httpx.Client(timeout=REMOTE_TIMEOUT_SECONDS, headers=headers) as client:
                snap_res = client.get(f"{node['url']}/api/telemetry/snapshot")
                if snap_res.status_code == 401:
                    status["error"] = "Authentication failed - check the node token."
                    return status
                snap_res.raise_for_status()
                snap = snap_res.json()

                cont_res = client.get(f"{node['url']}/api/container/status")
                cont = cont_res.json() if cont_res.status_code == 200 else {}
                share_res = client.get(f"{node['url']}/api/worker/status")
                share = share_res.json() if share_res.status_code == 200 else {}

            status.update(
                {
                    "online": True,
                    "gpu_name": snap.get("gpu_name", "unknown"),
                    "gpu_count": snap.get("gpu_count", 0),
                    "gpus": snap.get("gpus", []),
                    "vram_total_mb": snap.get("vram_total_mb", 0.0),
                    "vram_used_mb": snap.get("vram_used_mb", 0.0),
                    "vram_percent": snap.get("vram_percent", 0.0),
                    "gpu_utilization_percent": snap.get("gpu_utilization_percent", 0),
                    "temperature_c": snap.get("temperature_c", 0),
                    "telemetry_is_mock": snap.get("is_mock", True),
                    "host_platform": snap.get("host_platform", "unknown"),
                    "telemetry_degraded": snap.get("degraded", False),
                    "host": snap.get("host", {}),
                    "container_status": cont.get("container_status", "unknown"),
                    "gpu_passthrough": cont.get("gpu_passthrough", False),
                    "provider_jobs_enabled": bool(share.get("jobs_enabled", True)),
                    "jobs_enabled": bool(node.get("jobs_enabled", False) and share.get("jobs_enabled", True)),
                    "error": None,
                }
            )
        except Exception as e:
            status["error"] = f"unreachable: {type(e).__name__}"
        return status

    def get_cluster_status(self) -> Dict[str, Any]:
        nodes = [self._local_node_status()]
        nodes.extend(self._remote_node_status(n) for n in self._nodes)

        online = [n for n in nodes if n["online"]]
        pooled_vram_total = round(sum(n["vram_total_mb"] for n in online), 1)
        pooled_vram_used = round(sum(n["vram_used_mb"] for n in online), 1)
        total_gpus = sum(n["gpu_count"] for n in online if not n["telemetry_is_mock"])

        return {
            "type": "cluster_status",
            "nodes": nodes,
            "node_count": len(nodes),
            "online_count": len(online),
            "total_gpus": total_gpus,
            "pooled_vram_total_mb": pooled_vram_total,
            "pooled_vram_used_mb": pooled_vram_used,
            "pooled_vram_percent": round(
                (pooled_vram_used / pooled_vram_total) * 100, 1
            ) if pooled_vram_total > 0 else 0.0,
            "timestamp": time.time(),
        }


cluster_manager = ClusterManager()
