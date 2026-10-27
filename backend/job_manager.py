"""Invite-only GPU job dispatch and isolated Docker job execution.

This is a single-control-plane pilot: the daemon holding the cluster registry
chooses a node and dispatches a bounded script to that node's worker API. It
uses the existing daemon bearer tokens as the provider trust boundary.
"""

import logging
import io
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import settings
from backend.docker_manager import DeviceRequest, HAS_DOCKER, docker_manager

logger = logging.getLogger("connect.jobs")

MAX_SCRIPT_BYTES = 1024 * 1024
MAX_RUNTIME_SECONDS = 6 * 60 * 60
MAX_LOG_BYTES = 128 * 1024
MAX_PERSISTED_LOG_BYTES = 16 * 1024
MAX_RETAINED_JOBS = 200
MAX_QUEUED_JOBS = 100
MAX_QUEUED_PER_DEVELOPER = 10
MAX_JOB_OUTPUT_BYTES = 256 * 1024 * 1024
GPU_CAPABILITIES = [["gpu", "compute", "utility"]]


class JobManager:
    """Coordinates jobs on this daemon and dispatches to registered peers."""

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._reservations: Dict[str, int] = {}
        self._job_root = settings.RUNTIME_DIR / "jobs"
        self._job_root.mkdir(parents=True, exist_ok=True)
        self._pending_root = self._job_root / "pending"
        self._pending_root.mkdir(parents=True, exist_ok=True)
        self._state_file = settings.RUNTIME_DIR / "jobs.json"
        self._load()
        threading.Thread(target=self._queue_loop, daemon=True, name="gconnect-job-scheduler").start()

    def _load(self) -> None:
        try:
            if self._state_file.exists():
                saved = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._jobs = {
                    item["id"]: item for item in saved.get("jobs", [])
                    if isinstance(item, dict) and item.get("id")
                }
        except Exception as exc:
            logger.warning("Could not restore GPU job state: %s", type(exc).__name__)
            self._jobs = {}

        terminal = {"completed", "failed", "cancelled", "timeout", "interrupted"}
        for job in self._jobs.values():
            if job.get("state") in terminal:
                continue
            if job.get("state") == "queued":
                pending = Path(job.get("payload_path", ""))
                if pending.is_file():
                    continue
                job.update({"state": "failed", "finished_at": time.time(), "logs": "Queued job source was unavailable after restart."})
                continue
            if job.get("worker_url") and job.get("local_worker") is False:
                node_id = job.get("node", "")
                self._reservations[node_id] = self._reservations.get(node_id, 0) + int(job.get("gpu_count", 0))
                continue
            # Stop local/provider containers left behind by an unclean daemon
            # shutdown, then report the interruption instead of stale-running.
            if docker_manager.client and job.get("container_id"):
                try:
                    docker_manager.client.containers.get(job["container_id"]).remove(force=True)
                except Exception:
                    pass
            output_dir = self._safe_output_path(job.get("output_dir"))
            if output_dir and output_dir.is_dir():
                for leftover in output_dir.parent.iterdir():
                    if leftover == output_dir:
                        continue
                    if leftover.is_dir():
                        shutil.rmtree(leftover, ignore_errors=True)
                    else:
                        try:
                            leftover.unlink(missing_ok=True)
                        except OSError:
                            pass
            job.update({
                "state": "interrupted",
                "finished_at": time.time(),
                "logs": "Provider restarted while this job was active; it was stopped during recovery.",
            })
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self._state_file.with_suffix(".tmp")
            saved_jobs = []
            for job in self._jobs.values():
                saved = dict(job)
                if isinstance(saved.get("logs"), str):
                    saved["logs"] = saved["logs"][-MAX_PERSISTED_LOG_BYTES:]
                saved_jobs.append(saved)
            temp.write_text(json.dumps({"jobs": saved_jobs}, indent=2), encoding="utf-8")
            temp.replace(self._state_file)
            try:
                import os
                os.chmod(self._state_file, 0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Could not persist GPU job state: %s", type(exc).__name__)

    def _prune_locked(self) -> None:
        terminal = {"completed", "failed", "cancelled", "timeout", "interrupted"}
        excess = len(self._jobs) - MAX_RETAINED_JOBS
        if excess <= 0:
            return
        removable = sorted(
            (job for job in self._jobs.values() if job.get("state") in terminal),
            key=lambda job: job.get("created_at", 0),
        )[:excess]
        for job in removable:
            self._jobs.pop(job["id"], None)
            output_dir = self._safe_output_path(job.get("output_dir"))
            if output_dir and job.get("local_worker"):
                shutil.rmtree(output_dir.parent, ignore_errors=True)

    def _safe_output_path(self, raw_path: Optional[str]) -> Optional[Path]:
        if not raw_path:
            return None
        try:
            root = self._job_root.resolve()
            output = Path(raw_path).resolve()
            if Path(os.path.commonpath([str(root), str(output)])) != root:
                return None
            return output
        except (OSError, ValueError):
            return None

    @staticmethod
    def _public(job: Dict[str, Any]) -> Dict[str, Any]:
        private = {
            "worker_url", "worker_token", "output_dir", "payload_path", "owner_id",
            "local_worker", "worker_started", "reservation_released", "cancel_requested",
            "dispatch_failures", "container_id", "requested_node",
        }
        return {k: v for k, v in job.items() if k not in private}

    def _record(self, job_id: str, **values: Any) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.setdefault(job_id, {"id": job_id, "created_at": time.time()})
            job.update(values)
            job["updated_at"] = time.time()
            self._prune_locked()
            self._persist_locked()
            return dict(job)

    def submit(self, *, name: str, script: str, gpu_count: int, node_name: Optional[str],
               timeout_seconds: int, network_access: bool, distributed: bool = False,
               owner_id: str = "admin") -> Dict[str, Any]:
        # Refresh completed remote jobs before computing free reservations.
        self.list_jobs()
        encoded = script.encode("utf-8")
        if not encoded.strip():
            raise ValueError("Add Python code before submitting a GPU job.")
        if len(encoded) > MAX_SCRIPT_BYTES:
            raise ValueError("Script exceeds the 1 MB job submission limit.")
        if not 1 <= gpu_count <= 16:
            raise ValueError("GPU count must be between 1 and 16.")
        if not 30 <= timeout_seconds <= MAX_RUNTIME_SECONDS:
            raise ValueError("Job timeout must be between 30 seconds and 6 hours.")
        name = (name or "GPU job").strip()[:80] or "GPU job"

        from backend.cluster_manager import cluster_manager
        if node_name:
            if node_name == "this-pc":
                known = cluster_manager.local_jobs_enabled
            else:
                known = any(n["name"] == node_name and n.get("jobs_enabled") for n in cluster_manager._nodes)
            if not known:
                raise ValueError("That provider is not registered or has not enabled job sharing.")
        with self._lock:
            active = [j for j in self._jobs.values() if j.get("state") in {"queued", "starting", "running"}]
            owner_active = [j for j in active if owner_id == "admin" or j.get("owner_id") == owner_id]
            if len(active) >= MAX_QUEUED_JOBS or len(owner_active) >= MAX_QUEUED_PER_DEVELOPER:
                raise ValueError("Job queue is full for this account. Wait for a job to finish, then try again.")
            job_id = uuid.uuid4().hex
            payload_path = self._pending_root / f"{job_id}.py"
            payload_path.write_bytes(encoded)
            record = {
                "id": job_id,
                "owner_id": owner_id,
                "name": name,
                "state": "queued",
                "node": node_name,
                "requested_node": node_name,
                "gpu_count": gpu_count,
                "timeout_seconds": timeout_seconds,
                "network_access": network_access,
                "distributed": bool(distributed),
                "created_at": time.time(),
                "updated_at": time.time(),
                "logs": "Queued. Waiting for a provider with enough available GPUs.",
                "worker_url": None,
                "worker_token": None,
                "local_worker": None,
                "output_dir": None,
                "payload_path": str(payload_path),
                "dispatch_failures": 0,
            }
            self._jobs[job_id] = record
            self._prune_locked()
            self._persist_locked()
        self._dispatch_queued(job_id)
        with self._lock:
            return self._public(dict(self._jobs[job_id]))

    def _queue_loop(self) -> None:
        while True:
            try:
                with self._lock:
                    queued = sorted(
                        (j["id"] for j in self._jobs.values() if j.get("state") == "queued"),
                        key=lambda job_id: self._jobs[job_id].get("created_at", 0),
                    )
                for job_id in queued:
                    self._dispatch_queued(job_id)
            except Exception:
                logger.exception("GPU scheduler loop failed")
            time.sleep(3)

    def _dispatch_queued(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("state") != "queued":
                return False
            target = self._choose_node(int(job["gpu_count"]), job.get("requested_node"))
            if target is None:
                return False
            node_id = target["name"]
            self._reservations[node_id] = self._reservations.get(node_id, 0) + int(job["gpu_count"])
            job.update({
                "node": node_id,
                "worker_url": target.get("url"),
                "worker_token": target.get("token"),
                "local_worker": target.get("local", False),
                "state": "starting",
                "logs": "Provider selected. Starting job...",
                "updated_at": time.time(),
                "reservation_released": False,
            })
            self._persist_locked()
            payload_path = Path(job["payload_path"])
            payload = {
                "id": job_id,
                "name": job["name"],
                "gpu_count": int(job["gpu_count"]),
                "timeout_seconds": int(job["timeout_seconds"]),
                "network_access": bool(job.get("network_access", False)),
                "distributed": bool(job.get("distributed", False)),
            }

        try:
            payload["script"] = payload_path.read_text(encoding="utf-8")
            if target.get("local"):
                self.start_worker(payload)
            else:
                import httpx
                response = httpx.post(
                    f"{target['url']}/api/worker/jobs",
                    json=payload,
                    headers={"Authorization": f"Bearer {target['token']}"},
                    timeout=15.0,
                )
                response.raise_for_status()
            payload_path.unlink(missing_ok=True)
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.pop("payload_path", None)
                    self._persist_locked()
            return True
        except Exception as exc:
            self._release_job_reservation(job_id)
            with self._lock:
                current = self._jobs.get(job_id)
                if not current:
                    return False
                failures = int(current.get("dispatch_failures", 0)) + 1
                terminal = failures >= 5
                current.update({
                    "state": "failed" if terminal else "queued",
                    "node": current.get("requested_node"),
                    "worker_url": None,
                    "worker_token": None,
                    "local_worker": None,
                    "dispatch_failures": failures,
                    "logs": f"Provider dispatch failed ({type(exc).__name__}); " + ("job stopped." if terminal else "retrying shortly."),
                    "finished_at": time.time() if terminal else None,
                    "reservation_released": True,
                    "updated_at": time.time(),
                })
                if terminal:
                    Path(current.get("payload_path", "")).unlink(missing_ok=True)
                    current.pop("payload_path", None)
                self._persist_locked()
            return False

    def _choose_node(self, gpu_count: int, requested: Optional[str]) -> Optional[Dict[str, Any]]:
        from backend.cluster_manager import cluster_manager

        statuses = cluster_manager.get_cluster_status().get("nodes", [])
        private_nodes = {n["name"]: n for n in cluster_manager._nodes}
        candidates = []
        for node in statuses:
            if requested and node.get("name") != requested:
                continue
            if not node.get("local") and not node.get("jobs_enabled"):
                continue
            if not node.get("online") or not node.get("gpu_passthrough"):
                continue
            free_devices = sum(
                1 for gpu in node.get("gpus", [])
                if float(gpu.get("vram_free_mb", 0)) >= 512
            )
            already = self._reservations.get(node["name"], 0)
            if free_devices - already < gpu_count:
                continue
            if node.get("local"):
                candidates.append({"name": node["name"], "local": True})
            else:
                private = private_nodes.get(node["name"])
                if private:
                    candidates.append({
                        "name": node["name"], "url": private["url"], "token": private["token"], "local": False
                    })
        candidates.sort(key=lambda n: (self._reservations.get(n["name"], 0), n["name"]))
        return candidates[0] if candidates else None

    def start_worker(self, payload: Dict[str, Any]) -> None:
        job_id = payload["id"]
        script = payload.get("script", "")
        if not isinstance(script, str) or not script.strip() or len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise ValueError("Script must contain between 1 byte and 1 MB of Python source.")
        if not 1 <= int(payload.get("gpu_count", 0)) <= 16:
            raise ValueError("GPU count must be between 1 and 16.")
        if not 30 <= int(payload.get("timeout_seconds", 0)) <= MAX_RUNTIME_SECONDS:
            raise ValueError("Job timeout must be between 30 seconds and 6 hours.")
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id].get("worker_started"):
                return
            record = self._jobs.setdefault(job_id, {"id": job_id, "created_at": time.time()})
            record.update({
                "name": payload["name"], "state": "starting", "node": "this-pc",
                "gpu_count": payload["gpu_count"], "timeout_seconds": payload["timeout_seconds"],
                "network_access": payload["network_access"], "distributed": bool(payload.get("distributed", False)),
                "worker_started": True,
                "local_worker": True, "logs": "Preparing isolated Docker job...", "updated_at": time.time(),
            })
            self._persist_locked()
        threading.Thread(target=self._run_worker, args=(dict(payload),), daemon=True,
                         name=f"gconnect-job-{job_id[:8]}").start()

    def _run_worker(self, payload: Dict[str, Any]) -> None:
        job_id = payload["id"]
        container = None
        job_dir: Optional[Path] = None
        try:
            if not HAS_DOCKER or not docker_manager.docker_available or not docker_manager.client:
                raise RuntimeError("Docker is unavailable on this provider.")
            if not docker_manager.gpu_passthrough_available or DeviceRequest is None:
                raise RuntimeError("NVIDIA GPU passthrough is unavailable on this provider.")
            try:
                docker_manager.client.images.get(settings.CONTAINER_IMAGE)
            except Exception as exc:
                raise RuntimeError("Configured container image is not cached on this provider; pull it before accepting jobs.") from exc

            self._record(job_id, state="starting", logs="Preparing isolated Docker job...")
            self._job_root.mkdir(parents=True, exist_ok=True)
            job_dir = Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=str(self._job_root)))
            input_dir = job_dir / "input"
            output_dir = job_dir / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "main.py").write_text(payload["script"], encoding="utf-8")

            # This execution path is for invited/trusted developers. The job
            # sees only its read-only source and a writable output directory.
            run_script = ["python", "/job/main.py"]
            if payload.get("distributed"):
                run_script = ["torchrun", "--standalone", f"--nproc_per_node={int(payload['gpu_count'])}", "/job/main.py"]
            container = docker_manager.client.containers.run(
                image=settings.CONTAINER_IMAGE,
                command=["timeout", "--signal=TERM", "--kill-after=10s",
                         f"{int(payload['timeout_seconds'])}s", *run_script],
                name=f"gconnect-job-{job_id[:12]}",
                detach=True,
                remove=False,
                working_dir="/output",
                volumes={
                    str(input_dir.resolve()): {"bind": "/job", "mode": "ro"},
                    str(output_dir.resolve()): {"bind": "/output", "mode": "rw"},
                },
                device_requests=[DeviceRequest(count=payload["gpu_count"], capabilities=GPU_CAPABILITIES)],
                network_disabled=not payload["network_access"],
                mem_limit="8g",
                cpu_period=100000,
                cpu_quota=400000,
                pids_limit=512,
                shm_size="1g",
                read_only=True,
                tmpfs={"/tmp": "rw,nosuid,size=512m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                environment={"PYTHONUNBUFFERED": "1", "HF_HOME": "/tmp/hf"},
                labels={"dev.gconnect.job": job_id},
            )
            self._record(job_id, state="running", started_at=time.time(), container_id=container.short_id,
                         logs="Job running. Output will appear here when the process exits.", output_dir=str(output_dir))

            deadline = time.monotonic() + int(payload["timeout_seconds"])
            while time.monotonic() < deadline:
                with self._lock:
                    current = self._jobs.get(job_id, {})
                    if current.get("cancel_requested"):
                        try:
                            container.kill()
                        except Exception:
                            pass
                        self._record(job_id, state="cancelled", finished_at=time.time(), logs="Job cancelled.")
                        return
                container.reload()
                if self._directory_size(output_dir) > MAX_JOB_OUTPUT_BYTES:
                    try:
                        container.kill()
                    except Exception:
                        pass
                    self._record(job_id, state="failed", finished_at=time.time(),
                                 logs="Job output exceeded the 256 MB storage limit.")
                    shutil.rmtree(output_dir, ignore_errors=True)
                    return
                if container.status in {"exited", "dead"}:
                    break
                time.sleep(1)
            else:
                try:
                    container.kill()
                except Exception:
                    pass
                self._record(job_id, state="timeout", finished_at=time.time(), logs="Job exceeded its time limit.")
                return

            container.reload()
            result = container.wait(timeout=5)
            raw_logs = container.logs(stdout=True, stderr=True, tail=2000)
            logs = raw_logs.decode("utf-8", errors="replace")[-MAX_LOG_BYTES:]
            exit_code = int(result.get("StatusCode", 1))
            state = "completed" if exit_code == 0 else "timeout" if exit_code == 124 else "failed"
            if state == "timeout" and not logs:
                logs = "Job exceeded its time limit."
            self._record(job_id, state=state,
                         finished_at=time.time(), exit_code=exit_code,
                         logs=logs or ("Job completed successfully." if exit_code == 0 else "Job failed without output."),
                         output_dir=str(output_dir))
        except Exception as exc:
            logger.exception("GPU job %s failed", job_id)
            self._record(job_id, state="failed", finished_at=time.time(),
                         logs=f"{type(exc).__name__}: {str(exc)[:1000]}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            self._release_job_reservation(job_id)
            if job_dir is not None:
                # Keep output artifacts on successful jobs; clear staged code.
                output = job_dir / "output"
                for item in job_dir.iterdir():
                    if item == output:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)

    def worker_job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            result = dict(job)
        if result.get("state") == "running" and result.get("container_id") and docker_manager.client:
            try:
                container = docker_manager.client.containers.get(result["container_id"])
                logs = container.logs(stdout=True, stderr=True, tail=1000)
                result["logs"] = logs.decode("utf-8", errors="replace")[-MAX_LOG_BYTES:]
                self._record(job_id, logs=result["logs"])
            except Exception:
                pass
        return self._public(result)

    def worker_jobs(self, job_ids: list[str]) -> Dict[str, Any]:
        with self._lock:
            existing = [job_id for job_id in job_ids if job_id in self._jobs]
        return {"jobs": [self.worker_job(job_id) for job_id in existing]}

    @staticmethod
    def _directory_size(directory: Path) -> int:
        total = 0
        try:
            for root, dirs, files in os.walk(directory, followlinks=False):
                dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
                for name in files:
                    path = Path(root) / name
                    if not path.is_symlink():
                        try:
                            total += path.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
        return total

    def cancel_worker(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.get("state") in {"completed", "failed", "cancelled", "timeout", "interrupted"}:
                return False
            if job.get("state") == "queued":
                job["state"] = "cancelled"
                job["finished_at"] = time.time()
                job["logs"] = "Job cancelled while queued."
                path = job.pop("payload_path", None)
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
                self._persist_locked()
                return True
            job["cancel_requested"] = True
            job["updated_at"] = time.time()
            self._persist_locked()
            return True

    def _release_reservation(self, node_id: str, count: int) -> None:
        with self._lock:
            remaining = max(0, self._reservations.get(node_id, 0) - count)
            if remaining:
                self._reservations[node_id] = remaining
            else:
                self._reservations.pop(node_id, None)

    def _release_job_reservation(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id, {})
            if job.get("reservation_released"):
                return
            job["reservation_released"] = True
            node_id = job.get("node", "")
            gpu_count = int(job.get("gpu_count", 0))
            self._persist_locked()
        if node_id:
            self._release_reservation(node_id, gpu_count)

    def list_jobs(self, owner_id: str = "admin") -> Dict[str, Any]:
        terminal = {"completed", "failed", "cancelled", "timeout", "interrupted"}
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values()
                    if owner_id == "admin" or job.get("owner_id") == owner_id]
        remote_groups: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
        for job in jobs:
            if job.get("local_worker"):
                try:
                    self.worker_job(job["id"])
                except KeyError:
                    pass
                continue
            if not job.get("worker_url"):
                continue
            if job.get("state") in terminal:
                continue
            remote_groups.setdefault((job["worker_url"], job["worker_token"]), []).append(job)

        for (worker_url, worker_token), group in remote_groups.items():
            try:
                import httpx
                response = httpx.post(
                    f"{worker_url}/api/worker/jobs/status",
                    json={"ids": [job["id"] for job in group]},
                    headers={"Authorization": f"Bearer {worker_token}"}, timeout=8.0,
                )
                if response.status_code == 200:
                    statuses = {item.get("id"): item for item in response.json().get("jobs", [])}
                    for job in group:
                        remote = statuses.get(job["id"])
                        if not remote:
                            continue
                        was_terminal = job.get("state") in terminal
                        is_terminal = remote.get("state") in terminal
                        self._record(job["id"], state=remote.get("state", job["state"]),
                                     logs=remote.get("logs", job.get("logs", "")),
                                     started_at=remote.get("started_at"), finished_at=remote.get("finished_at"),
                                     exit_code=remote.get("exit_code"))
                        if is_terminal and not was_terminal:
                            self._release_job_reservation(job["id"])
            except Exception:
                pass
        with self._lock:
            public_jobs = [self._public(j) for j in self._jobs.values()
                           if owner_id == "admin" or j.get("owner_id") == owner_id]
        public_jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
        return {"jobs": public_jobs}

    def get_job(self, job_id: str, owner_id: str = "admin") -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or (owner_id != "admin" and job.get("owner_id") != owner_id):
                return None
            result = dict(job)
        if result.get("local_worker") is False and result.get("worker_url"):
            try:
                import httpx
                response = httpx.get(f"{result['worker_url']}/api/worker/jobs/{job_id}",
                                     headers={"Authorization": f"Bearer {result['worker_token']}"}, timeout=5.0)
                if response.status_code == 200:
                    remote = response.json()
                    was_terminal = result.get("state") in {"completed", "failed", "cancelled", "timeout", "interrupted"}
                    result.update({k: remote[k] for k in ("state", "logs", "started_at", "finished_at", "exit_code") if k in remote})
                    self._record(job_id, **{k: result[k] for k in ("state", "logs", "started_at", "finished_at", "exit_code") if k in result})
                    if result.get("state") in {"completed", "failed", "cancelled", "timeout", "interrupted"} and not was_terminal:
                        self._release_job_reservation(job_id)
            except Exception:
                pass
        return self._public(result)

    def cancel(self, job_id: str, owner_id: str = "admin") -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if (not job or (owner_id != "admin" and job.get("owner_id") != owner_id)
                    or job.get("state") in {"completed", "failed", "cancelled", "timeout", "interrupted"}):
                return False
            if job.get("state") == "queued":
                job["state"] = "cancelled"
                job["finished_at"] = time.time()
                job["logs"] = "Job cancelled while queued."
                path = job.pop("payload_path", None)
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
                self._persist_locked()
                return True
            if job.get("local_worker"):
                job["cancel_requested"] = True
                self._persist_locked()
                return True
            url, token = job.get("worker_url"), job.get("worker_token")
        try:
            import httpx
            response = httpx.post(f"{url}/api/worker/jobs/{job_id}/cancel",
                                  headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    def cancel_owner_jobs(self, owner_id: str) -> int:
        with self._lock:
            ids = [job["id"] for job in self._jobs.values()
                   if job.get("owner_id") == owner_id
                   and job.get("state") in {"queued", "starting", "running"}]
        return sum(1 for job_id in ids if self.cancel(job_id, owner_id))

    def cancel_provider_jobs(self) -> int:
        with self._lock:
            ids = [job["id"] for job in self._jobs.values()
                   if job.get("local_worker") and job.get("state") in {"queued", "starting", "running"}]
        return sum(1 for job_id in ids if self.cancel_worker(job_id))

    def cancel_node_jobs(self, node_name: str) -> int:
        with self._lock:
            ids = [job["id"] for job in self._jobs.values()
                   if job.get("node") == node_name
                   and job.get("state") in {"queued", "starting", "running"}]
        return sum(1 for job_id in ids if self.cancel(job_id))

    def artifact_bytes(self, job_id: str, owner_id: str = "admin") -> bytes:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or (owner_id != "admin" and job.get("owner_id") != owner_id):
                raise KeyError(job_id)
            remote_url = job.get("worker_url") if job.get("local_worker") is False else None
            remote_token = job.get("worker_token")
            output_dir = job.get("output_dir")

        if remote_url:
            import httpx
            response = httpx.get(
                f"{remote_url}/api/worker/jobs/{job_id}/artifacts",
                headers={"Authorization": f"Bearer {remote_token}"}, timeout=45.0,
            )
            response.raise_for_status()
            if len(response.content) > 256 * 1024 * 1024:
                raise ValueError("Job artifacts exceed the 256 MB download limit.")
            return response.content

        root = (settings.RUNTIME_DIR / "jobs").resolve()
        if not output_dir:
            raise FileNotFoundError("Job has no output directory.")
        folder = Path(output_dir).resolve()
        try:
            if Path(os.path.commonpath([str(root), str(folder)])) != root:
                raise ValueError("Invalid job output path.")
        except ValueError as exc:
            raise ValueError("Invalid job output path.") from exc
        if not folder.is_dir():
            raise FileNotFoundError("Job output directory is unavailable.")

        archive = io.BytesIO()
        total = 0
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for path in folder.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                total += size
                if total > 256 * 1024 * 1024:
                    raise ValueError("Job artifacts exceed the 256 MB download limit.")
                zipped.write(path, path.relative_to(folder).as_posix())
        return archive.getvalue()


job_manager = JobManager()
