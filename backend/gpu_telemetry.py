import asyncio
import logging
import math
import time
from typing import Any, Dict

from backend.config import settings

logger = logging.getLogger("connect.telemetry")

try:
    import pynvml
    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False
    logger.warning("pynvml library not installed. Using simulated GPU telemetry.")


class GPUTelemetryService:
    def __init__(self):
        self.nvml_initialized = False
        self.gpu_count = 0
        self.primary_handle = None
        self.gpu_name = "NVIDIA GeForce RTX 4090 (Simulated)"
        self._init_nvml()

    def _init_nvml(self):
        if not HAS_PYNVML:
            return

        try:
            pynvml.nvmlInit()
            self.gpu_count = pynvml.nvmlDeviceGetCount()
            if self.gpu_count > 0:
                self.primary_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                raw_name = pynvml.nvmlDeviceGetName(self.primary_handle)
                self.gpu_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
                self.nvml_initialized = True
                logger.info(f"Initialized NVML. Detected {self.gpu_count} GPU(s). Primary: {self.gpu_name}")
            else:
                logger.warning("NVML initialized but 0 GPU devices reported.")
        except Exception as e:
            logger.warning(f"Could not initialize NVML: {e}. Falling back to simulated GPU telemetry.")
            self.nvml_initialized = False

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns the current GPU telemetry payload."""
        if self.nvml_initialized and self.primary_handle:
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.primary_handle)
                util_rates = pynvml.nvmlDeviceGetUtilizationRates(self.primary_handle)
                temp = pynvml.nvmlDeviceGetTemperature(self.primary_handle, pynvml.NVML_TEMPERATURE_GPU)

                total_mb = round(mem_info.total / (1024 * 1024), 1)
                used_mb = round(mem_info.used / (1024 * 1024), 1)
                free_mb = round(mem_info.free / (1024 * 1024), 1)
                vram_percent = round((used_mb / total_mb) * 100, 1) if total_mb > 0 else 0

                return {
                    "type": "gpu_stat",
                    "gpu_name": self.gpu_name,
                    "gpu_count": self.gpu_count,
                    "vram_total_mb": total_mb,
                    "vram_used_mb": used_mb,
                    "vram_free_mb": free_mb,
                    "vram_percent": vram_percent,
                    "gpu_utilization_percent": util_rates.gpu,
                    "memory_utilization_percent": util_rates.memory,
                    "temperature_c": temp,
                    "is_mock": False,
                    "timestamp": time.time(),
                }
            except Exception as e:
                logger.error(f"Error querying NVML stats: {e}")

        # Simulated / Mock fallback
        t = time.time()
        fluctuation = math.sin(t / 5.0) * 450
        mock_used = round(4200.0 + fluctuation, 1)
        mock_total = 24576.0  # 24GB VRAM
        mock_vram_pct = round((mock_used / mock_total) * 100, 1)
        mock_util = int(abs(math.sin(t / 8.0)) * 45 + 12)
        mock_temp = int(58 + math.sin(t / 12.0) * 6)

        return {
            "type": "gpu_stat",
            "gpu_name": self.gpu_name,
            "gpu_count": 1,
            "vram_total_mb": mock_total,
            "vram_used_mb": mock_used,
            "vram_free_mb": round(mock_total - mock_used, 1),
            "vram_percent": mock_vram_pct,
            "gpu_utilization_percent": mock_util,
            "memory_utilization_percent": int(mock_vram_pct),
            "temperature_c": mock_temp,
            "is_mock": True,
            "timestamp": t,
        }

    async def stream_telemetry(self, websocket):
        """Asynchronous stream of telemetry stats pushed to the connected WebSocket."""
        try:
            while True:
                snapshot = self.get_snapshot()
                await websocket.send_json(snapshot)
                await asyncio.sleep(settings.TELEMETRY_INTERVAL_SECONDS)
        except Exception as e:
            logger.debug(f"Telemetry stream closed: {e}")


gpu_telemetry_service = GPUTelemetryService()

