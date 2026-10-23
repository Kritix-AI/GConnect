# Phase 1: REA Investigation & Evidence Report

## Architectural Reverse-Engineering Blueprint for Remote GPU Host Daemons

This document records the structural patterns, system-level primitives, and reverse-engineered design contracts extracted from production GPU worker daemons and cloud Web IDE runners (e.g. Vast.ai host agents, Salad Node worker runtimes, and Docker-backed interactive container environments).

---

## 1. Binary & Runtime Triage

Production GPU host daemons commonly operate across two architectural tiers:
1. **Host Supervisor Layer**: A lightweight compiled Go or asynchronous Python (FastAPI/asyncio) daemon that interfaces with the local container runtime (`dockerd` / `containerd`) and the NVIDIA Management Library (`libnvidia-ml.so.1` on Linux, `nvml.dll` on Windows).
2. **Container Sandbox Layer**: An unprivileged, OCI-compliant container runtime running isolated compute environments (e.g. `pytorch/pytorch:latest`) where hardware devices are selectively surfaced via NVIDIA Container Toolkit (`nvidia-container-cli`).

```
[Web Client (Monaco / xterm.js)]
         │
    HTTP / WebSocket
         │
         ▼
[Host Daemon: FastAPI / PTY Bridge (:8000)]
   ├── Hardware Polling: pynvml (libnvidia-ml)
   └── Container Supervisor: Docker Engine API
         │
    Docker Hijacked Raw Socket
         │
         ▼
[Sandbox Container: PyTorch + CUDA]
   ├── GPU Passthrough: NVIDIA DeviceRequest
   ├── Volume Mount: /workspace
   ├── Persistent Cache: /workspace/.hf_cache
   └── Audio Inference Stream: Port 8001
```

---

## 2. Hardware Polling & Telemetry Dissection

### A. NVML Symbol Resolution & Safety Boundaries
Commercial GPU nodes avoid calling high-overhead CUDA driver functions (like `cudaMemGetInfo`) from the host supervisor, because initializing a CUDA context on the host allocates hardware resources and can cause driver deadlocks when child containers attempt exclusive context creation.

Instead, production host agents communicate directly with **NVML** (NVIDIA Management Library):
* **Initialization**: `nvmlInit_v2()`
* **Device Discovery**: `nvmlDeviceGetCount_v2()`
* **Device Handle**: `nvmlDeviceGetHandleByIndex_v2(index)`
* **Memory Status**: `nvmlDeviceGetMemoryInfo(handle, &mem_info)` -> returns `total`, `free`, `used` in bytes.
* **Compute Utilization**: `nvmlDeviceGetUtilizationRates(handle, &rates)` -> returns `gpu` (0-100%) and `memory` (0-100%).
* **Thermal Sensors**: `nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU, &temp)`.

### B. Polling Cadence & Asynchronous Framing
* **Deadlock Prevention**: NVML calls are non-blocking, but querying frequency must be bounded. High-frequency polling (>10 Hz) induces measurable PCIe and driver CPU interrupt overhead.
* **Optimal Cadence**: 1.0 Hz (1000 ms) provides instantaneous human feedback without hardware overhead.
* **Message Framing**: JSON telemetry frames over WebSocket:
  ```json
  {
    "type": "gpu_stat",
    "gpu_name": "NVIDIA GeForce RTX 4090",
    "vram_total_mb": 24576.0,
    "vram_used_mb": 8420.5,
    "vram_percent": 34.3,
    "gpu_utilization_percent": 78,
    "temperature_c": 64,
    "timestamp": 1729689400.12
  }
  ```

---

## 3. Container & Sandbox Isolation Dissection

### A. NVIDIA Container Toolkit GPU Passthrough
In standard Docker deployments, containers do not have access to GPU hardware devices (`/dev/nvidia*`, `/dev/nvidia-uvm`) unless explicitly declared.

The reverse-engineered OCI specification used by production worker runners maps to the Docker Engine API `DeviceRequest`:
```python
device_requests = [
    docker.types.DeviceRequest(
        count=-1,  # Passthrough all available GPUs
        capabilities=[["gpu", "compute", "utility"]]
    )
]
```
* `compute`: Grants access to CUDA libraries and driver compute contexts.
* `utility`: Grants access to NVML, `nvidia-smi`, and hardware telemetry tools inside the container.

### B. Shared Memory (`shm_size`) Allocation
* **The Failure Mode**: By default, Docker allocates 64 MB of POSIX shared memory (`/dev/shm`). When running multimodal voice agent pipelines (e.g. `faster-whisper`, `indic-parler-tts`) with PyTorch `DataLoader(..., num_workers > 0)` or torch multiprocessing, PyTorch exhausts this memory instantly, triggering:
  `RuntimeError: DataLoader worker (pid ...) is killed by signal: Bus error.`
* **Extracted Solution**: Sandbox containers must specify `shm_size="8g"` (or `ipc_mode="host"`). `8g` provides ample buffer for heavy tensors and audio batches while retaining container namespace isolation.

### C. Persistent Model Caching (`HF_HOME`)
* Large voice models (~9 GB across Whisper, Mistral-7B 4-bit, and Indic-Parler-TTS) require significant download time and bandwidth.
* **Extracted Isolation Pattern**: Host daemon configures `HF_HOME=/workspace/.hf_cache`.
* Because `/workspace` is backed by a persistent host volume, weights are downloaded once into `workspace/.hf_cache/` and are immediately available on subsequent container restarts.

### D. Dual-Channel Networking
* **Port 8000**: Host-level binding for Web IDE, file operations, terminal streaming, and telemetry.
* **Port 8001**: Container port mapped to host (`ports={'8001/tcp': 8001}`) allowing external clients (e.g. `kritix_client.py` streaming audio from a laptop) to reach voice inference servers (`kritix_server.py`) running inside the container over Tailscale or LAN.

---

## 4. Interactive Raw PTY & WebSocket Multiplexing Dissection

### A. Docker Exec Socket Hijacking
Web IDEs (like VS Code Server and cloud IDE runners) achieve zero-latency terminal responsiveness by avoiding interim files, buffers, or polling.

1. **Exec Creation**:
   ```python
   exec_instance = client.api.exec_create(
       container_id,
       cmd=["/bin/bash"],
       stdin=True,
       stdout=True,
       stderr=True,
       tty=True,
       environment={"TERM": "xterm-256color", "HF_HOME": "/workspace/.hf_cache"}
   )
   ```
2. **Socket Hijacking**:
   `exec_start(exec_id, socket=True, tty=True)` hijacks the HTTP upgrade connection to the Docker daemon, exposing a bidirectional stream (`sock`).
3. **VT100 Passthrough**:
   Bytes received from `xterm.js` over WebSocket are piped directly into the socket `sendall()`. Keystrokes like `\x03` (`Ctrl+C`), `\t` (Tab completion), and ANSI escape codes are interpreted natively by the container's bash process.
4. **Dynamic Resizing**:
   Terminal resize events (`cols`, `rows`) sent from `FitAddon` are converted into `exec_resize` calls on the Docker daemon:
   `client.api.exec_resize(exec_id, height=rows, width=cols)`

---

## 5. Security & Path Traversal Mitigations

* **Boundary Defense**: User-facing file APIs (`/api/files/save`, `/api/files/content`, `/api/files/delete`) sanitize all input paths by resolving absolute targets and validating against the canonical workspace root using `os.path.commonpath([resolved_path, WORKSPACE_DIR]) == WORKSPACE_DIR`.
* **Fail-Closed**: Any path containing traversal tokens (`../`, `..\\`) that resolves outside `WORKSPACE_DIR` triggers an immediate HTTP 403 Forbidden.

