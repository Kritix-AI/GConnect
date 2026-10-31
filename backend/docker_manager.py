import hashlib
import json
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger("connect.docker")

try:
    import docker
    from docker.types import DeviceRequest
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    logger.warning("docker Python library not installed.")

# Label holding a fingerprint of the sandbox configuration. When the desired
# config changes (image, shm, ports, cache mode...) the container is recreated
# so settings actually apply instead of being silently ignored.
CONFIG_LABEL = "dev.gconnect.config"


def _nvidia_cdi_specs() -> List[str]:
    """Names of NVIDIA CDI spec files found in well-known CDI directories
    (Linux + Docker Desktop paths)."""
    from pathlib import Path

    candidates = [
        "/var/run/cdi",
        "/etc/cdi",
        "/etc/nvidia-cdi",
        str(Path.home() / ".config" / "cdi"),
    ]
    found: List[str] = []
    for c in candidates:
        p = Path(c)
        if p.is_dir():
            for spec in p.glob("*.json"):
                if "nvidia" in spec.name.lower():
                    found.append(spec.name)
    return found


def _rmtree_supported_kwargs() -> Dict[str, Any]:
    """shutil.rmtree got `onexc` in 3.12; `onerror` is deprecated."""
    import inspect
    params = inspect.signature(shutil.rmtree).parameters
    if "onexc" in params:
        return {"onexc": True}
    return {"onerror": True}


class DockerSandboxManager:
    def __init__(self):
        self.client: Optional[Any] = None
        self.docker_available: bool = False
        self.gpu_passthrough_available: bool = False
        self.gpu_degraded_reason: str = ""
        self.is_pulling: bool = False
        self.is_creating: bool = False
        self._init_docker()

    def _init_docker(self):
        if not HAS_DOCKER:
            return

        try:
            self.client = docker.from_env()
            self.client.ping()
            self.docker_available = True
            logger.info("Connected to Docker daemon successfully.")
            self._check_gpu_capability()
        except Exception as e:
            logger.warning(f"Could not connect to Docker daemon: {e}. Container features will be in local fallback mode.")
            self.docker_available = False

    def _check_gpu_capability(self):
        """Detect NVIDIA GPU support: the classic `nvidia` runtime OR CDI
        devices (Docker Desktop / newer toolkits expose GPUs via CDI without
        registering an 'nvidia' runtime)."""
        if not self.docker_available or not self.client:
            return

        try:
            info = self.client.info()
            runtimes = info.get("Runtimes", {}) or {}
            if "nvidia" in runtimes:
                self.gpu_passthrough_available = True
                logger.info("NVIDIA Container Toolkit detected in Docker runtimes.")
                return

            cdi_specs = _nvidia_cdi_specs()
            if cdi_specs:
                self.gpu_passthrough_available = True
                logger.info("NVIDIA CDI device specs detected (%s).", ", ".join(cdi_specs[:3]))
                return

            self.gpu_passthrough_available = False
            self.gpu_degraded_reason = (
                "No 'nvidia' runtime and no NVIDIA CDI device specs found in Docker. "
                "Install the NVIDIA Container Toolkit to enable GPU passthrough."
            )
            logger.warning(self.gpu_degraded_reason)
        except Exception as e:
            self.gpu_passthrough_available = False
            self.gpu_degraded_reason = f"NVIDIA container runtime check failed: {e}"
            logger.info(self.gpu_degraded_reason)

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def _desired_config_fingerprint(self, workspace_path: str) -> str:
        payload = {
            "image": settings.CONTAINER_IMAGE,
            "shm": settings.SHM_SIZE,
            "inference_port": settings.INFERENCE_PORT,
            "hf_home": "/root/.cache/huggingface" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache",
            "workspace": workspace_path,
        }
        blob = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def get_or_create_container(self):
        """Ensures the sandbox container is running with GPU, 8GB shm, port
        8001, and /workspace mount. Recreates the container when the desired
        configuration changed (old container removed; workspace data is on a
        host volume and survives)."""
        if not self.docker_available or not self.client:
            return None

        workspace_path = str(settings.WORKSPACE_DIR.resolve())
        hf_cache_path = os.path.join(workspace_path, settings.HF_CACHE_DIR_NAME)
        if not settings.EPHEMERAL_MODE:
            os.makedirs(hf_cache_path, exist_ok=True)

        desired_fp = self._desired_config_fingerprint(workspace_path)

        try:
            try:
                container = self.client.containers.get(settings.CONTAINER_NAME)
                if container.status != "running":
                    logger.info(f"Starting stopped container {settings.CONTAINER_NAME}...")
                    container.start()
                    container.reload()
                    return container

                labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
                if labels.get(CONFIG_LABEL) != desired_fp:
                    # Also covers containers created before the fingerprint
                    # existed (no label): they are upgraded/recreated too.
                    logger.warning(
                        "Sandbox configuration changed (fingerprint %s -> %s). "
                        "Recreating container; workspace volume is preserved.",
                        labels.get(CONFIG_LABEL) or "<none>", desired_fp,
                    )
                    try:
                        container.remove(force=True)
                    except Exception as rm_err:
                        logger.error(f"Could not remove outdated container: {rm_err}")
                        return container
                    # fall through to creation
                else:
                    return container
            except Exception:
                pass

            logger.info(f"Creating sandbox container '{settings.CONTAINER_NAME}' from {settings.CONTAINER_IMAGE}...")
            self.is_creating = True
            try:
                self.client.images.get(settings.CONTAINER_IMAGE)
            except Exception:
                self.is_pulling = True
                logger.info(f"Image '{settings.CONTAINER_IMAGE}' not cached locally. Pulling in background...")

            hf_home = "/root/.cache/huggingface" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"

            environment = {
                "HF_HOME": hf_home,
                "PYTHONUNBUFFERED": "1",
                "DEBIAN_FRONTEND": "noninteractive",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8"
            }

            volumes = {
                workspace_path: {
                    "bind": "/workspace",
                    "mode": "rw"
                }
            }

            ports = {
                "8001/tcp": settings.INFERENCE_PORT
            }

            container = self._run_container_with_gpu_fallback(
                environment=environment, volumes=volumes, ports=ports, config_fp=desired_fp
            )

            logger.info(f"Container '{settings.CONTAINER_NAME}' started (ID: {container.short_id}).")
            return container

        except Exception as e:
            logger.error(f"Failed to create/start Docker sandbox: {e}")
            return None
        finally:
            self.is_creating = False
            self.is_pulling = False

    def _run_container_with_gpu_fallback(self, environment, volumes, ports, config_fp: str):
        """Try GPU-enabled creation; on daemon rejection fall back to CPU-only
        with a loud, surfaced warning instead of failing opaquely."""
        labels = {CONFIG_LABEL: config_fp}
        common = dict(
            image=settings.CONTAINER_IMAGE,
            name=settings.CONTAINER_NAME,
            command="/bin/bash",
            stdin_open=True,
            tty=True,
            detach=True,
            working_dir="/workspace",
            volumes=volumes,
            environment=environment,
            ports=ports,
            shm_size=settings.SHM_SIZE,
            restart_policy={"Name": "unless-stopped"},
            labels=labels,
        )
        if self.gpu_passthrough_available:
            try:
                return self.client.containers.run(
                    device_requests=[
                        DeviceRequest(count=-1, capabilities=[["gpu", "compute", "utility"]])
                    ],
                    **common,
                )
            except Exception as e:
                self.gpu_passthrough_available = False
                self.gpu_degraded_reason = (
                    f"Docker refused GPU device request ({e}). Container started WITHOUT GPU. "
                    "Check the NVIDIA Container Toolkit installation."
                )
                logger.error(self.gpu_degraded_reason)
                # Remove the half-created container if the daemon left one.
                try:
                    stale = self.client.containers.get(settings.CONTAINER_NAME)
                    stale.remove(force=True)
                except Exception:
                    pass
        else:
            logger.warning(
                "Starting container WITHOUT GPU passthrough: %s", self.gpu_degraded_reason or "capability unknown"
            )
        return self.client.containers.run(**common)

    def get_status(self) -> Dict[str, Any]:
        """Returns the current container and Docker daemon status."""
        hf_cache_location = "/root/.cache/huggingface (ephemeral)" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"
        status = {
            "docker_available": self.docker_available,
            "gpu_passthrough": self.gpu_passthrough_available,
            "gpu_degraded_reason": self.gpu_degraded_reason,
            "container_name": settings.CONTAINER_NAME,
            "container_status": "not_running",
            "container_id": None,
            "inference_port": settings.INFERENCE_PORT,
            "shm_size": settings.SHM_SIZE,
            "ephemeral_mode": settings.EPHEMERAL_MODE,
            "hf_cache": hf_cache_location
        }

        if not self.docker_available or not self.client:
            return status

        if getattr(self, "is_pulling", False):
            status["container_status"] = "pulling_image"
            status["lifecycle_message"] = f"Downloading base image '{settings.CONTAINER_IMAGE}' in background..."
            return status

        if getattr(self, "is_creating", False):
            status["container_status"] = "creating"
            status["lifecycle_message"] = "Initializing sandbox container..."
            return status

        try:
            container = self.client.containers.get(settings.CONTAINER_NAME)
            status["container_status"] = container.status
            status["container_id"] = container.short_id
        except Exception:
            status["container_status"] = "not_found"

        return status

    def restart_container(self) -> bool:
        """Recreates the sandbox container (remove + create with current
        config). A plain `docker restart` is NOT enough: after a Docker
        Desktop restart, GPU containers frequently come back without GPU
        injection - a full recreate re-runs the nvidia runtime hooks.
        The workspace is a host volume, so no data is lost."""
        if not self.docker_available or not self.client:
            return False

        try:
            container = self.client.containers.get(settings.CONTAINER_NAME)
            container.remove(force=True)
        except Exception:
            pass  # not running / already gone - create fresh anyway

        created = self.get_or_create_container()
        return created is not None

    # ------------------------------------------------------------------
    # Model cache maintenance
    # ------------------------------------------------------------------

    def wipe_model_cache(self) -> Dict[str, Any]:
        """Deletes model cache files from the host workspace, reporting bytes
        that were actually removed and any entries that failed to delete."""
        hf_cache_path = settings.WORKSPACE_DIR.resolve() / settings.HF_CACHE_DIR_NAME

        if not hf_cache_path.exists():
            return {
                "status": "success",
                "freed_mb": 0.0,
                "message": "No model cache found on host disk.",
                "errors": [],
            }

        running_warning = ""
        try:
            if self.docker_available and self.client:
                c = self.client.containers.get(settings.CONTAINER_NAME)
                if c.status == "running":
                    running_warning = " Container was running during the wipe; a download in flight may re-create partial files."
        except Exception:
            pass

        deleted_bytes = 0
        errors: List[str] = []

        def _rm_error_handler(func, path, _exc):
            errors.append(str(path))

        use_onexc = _rmtree_supported_kwargs().get("onexc", False)

        def _force_writable(path: Path) -> None:
            try:
                os.chmod(path, stat.S_IWRITE)
            except Exception:
                pass

        for item in list(hf_cache_path.iterdir()):
            try:
                if item.is_dir():
                    before = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                    if use_onexc:
                        shutil.rmtree(item, onexc=lambda f, p, e: _rm_error_handler(f, p, e))
                    else:
                        shutil.rmtree(item, onerror=_rm_error_handler)
                    if not item.exists():
                        deleted_bytes += before
                else:
                    size = item.stat().st_size
                    _force_writable(item)
                    item.unlink()
                    deleted_bytes += size
            except Exception as e:
                errors.append(f"{item}: {e}")

        if settings.EPHEMERAL_MODE and not errors:
            try:
                _force_writable(hf_cache_path)
                hf_cache_path.rmdir()
            except Exception:
                pass

        freed_mb = round(deleted_bytes / (1024 * 1024), 2)
        if errors:
            logger.warning("Model cache wipe finished with %d errors: %s", len(errors), errors[:3])
            return {
                "status": "partial",
                "freed_mb": freed_mb,
                "message": f"Freed {freed_mb} MB; {len(errors)} entries could not be deleted.{running_warning}",
                "errors": errors[:20],
            }

        logger.info(f"Wiped model cache. Freed {freed_mb} MB.")
        return {
            "status": "success",
            "freed_mb": freed_mb,
            "message": f"Successfully freed {freed_mb} MB from host disk.{running_warning}".strip(),
            "errors": [],
        }

    # ------------------------------------------------------------------
    # Interactive exec PTY
    # ------------------------------------------------------------------

    def create_exec_pty(self) -> Optional[Tuple[str, Any]]:
        """
        Creates an interactive bash session inside the container with hijacked raw socket.
        Returns: (exec_id, socket)
        """
        container = self.get_or_create_container()
        if not container:
            return None

        hf_home = "/root/.cache/huggingface" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"

        prompt_cmd = (
            'PS1="\\[\\e[1;36m\\]gconnect\\[\\e[0m\\]:\\[\\e[1;32m\\]\\w\\[\\e[0m\\]\\$ \\[\\e[1;37m\\]"; '
            'trap \'printf "\\e[0m"\' DEBUG'
        )

        try:
            exec_instance = self.client.api.exec_create(
                container=container.id,
                cmd=["/bin/bash"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                environment={
                    "TERM": "xterm-256color",
                    "HF_HOME": hf_home,
                    "PROMPT_COMMAND": prompt_cmd,
                }
            )
            exec_id = exec_instance["Id"]
            sock = self.client.api.exec_start(exec_id, socket=True, tty=True)
            return exec_id, sock
        except Exception as e:
            logger.error(f"Error creating container exec PTY: {e}")
            return None

    def resize_exec(self, exec_id: str, cols: int, rows: int):
        """Resizes the terminal dimensions of an active exec instance."""
        if not self.docker_available or not self.client or not exec_id:
            return

        try:
            self.client.api.exec_resize(exec_id, height=rows, width=cols)
        except Exception as e:
            logger.debug(f"Failed to resize exec window {exec_id}: {e}")


docker_manager = DockerSandboxManager()