# Connect: Personal Remote GPU Development Platform

`Connect` is a lightweight, production-grade remote GPU development platform tailored for editing, executing, and monitoring Python scripts on a dedicated NVIDIA GPU workstation (such as a senior teammate's desktop or cloud machine).

It is specifically optimized for heavy voice and multimodal AI pipelines (such as `Kritix-Voice-Agent`, utilizing Faster-Whisper, 4-bit Mistral-7B, Indic-Parler-TTS, and Wav2Vec2) without risking host machine instability.

---

## 🌟 Key Features

* **Persistent Model Cache (`HF_HOME=/workspace/.hf_cache`)**:
  Voice pipelines download ~9 GB of models (Faster-Whisper, Mistral-7B, Indic-Parler-TTS). `Connect` mounts and configures the cache on persistent host storage so models are downloaded **once** and preserved across container restarts.

* **Dual-Channel Networking**:
  * **Primary Port (`8000`)**: Web IDE dashboard, Monaco code editor, file explorer APIs, raw PTY terminal WebSocket (`/ws/terminal`), and live NVML telemetry (`/ws/telemetry`).
  * **Inference Port (`8001`)**: Forwarded directly into the container (`8001:8001`) so voice servers (`kritix_server.py`) can receive low-latency microphone audio streams from client laptops over Tailscale or LAN.

* **Interactive Raw PTY Engine**:
  Hijacks Docker's raw exec socket (`tty=True`, `stdin=True`) to pipe unbuffered VT100/ANSI streams to `xterm.js`. Supports interactive token pasting (`HF_TOKEN`), `htop`, live `tqdm` progress bars, `Ctrl+C`, and dynamic window resizing.

* **PyTorch Memory & GPU Passthrough**:
  * Launches `pytorch/pytorch:latest` with `shm_size="8g"` to prevent PyTorch `DataLoader` multi-processing `Bus error` crashes.
  * Passthrough via NVIDIA Container Toolkit `DeviceRequest(count=-1, capabilities=[['gpu', 'compute', 'utility']])`.
  * Real-time 1 Hz `pynvml` telemetry broadcasting GPU model, VRAM used/total, GPU utilization %, and temperature (with graceful CPU/Mock fallback).

* **Modern Dark-Mode Web IDE**:
  * Left sidebar file manager with inline create/delete.
  * Monaco Editor with Python syntax highlighting and 1.5s debounced auto-save.
  * xterm.js terminal with draggable split divider.
  * One-click "▶ Run Script (`Ctrl+Enter`)" trigger that executes `python <active_file>.py` in the container shell.

* **Security & Host Isolation**:
  * Filesystem access strictly isolated to the mounted `workspace/` directory.
  * Path traversal prevention enforced via `os.path.commonpath`.

---

## 🏗️ Architecture

```
[Client Laptop (Browser / Voice Client)]
   │
   ├── Port 8000: Web IDE / Monaco / xterm.js / Telemetry
   └── Port 8001: Live Audio Stream to Voice Server
         │
         ▼
[Host Machine Daemon: FastAPI (:8000)]
   ├── Hardware Polling: pynvml (libnvidia-ml)
   └── Container Supervisor: Docker Engine API
         │
    Docker Hijacked Raw Socket
         │
         ▼
[Sandbox Container: PyTorch + CUDA]
   ├── GPU Passthrough: NVIDIA DeviceRequest
   ├── Shared Memory: shm_size=8g
   ├── Volume Mount: /workspace
   ├── Persistent Weights: /workspace/.hf_cache
   └── Voice Inference Server: Port 8001
```

---

## 🚀 Quickstart

### 1. Host Machine Setup (Remote GPU PC)

#### Prerequisites:
* Python 3.10+
* Docker Desktop or Docker Engine
* NVIDIA Driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (for hardware GPU passthrough)

#### Windows Host:
Simply run:
```cmd
scripts\setup_host.bat
```

#### Linux Host:
```bash
chmod +x scripts/setup_host.sh
./scripts/setup_host.sh
```

Or run manually:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

### 2. Client Connection (Your Laptop)

1. Open your browser and navigate to:
   * **Local / LAN**: `http://<GPU_HOST_IP>:8000`
   * **Tailscale VPN**: `http://<TAILSCALE_HOST_IP>:8000`
2. The dark-mode IDE will load immediately.
3. Select or create a `.py` script in the sidebar file tree.
4. Press `Ctrl+Enter` or click **Run Script** to execute in the container terminal!

---

### 3. Voice Inference Server (`Kritix-Voice-Agent`)

When running `kritix_server.py` inside the container:
```bash
# In the Connect terminal:
python kritix_server.py
```
On your local laptop, run `kritix_client.py` pointing to the host's port `8001`:
```bash
python kritix_client.py --host <GPU_HOST_IP> --port 8001
```

---

## 🧪 Testing

Run the automated test suite verifying file APIs, path traversal blocks, and telemetry:
```bash
pytest tests/ -v
```

---

## ⚙️ Configuration Options

Environment variables can be set to override default behavior:

| Variable | Default | Description |
|---|---|---|
| `CONNECT_HOST` | `0.0.0.0` | Host daemon bind IP |
| `CONNECT_PORT` | `8000` | Web IDE port |
| `CONNECT_INFERENCE_PORT` | `8001` | Audio inference streaming port forwarded to container |
| `CONNECT_CONTAINER_IMAGE` | `pytorch/pytorch:latest` | Base Docker sandbox image |
| `CONNECT_SHM_SIZE` | `8g` | Container shared memory size |
| `CONNECT_WORKSPACE_DIR` | `./workspace` | Directory mounted to `/workspace` |
| `CONNECT_EPHEMERAL_MODE` | `false` | When `true`, models are kept strictly inside the container's temporary layer (Google Colab style) rather than host disk |

### 🧹 Ephemeral Model Mode & Disk Cleanup
* **Google Colab-Style Ephemeral Mode**: Set `CONNECT_EPHEMERAL_MODE=true` to ensure downloaded model weights (`~9 GB`) remain inside container temporary storage and are never written to the host's persistent disk.
* **On-Demand Disk Cleanup**: Click **"🧹 Clean Models (Free Disk)"** in the top bar or send `POST /api/cache/wipe` at any time to instantly clear downloaded model cache from the host machine and reclaim disk space.

