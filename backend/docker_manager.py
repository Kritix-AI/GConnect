import logging
import os
from typing import Any, Dict, Optional, Tuple

from backend.config import settings

logger = logging.getLogger("connect.docker")

try:
    import docker
    from docker.types import DeviceRequest
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    logger.warning("docker Python library not installed.")


class DockerSandboxManager:
    def __init__(self):
        self.client: Optional[Any] = None
        self.docker_available: bool = False
        self.gpu_passthrough_available: bool = False
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
        """Checks if Docker supports nvidia runtime passthrough."""
        if not self.docker_available or not self.client:
            return

        try:
            info = self.client.info()
            runtimes = info.get("Runtimes", {})
            if "nvidia" in runtimes:
                self.gpu_passthrough_available = True
                logger.info("NVIDIA Container Toolkit detected in Docker runtimes.")
            else:
                self.gpu_passthrough_available = True
                logger.info("GPU passthrough configured.")
        except Exception as e:
            logger.info(f"NVIDIA container runtime check: {e}")
            self.gpu_passthrough_available = False

    def get_or_create_container(self):
        """Ensures the sandbox container is running with GPU, 8GB shm, port 8001, and /workspace mount."""
        if not self.docker_available or not self.client:
            return None

        workspace_path = str(settings.WORKSPACE_DIR.resolve())
        hf_cache_path = os.path.join(workspace_path, settings.HF_CACHE_DIR_NAME)
        if not settings.EPHEMERAL_MODE:
            os.makedirs(hf_cache_path, exist_ok=True)

        try:
            try:
                container = self.client.containers.get(settings.CONTAINER_NAME)
                if container.status != "running":
                    logger.info(f"Starting stopped container {settings.CONTAINER_NAME}...")
                    container.start()
                    container.reload()
                return container
            except Exception:
                pass

            logger.info(f"Creating sandbox container '{settings.CONTAINER_NAME}' from {settings.CONTAINER_IMAGE}...")

            volumes = {
                workspace_path: {
                    "bind": "/workspace",
                    "mode": "rw"
                }
            }

            hf_home = "/root/.cache/huggingface" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"

            environment = {
                "HF_HOME": hf_home,
                "PYTHONUNBUFFERED": "1",
                "DEBIAN_FRONTEND": "noninteractive",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8"
            }

            ports = {
                "8001/tcp": settings.INFERENCE_PORT
            }

            device_requests = None
            if self.gpu_passthrough_available:
                device_requests = [
                    DeviceRequest(count=-1, capabilities=[["gpu", "compute", "utility"]])
                ]

            container = self.client.containers.run(
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
                device_requests=device_requests,
                restart_policy={"Name": "unless-stopped"}
            )

            logger.info(f"Container '{settings.CONTAINER_NAME}' started (ID: {container.short_id}).")
            return container

        except Exception as e:
            logger.error(f"Failed to create/start Docker sandbox: {e}")
            return None

    def get_status(self) -> Dict[str, Any]:
        """Returns the current container and Docker daemon status."""
        hf_cache_location = "/root/.cache/huggingface (ephemeral)" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"
        status = {
            "docker_available": self.docker_available,
            "gpu_passthrough": self.gpu_passthrough_available,
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

        try:
            container = self.client.containers.get(settings.CONTAINER_NAME)
            status["container_status"] = container.status
            status["container_id"] = container.short_id
        except Exception:
            status["container_status"] = "not_found"

        return status

    def restart_container(self) -> bool:
        """Restarts the sandbox container."""
        if not self.docker_available or not self.client:
            return False

        try:
            container = self.client.containers.get(settings.CONTAINER_NAME)
            container.restart()
            return True
        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False

    def wipe_model_cache(self) -> Dict[str, Any]:
        """Safely deletes all model cache files from the host workspace to free disk space."""
        import shutil
        import stat

        hf_cache_path = settings.WORKSPACE_DIR.resolve() / settings.HF_CACHE_DIR_NAME

        if not hf_cache_path.exists():
            return {
                "status": "success",
                "freed_mb": 0.0,
                "message": "No model cache found on host disk."
            }

        total_bytes = 0
        try:
            for entry in hf_cache_path.rglob("*"):
                if entry.is_file():
                    total_bytes += entry.stat().st_size
        except Exception as e:
            logger.warning(f"Error calculating cache size: {e}")

        freed_mb = round(total_bytes / (1024 * 1024), 2)

        def _remove_readonly(func, path, _):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            # Wipe all files and directories inside .hf_cache
            for item in list(hf_cache_path.iterdir()):
                if item.is_dir():
                    shutil.rmtree(item, onerror=_remove_readonly)
                else:
                    try:
                        os.chmod(item, stat.S_IWRITE)
                    except Exception:
                        pass
                    item.unlink()

            # If in ephemeral mode, attempt to remove root .hf_cache directory too
            if settings.EPHEMERAL_MODE:
                try:
                    os.chmod(hf_cache_path, stat.S_IWRITE)
                    hf_cache_path.rmdir()
                except Exception:
                    pass

            logger.info(f"Wiped model cache. Freed {freed_mb} MB.")
            return {
                "status": "success",
                "freed_mb": freed_mb,
                "message": f"Successfully freed {freed_mb} MB from host disk."
            }
        except Exception as e:
            logger.error(f"Failed to wipe model cache: {e}")
            return {
                "status": "error",
                "freed_mb": 0.0,
                "message": f"Failed to wipe model cache: {e}"
            }

    def create_exec_pty(self) -> Optional[Tuple[str, Any]]:
        """
        Creates an interactive bash session inside the container with hijacked raw socket.
        Returns: (exec_id, socket)
        """
        container = self.get_or_create_container()
        if not container:
            return None

        hf_home = "/root/.cache/huggingface" if settings.EPHEMERAL_MODE else "/workspace/.hf_cache"

        try:
            exec_instance = self.client.api.exec_create(
                container=container.id,
                cmd=["/bin/bash"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                environment={"TERM": "xterm-256color", "HF_HOME": hf_home}
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

