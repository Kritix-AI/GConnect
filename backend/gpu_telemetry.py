import asyncio
import logging
import platform
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

from backend.config import settings

logger = logging.getLogger("connect.telemetry")

try:
    import pynvml
    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False
    logger.warning("nvidia-ml-py library not installed. GPU telemetry unavailable.")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil not installed. CPU/RAM fallback telemetry unavailable.")


def _detect_cpu_name() -> str:
    """Best-effort human-readable CPU model name (registry on Windows,
    /proc/cpuinfo elsewhere, platform.processor() as last resort)."""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if name and name.strip():
                    return name.strip()
        else:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "CPU"

# Serve the last successful snapshot for this long when a sampling call fails,
# so transient NVML errors never flip the UI to zeros (observed as the
# "real GPU name + NO GPU badge + 0.0 GB" glitch).
LAST_GOOD_TTL_SECONDS = 5.0


class GPUTelemetryService:
    """Samples NVML on a single background task (never on the event loop)
    and fans each snapshot out to subscribed WebSocket queues.

    NVML is not thread-safe and get_snapshot() is called from several threads
    (broadcaster executor, /api/telemetry/snapshot, /api/cluster/status) - all
    sampling is serialized behind a lock."""

    def __init__(self):
        self.nvml_initialized = False
        self.gpu_count = 0
        self.handles: List[Any] = []
        self.gpu_name = "No NVIDIA GPU detected"
        self._cpu_name = _detect_cpu_name()
        self._subscribers: Set[asyncio.Queue] = set()
        self._broadcast_task: asyncio.Task | None = None
        self._nvml_lock = threading.Lock()
        self._last_good: Optional[Dict[str, Any]] = None
        self._last_good_ts: float = 0.0
        self._nvml_last_init_attempt: float = 0.0
        self._init_nvml()

    def _init_nvml(self):
        if not HAS_PYNVML:
            return

        self._nvml_last_init_attempt = time.time()
        try:
            pynvml.nvmlInit()
            self.gpu_count = pynvml.nvmlDeviceGetCount()
            if self.gpu_count > 0:
                self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.gpu_count)]
                raw_name = pynvml.nvmlDeviceGetName(self.handles[0])
                self.gpu_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
                self.nvml_initialized = True
                logger.info(f"Initialized NVML. Detected {self.gpu_count} GPU(s). Primary: {self.gpu_name}")
            else:
                logger.warning("NVML initialized but 0 GPU devices reported.")
        except Exception as e:
            logger.warning(f"Could not initialize NVML: {e}. Falling back to simulated GPU telemetry.")
            self.nvml_initialized = False

    def _maybe_reinit_nvml(self) -> None:
        """If NVML failed to initialize at startup (e.g. driver not ready
        yet), retry periodically instead of staying 'NO GPU' forever."""
        if self.nvml_initialized or not HAS_PYNVML:
            return
        if time.time() - self._nvml_last_init_attempt < 30.0:
            return
        with self._nvml_lock:
            if not self.nvml_initialized:
                logger.info("Retrying NVML initialization...")
                self._init_nvml()

    # ------------------------------------------------------------------
    # Sampling (blocking NVML calls - always run in an executor)
    # ------------------------------------------------------------------

    def _sample_gpu(self, handle) -> Dict[str, Any]:
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util_rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

        total_mb = round(mem_info.total / (1024 * 1024), 1)
        used_mb = round(mem_info.used / (1024 * 1024), 1)
        free_mb = round(mem_info.free / (1024 * 1024), 1)
        vram_percent = round((used_mb / total_mb) * 100, 1) if total_mb > 0 else 0

        # NVML's `gpu` rate is SM (shader/compute) utilization only. Desktop
        # workloads (DWM compositing, video, copies) barely touch the SMs, so
        # SM-only numbers look far below Task Manager, which reports the
        # busiest engine. Shown LOAD = max(SM, memory-controller) - the same
        # convention overlay tools use; exact SM value stays in the payload.
        sm = int(util_rates.gpu)
        mc = int(getattr(util_rates, "memory", 0) or 0)

        return {
            "name": self.gpu_name,
            "index": 0,
            "vram_total_mb": total_mb,
            "vram_used_mb": used_mb,
            "vram_free_mb": free_mb,
            "vram_percent": vram_percent,
            "gpu_utilization_percent": max(sm, mc),
            "sm_utilization_percent": sm,
            "memory_controller_percent": mc,
            "temperature_c": temp,
        }

    def _sample_host(self) -> Dict[str, Any]:
        """CPU + RAM usage - the UI shows these instead of the GPU bar on
        machines with no NVIDIA GPU. Empty dict when psutil is unavailable."""
        if not HAS_PSUTIL:
            return {}
        vm = psutil.virtual_memory()
        # Bare instances (unit tests) may skip __init__; detect lazily.
        cpu_name = getattr(self, "_cpu_name", None) or _detect_cpu_name()
        return {
            "name": cpu_name,
            "percent": psutil.cpu_percent(interval=None),
            "cores": psutil.cpu_count(logical=True) or 0,
            "ram_total_mb": round(vm.total / (1024 * 1024), 1),
            "ram_used_mb": round((vm.total - vm.available) / (1024 * 1024), 1),
            "ram_percent": round(vm.percent, 1),
        }

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns the current GPU telemetry payload (blocking NVML access).
        Call via run_in_executor from async contexts. Summary fields are
        aggregates across ALL detected GPUs; per-GPU detail is in gpus[]."""
        self._maybe_reinit_nvml()

        if self.nvml_initialized and self.handles:
            try:
                # Serialize all NVML access: the library is not thread-safe
                # and concurrent calls fail intermittently.
                with self._nvml_lock:
                    gpus = [self._sample_gpu(h) for h in self.handles]

                for i, g in enumerate(gpus):
                    g["index"] = i

                total_mb = round(sum(g["vram_total_mb"] for g in gpus), 1)
                used_mb = round(sum(g["vram_used_mb"] for g in gpus), 1)
                vram_percent = round((used_mb / total_mb) * 100, 1) if total_mb > 0 else 0
                util = round(sum(g["gpu_utilization_percent"] for g in gpus) / len(gpus))
                sm_util = round(sum(g["sm_utilization_percent"] for g in gpus) / len(gpus))
                temp = round(sum(g["temperature_c"] for g in gpus) / len(gpus))

                snapshot = {
                    "type": "gpu_stat",
                    "gpu_name": self.gpu_name,
                    "gpu_count": self.gpu_count,
                    "vram_total_mb": total_mb,
                    "vram_used_mb": used_mb,
                    "vram_free_mb": round(total_mb - used_mb, 1),
                    "vram_percent": vram_percent,
                    "gpu_utilization_percent": util,
                    "sm_utilization_percent": sm_util,
                    "memory_utilization_percent": vram_percent,
                    "temperature_c": temp,
                    "gpus": gpus,
                    "host": self._sample_host(),
                    "is_mock": False,
                    "degraded": False,
                    "timestamp": time.time(),
                }
                self._last_good = snapshot
                self._last_good_ts = time.time()
                return snapshot
            except Exception as e:
                logger.error(f"Error querying NVML stats: {e}")

            # Transient failure: serve the last real values instead of
            # flipping the UI to zeros.
            if self._last_good and (time.time() - self._last_good_ts) < LAST_GOOD_TTL_SECONDS:
                stale = dict(self._last_good)
                stale["timestamp"] = time.time()
                return stale

        # Honest fallback: no fabricated numbers, zeros + explicit label.
        # `degraded` distinguishes "GPU exists but signal lost" from
        # "no NVIDIA hardware/driver at all".
        return {
            "type": "gpu_stat",
            "gpu_name": self.gpu_name,
            "gpu_count": 0,
            "vram_total_mb": 0.0,
            "vram_used_mb": 0.0,
            "vram_free_mb": 0.0,
            "vram_percent": 0.0,
            "gpu_utilization_percent": 0,
            "memory_utilization_percent": 0,
            "temperature_c": 0,
            "gpus": [],
            "host": self._sample_host(),
            "is_mock": True,
            "degraded": self.nvml_initialized,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Broadcast fan-out
    # ------------------------------------------------------------------

    async def start(self):
        """Starts the single sampler task (called from app lifespan)."""
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())
            logger.info("GPU telemetry broadcaster started.")

    async def stop(self):
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except (asyncio.CancelledError, Exception):
                pass
            self._broadcast_task = None

    async def _broadcast_loop(self):
        loop = asyncio.get_running_loop()
        failures = 0
        while True:
            try:
                # Never block the event loop with NVML calls.
                snapshot = await loop.run_in_executor(None, self.get_snapshot)
                failures = 0
                # Iterate a copy: subscribe()/unsubscribe() mutate the set
                # from other tasks ("Set changed size during iteration").
                for q in list(self._subscribers):
                    try:
                        q.put_nowait(snapshot)
                    except asyncio.QueueFull:
                        # Client fell behind (e.g. webview busy): drop stale
                        # snapshots and deliver the newest. Never abandon the
                        # subscriber - that froze the UI at old values.
                        while True:
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        try:
                            q.put_nowait(snapshot)
                        except asyncio.QueueFull:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                if failures <= 3 or failures % 20 == 0:
                    logger.warning(f"Telemetry broadcast iteration failed ({failures}): {e}")
            await asyncio.sleep(settings.TELEMETRY_INTERVAL_SECONDS)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._subscribers.add(q)
        # Self-healing: if the broadcaster task died, restart it so this
        # subscriber (and every other one) starts receiving again.
        if self._broadcast_task is None or self._broadcast_task.done():
            logger.warning("Telemetry broadcaster was not running - restarting on subscribe.")
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    async def stream_telemetry(self, websocket):
        """Streams telemetry to one WebSocket. Waits for broadcaster pushes,
        but if none arrive within 2s (broadcaster dead or wedged), samples
        directly - the UI can never freeze at old values."""
        loop = asyncio.get_running_loop()
        q = self.subscribe()
        try:
            # First stat delivered immediately for snappy UI.
            first = await loop.run_in_executor(None, self.get_snapshot)
            await websocket.send_json(first)
            while True:
                try:
                    snapshot = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Broadcaster silent: sample directly and keep the UI live.
                    snapshot = await loop.run_in_executor(None, self.get_snapshot)
                await websocket.send_json(snapshot)
        except Exception as e:
            logger.debug(f"Telemetry stream closed: {e}")
        finally:
            self.unsubscribe(q)


gpu_telemetry_service = GPUTelemetryService()
