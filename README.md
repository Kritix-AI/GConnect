<div align="center">

# ⚡ GConnect

### Universal Remote GPU Development Platform & Web IDE

**Turn any PC with an NVIDIA GPU into your private, self-hosted cloud GPU studio (Colab/Vast.ai alternative) — and pool GPUs from every PC you own into one place.**

[![Version](https://img.shields.io/badge/version-2.2.2-3fb950?style=flat-square)](docs/CHANGELOG.md)
[![CI](https://github.com/Kritix-AI/GConnect/actions/workflows/ci.yml/badge.svg?branch=v2)](https://github.com/Kritix-AI/GConnect/actions/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-58a6ff?style=flat-square)](#-installation--uninstallation)
[![License: MIT](https://img.shields.io/badge/license-MIT-8b949e?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-57%20backend%20%2B%2015%20E2E-2ea043?style=flat-square)](#-testing)
[![Security](https://img.shields.io/badge/security%20audit-26%20issues%20fixed-f85149?style=flat-square)](docs/SECURITY_AUDIT.md)

**Desktop App** · **Docker GPU Sandbox** · **Monaco IDE** · **Live Terminal** · **Real-time Telemetry** · **Multi-PC GPU Cluster**

</div>

---

## 📖 Table of Contents

- [✨ What is GConnect?](#-what-is-gconnect)
- [🌟 Features](#-features)
- [🖥️ Screenshots](#️-screenshots)
- [📦 Installation & Uninstallation](#-installation--uninstallation)
  - [Desktop App (Recommended — Turnkey)](#desktop-app-recommended--turnkey)
  - [⚡ Mode-Driven Auto-Switching](#-mode-driven-auto-switching-how-it-works)
  - [🧹 Clean Uninstallation](#-clean-uninstallation)
  - [Headless Host Daemon Mode (Console / Server)](#headless-host-daemon-mode-console--server)
- [🚀 Quickstart](#-quickstart)
- [🌐 GPU Cluster — Pool GPUs from Multiple PCs](#-gpu-cluster--pool-gpus-from-multiple-pcs)
- [⚙️ Configuration](#️-configuration)
- [🏗️ Architecture](#️-architecture)
- [🔐 Security Model](#-security-model)
- [📊 Telemetry Explained](#-telemetry-explained)
- [🧪 Testing](#-testing)
- [🛠️ Building from Source](#️-building-from-source)
- [📋 API Reference](#-api-reference)
- [🩺 Troubleshooting](#-troubleshooting)
- [📁 Project Structure](#-project-structure)
- [🗺️ Roadmap](#️-roadmap)
- [📄 License](#-license)

---

## ✨ What is GConnect?

GConnect is an **open, self-hosted remote GPU development platform and Web IDE** built for developers who train, fine-tune, and run deep learning workloads on their own hardware instead of paying for rented cloud instances (Google Colab, RunPod, Vast.ai).

It works out-of-the-box for **any Python / PyTorch project**:
- 🧠 **Large Language Models & GenAI**: Hugging Face Transformers, vLLM, Ollama, Diffusers.
- 👁️ **Computer Vision & Perception**: YOLO, TorchVision, OpenCV, image generation pipelines.
- 🎙️ **Speech & Multimodal AI**: Faster-Whisper, Indic-Parler-TTS, Wav2Vec2, AudioCraft.
- 🔬 **Data Science & Machine Learning**: Scikit-learn, XGBoost, PyTorch distributed training.

Run the **GConnect desktop app** (or a lightweight daemon on a headless GPU server), and you get:

🖥️ **A full IDE in your browser or desktop window** — Monaco editor with multi-tab editing, Python syntax & pyflakes AST diagnostics, auto-save with conflict protection, and one-click script execution (`Ctrl+Enter`) inside a sandboxed Docker container.

⚡ **All your GPUs, no risk to the host** — your code runs inside a `pytorch/pytorch` container with hardware GPU passthrough (`DeviceRequest` via NVIDIA Container Toolkit), 8 GB shared memory (no DataLoader bus errors), and your workspace mounted as a persistent volume.

📊 **Real-time hardware telemetry** — live GPU utilization, VRAM meters, and temperature at 2 Hz directly from NVML, with multi-GPU and multi-PC aggregation.

🌐 **A GPU Cluster** — register GConnect daemons running on your other PCs and see every GPU you own in one dashboard with pooled VRAM totals and Ray distributed execution.

💾 **Universal Model Caching & Port Forwarding** — persistent Hugging Face cache (`HF_HOME=/workspace/.hf_cache`) ensures heavy weights download **once** and survive container restarts; forwarded auxiliary port (`8001`) streams user apps (Gradio, Streamlit, FastAPI, voice inference) directly to client laptops.

---

## 🌟 Features

| | Feature | Details |
|---|---|---|
| 🖥️ | **Desktop app, local OR remote** | Connection screen on first launch: run against **this PC's** sandbox or a **remote GPU workstation** (daemon URL + token) — switch anytime from the header |
| ⚡ | **1-Click GPU Sharing & Invite Card** | Turnkey GPU sharer dashboard: auto-detects real reachable IP (Tailscale/LAN), Windows Firewall status with 1-click UAC fix, hardware specs, and 1-click copyable invite |
| 🚀 | **Mode-Driven Auto-Switching** | "Develop on This PC" (loopback only, 127.0.0.1) vs "⚡ Share My GPU" (opt-in 0.0.0.0 public binding) — zero config files or env editing |
| 📝 | **Monaco Code Editor** | Multi-tab, syntax highlighting for 70+ languages, debounced auto-save, **mtime-based conflict protection** (409 on external changes) |
| 🚨 | **PROBLEMS Panel** | VS Code-style tab: Python syntax + pyflakes diagnostics as red/yellow squiggles and a clickable problem list (jumps to `file:line`), plus runtime tracebacks parsed from the terminal |
| 🖥️ | **Interactive Terminal** | Raw Docker exec PTY piped to xterm.js — interactive prompts, `pip`, `htop`, live `tqdm`, `Ctrl+C`, dynamic resize |
| ⚠️ | **Sandbox Status Banner** | Docker down? A yellow banner + Run-dialog tell you *where* and why — scripts never die silently |
| 📊 | **Live Telemetry** | 2 Hz NVML sampling, all GPUs, self-healing stream, "NO SIGNAL"/"STALE" honesty indicators; **GPU-less machines show CPU + RAM instead** |
| 🌐 | **GPU Cluster** | Multi-PC node registry, pooled VRAM, per-node/per-GPU stats, live status |
| 🐳 | **Non-blocking Docker Sandbox** | Auto-created/reconciled container, GPU passthrough, 8 GB shm, background image verification without UI freeze |
| 💾 | **Persistent Model Cache** | `HF_HOME=/workspace/.hf_cache` — weights for LLMs, diffusion models, or voice checkpoints download once |
| 🔌 | **Auxiliary Streaming Port (8001)** | Forwarded directly into container — host a **Gradio demo**, **Streamlit app**, **FastAPI server**, or audio stream |
| 🎵 | **In-Browser Audio Preview** | Click any `.wav`/`.mp3`/`.ogg`/`.flac` to play it in-app with waveform controls — perfect for speech AI |
| 📐 | **Resizable Layout** | Draggable sidebar (double-click resets) and editor/terminal split — VS Code-style |
| 📦 | **One-click Export** | Download files or the whole workspace as a streamed ZIP (excluding weights/caches) |
| 🔐 | **Secure by Default** | Token auth everywhere, loopback-only binding, no CORS wildcards — [full audit](docs/SECURITY_AUDIT.md), [report a vulnerability](.github/SECURITY.md) |
| 🖴️ | **Single System Tray** | Clean, single interactive system tray: open IDE, restart daemon, quit |
| 🧹 | **Disk Cleanup** | One-click model cache wipe with honest freed-space reporting |
| 🔌 | **Fully Offline** | All frontend libraries vendored — no CDNs, works on air-gapped networks |

---

## 🖥️ Screenshots

> Replace these placeholders with real screenshots before publishing.

| Web IDE (Monaco + xterm) | GPU Cluster Panel |
|---|---|
| *(editor + terminal split view)* | *(multi-PC nodes, pooled VRAM)* |

| Live Telemetry Bar | System Tray |
|---|---|
| *(GPU name, VRAM meter, UTILIZATION, TEMP)* | *(tray menu with daemon controls)* |

---

## 📦 Installation & Uninstallation

### Desktop App (Recommended — Turnkey)

**Prerequisites:** Windows 10/11 (macOS/Linux: build from source). Docker Desktop is recommended for container sandboxing (app degrades gracefully with clear notices if Docker is paused).

1. Grab the installer from the [**Releases page**](https://github.com/Kritix-AI/GConnect/releases/latest): download **`GConnect_2.2.2_x64-setup.exe`** (verify against the SHA-256 in the release notes if you like).
2. Run the installer — zero manual terminal commands, zero batch scripts, and zero `.env` setup required.
3. Launch **GConnect** from your Start Menu or Desktop shortcut.

---

### ⚡ Mode-Driven Auto-Switching: How it Works

When you open GConnect, you get three seamless options directly in the UI:

1. **`🖥️ Develop on This PC` (Private Mode)**:
   - Backend binds strictly to `127.0.0.1` (`CONNECT_ALLOW_LAN=false`).
   - Completely safe and isolated for local development on your machine.
2. **`⚡ Share My GPU` (Host Sharer Mode)**:
   - One-click consent: GConnect automatically spins up the backend with `CONNECT_ALLOW_LAN=true` and `CONNECT_HOST=0.0.0.0`.
   - Automatically queries your network adapters and displays your **real reachable IP** (prioritizing Tailscale `100.x.x.x`, or primary LAN `192.168.x.x`). **`127.0.0.1` is never shown to teammates in invites.**
   - Displays live GPU model, VRAM, and Windows Firewall status (with a 1-click UAC firewall rule button).
   - Generates a 1-click copyable invite card:
     ```text
     🚀 Connect to my GConnect GPU Workstation!
     Host URL: http://<real-ip>:8000
     Token: gconnect_sec_xxxxxx
     ```
3. **`🚀 Connect to GPU` (Remote Client Mode)**:
   - Paste the host URL and token from your teammate.
   - GConnect connects directly to the remote GPU workstation, streaming Monaco editor, file tree, GPU telemetry, and xterm terminal.

> **💡 Switch Anytime:** You can toggle between "Develop on This PC", "Connect to GPU", and "Share My GPU" at any point without restarting GConnect by clicking **Connection** in the top header!

---

### 🧹 Clean Uninstallation

GConnect adheres to standard Windows application packaging and leaves no lingering background services.

#### 1. Uninstall Application
- **Via Windows Settings:**
  - Press `Win + I` ➔ Go to **Apps** ➔ **Installed apps** (or *Apps & features*).
  - Search for **GConnect** ➔ Click the three dots `...` ➔ Select **Uninstall**.
- **Via Control Panel:**
  - Open `control.exe` ➔ Click **Programs and Features**.
  - Right-click **GConnect** ➔ Click **Uninstall**.

#### 2. Optional: Remove Configuration & App Data
The desktop app stores its settings and user workspace at `%APPDATA%\com.gconnect.app`. To completely remove all local data:
```powershell
Remove-Item -Recurse -Force "$env:APPDATA\com.gconnect.app"
```

#### 3. Optional: Remove Docker Sandbox Container & Image
If you used the local Docker GPU sandbox and want to reclaim disk space:
```powershell
# Remove the sandbox container
docker rm -f connect_dev_sandbox

# Remove the PyTorch CUDA base image (~9.5 GB)
docker rmi pytorch/pytorch:latest
```

---

### Headless Host Daemon Mode (Console / Server)

If you are running on a headless Linux or Windows server without the desktop UI:

**Windows:**
```cmd
scripts\setup_host.bat                      :: automatically binds to 0.0.0.0 for LAN/Tailscale sharing
```

**Linux / macOS:**
```bash
chmod +x scripts/setup_host.sh
./scripts/setup_host.sh                     :: automatically binds to 0.0.0.0 for LAN/Tailscale sharing
```

Or manually:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

---

## 🚀 Quickstart

1. **Launch GConnect** (desktop app, or daemon + browser)
2. The IDE opens with a sample `hello_gpu.py` — press **`Ctrl+Enter`** to run it in the sandbox
3. Watch the terminal pane: PyTorch version, CUDA availability, GPU name, and a test CUDA matrix allocation
4. The telemetry bar shows your **real GPU** — name, VRAM meter, utilization, temperature (green dot = live data)

**Universal Workload Examples:**

- **🧠 Train or fine-tune an LLM (Hugging Face / PyTorch):**
  ```bash
  # Inside the GConnect container terminal:
  pip install transformers datasets accelerate
  python train_model.py
  ```
  *(Model weights cache automatically to `/workspace/.hf_cache` so downloads are never repeated).*

- **🔌 Host a live Gradio / Streamlit / FastAPI UI (Forwarded Port 8001):**
  ```bash
  # Run any web server inside the container on port 8001:
  python app_gradio.py --port 8001
  ```
  *(Access immediately from your client laptop at `http://<GPU_PC_IP>:8001`).*

- **🎙️ Voice AI & Multimodal Inference (Whisper / TTS):**
  ```bash
  python voice_server.py --port 8001
  ```
  *(Generated audio files `.wav` appear in the file tree — click any audio file to preview instantly in-browser).*

---

## 🌐 GPU Cluster — Pool GPUs from Multiple PCs

Connect the GConnect daemon on each of your PCs and see **every GPU you own in one dashboard with pooled VRAM**.

```
┌─────────────────────────── Your Desk ───────────────────────────┐
│                                                                  │
│  GConnect Window                                                 │
│  ┌─────────────── GPU Cluster ────────────────┐                  │
│  │ ● this-pc      2 GPUs   11.2/24.0 GB      │                  │
│  │ ● aryan-laptop 1 GPU    3.1/12.0 GB       │                  │
│  │ ● rig-2        2 GPUs   0.8/48.0 GB       │                  │
│  │ POOLED: 5 GPUs · 15.1 / 84.0 GB VRAM      │                  │
│  └────────────────────────────────────────────┘                  │
└──────────────────────────────────────────────────────────────────┘
```

**Setup:**

1. Run GConnect on **every PC** (desktop app, or daemon with `CONNECT_ALLOW_LAN=true` + a fixed `CONNECT_AUTH_TOKEN` — see [.env.example](.env.example))
2. On your main PC: click **Cluster** in the header → add each remote (`http://<pc-ip>:8000` + its token)
3. Live pooled stats refresh automatically; offline nodes are flagged, never crash the panel

> **Honest engineering note:** VRAM never merges into one virtual CUDA device — no framework can do that across machines. "Pooling" means GConnect gives you one control room + real distributed execution: the bundled **Ray** bootstrap (`workspace/cluster/ray_node.py`) shards your workload across every GPU on every node, and `train_ddp.py` handles multi-GPU on a single machine. Details in [`workspace/cluster/README.md`](workspace/cluster/README.md).

<details>
<summary><b>📖 Deep-dive: how distributed training works here</b></summary>

**Single PC, multiple GPUs** — nothing extra to install:
```bash
torchrun --standalone --nproc_per_node=gpu /workspace/cluster/train_ddp.py
```

**Big-model sharding without code changes:**
```python
model = AutoModelForCausalLM.from_pretrained(name, device_map="auto", torch_dtype="auto")
```

**Multiple PCs (Ray cluster):**
```bash
# PC 1 (head):
python /workspace/cluster/ray_node.py --head
# PC 2..N (each GConnect terminal):
python /workspace/cluster/ray_node.py --join <pc1-ip>:6379

# From any node:
ray.init("ray://<pc1-ip>:10001")
ray.cluster_resources()   # every GPU on every PC
```
Requirements: same Ray version on all nodes; PCs must reach each other (Tailscale works great). Install with `pip install ray[train]`.

</details>

---

## ⚙️ Configuration

All settings via environment variables (`CONNECT_` prefix) or a `.env` file — see the annotated [`.env.example`](.env.example).

### Network & Security

| Variable | Default | Description |
|---|---|---|
| `CONNECT_HOST` | `127.0.0.1` | Bind address (loopback enforced unless `ALLOW_LAN`) |
| `CONNECT_PORT` | `8000` | Daemon port (desktop app auto-picks a free one) |
| `CONNECT_ALLOW_LAN` | `false` | **Opt-in** LAN exposure (`0.0.0.0`); requires a token |
| `CONNECT_AUTH_TOKEN` | *auto-generated* | Shared secret; persisted to `.gconnect/auth_token` if unset |
| `CONNECT_AUTH_DISABLED` | `false` | Dev-only; ignored when `ALLOW_LAN=true` |
| `CONNECT_INFERENCE_PORT` | `8001` | Container-forwarded port for audio streams |

### Workspace & Sandbox

| Variable | Default | Description |
|---|---|---|
| `CONNECT_WORKSPACE_DIR` | `./workspace` | Mounted at `/workspace` in the container |
| `CONNECT_CONTAINER_IMAGE` | `pytorch/pytorch:latest` | Sandbox image |
| `CONNECT_SHM_SIZE` | `8g` | Container shared memory (prevents DataLoader bus errors) |
| `CONNECT_EPHEMERAL_MODE` | `false` | Colab-style: models stay in the container layer, never touch host disk |
| `CONNECT_TELEMETRY_INTERVAL_SECONDS` | `0.5` | Telemetry cadence (2 Hz default) |

### Dangerous / Dev-only

| Variable | Default | Description |
|---|---|---|
| `CONNECT_ALLOW_LOCAL_FALLBACK` | `false` | ⚠️ Runs a **host** shell in the terminal when Docker is down — bypasses all container isolation. Loopback dev machines only. |

<details>
<summary><b>Remote Access (LAN / Tailscale)</b></summary>

```bash
# On the GPU workstation:
CONNECT_ALLOW_LAN=true \
CONNECT_AUTH_TOKEN=<long-random-secret> \
./scripts/setup_host.sh
```

From your laptop: open `http://<gpu-host>:8000`, paste the same token once (kept in `sessionStorage` for that tab). **Use an encrypted overlay (Tailscale/WireGuard)** — the daemon itself is HTTP.

</details>

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    GConnect Desktop App (Tauri v2)               │
│  ┌────────────────────────┐   ┌──────────────────────────────┐  │
│  │  Webview (IDE)         │   │  Rust Core                   │  │
│  │  ├─ Monaco editor      │◄──┤  ├─ spawns daemon sidecar    │  │
│  │  ├─ xterm terminal     │IPC│  ├─ backend_info {port,tok}  │  │
│  │  ├─ Cluster panel      │   │  ├─ system tray              │  │
│  │  └─ Telemetry bar      │   │  ├─ single-instance lock     │  │
│  └───────────┬────────────┘   │  └─ parent-PID watchdog      │  │
│              │ HTTP/WS + Bearer token                         │  │
│  ┌───────────▼───────────────────────────────────────────┐    │  │
│  │   Daemon (FastAPI, loopback)                          │    │  │
│  │   ├─ Auth middleware + origin allowlist               │    │  │
│  │   ├─ File APIs (atomic, traversal-safe, streaming ZIP)│    │  │
│  │   ├─ GPU telemetry broadcaster (NVML, 2 Hz, all GPUs) │    │  │
│  │   ├─ Cluster manager (multi-PC pooling)               │    │  │
│  │   └─ PTY bridge ◄── hijacked Docker exec socket       │    │  │
│  └───────────┬───────────────────────────────────────────┘    │  │
└──────────────┼────────────────────────────────────────────────┴──┘
               ▼
   ┌────────────────────────┐        ┌─────────────────────────┐
   │ Docker Sandbox         │        │ Remote GConnect Nodes   │
   │ pytorch/pytorch + CUDA │        │ (other PCs, optional)   │
   │ ├─ GPU: all devices    │        │ telemetry + status      │
   │ ├─ shm: 8 GB           │        │ polled for the Cluster  │
   │ ├─ /workspace volume   │        └─────────────────────────┘
   │ ├─ HF cache persistent │
   │ └─ voice stream :8001  │
   └────────────────────────┘
```

---

## 🔐 Security Model

GConnect's terminal is a shell in a container — the daemon is treated as **infrastructure, not a toy**:

- 🔑 **Token auth** on every `/api/*` and `/ws/*` route (Bearer header or `?token=` for downloads/WebSockets). Desktop mode: token is generated per-run and delivered to the webview via Tauri IPC — never in `localStorage`.
- 📍 **Loopback by default.** Network exposure requires `CONNECT_ALLOW_LAN=true` **and** a token.
- 🛡️ **Cross-origin rejection** — same-origin + Tauri webview origins only (CSRF / DNS-rebinding defense); wildcard CORS removed entirely.
- 🚫 **The host-shell fallback is disabled by default** (it was an unauthenticated host RCE; now opt-in for dev machines).
- 🔒 **Path traversal defense** on every file route (verified by tests), symlink-safe directory scans, atomic writes.
- 🧾 **Strict Tauri CSP** (`self` + loopback only), `core:default`-only capabilities, devtools disabled in production builds, OpenAPI docs endpoints off.
- 🔍 **Full audit trail:** [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) — 26 issues found and fixed, with the reasoning for every design decision.

**Treat your auth token like a password.** Remote mode should run inside Tailscale/WireGuard.

---

## 📊 Telemetry Explained

The header bar shows, at 2 Hz:

| Metric | Meaning |
|---|---|
| **GPU name** (×N) | All detected GPUs; hover for per-GPU breakdown |
| **VRAM** | Used/total across all local GPUs (aggregated) |
| **UTILIZATION** | `max(SM, memory-controller)` — same convention as Task Manager/overlays. Hover shows the split: `SM 4% · MemCtl 14%` |
| **TEMP** | Average GPU core temperature |
| 🟢 **Live dot** | Pulses while data flows; turns red + **STALE** badge if updates stop (>3s) |
| **NO SIGNAL / NO GPU** | Honest badges — signal lost vs. no NVIDIA hardware. Fabricated numbers are never shown |

Why not exactly Task Manager's number? Sampling moments differ by a second and VRAM accounting differs by ~100-200 MB (reserved memory). Trends and percentages match.

---

## 🧪 Testing

CI runs both suites on every push and pull request (GitHub Actions, [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

```bash
# Backend / API suite (Python)
pip install -r backend/requirements.txt
pytest tests/ -v            # or: npm run test:backend

# End-to-end suite (real browser against a real disposable daemon)
npm install
npm run test:e2e            # add :headed to watch the browser
```

**54 backend tests + 15 E2E tests**, all against isolated temp workspaces (they can never touch your real files or model cache):

- Auth: 401s, token via header/query, cross-origin rejection, Tauri-origin CORS grant, public health, docs disabled, loopback default
- Files: lifecycle, traversal matrix (save/read/delete/raw/download/rename), mtime conflicts (409), atomic writes, size caps, symlink skipping, streaming ZIP
- Lint: `/api/lint` syntax errors, pyflakes severities, oversized-content rejection
- Telemetry: NVML thread-safety, last-good cache, degraded flag, utilization convention, broadcaster self-healing, CPU/RAM host fallback payload
- Cluster: registry CRUD + persistence, token masking, offline-node handling, API flows
- Docker: GPU capability detection, config fingerprint reconciliation
- E2E (Chromium): auth overlay, file create/edit/autosave/rename/delete, audio modal, telemetry mode (GPU vs CPU/RAM), Docker-banner warning + Run dialog, PROBLEMS tab (lint entries + click-through), cluster panel, sidebar resize

---

## 🛠️ Building from Source

**Prerequisites:** Node 18+, Rust (MSVC on Windows), Python 3.10+, Docker optional.

```bash
git clone https://github.com/Kritix-AI/GConnect.git
cd GConnect

npm install                      # Tauri CLI + Playwright

python scripts/build_sidecar.py  # build the daemon sidecar (PyInstaller)

npm run dev                      # development (hot reload)
npm run build                    # production installer (NSIS on Windows)
```

**Output:** `src-tauri/target/release/bundle/nsis/GConnect_<version>_x64-setup.exe` — published as a [Release](https://github.com/Kritix-AI/GConnect/releases) asset for each version tag.

macOS/Linux: identical commands; the sidecar script auto-detects the Rust target triple. Extend `bundle.targets` in [`tauri.conf.json`](src-tauri/tauri.conf.json) for `dmg`/`appimage`/`deb`.

<details>
<summary><b>Useful scripts</b></summary>

| Command | Purpose |
|---|---|
| `npm run dev` | Desktop app in dev mode |
| `npm run build` | Release installer |
| `npm run sidecar` | Build/rebuild the daemon sidecar |
| `npm run test:backend` | Run the Python test suite |
| `npm run test:e2e` | Run the Playwright browser suite |
| `python scripts/build_sidecar.py --clean` | Remove sidecar build artifacts |

</details>

---

## 📋 API Reference

Base URL: `http://127.0.0.1:<port>` — all routes except `/api/health` and `/api/host/*` (loopback-only) require the token.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe (public) |
| `GET` | `/api/host/share-info` | Hardware specs, detected LAN/Tailscale IPs, and auth token (loopback) |
| `POST` | `/api/host/configure-token` | Set custom host authentication passcode (loopback) |
| `GET` | `/api/host/firewall-status` | Check Windows Firewall status for ports 8000 & 8001 |
| `POST` | `/api/host/firewall-rule` | Request elevated UAC firewall rule for inbound ports 8000 & 8001 |
| `GET` | `/api/telemetry/snapshot` | Current GPU stats (all GPUs) |
| `WS` | `/ws/telemetry` | Live telemetry stream (2 Hz) |
| `WS` | `/ws/terminal` | Interactive container PTY (text = input, binary = control) |
| `GET` | `/api/files` | Workspace tree |
| `GET` | `/api/files/content?path=` | Read file (size-capped, returns `mtime`) |
| `POST` | `/api/files/save` | Atomic save (optional `expected_mtime` → 409 on conflict) |
| `POST` | `/api/files/create` · `DELETE /api/files/delete` · `POST /api/files/rename` | Manage files |
| `GET` | `/api/files/raw?path=` | Serve media (audio player) |
| `GET` | `/api/files/download?path=` | Download file / streamed ZIP |
| `GET` | `/api/container/status` · `POST /api/container/restart` | Sandbox control |
| `POST` | `/api/cache/wipe` | Free disk (honest reporting) |
| `GET` | `/api/cluster/status` | All nodes + pooled VRAM |
| `GET/POST/DELETE` | `/api/cluster/nodes` | Cluster registry (tokens never returned) |

---

## 🩺 Troubleshooting

<details>
<summary><b>Terminal says "Docker sandbox is not running"</b></summary>

Start Docker Desktop, then reconnect (the terminal auto-retries every 3s). The old behavior — silently giving you a **host** shell — is now disabled by default for security. Dev-only override: `CONNECT_ALLOW_LOCAL_FALLBACK=true`.
</details>

<details>
<summary><b>Telemetry shows "NO GPU" though I have a GPU</b></summary>

The daemon retries NVML every 30s — it usually self-heals if the driver was busy at startup. If it persists: confirm `nvidia-smi` works in a terminal, and check `.gconnect/logs/daemon.log` (or the app-data `logs/` folder in desktop mode).
</details>

<details>
<summary><b>Port 8000 seems occupied / weird 401 errors</b></summary>

The desktop app auto-picks a free port and records it in `runtime/backend.json` — the UI always uses the right one. For host mode, set `CONNECT_PORT`. If a stale daemon from an old build survives a force-quit, end the `gconnect-backend` process in Task Manager (new builds include a watchdog that prevents this).
</details>

<details>
<summary><b>`npm run build` fails with "not enough space" (os error 112)</b></summary>

The NSIS bundler stages in your `TEMP` dir. Free space on that drive, or redirect: `TMP=D:\tmp TEMP=D:\tmp npm run build`.
</details>

<details>
<summary><b>NSIS download times out during build</b></summary>

Pre-seed the NSIS toolchain cache — download [`nsis-3.13.zip`](https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.13/nsis-3.13.zip), extract so the files sit **directly inside** `%LOCALAPPDATA%\tauri\NSIS\`, and place [`nsis_tauri_utils.dll` v0.5.3](https://github.com/tauri-apps/nsis-tauri-utils/releases/download/nsis_tauri_utils-v0.5.3/nsis_tauri_utils.dll) at `%LOCALAPPDATA%\tauri\NSIS\Plugins\x86-unicode\additional\`.
</details>

<details>
<summary><b>Cluster node shows offline / "Authentication failed"</b></summary>

The remote PC must run its daemon with `CONNECT_ALLOW_LAN=true` and you must add it with that daemon's exact token. Check you used `http://` (not `https://`) and that the PCs can reach each other (ping the port).
</details>

---

## 📁 Project Structure

```
GConnect/
├── backend/                 # FastAPI daemon (Python)
│   ├── auth.py              #   token auth + origin policy + Tauri CORS + loopback host APIs
│   ├── cluster_manager.py   #   multi-PC GPU cluster
│   ├── gpu_telemetry.py     #   NVML sampling + WS broadcaster + CPU/RAM fallback
│   ├── linter.py            #   /api/lint (ast + pyflakes) for PROBLEMS
│   ├── network_utils.py     #   Tailscale (100.x.x.x) & LAN IP discovery
│   ├── docker_manager.py    #   sandbox lifecycle + non-blocking background verification
│   ├── file_manager.py      #   file APIs (traversal-safe, atomic)
│   ├── pty_bridge.py        #   WebSocket <-> container PTY
│   └── main.py              #   app wiring + host sharing endpoints
├── frontend/                # IDE (vanilla JS, fully offline)
│   ├── index.html           #   layout + modals (connection, share, cluster, problems)
│   ├── css/                 #   GitHub-dark theme + share overlay styling
│   ├── js/                  #   auth, editor, terminal, share, problems, cluster, telemetry
│   └── vendor/              #   Monaco 0.45, xterm 5.3, Lucide (pinned)
├── src-tauri/               # Desktop app (Rust)
│   ├── src/main.rs          #   mode auto-switching, sidecar lifecycle, single tray, UAC IPC
│   ├── tauri.conf.json      #   CSP, window, single interactive tray bundle
│   └── capabilities/        #   minimal permissions (core:default)
├── packaging/               # PyInstaller sidecar build
│   ├── backend_entry.py     #   sidecar entrypoint
│   └── gconnect_backend.spec#   spec (SPECPATH-relative, CWD-independent)
├── scripts/                 # setup_host (win/linux), build_sidecar
├── tests/                   # 57 backend tests (conftest isolates everything)
├── e2e/                     # 15 Playwright tests + webServer config
├── playwright.config.js     # boots a disposable daemon on :8900 per run
├── workspace/               # your files (mounted into the container)
│   └── cluster/             #   Ray + torchrun distributed helpers
├── docs/                    # SECURITY_AUDIT.md, CHANGELOG.md
└── .env.example             # all configuration, annotated
```

---

## 🗺️ Roadmap

- [ ] macOS & Linux installer builds (CI)
- [ ] Auto-updater (needs release signing keys)
- [ ] Cluster-wide job launcher (dispatch training runs to nodes from the UI)
- [ ] Per-node workspaces sync
- [ ] Jupyter kernel inside the sandbox
- [ ] GPU process list (who's using VRAM)

---

## 📄 License

[MIT](LICENSE) © Kritix-AI

---

<div align="center">

**GConnect** — your GPUs, your rules. ⚡

[Report a bug](https://github.com/Kritix-AI/GConnect/issues) · [Request a feature](https://github.com/Kritix-AI/GConnect/issues) · [Read the audit](docs/SECURITY_AUDIT.md)

</div>
